#!/usr/bin/env python3
"""Real-agent tool-requiring PROBE (mem-rk41.4) — a cheap kill-switch before the adapter.

Runs ONE bridged tool-requiring goal (Write the current value of a superseded fact to a
file) through a REAL ``claude -p`` agent under two arms and two memory-trust channels,
scores the Write action externally, and prints a verdict. See
``membench.runner.realagent_probe`` for the shape + scorer and why ``builtin`` is out of
scope.

    ARMS      none (empty memory)   vs   oracle (id-exact current value surfaced)
    CHANNELS  recalled (low-trust)  and  trusted (authoritative, pjh8.2 upper bound)

DECISION (necessity + usability ceiling gate):
  * none passes  -> the value LEAKED into the prompt; the fixture is broken, not a win.
  * none fails AND oracle passes -> a real agent USES perfect memory to drive the tool
    with the current value => the substrate can separate memory quality under a real
    agent => the world->TaskBundle adapter is worth building.
  * oracle fails at the ceiling -> no real substrate will separate => KILL the path.

PAID + EXTERNAL: the real arms spend OAuth-subscription tokens via ``claude -p``. This
script HALTS branch-ready — run ``--dry-run`` (no token, simulated agent) to prove the
wiring, then launch the paid run explicitly.

MANDATORY for the paid run: wrap in ``scix-batch`` (transient cgroup + RAM ceiling) —
a real agent in the default shell cgroup can OOM-kill the supervisor. This script does
NOT self-wrap:

    scix-batch -- env CLAUDE_CODE_OAUTH_TOKEN=... \
        uv run python scripts/probe_realagent_toolreq.py --repeats 3

    # prove the plumbing first (free, no token, no claude):
    PYTHONPATH=. python3 scripts/probe_realagent_toolreq.py --dry-run
"""

from __future__ import annotations

import argparse
import os

from membench.runner.headless_agent import (
    CellRecorder,
    MemoryChannel,
    a_paid_run_needs_a_model,
)
from membench.runner.realagent_probe import (
    DEFAULT_CURRENT_VALUE,
    ArmOutcome,
    build_probe_step,
    oracle_memory,
    run_arm,
)
from membench.schemas.sequence import SequenceStep

# ArmOutcome + the arm loop now live in membench.runner.realagent_probe (shared with the
# mem-rk41.3 corpus driver); re-exported here so importers of this CLI keep working.
__all__ = ["ArmOutcome", "main"]

ENV_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"
# A paid probe left unpinned executes under the CLI's own default — a model this codebase never
# records — so a resume across a model change could serve one model's numbers as another's
# (mem-bzv2p). This probe keeps no cache, but it shares the spend gate the grids are held to.
_REFUSE_UNPINNED_MODEL = (
    "REFUSING to spend: no model named. An unpinned paid run executes under the CLI's own\n"
    "  default, which this benchmark never records.\n"
    "  Pass --model <id>, or set MEMBENCH_AGENT_MODEL, then re-run (or --dry-run for free)."
)
ARMS = ("none", "oracle")
CHANNELS = (MemoryChannel.RECALLED, MemoryChannel.TRUSTED)


def _arm_memory(arm: str) -> dict[str, str]:
    """The per-arm surfaced memory: empty for ``none``, the id-exact ceiling for ``oracle``."""
    if arm == "none":
        return {}
    if arm == "oracle":
        return oracle_memory()
    raise ValueError(f"unknown arm {arm!r} (expected one of {ARMS})")


def _run_arm(
    *,
    step: SequenceStep,
    arm: str,
    channel: MemoryChannel,
    repeats: int,
    model: str,
    dry_run: bool,
) -> ArmOutcome:
    """This probe's single-subject arm run: delegate to the shared ``run_arm`` with the
    probe's ``none``/``oracle`` memory and its one hardcoded current value.

    The probe has no resume cache, so it passes a throwaway ``CellRecorder`` (never read back) and
    keeps only the score (``realagent_probe.run_arm``)."""
    outcome = run_arm(
        arm=arm,
        step=step,
        memory=_arm_memory(arm),
        channel=channel,
        repeats=repeats,
        model=model,
        dry_run=dry_run,
        current_values=[DEFAULT_CURRENT_VALUE],
        recorder=CellRecorder(),
    )
    return outcome


def _verdict(outcomes: list[ArmOutcome]) -> str:
    by = {(o.arm, o.channel): o for o in outcomes}
    lines: list[str] = []
    for channel in (c.value for c in CHANNELS):
        none_o = by.get(("none", channel))
        oracle_o = by.get(("oracle", channel))
        if none_o is None or oracle_o is None:
            continue
        n, orc, runs = none_o.passes, oracle_o.passes, oracle_o.runs
        if none_o.passes > 0:
            call = f"LEAK: none passed {n}/{none_o.runs} — value reached the prompt, fixture broken"
        elif orc == runs and runs > 0:
            call = f"SEPARATES: none 0/{runs}, oracle {orc}/{runs} — adapter worth building"
        elif orc == 0:
            call = f"KILL: oracle ceiling 0/{runs} — no real substrate separates"
        else:
            call = f"WEAK: none 0/{runs}, oracle {orc}/{runs} — inconclusive, add repeats"
        lines.append(f"[{channel}] {call}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeats", type=int, default=3, help="runs per (arm, channel) — stochastic")
    ap.add_argument(
        "--model",
        default="",
        help="pins --model; else MEMBENCH_AGENT_MODEL; a paid run refuses if neither names one",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="simulate the agent; no token, no claude"
    )
    args = ap.parse_args()

    if not args.dry_run and not os.environ.get(ENV_OAUTH):
        print(
            f"REFUSING to spend: {ENV_OAUTH} is unset. Source it from an account home\n"
            "  (~/.claude-homes/accountN/.claude/.credentials.json) and re-run under scix-batch,\n"
            "  or pass --dry-run to prove the wiring for free."
        )
        return 2

    if a_paid_run_needs_a_model(args.model, dry_run=args.dry_run):
        print(_REFUSE_UNPINNED_MODEL)
        return 2

    step = build_probe_step()
    mode = "DRY-RUN (simulated agent, no tokens)" if args.dry_run else "PAID real claude -p"
    print(f"probe: {mode}; {args.repeats} repeats x {len(ARMS)} arms x {len(CHANNELS)} channels")

    outcomes: list[ArmOutcome] = []
    for channel in CHANNELS:
        for arm in ARMS:
            o = _run_arm(
                step=step,
                arm=arm,
                channel=channel,
                repeats=args.repeats,
                model=args.model,
                dry_run=args.dry_run,
            )
            outcomes.append(o)
            print(f"  {o.arm:>7} / {o.channel:<8} : {o.passes}/{o.runs} goal-pass")

    print("\n=== verdict ===")
    print(_verdict(outcomes))
    if args.dry_run:
        print(
            "\n(DRY-RUN proves arm wiring + scorer discriminate; real behaviour is the paid run.)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
