"""§4.4 real-run substrate: HeadlessClaudeAgent prompt assembly + stream parsing, and
the trajectory driver. Hermetic — a fake CLI runner returns a canned Claude Code
stream-json; no real `claude`, no network, no scix-batch."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from membench.metrics.action_impact_run import ArmStepTrajectory
from membench.runner.agent import Agent
from membench.runner.headless_agent import (
    HeadlessAgentError,
    HeadlessClaudeAgent,
    build_agent_prompt,
)
from membench.runner.trajectory_run import (
    run_arm_trajectories,
    run_sequence_arms,
    run_step_trajectory,
)
from membench.schemas.sequence import BenchmarkSequence, SequenceStep


def _step(
    step_id: str = "s1",
    request: str = "Fix the failing import",
    tools: list[str] | None = None,
) -> SequenceStep:
    return SequenceStep(
        step_id=step_id,
        user_request=request,
        available_tools=tools if tools is not None else ["Read", "Edit", "Bash"],
    )


def _stream_json(
    *tool_uses: tuple[str, dict[str, Any]],
    result: str = "done",
    usage: tuple[int, int] | None = (10, 5),
) -> str:
    """A minimal Claude Code stream-json transcript: one assistant event whose content
    holds the given tool_use blocks, then a terminal result event."""
    content = [{"type": "tool_use", "name": name, "input": inp} for name, inp in tool_uses]
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if usage is not None:
        message["usage"] = {"input_tokens": usage[0], "output_tokens": usage[1]}
    lines = [
        json.dumps({"type": "assistant", "message": message}),
        json.dumps({"type": "result", "result": result}),
    ]
    return "\n".join(lines) + "\n"


def _fake_runner(stdout: str, *, returncode: int = 0, stderr: str = ""):
    captured: dict[str, Any] = {}

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    runner.captured = captured  # type: ignore[attr-defined]
    return runner


# --------------------------------------------------------------------------- #
# prompt assembly
# --------------------------------------------------------------------------- #
def test_prompt_none_arm_is_bare_request() -> None:
    prompt = build_agent_prompt(_step(request="Do X"), {})
    assert "Retrieved memory" not in prompt  # empty surface == the control condition
    assert "Do X" in prompt


def test_prompt_injects_memory_block() -> None:
    prompt = build_agent_prompt(_step(), {"m1": "prefer ripgrep", "m2": "tests live in tests/"})
    assert "Retrieved memory" in prompt
    assert "[m1] prefer ripgrep" in prompt
    assert "[m2] tests live in tests/" in prompt


# --------------------------------------------------------------------------- #
# agent: argv + stream parsing
# --------------------------------------------------------------------------- #
def test_protocol_conformance() -> None:
    assert isinstance(HeadlessClaudeAgent(), Agent)


def test_argv_has_stream_json_and_strict_mcp_and_tools() -> None:
    runner = _fake_runner(_stream_json(("Read", {"path": "a.py"})))
    agent = HeadlessClaudeAgent(runner=runner)
    agent.run_step(_step(tools=["Read", "Edit"]), {}, _ctx())
    argv = runner.captured["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert "stream-json" in argv and "--verbose" in argv
    assert "--strict-mcp-config" in argv  # boot-hang guard
    assert "--allowedTools" in argv and "Read,Edit" in argv


def test_no_model_flag_when_unpinned() -> None:
    runner = _fake_runner(_stream_json())
    HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())
    assert "--model" not in runner.captured["argv"]


def test_model_flag_when_pinned() -> None:
    runner = _fake_runner(_stream_json())
    HeadlessClaudeAgent(model="claude-sonnet-4-6", runner=runner).run_step(_step(), {}, _ctx())
    argv = runner.captured["argv"]
    assert "--model" in argv and "claude-sonnet-4-6" in argv


def test_run_step_parses_stream_into_result() -> None:
    runner = _fake_runner(
        _stream_json(
            ("Read", {"path": "a.py"}), ("Edit", {"path": "a.py"}), result="fixed", usage=(120, 40)
        )
    )
    result = HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())
    assert result.final_answer == "fixed"
    assert [t.name for t in result.tool_calls] == ["Read", "Edit"]
    assert result.input_tokens == 120 and result.output_tokens == 40
    assert result.raw_stream  # verbatim stream kept for bbon extraction
    assert result.check_results == {} and result.writes_performed == {}


def test_run_step_raises_on_nonzero_exit() -> None:
    runner = _fake_runner("", returncode=1, stderr="boom")
    with pytest.raises(HeadlessAgentError, match="exit 1"):
        HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())


def test_run_step_raises_on_missing_cli() -> None:
    def runner(argv, **kwargs):
        raise FileNotFoundError("claude")

    with pytest.raises(HeadlessAgentError, match="not found"):
        HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())


def test_run_step_raises_on_timeout() -> None:
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 600)

    with pytest.raises(HeadlessAgentError, match="did not respond"):
        HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())


# --------------------------------------------------------------------------- #
# driver: agent -> bbon extract -> ArmStepTrajectory
# --------------------------------------------------------------------------- #
def test_run_step_trajectory_extracts_attempt_steps() -> None:
    runner = _fake_runner(_stream_json(("Read", {"path": "a.py"}), ("Grep", {"q": "import"})))
    agent = HeadlessClaudeAgent(runner=runner)
    traj = run_step_trajectory(agent, _step(), arm="ours", sequence_id="seq1", work_id="mem-x")
    assert isinstance(traj, ArmStepTrajectory)
    assert traj.arm == "ours" and traj.sequence_id == "seq1" and traj.step_id == "s1"
    assert [s.kind for s in traj.steps] == ["Read", "Grep"]  # one AttemptStep per tool_use
    assert traj.status == "completed"
    assert traj.work_id == "mem-x"


def test_run_arm_trajectories_over_sequence() -> None:
    seq = BenchmarkSequence(
        sequence_id="seq1",
        title="t",
        steps=[_step("s1"), _step("s2", request="Now run the tests")],
    )
    runner = _fake_runner(_stream_json(("Bash", {"cmd": "pytest"})))
    trajs = run_arm_trajectories(HeadlessClaudeAgent(runner=runner), seq, arm="none")
    assert [t.step_id for t in trajs] == ["s1", "s2"]
    assert all(t.arm == "none" for t in trajs)
    assert all([s.kind for s in t.steps] == ["Bash"] for t in trajs)


def test_run_sequence_arms_keys_by_arm() -> None:
    seq = BenchmarkSequence(sequence_id="seq1", title="t", steps=[_step("s1")])
    runner = _fake_runner(_stream_json(("Read", {"path": "a"})))
    agent = HeadlessClaudeAgent(runner=runner)
    # `none` surfaces nothing; `ours` surfaces a memory — both run, keyed by arm.
    out = run_sequence_arms(
        agent,
        seq,
        memory_by_arm={"none": lambda _s: {}, "ours": lambda _s: {"m1": "hint"}},
    )
    assert set(out) == {"none", "ours"}
    assert out["none"][0].arm == "none" and out["ours"][0].arm == "ours"


# --------------------------------------------------------------------------- #
# helper
# --------------------------------------------------------------------------- #
def _ctx():
    from membench.runtime import IdClock, StepContext

    return StepContext(trial_id="t1", session_id="none", step_id="s1", clock=IdClock())
