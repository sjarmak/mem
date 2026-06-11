"""Tests for `membench.bbon.extract`: terminal status, step extraction from a
Claude Code stream, and attempt construction. Synthetic streams only."""

import json

import pytest

from membench.bbon.extract import build_attempt, steps_from_stream, terminal_status

_HEX64 = "c" * 64


def _tool_use_line(name: str, inp: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]},
        }
    )


def test_terminal_status_unknown_when_no_outcomes() -> None:
    assert terminal_status([]) == "unknown"


def test_terminal_status_completed_when_all_runners_end_green() -> None:
    outcomes = [
        {"runner": "tsc", "status": "fail"},
        {"runner": "tsc", "status": "pass"},
        {"runner": "pytest", "status": "pass"},
    ]
    assert terminal_status(outcomes) == "completed"


def test_terminal_status_failed_when_a_runner_ends_red() -> None:
    outcomes = [
        {"runner": "tsc", "status": "pass"},
        {"runner": "pytest", "status": "pass"},
        {"runner": "pytest", "status": "fail"},
    ]
    assert terminal_status(outcomes) == "failed"


def test_terminal_status_raises_on_malformed_outcome() -> None:
    with pytest.raises(ValueError, match="missing runner/status"):
        terminal_status([{"runner": "tsc"}])


def test_steps_from_stream_orders_and_addresses() -> None:
    stream = "\n".join(
        [
            _tool_use_line("Read", {"file_path": "a"}),
            _tool_use_line("Edit", {"file_path": "a"}),
        ]
    )
    steps = steps_from_stream(stream, _HEX64)
    assert [s.kind for s in steps] == ["Read", "Edit"]
    assert [s.step_index for s in steps] == [0, 1]
    assert steps[0].input == {"file_path": "a"}
    # content-addressed: same (attempt, index, kind, input) reproduces the id.
    again = steps_from_stream(stream, _HEX64)
    assert [s.id for s in steps] == [s.id for s in again]


def test_steps_from_stream_handles_missing_name_and_input() -> None:
    stream = json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use"}]}})
    steps = steps_from_stream(stream, _HEX64)
    assert len(steps) == 1
    assert steps[0].kind == "unknown"
    assert steps[0].input == {}


def test_build_attempt_sets_status_and_result_and_steps() -> None:
    record = {"trace": {"tool_outcomes": [{"runner": "tsc", "status": "pass"}]}}
    stream = _tool_use_line("Read", {"file_path": "a"})
    attempt, steps = build_attempt("w1", "warm", record, stream, metrics={"total_tokens": 300.0})
    assert attempt.work_id == "w1"
    assert attempt.arm == "warm"
    assert attempt.status == "completed"
    assert attempt.result == {"total_tokens": 300.0}
    assert len(steps) == 1 and steps[0].attempt_id == attempt.id


def test_build_attempt_id_is_deterministic_over_work_and_arm() -> None:
    record: dict[str, object] = {"trace": {"tool_outcomes": []}}
    a1, _ = build_attempt("w1", "warm", record, "")
    a2, _ = build_attempt("w1", "warm", record, "")
    a3, _ = build_attempt("w1", "cold", record, "")
    assert a1.id == a2.id
    assert a1.id != a3.id
    assert a1.status == "unknown"


def test_build_attempt_requires_work_id_and_arm() -> None:
    with pytest.raises(ValueError, match="work_id is required"):
        build_attempt("", "warm", {"trace": {"tool_outcomes": []}}, "")
    with pytest.raises(ValueError, match="arm is required"):
        build_attempt("w1", "", {"trace": {"tool_outcomes": []}}, "")
