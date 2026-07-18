"""Real-agent tool-requiring probe building blocks (mem-rk41.4) — all SDK-free.

Exercises the store-free step builder + external action scorer that let the same
``outcome_check_passes`` grader apply to a REAL agent's parsed tool_calls. No
``claude`` is spawned; tool_calls are fabricated to stand in for a real Write stream.
"""

from __future__ import annotations

from membench.runner.realagent_probe import (
    DEFAULT_CURRENT_ID,
    DEFAULT_CURRENT_VALUE,
    DEFAULT_STALE_VALUE,
    REAL_TOOL,
    build_probe_step,
    oracle_memory,
    score_goal_action,
)
from membench.schemas.trace import ToolCall


def _write(content: str) -> ToolCall:
    return ToolCall(name=REAL_TOOL, arguments={"file_path": "config.json", "content": content})


def test_probe_step_is_tool_requiring_on_a_real_tool() -> None:
    step = build_probe_step()
    assert step.available_tools == [REAL_TOOL]
    assert step.expected_memory_reads == [DEFAULT_CURRENT_ID]
    (check,) = step.outcome_checks
    (action,) = check.requires_action
    assert action.tool == REAL_TOOL
    assert action.arg_values == [DEFAULT_CURRENT_VALUE]
    assert action.forbidden_values == [DEFAULT_STALE_VALUE]


def test_oracle_memory_surfaces_only_current() -> None:
    mem = oracle_memory()
    assert DEFAULT_CURRENT_ID in mem
    assert DEFAULT_CURRENT_VALUE in mem[DEFAULT_CURRENT_ID]
    assert DEFAULT_STALE_VALUE not in mem[DEFAULT_CURRENT_ID]


def test_scorer_passes_on_write_of_current_value() -> None:
    step = build_probe_step()
    assert score_goal_action(step, tool_calls=[_write(DEFAULT_CURRENT_VALUE)]) is True


def test_scorer_fails_on_stale_value_in_the_action() -> None:
    # Staleness is reward-bearing THROUGH the tool argument: a stale Write fails here.
    step = build_probe_step()
    assert score_goal_action(step, tool_calls=[_write(DEFAULT_STALE_VALUE)]) is False


def test_scorer_fails_when_no_write_call_made() -> None:
    step = build_probe_step()
    assert score_goal_action(step, tool_calls=[]) is False
    # A call to a different tool does not satisfy the required Write action.
    other = ToolCall(name="Bash", arguments={"command": f"echo {DEFAULT_CURRENT_VALUE}"})
    assert score_goal_action(step, tool_calls=[other]) is False


def test_scorer_fails_when_write_carries_both_current_and_stale() -> None:
    # No Write call may carry a forbidden (stale) value, even alongside the current one.
    step = build_probe_step()
    both = _write(f"{DEFAULT_CURRENT_VALUE} (was {DEFAULT_STALE_VALUE})")
    assert score_goal_action(step, tool_calls=[both]) is False


# --- decision rule (scripts/probe_realagent_toolreq.py) ------------------------------
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from probe_realagent_toolreq import ArmOutcome, _arm_memory, _run_arm, _verdict, main  # noqa: E402

from membench.runner.headless_agent import MemoryChannel  # noqa: E402


def test_arm_memory_none_is_empty_oracle_surfaces_current() -> None:
    assert _arm_memory("none") == {}
    assert DEFAULT_CURRENT_VALUE in next(iter(_arm_memory("oracle").values()))


def test_spend_gate_refuses_and_never_invokes_claude_without_token(monkeypatch) -> None:
    # The one spend-critical line: no token + no --dry-run must exit 2 and never spawn claude.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["probe", "--repeats", "1"])

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("subprocess.run must NOT be called when the spend gate fires")

    # The real spend now lives in the shared run_arm (realagent_probe); patch it there.
    from membench.runner import realagent_probe as _rp

    monkeypatch.setattr(_rp.subprocess, "run", _boom)
    assert main() == 2


