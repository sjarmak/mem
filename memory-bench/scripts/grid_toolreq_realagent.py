#!/usr/bin/env python3
"""mem-rk41.3 / .3.1 — real-agent none/oracle/ours ceiling-at-scale over the
tool-requiring corpus.

The corpus-wide generalization of the mem-rk41.4 single-goal probe: it adapts every frozen
tool-requiring world (``membench.runner.toolreq_realagent``) onto the ``Write``-bridge and
runs the SAME arm loop + external scorer the probe uses (``realagent_probe.run_arm`` /
``score_goal_action``), across ``none`` (empty memory, a leak detector), ``oracle`` (the
id-exact ceiling), and ``ours`` (a genuine ``mem retrieve`` payload), under both
memory-trust channels. Values are OPAQUE on this path, so ``none`` can pass only on a
genuine leak — the verdict rule is the probe's, restored.

``ours`` (mem-rk41.3.1) seeds a fresh ``.mem/store.db`` from the SAME corpus, using
rk41.5's ``toolreq_bundle_adapter`` substrate (the shared value-free apply_config staleness
signature) plus lessons whose facts state each sequence's CURRENT opaque value — the SAME
opaque token space ``oracle`` surfaces. That shared value space is load-bearing: under a
different value-map, ``ours`` could never satisfy the scorer even if retrieval fired
correctly. Cross-task retrieval will generally NOT surface the queried task's own
sequence-unique opaque value, so ``ours`` scoring near ``none`` rather than near ``oracle``
is the expected, honest substrate finding, not a defect to work around.

This is the paid ceiling driver. It STAGES but never over-reaches:

* ``--dry-run`` runs the identical loop with a simulated memory-copying agent — no token,
  no ``claude`` — proving the arms separate end to end for FREE. Seeding the ``ours``
  store and resolving its payload are ALSO free (real ``mem`` CLI calls, no agent turn) and
  run on every invocation, dry-run or paid alike — only ``claude -p`` is spend-gated;
* a real run REFUSES to spend without ``CLAUDE_CODE_OAUTH_TOKEN`` and prints the exact
  ``scix-batch`` go-command + the run count / worst-case wall-clock, so the paid fire stays
  an explicit, cost-disclosed, per-action decision (Stephanie's call);
* per-task results persist to ``--out/<work_id>.json`` and are REUSED on re-run, so a
  token-expiry or OOM mid-sweep does not re-pay for finished tasks.

This file is the SHELL: argparse, repo-root path constants, and printing — nothing else. Every
measured step is a typed ``membench`` call it merely wires together: the grid core (arms, verdict,
fingerprints, and the resume cache whose every defect this file has shipped) is
``membench.runner.toolreq_grid``, and the ``ours``-store seeder + payload resolver are
``membench.runner.toolreq_realagent.seed_ours_store_and_resolve_payloads`` /
``membench.harbor.bundle_grid.resolve_payloads``. All of it is inside ``mypy --strict``,
which this file is not. Read the cache invariant in ``toolreq_grid``.

It does NOT run the ``builtin`` arm (mem-rk41.3.2): that needs a persistent native-memory
env, separately substantial.

    # FREE — prove the wiring over the whole corpus (still needs bin/mem built):
    uv run python scripts/grid_toolreq_realagent.py --corpus-dir fixtures/worlds-tool --dry-run

    # PAID — Stephanie's per-action go (wrap in scix-batch; the script prints the command):
    scix-batch -- env CLAUDE_CODE_OAUTH_TOKEN=... \
        uv run python scripts/grid_toolreq_realagent.py --corpus-dir fixtures/worlds-tool
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from membench.runner.headless_agent import (
    DEFAULT_TIMEOUT_S,
    REFUSE_UNPINNED_MODEL,
    HeadlessAgentError,
    a_paid_run_needs_a_model,
)
from membench.runner.toolreq_grid import SUMMARY_NAME, run_corpus, worst_case_paid_call_count
from membench.runner.toolreq_realagent import (
    ToolReqRealAgentTask,
    load_corpus_with_sequences,
    seed_ours_store_and_resolve_payloads,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = PROJECT_ROOT / "memory-bench/fixtures/worlds-tool"
DEFAULT_OUT = PROJECT_ROOT / ".mem/toolreq-realagent"
DEFAULT_MEM_BIN = str(PROJECT_ROOT / "bin/mem")
ENV_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"

# The channel-non-uniform plan shape the disclosed paid-call count is FOR (see toolreq_grid): one
# string so the two spend disclosures below cannot describe the same fire two different ways.
_PAID_SHAPE = "none once + oracle,ours per channel"


def _print_go_command(
    tasks: Sequence[ToolReqRealAgentTask], repeats: int, out_dir: Path, corpus_dir: Path
) -> None:
    # DERIVED from the plan (worst_case_paid_call_count), never `n_tasks x arm x channel x repeat`:
    # `none` is measured ONCE per task (channel-invariant; mem-dg5fm), so a hardcoded
    # `len(ARMS) x len(CHANNELS)` would over-disclose the fire by `n_tasks x repeats` calls. The
    # grid is now channel-NON-uniform (RECALLED runs none+oracle+ours, TRUSTED runs oracle+ours),
    # so there is no honest single `x channel` factor to print — the total and shape ARE the gate.
    n_tasks = len(tasks)
    runs = worst_case_paid_call_count(tasks, repeats=repeats)
    worst_hours = runs * DEFAULT_TIMEOUT_S / 3600.0
    print(
        f"REFUSING to spend: {ENV_OAUTH} is unset.\n"
        f"  This paid sweep is at most {runs} real `claude -p` run(s) "
        f"({n_tasks} task x {repeats} repeat; {_PAID_SHAPE}); "
        f"worst-case wall-clock ~{worst_hours:.1f}h at the {DEFAULT_TIMEOUT_S:.0f}s timeout.\n"
        f"  Per-task results persist to {out_dir} and are reused on re-run (resumable).\n"
        "  To fire (Stephanie's per-action go), source the token from an account home and "
        "wrap in scix-batch:\n\n"
        f"    scix-batch -- env {ENV_OAUTH}=... \\\n"
        f"        uv run python scripts/grid_toolreq_realagent.py --corpus-dir {corpus_dir}\n\n"
        "  Or prove the wiring for free first:\n"
        f"    uv run python scripts/grid_toolreq_realagent.py --corpus-dir {corpus_dir} --dry-run"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repeats", type=int, default=3, help="runs per (task, arm, channel)")
    parser.add_argument(
        "--model",
        default="",
        help="pins --model; else MEMBENCH_AGENT_MODEL; a paid run refuses if neither names one",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N tasks (smoke subset)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="simulate the agent; no token, no claude"
    )
    parser.add_argument("--mem-bin", default=DEFAULT_MEM_BIN, help="the built mem CLI binary")
    parser.add_argument(
        "--store", type=Path, default=None, help="ours store path (default <out>/store.db)"
    )
    args = parser.parse_args(argv)

    # `--repeats 0` runs zero agent turns per cell and persists six 0/0 rows, which
    # `task_verdict` then reads as a confident "KILL: oracle ceiling 0/0 — no separation" for a
    # task that was NEVER EVALUATED. Refuse it at the flag, with a message that says why;
    # `RunIdentity.repeats` / `CellOutcome.runs` (both >= 1) are the structural backstop.
    if args.repeats < 1:
        parser.error("--repeats must be >= 1; 0 evaluates nothing and fabricates a 0/0 verdict")

    corpus_dir = args.corpus_dir
    sequences, tasks = load_corpus_with_sequences(corpus_dir)
    if args.limit is not None:
        sequences = sequences[: args.limit]
        tasks = tasks[: args.limit]
    if not tasks:
        print(f"no tool-requiring tasks under {corpus_dir}", file=sys.stderr)
        return 1

    if not args.dry_run and not os.environ.get(ENV_OAUTH):
        _print_go_command(tasks, args.repeats, args.out, corpus_dir)
        return 2

    if a_paid_run_needs_a_model(args.model, dry_run=args.dry_run):
        print(REFUSE_UNPINNED_MODEL)
        return 2

    mode = "DRY-RUN (simulated agent, no tokens)" if args.dry_run else "PAID real claude -p"
    print(
        f"toolreq real-agent sweep: {mode}; {len(tasks)} task(s) x {args.repeats} repeat; "
        f"up to {worst_case_paid_call_count(tasks, repeats=args.repeats)} `claude -p` call(s) "
        f"({_PAID_SHAPE})"
    )
    store_path = args.store if args.store is not None else args.out / "store.db"
    try:
        summary = run_corpus(
            tasks,
            sequences,
            out_dir=args.out,
            repeats=args.repeats,
            model=args.model,
            dry_run=args.dry_run,
            store_path=store_path,
            mem_bin=args.mem_bin,
            seed_fn=seed_ours_store_and_resolve_payloads,
        )
    except HeadlessAgentError as exc:
        # A rate-limited/flaky/timed-out `claude -p` mid-sweep gets a diagnosed halt, never a raw
        # traceback: finished tasks are already persisted, so resuming is cheap — but only if the
        # operator is told so. The sibling driver (`grid_toolreq_builtin.py`) gives exactly this
        # treatment on the same boundary, and the two must not disagree about what a paid-path halt
        # looks like. `run_corpus` also resolves the CLI version up front, so this arm covers the
        # halts that happen BEFORE any spend as well as the ones partway through.
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
        f"channels; leaked={summary['leaked'] or 'none'}; summary -> {summary_path}"
    )
    if args.dry_run:
        print("(DRY-RUN proves arm wiring + scorer discriminate; real behaviour is the paid run.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
