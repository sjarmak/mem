#!/usr/bin/env python3
"""§4.4 real-run substrate SMOKE (mem-lvp.22).

Proves the producer→extract→trajectory path end to end on ONE real lvp.8 sequence
step: a real headless `claude -p` run (OAuth, FREE) → its raw stream →
`bbon.extract.steps_from_stream` → an `AttemptStep` trajectory the action-impact
harness consumes. The `none` arm (empty surfaced memory) is used — it fully exercises
the mechanism with no store/graph wiring (that is the run bead, mem-lvp.19).

MANDATORY: run under `scix-batch` (transient cgroup + RAM ceiling) — a real agent run
in the default shell cgroup can OOM-kill the supervisor/mayor. This script does NOT
self-wrap; the entrypoint must:

    scix-batch -- uv run python scripts/smoke_realrun_trajectory.py [fixture.json]

This is a SMOKE: it prints the trajectory shape and exits. It does NOT score (no
judge call) and writes no results — the gated 5-axis numbers are mem-lvp.19.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from membench.dataset import load_sequence
from membench.runner.headless_agent import HeadlessClaudeAgent
from membench.runner.trajectory_run import run_step_trajectory

_DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "sequences"
    / "postgres_query_optimization.json"
)


def main(argv: list[str]) -> int:
    fixture = Path(argv[1]) if len(argv) > 1 else _DEFAULT_FIXTURE
    seq = load_sequence(fixture)
    step = seq.steps[0]
    print(f"[smoke] sequence={seq.sequence_id} step={step.step_id} arm=none")
    print(f"[smoke] request: {step.user_request[:120]!r}")
    print(f"[smoke] available_tools: {step.available_tools}")

    # constrain_tools=False: the lvp.8 fixtures' available_tools are DOMAIN-semantic
    # (e.g. "psql"), not Claude Code tool names (Read/Bash/Edit), so passing them as
    # --allowedTools would block every real tool and force an empty trajectory. Mapping
    # domain tools → CC tools (or running in a rig that exposes them) is the run bead's
    # job (mem-lvp.19); the smoke just proves the producer→extract path with the agent's
    # native toolset.
    #
    # cwd = an isolated temp dir: the agent must NOT run in a mem worktree (its
    # SessionStart hooks fail the session, and the repo's own CLAUDE.md/memory would
    # confound the memory variable under test).
    with tempfile.TemporaryDirectory(prefix="mem-lvp22-smoke-") as sandbox:
        print(f"[smoke] sandbox cwd: {sandbox}")
        agent = HeadlessClaudeAgent(constrain_tools=False, cwd=sandbox)
        traj = run_step_trajectory(agent, step, arm="none", sequence_id=seq.sequence_id)

    print(f"[smoke] status={traj.status} attempt_steps={len(traj.steps)}")
    for i, s in enumerate(traj.steps):
        preview = {k: str(v)[:40] for k, v in list(s.input.items())[:2]}
        print(f"[smoke]   step[{i}] kind={s.kind} input={preview}")
    if not traj.steps:
        print(
            "[smoke] WARNING: 0 tool_use blocks — the agent answered without tools "
            "(valid for a trivial step). The producer→extract path still ran; pick a "
            "tool-requiring step/fixture to exercise a non-empty trajectory."
        )
    print("[smoke] OK — producer→extract→trajectory path proven")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
