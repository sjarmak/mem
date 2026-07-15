#!/usr/bin/env python3
"""mem-rk41.3.2 — builtin native-memory persistent-env arm, staged over the tool-requiring corpus.

The ``builtin`` sibling to ``grid_toolreq_realagent.py``'s none/oracle/ours sweep: for every frozen
tool-requiring task, run ``membench.runner.toolreq_builtin.run_builtin_arm`` under both memory-trust
channels — TWO real ``claude -p`` calls per repeat (establish, then a bare goal call) sharing one
sandbox cwd + one ``CLAUDE_CONFIG_DIR``, so Claude Code's own native memory is the sole continuity
channel. It does NOT run ``none``, ``oracle`` (mem-rk41.3) or ``ours`` (mem-rk41.3.1).

This is the paid ceiling driver. It STAGES but never over-reaches:

* ``--dry-run`` runs the identical two-call loop with a simulated memory-copying agent
  — no token, no ``claude`` — proving the establish/goal wiring end to end for FREE;
* a real run REFUSES to spend without ``CLAUDE_CODE_OAUTH_TOKEN`` and prints the exact
  ``scix-batch`` go-command + the call count / worst-case wall-clock (double the
  none/oracle cost per repeat: establish + goal), so the paid fire stays an explicit,
  cost-disclosed, per-action decision (Stephanie's call);
* a real run also spends one cheap PREFLIGHT establish+check cycle before the first task
  it will MEASURE and HALTS if the fact never reached native memory, so a mechanism that
  never fires produces a diagnosed refusal rather than an uninterpretable null. The arm
  turns the mechanism ON itself (``autoMemoryEnabled`` is seeded into each pristine
  ``CLAUDE_CONFIG_DIR``), so a halt is a real finding, not an account to go fix;
* per-task results persist to ``--out/<work_id>.json`` and are REUSED on re-run, so a
  token-expiry or OOM mid-sweep does not re-pay for finished tasks — and a resume served
  entirely from that cache measures nothing, so it spends NOTHING, preflight included
  (mem-dblue).

This file is the SHELL: argparse, the spend gate, and printing. The grid — the arm's cells, the
verdict rule, the fingerprints, the preflight and its halt rule, and the resume cache whose every
defect an untyped ``scripts/`` cache has shipped before — is
``membench.runner.toolreq_builtin_grid`` on top of the shared ``membench.runner.resume_cache``,
inside ``mypy --strict``. Read the cache invariant there.

    # FREE — prove the two-call establish/goal wiring end to end:
    uv run python scripts/grid_toolreq_builtin.py --corpus-dir fixtures/worlds-tool --dry-run

    # PAID — Stephanie's per-action go (wrap in scix-batch; the script prints the command):
    scix-batch -- env CLAUDE_CODE_OAUTH_TOKEN=... \
        uv run python scripts/grid_toolreq_builtin.py --corpus-dir fixtures/worlds-tool
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from membench.runner.headless_agent import CHANNELS, DEFAULT_TIMEOUT_S, HeadlessAgentError
from membench.runner.toolreq_builtin_grid import (
    AGENT_ERROR,
    LEAK,
    NOT_ENGAGED,
    SUMMARY_NAME,
    PreflightHaltError,
    calls_per_repeat,
    paid_call_count,
    preflight_gate,
    run_corpus,
)
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, load_corpus_with_sequences

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = PROJECT_ROOT / "memory-bench/fixtures/worlds-tool"
DEFAULT_OUT = PROJECT_ROOT / ".mem/toolreq-builtin"
ENV_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"


def _print_go_command(
    tasks: Sequence[ToolReqRealAgentTask], repeats: int, out_dir: Path, corpus_dir: Path
) -> None:
    n_tasks = len(tasks)
    calls = paid_call_count(tasks, repeats=repeats)
    worst_hours = calls * DEFAULT_TIMEOUT_S / 3600.0
    # One factor for every task: the leg count is the ARM's, not the task's (`paid_call_count`), so
    # the factorization the human reads below multiplies out to `calls` by construction.
    per_repeat = calls_per_repeat(tasks[0])
    print(
        f"REFUSING to spend: {ENV_OAUTH} is unset.\n"
        f"  This paid sweep is {calls} real `claude -p` call(s) "
        f"({n_tasks} task x {len(CHANNELS)} channel x {repeats} repeat x "
        f"{per_repeat} calls/repeat [establish+goal] — {per_repeat}x the none/oracle "
        f"1-call/repeat cost); worst-case wall-clock ~{worst_hours:.1f}h at the "
        f"{DEFAULT_TIMEOUT_S:.0f}s timeout.\n"
        f"  Plus one PREFLIGHT establish+check cycle ({per_repeat} calls) before the first "
        "task actually measured — and none at all if every task is already cached.\n"
        f"  Per-task results persist to {out_dir} and are reused on re-run (resumable).\n"
        "  MECHANISM: native memory is turned ON by this script — `autoMemoryEnabled` is a "
        "$CLAUDE_CONFIG_DIR/settings.json key, so the pristine per-repeat config dir is "
        "seeded with it directly. It is NOT an account/pool-level flag, and needs nothing "
        "enabled on the OAuth account (mem-rk41.3.2 Q3).\n"
        "  To fire (Stephanie's per-action go), source the token from an account home and "
        "wrap in scix-batch:\n\n"
        f"    scix-batch -- env {ENV_OAUTH}=... \\\n"
        f"        uv run python scripts/grid_toolreq_builtin.py --corpus-dir {corpus_dir}\n\n"
        "  Or prove the wiring for free first:\n"
        f"    uv run python scripts/grid_toolreq_builtin.py --corpus-dir {corpus_dir} --dry-run"
    )


# What a human should DO about each halt kind. The counsel is the shell's (IO policy); which kind a
# preflight IS is a decision about a measurement and lives in the grid, under the type checker
# (`preflight_kind`). Keyed by kind so a new kind there is a KeyError here, not a silent generic.
_HALT_COUNSEL = {
    AGENT_ERROR: (
        "This is a diagnosed halt, not a mechanism-disabled verdict (mem-rk41.3.2 Q3); "
        "retry once the underlying failure (network, rate limit, CLI issue) is resolved."
    ),
    NOT_ENGAGED: (
        "This is NOT an account/pool problem to go chase: `autoMemoryEnabled` is a "
        "$CLAUDE_CONFIG_DIR/settings.json key and this script already seeds it true in the "
        "pristine per-repeat config dir (mem-rk41.3.2 Q3). A halt here means the mechanism "
        "genuinely did not fire — the finding the arm exists to surface, not a "
        "misconfiguration to work around."
    ),
    LEAK: (
        "The mechanism is not what failed — the ISOLATION is. The goal leg answered with "
        "native memory disengaged, so the sandbox cwd carried the fact between the legs and "
        "every cell this sweep would measure is uninterpretable for the same reason. Fix the "
        "firewall before spending; a NOT-ENGAGED verdict from this corpus cannot be trusted "
        "while a leg can pass without the mechanism."
    ),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repeats", type=int, default=3, help="runs per (task, channel)")
    parser.add_argument(
        "--model", default="", help="pins --model; empty reads MEMBENCH_AGENT_MODEL"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N tasks (smoke subset)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="simulate the agent; no token, no claude"
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="skip the real preflight establish+check cycle (not recommended)",
    )
    args = parser.parse_args(argv)

    # `--repeats 0` runs zero agent turns per cell and persists 0/0 rows, which the verdict rule
    # would read as a confident "NOT-ENGAGED: the fact never reached native memory (0/0)" for a task
    # that was NEVER EVALUATED. Refuse it at the flag, with a message that says why;
    # `BaseRunIdentity.repeats` / `BaseCellOutcome.runs` (both >= 1) are the structural backstop.
    if args.repeats < 1:
        parser.error("--repeats must be >= 1; 0 evaluates nothing and fabricates a 0/0 verdict")

    corpus_dir = args.corpus_dir
    _, tasks = load_corpus_with_sequences(corpus_dir)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    if not tasks:
        print(f"no tool-requiring tasks under {corpus_dir}", file=sys.stderr)
        return 1

    if not args.dry_run and not os.environ.get(ENV_OAUTH):
        _print_go_command(tasks, args.repeats, args.out, corpus_dir)
        return 2

    # Why a hook rather than a step run here: `preflight_gate`. `--dry-run` spends nothing by
    # definition, so it takes no gate at all.
    gate = (
        None
        if args.dry_run or args.skip_preflight
        else preflight_gate(tasks[0], model=args.model, announce=print)
    )

    mode = "DRY-RUN (simulated agent, no tokens)" if args.dry_run else "PAID real claude -p"
    print(
        f"toolreq builtin-arm sweep: {mode}; {len(tasks)} task(s) x {len(CHANNELS)} channel x "
        f"{args.repeats} repeat x {calls_per_repeat(tasks[0])} calls/repeat"
    )
    try:
        summary = run_corpus(
            tasks,
            out_dir=args.out,
            repeats=args.repeats,
            model=args.model,
            dry_run=args.dry_run,
            before_first_spend=gate,
        )
    except PreflightHaltError as halt:
        # What keeps a preflight agent-error out of the SWEEP HALT arm below is the CONVERSION at
        # the gate, not this arm's position: the two are sibling RuntimeErrors, neither catches the
        # other. The distinction is worth the type — a preflight failure has measured nothing, so
        # the mid-sweep counsel ("finished tasks are persisted, re-run to resume") would point the
        # operator at a resume with nothing to skip.
        print(
            f"PREFLIGHT HALT: {halt.line}. Refusing to spend on the full sweep.\n"
            f"  {_HALT_COUNSEL[halt.kind]}",
            file=sys.stderr,
        )
        return 3
    except HeadlessAgentError as exc:
        # The preflight is not the only paid boundary: a rate-limited/flaky/timed-out
        # `claude -p` mid-sweep gets the same diagnosed-halt treatment, never a raw
        # traceback. Finished tasks are already persisted, so resuming is cheap.
        print(
            f"SWEEP HALT: a real agent call failed mid-sweep ({exc}) — stopping instead "
            f"of spending into a broken link. Finished tasks are persisted under "
            f"{args.out} and are REUSED on re-run: re-run the same command to resume "
            "once the underlying failure (network, rate limit, CLI issue) is resolved.",
            file=sys.stderr,
        )
        return 3
    for record in summary["per_task"]:
        print(f"  {record['work_id']:<24} {record['verdict']}")
    summary_path = args.out / SUMMARY_NAME
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n{summary['separates_all_channels']}/{summary['n_tasks']} task(s) separate on both "
        f"channels; leaked={summary['leaked'] or 'none'}; "
        f"not_engaged={summary['not_engaged'] or 'none'}; summary -> {summary_path}"
    )
    if args.dry_run:
        print(
            "(DRY-RUN proves the two-call establish/goal wiring + scoring path ONLY — it "
            "does NOT exercise whether a real claude -p session persists to MEMORY.md.)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
