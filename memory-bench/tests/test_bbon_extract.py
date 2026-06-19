"""Tests for `membench.bbon.extract`: terminal status, step extraction from a
Claude Code stream, and attempt construction. Synthetic streams only."""

import json

import pytest

from membench.bbon.extract import (
    build_attempt,
    steps_from_stream,
    terminal_status,
    tool_results_by_id,
)

_HEX64 = "c" * 64


def _tool_use_line(name: str, inp: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]},
        }
    )


def _tool_use_line_id(tool_use_id: str, name: str, inp: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": inp}]
            },
        }
    )


def _tool_result_line(tool_use_id: str, content: object, *, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": tool_use_id,
                     "content": content, "is_error": is_error}
                ]
            },
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


# --------------------------------------------------------------------------- #
# tool-output capture (mem-lvp.24 Finding-2 fix)
# --------------------------------------------------------------------------- #
def test_tool_results_by_id_string_content() -> None:
    stream = _tool_result_line("toolu_1", "rows: 42")
    assert tool_results_by_id(stream) == {"toolu_1": {"is_error": False, "content": "rows: 42"}}


def test_tool_results_by_id_text_subblocks() -> None:
    blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    stream = _tool_result_line("toolu_1", blocks)
    assert tool_results_by_id(stream)["toolu_1"]["content"] == "ab"


def test_tool_results_skips_unattributable() -> None:
    # a tool_result with no tool_use_id cannot be attributed → skipped.
    stream = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "orphan"}]}})
    assert tool_results_by_id(stream) == {}


def test_steps_from_stream_attaches_output_by_id() -> None:
    stream = "\n".join([
        _tool_use_line_id("toolu_1", "Bash", {"command": "psql -c '\\d orders'"}),
        _tool_result_line("toolu_1", "Table orders: id PK, customer_id FK", is_error=False),
    ])
    steps = steps_from_stream(stream, _HEX64)
    assert len(steps) == 1
    assert steps[0].kind == "Bash"
    assert steps[0].output == {"is_error": False, "content": "Table orders: id PK, customer_id FK"}


def test_steps_from_stream_output_empty_when_no_result() -> None:
    # backward-compatible: a tool_use with no matching result → output stays {}.
    steps = steps_from_stream(_tool_use_line_id("toolu_x", "Read", {"path": "a"}), _HEX64)
    assert steps[0].output == {}


def test_steps_from_stream_truncates_long_output() -> None:
    big = "x" * 9000
    stream = "\n".join([
        _tool_use_line_id("toolu_1", "Bash", {"command": "cat huge"}),
        _tool_result_line("toolu_1", big),
    ])
    out = steps_from_stream(stream, _HEX64)[0].output
    assert out["truncated"] is True
    assert len(out["content"]) == 4000


def test_output_capture_does_not_change_step_id() -> None:
    # the id is content-addressed over input only → same id with/without a result.
    with_result = "\n".join([
        _tool_use_line_id("toolu_1", "Read", {"path": "a"}),
        _tool_result_line("toolu_1", "contents"),
    ])
    without = _tool_use_line_id("toolu_1", "Read", {"path": "a"})
    assert steps_from_stream(with_result, _HEX64)[0].id == steps_from_stream(without, _HEX64)[0].id
