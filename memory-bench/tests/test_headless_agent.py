"""Hermetic tests for `HeadlessClaudeAgent` — prompt assembly, channel framing, and
stream parsing — with an injected `runner` so no real `claude -p` is ever spawned.

Scope: the live-agent substrate the ours-LIVE provider (mem-mtqi) consumes. The deep
trajectory/action-impact path (`trajectory_run`, `action_impact_run`) is NOT on this
branch, so its tests are out of scope here; this file pins only the seam the live
provider depends on.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from membench.runner.agent import Agent, AgentStepResult
from membench.runner.headless_agent import (
    HeadlessAgentError,
    HeadlessClaudeAgent,
    MemoryChannel,
    build_agent_prompt,
)
from membench.runtime import IdClock, StepContext
from membench.schemas.sequence import SequenceStep


def _step(**kw: Any) -> SequenceStep:
    defaults: dict[str, Any] = {
        "step_id": "s1",
        "user_request": "Set the postgres max_connections.",
        "expected_memory_reads": [],
        "expected_memory_writes": {},
        "outcome_checks": [],
    }
    defaults.update(kw)
    return SequenceStep(**defaults)


def _ctx() -> StepContext:
    clock = IdClock()
    return StepContext(trial_id="t1", session_id="sess", step_id="s1", clock=clock)


def _stream(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(e) for e in events)


def _fake_runner(stdout: str, returncode: int = 0, stderr: str = "") -> Any:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    return run


def test_recalled_header_is_low_trust() -> None:
    prompt = build_agent_prompt(_step(), {"m1": "max_connections=200"}, MemoryChannel.RECALLED)
    assert "may be relevant" in prompt
    assert "max_connections=200" in prompt
    assert "authoritative" not in prompt.lower()


def test_trusted_header_frames_as_ground_truth() -> None:
    prompt = build_agent_prompt(_step(), {"m1": "max_connections=200"}, MemoryChannel.TRUSTED)
    assert "authoritative ground truth" in prompt.lower()
    assert "do not re-derive" in prompt.lower()
    assert "max_connections=200" in prompt


def test_empty_memory_yields_bare_request_under_both_channels() -> None:
    for channel in (MemoryChannel.RECALLED, MemoryChannel.TRUSTED):
        prompt = build_agent_prompt(_step(), {}, channel)
        assert prompt == "## Task\nSet the postgres max_connections."


def test_agent_satisfies_agent_protocol() -> None:
    assert isinstance(HeadlessClaudeAgent(runner=_fake_runner("")), Agent)


def test_run_step_parses_tools_tokens_and_result() -> None:
    stream = _stream(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "psql"}}],
                "usage": {"input_tokens": 11, "output_tokens": 5},
            },
        },
        {"type": "result", "result": "done"},
    )
    agent = HeadlessClaudeAgent(runner=_fake_runner(stream))
    result = agent.run_step(_step(), {}, _ctx())
    assert isinstance(result, AgentStepResult)
    assert result.final_answer == "done"
    assert [c.name for c in result.tool_calls] == ["Bash"]
    assert result.input_tokens == 11
    assert result.output_tokens == 5
    assert result.raw_stream == stream
    # A real agent self-grades nothing and declares no write policy here.
    assert result.check_results == {}
    assert result.writes_performed == {}


def test_trusted_channel_threads_into_prompt() -> None:
    captured: dict[str, str] = {}

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        # argv[2] is the prompt (claude -p <prompt> ...).
        captured["prompt"] = argv[2]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    agent = HeadlessClaudeAgent(runner=run, memory_channel=MemoryChannel.TRUSTED)
    agent.run_step(_step(), {"m1": "v=1"}, _ctx())
    assert "authoritative ground truth" in captured["prompt"].lower()


def test_nonzero_exit_raises() -> None:
    agent = HeadlessClaudeAgent(runner=_fake_runner("", returncode=2, stderr="boom"))
    with pytest.raises(HeadlessAgentError, match="boom"):
        agent.run_step(_step(), {}, _ctx())


def test_missing_cli_raises() -> None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("claude")

    agent = HeadlessClaudeAgent(runner=run)
    with pytest.raises(HeadlessAgentError, match="not found"):
        agent.run_step(_step(), {}, _ctx())


def test_no_model_no_flag() -> None:
    agent = HeadlessClaudeAgent(runner=_fake_runner(""))
    assert "--model" not in agent._argv("p", _step())
    assert agent._resolved_model == "cli-default"


def test_explicit_model_passed_and_recorded() -> None:
    agent = HeadlessClaudeAgent(runner=_fake_runner(""), model="claude-sonnet")
    argv = agent._argv("p", _step())
    assert argv[argv.index("--model") + 1] == "claude-sonnet"
    assert agent._resolved_model == "claude-sonnet"
