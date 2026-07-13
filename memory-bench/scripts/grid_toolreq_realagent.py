#!/usr/bin/env python3
"""mem-rk41.3 — real-agent none/oracle ceiling-at-scale over the tool-requiring corpus.

The corpus-wide generalization of the mem-rk41.4 single-goal probe: it adapts every frozen
tool-requiring world (``membench.runner.toolreq_realagent``) onto the ``Write``-bridge and
runs the SAME arm loop + external scorer the probe uses (``realagent_probe.run_arm`` /
``score_goal_action``), across ``none`` (empty memory, a leak detector) and ``oracle`` (the
id-exact ceiling), under both memory-trust channels. Values are OPAQUE on this path, so
``none`` can pass only on a genuine leak — the verdict rule is the probe's, restored.

This is the paid ceiling driver. It STAGES but never over-reaches:

* ``--dry-run`` runs the identical loop with a simulated memory-copying agent — no token,
  no ``claude`` — proving the arms separate end to end for FREE;
* a real run REFUSES to spend without ``CLAUDE_CODE_OAUTH_TOKEN`` and prints the exact
  ``scix-batch`` go-command + the run count / worst-case wall-clock, so the paid fire stays
  an explicit, cost-disclosed, per-action decision (Stephanie's call);
* per-task results persist to ``--out/<work_id>.json`` and are REUSED on re-run, so a
  token-expiry or OOM mid-sweep does not re-pay for finished tasks.

It does NOT run the ``ours`` or ``builtin`` arms (mem-rk41.3.1 / .2): ``ours`` needs the
rk41.5 store payload baked into the prompt, ``builtin`` needs a persistent native-memory
env — both separately substantial.

    # FREE — prove the wiring over the whole corpus:
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
from dataclasses import asdict
from pathlib import Path
from typing import Any

from membench.runner.headless_agent import DEFAULT_TIMEOUT_S, MemoryChannel
from membench.runner.realagent_probe import ArmOutcome, run_arm
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, load_corpus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = PROJECT_ROOT / "memory-bench/fixtures/worlds-tool"
DEFAULT_OUT = PROJECT_ROOT / ".mem/toolreq-realagent"
ENV_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"

ARMS = ("none", "oracle")
CHANNELS = (MemoryChannel.RECALLED, MemoryChannel.TRUSTED)


def arm_memory(task: ToolReqRealAgentTask, arm: str) -> dict[str, str]:
    """The surfaced memory per arm: nothing for ``none`` (the leak detector), the id-exact
    opaque ceiling for ``oracle``."""
    if arm == "none":
        return {}
    if arm == "oracle":
        return dict(task.oracle_memory)
    raise ValueError(f"unknown arm {arm!r} (expected one of {ARMS})")


def evaluate_task(
    task: ToolReqRealAgentTask,
    *,
    repeats: int,
    model: str,
    dry_run: bool,
    channels: Sequence[MemoryChannel] = CHANNELS,
) -> list[ArmOutcome]:
    """Run every (arm, channel) cell for one task."""
    outcomes: list[ArmOutcome] = []
    for channel in channels:
        for arm in ARMS:
            outcomes.append(
                run_arm(
                    arm=arm,
                    step=task.goal_step,
                    memory=arm_memory(task, arm),
                    channel=channel,
                    repeats=repeats,
                    model=model,
                    dry_run=dry_run,
                    current_values=task.current_opaque_values,
                )
            )
    return outcomes


def task_verdict(outcomes: Sequence[ArmOutcome]) -> str:
    """The probe's per-channel verdict rule (valid again under opaque values): ``none``
    passing means a leak; ``none`` 0 + ``oracle`` ceiling means the arms separate."""
    by = {(o.arm, o.channel): o for o in outcomes}
    lines: list[str] = []
    for channel in (c.value for c in CHANNELS):
        none_o = by.get(("none", channel))
        oracle_o = by.get(("oracle", channel))
        if none_o is None or oracle_o is None:
            continue
        runs = oracle_o.runs
        if none_o.passes > 0:
            call = f"LEAK: none {none_o.passes}/{none_o.runs} — value reached the prompt"
        elif oracle_o.passes == runs and runs > 0:
            call = f"SEPARATES: none 0/{runs}, oracle {oracle_o.passes}/{runs}"
        elif oracle_o.passes == 0:
            call = f"KILL: oracle ceiling 0/{runs} — no separation"
        else:
            call = f"WEAK: none 0/{runs}, oracle {oracle_o.passes}/{runs} — add repeats"
        lines.append(f"[{channel}] {call}")
    return " | ".join(lines)


def run_corpus(
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    out_dir: Path,
    repeats: int,
    model: str,
    dry_run: bool,
    resume: bool = True,
) -> dict[str, Any]:
    """Evaluate every task, persisting one ``<work_id>.json`` each. A persisted result is
    reused only when its ``(repeats, dry_run, model)`` identity matches — so a FREE dry-run's
    simulated result can never satisfy a PAID run over the same ``--out`` (the highest-
    severity confound: a paid ceiling silently reporting a fabricated pass). A corrupt or
    partial cache file (a process killed mid-write) is treated as a miss and re-executed,
    never a crash."""
    out_dir.mkdir(parents=True, exist_ok=True)
    per_task: list[dict[str, Any]] = []
    executed = 0
    reused = 0
    identity = {"repeats": repeats, "dry_run": dry_run, "model": model}
    for task in tasks:
        result_path = out_dir / f"{task.work_id}.json"
        cached: dict[str, Any] | None = None
        if resume and result_path.is_file():
            try:
                loaded = json.loads(result_path.read_text(encoding="utf-8"))
                if all(loaded.get(key) == value for key, value in identity.items()):
                    cached = loaded
            except (json.JSONDecodeError, OSError):
                cached = None  # corrupt/partial file -> re-execute this one task
        if cached is not None:
            outcomes = [ArmOutcome(**row) for row in cached["outcomes"]]
            reused += 1
        else:
            outcomes = evaluate_task(task, repeats=repeats, model=model, dry_run=dry_run)
            executed += 1
        record = {
            "work_id": task.work_id,
            **identity,
            "outcomes": [asdict(o) for o in outcomes],
            "verdict": task_verdict(outcomes),
        }
        # Atomic publish: write a sibling temp file then rename, so a kill mid-write leaves
        # either the old result or the new one, never a half-written JSON the next resume trips on.
        tmp_path = result_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(result_path)
        per_task.append(record)
    separates = sum(1 for r in per_task if r["verdict"].count("SEPARATES") == len(CHANNELS))
    leaked = [r["work_id"] for r in per_task if "LEAK" in r["verdict"]]
    return {
        "n_tasks": len(tasks),
        "executed": executed,
        "reused": reused,
        "dry_run": dry_run,
        "repeats": repeats,
        "per_task": per_task,
        "separates_all_channels": separates,
        "leaked": leaked,
    }


def _print_go_command(n_tasks: int, repeats: int, out_dir: Path, corpus_dir: Path) -> None:
    runs = n_tasks * len(ARMS) * len(CHANNELS) * repeats
    worst_hours = runs * DEFAULT_TIMEOUT_S / 3600.0
    print(
        f"REFUSING to spend: {ENV_OAUTH} is unset.\n"
        f"  This paid sweep is {runs} real `claude -p` run(s) "
        f"({n_tasks} task x {len(ARMS)} arm x {len(CHANNELS)} channel x {repeats} repeat); "
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
        "--model", default="", help="pins --model; empty reads MEMBENCH_AGENT_MODEL"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N tasks (smoke subset)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="simulate the agent; no token, no claude"
    )
    args = parser.parse_args(argv)

    corpus_dir = args.corpus_dir
    tasks = load_corpus(corpus_dir)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    if not tasks:
        print(f"no tool-requiring tasks under {corpus_dir}", file=sys.stderr)
        return 1

    if not args.dry_run and not os.environ.get(ENV_OAUTH):
        _print_go_command(len(tasks), args.repeats, args.out, corpus_dir)
        return 2

    mode = "DRY-RUN (simulated agent, no tokens)" if args.dry_run else "PAID real claude -p"
    print(
        f"toolreq real-agent sweep: {mode}; {len(tasks)} task(s) x {len(ARMS)} arm x "
        f"{len(CHANNELS)} channel x {args.repeats} repeat"
    )
    summary = run_corpus(
        tasks, out_dir=args.out, repeats=args.repeats, model=args.model, dry_run=args.dry_run
    )
    for record in summary["per_task"]:
        print(f"  {record['work_id']:<24} {record['verdict']}")
    summary_path = args.out / "summary-toolreq-realagent.json"
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
