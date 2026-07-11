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
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass

from membench.runner.headless_agent import CliRunner, HeadlessClaudeAgent, MemoryChannel
from membench.runner.realagent_probe import (
    DEFAULT_CURRENT_VALUE,
    build_probe_step,
    oracle_memory,
    score_goal_action,
)
from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep

ENV_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"
ARMS = ("none", "oracle")
CHANNELS = (MemoryChannel.RECALLED, MemoryChannel.TRUSTED)


@dataclass(frozen=True)
class ArmOutcome:
    arm: str
    channel: str
    passes: int
    runs: int


def _arm_memory(arm: str) -> dict[str, str]:
    """The per-arm surfaced memory: empty for ``none``, the id-exact ceiling for ``oracle``."""
    if arm == "none":
        return {}
    if arm == "oracle":
        return oracle_memory()
    raise ValueError(f"unknown arm {arm!r} (expected one of {ARMS})")


def _dry_run_runner(current_value: str) -> CliRunner:
    """A CliRunner stand-in that SIMULATES an honest memory-copying agent: it Writes the
    current value iff that value appears in the prompt (i.e. the arm surfaced it), else
    it makes no tool call. This proves the arm wiring + external scorer discriminate end
    to end offline — it is NOT a measurement of real agent behaviour."""

    def run(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        # HeadlessClaudeAgent._argv builds ["claude", "-p", prompt, ...]; assert the
        # layout so a future reordering fails loudly instead of silently never firing.
        assert list(argv[:2]) == ["claude", "-p"], f"unexpected argv layout: {argv[:3]}"
        prompt = argv[2] if len(argv) > 2 else ""
        events: list[dict[str, object]] = []
        if current_value in prompt:
            events.append(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"file_path": "config.json", "content": current_value},
                            }
                        ],
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                }
            )
        events.append({"type": "result", "result": "done"})
        stdout = "\n".join(json.dumps(e) for e in events)
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout=stdout, stderr="")

    return run


def _run_arm(
    *,
    step: SequenceStep,
    arm: str,
    channel: MemoryChannel,
    repeats: int,
    model: str,
    dry_run: bool,
) -> ArmOutcome:
    runner = _dry_run_runner(DEFAULT_CURRENT_VALUE) if dry_run else subprocess.run
    memory = _arm_memory(arm)
    passes = 0
    for i in range(repeats):
        # A fresh neutral sandbox PER repeat: never a mem worktree (its CLAUDE.md/hooks
        # would confound the memory variable), and no config.json bleeds across trials.
        with tempfile.TemporaryDirectory(prefix=f"probe-{arm}-") as sandbox:
            agent = HeadlessClaudeAgent(
                model=model,
                runner=runner,
                memory_channel=channel,
                constrain_tools=True,  # --allowedTools Write
                cwd=sandbox,
            )
            ctx = StepContext(
                trial_id=f"probe-{arm}-{channel.value}-{i}",
                session_id=f"probe-{arm}-{channel.value}",
                step_id=step.step_id,
            )
            result = agent.run_step(step, memory, ctx)
        if score_goal_action(step, tool_calls=result.tool_calls, final_answer=result.final_answer):
            passes += 1
    return ArmOutcome(arm=arm, channel=channel.value, runs=repeats, passes=passes)


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
    ap.add_argument("--model", default="", help="pins --model; empty reads MEMBENCH_AGENT_MODEL")
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