def test_spend_gate_refuses_without_a_named_model(monkeypatch) -> None:
    # mem-bzv2p: token present but no model named (no --model, no MEMBENCH_AGENT_MODEL) must also
    # exit 2 and never spawn claude — this probe keeps no cache, but shares the spend gate the grids
    # are held to, where an unpinned run's identity keys on "" and serves one model's numbers as
    # another's.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")
    monkeypatch.delenv("MEMBENCH_AGENT_MODEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["probe", "--repeats", "1"])

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("subprocess.run must NOT be called when the model gate fires")

    from membench.runner import realagent_probe as _rp

    monkeypatch.setattr(_rp.subprocess, "run", _boom)
    assert main() == 2


def test_spend_gate_refuses_with_the_metered_api_key_set(monkeypatch, capsys) -> None:
    # mem-9bh93: this probe spawns real `claude -p` through the same run_arm as the grids, so it has
    # the same leak: an exported ANTHROPIC_API_KEY reroutes the child off OAuth onto the paid API.
    # Token and model present (both prior gates pass), key set -> exit 2 and never spawn claude.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("MEMBENCH_AGENT_MODEL", "sonnet")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-refused")
    monkeypatch.setattr(sys, "argv", ["probe", "--repeats", "1"])

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("subprocess.run must NOT be called when the api-key gate fires")

    from membench.runner import realagent_probe as _rp

    monkeypatch.setattr(_rp.subprocess, "run", _boom)
    assert main() == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_api_key_gate_fires_before_the_token_gate(monkeypatch, capsys) -> None:
    # The api-key gate is placed before the OAuth-token gate (mem-9bh93), matching the grid drivers,
    # so a set key is refused regardless of the token. Token UNSET + key SET must surface the
    # API-KEY refusal, not the token "source it and re-run" message (which wastes a round trip).
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("MEMBENCH_AGENT_MODEL", "sonnet")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-refused")
    monkeypatch.setattr(sys, "argv", ["probe", "--repeats", "1"])

    assert main() == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_dry_run_arm_wiring_discriminates_end_to_end() -> None:
    # The dry-run path (simulated memory-copying agent) must show the arms separate:
    # none never gets the value in its prompt (fails), oracle does (passes) — no claude.
    step = build_probe_step()
    none_o = _run_arm(
        step=step, arm="none", channel=MemoryChannel.RECALLED, repeats=2, model="", dry_run=True
    )
    oracle_o = _run_arm(
        step=step, arm="oracle", channel=MemoryChannel.RECALLED, repeats=2, model="", dry_run=True
    )
    assert none_o.passes == 0
    assert oracle_o.passes == oracle_o.runs == 2


def test_a_contaminated_ancestor_chain_refuses_this_arms_sandbox(tmp_path, monkeypatch) -> None:
    # mem-rx11w's guard has to hold on BOTH paid cwd mints, not just the builtin grid's — they
    # are separate call sites, and the guard being reachable from `run_builtin_arm` says
    # nothing about this one. An ancestor CLAUDE.md, which Claude Code auto-loads by walking
    # up from the cwd at launch, must refuse here rather than measure a sandbox that is not
    # neutral.
    #
    # dry_run=True on purpose: `paid_sandbox` takes no dry_run and is called identically on
    # both paths, so this exercises the SAME guard while spawning nothing and needing no
    # token. It also pins the fail-closed choice — a free run is refused too, because the
    # refusal is about the sandbox, not about who is paying.
    import tempfile as _tempfile

    from membench.runner.sandbox import SandboxContaminationError

    (tmp_path / "CLAUDE.md").write_text("remember: probe-w-t0-CURRENT", encoding="utf-8")
    monkeypatch.setattr(_tempfile, "tempdir", str(tmp_path))

    with pytest.raises(SandboxContaminationError) as exc:
        _run_arm(
            step=build_probe_step(),
            arm="none",
            channel=MemoryChannel.RECALLED,
            repeats=1,
            model="",
            dry_run=True,
        )
    assert str(tmp_path / "CLAUDE.md") in str(exc.value)  # names the offending path
    assert "TMPDIR" in str(exc.value)  # and the knob that fixes it


def _outcomes(none_pass: int, oracle_pass: int, runs: int = 3) -> list[ArmOutcome]:
    return [
        ArmOutcome(arm="none", channel="recalled", passes=none_pass, runs=runs),
        ArmOutcome(arm="oracle", channel="recalled", passes=oracle_pass, runs=runs),
    ]


def test_verdict_separates_when_none_fails_and_oracle_ceiling_passes() -> None:
    assert "SEPARATES" in _verdict(_outcomes(none_pass=0, oracle_pass=3))


def test_verdict_kills_when_oracle_ceiling_never_passes() -> None:
    assert "KILL" in _verdict(_outcomes(none_pass=0, oracle_pass=0))


def test_verdict_flags_leak_when_none_passes() -> None:
    # none passing means the value reached the prompt — a broken fixture, not a win.
    assert "LEAK" in _verdict(_outcomes(none_pass=2, oracle_pass=3))


def test_verdict_weak_when_oracle_partial() -> None:
    assert "WEAK" in _verdict(_outcomes(none_pass=0, oracle_pass=1))
