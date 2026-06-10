"""Cross-language parity guard for the `mem extract-errors` CLI (mem-apg.3.1.1).

The CLI (TS) emits fresh-run trace_error rows that the grid driver's injected
ErrorExtractor feeds to the scorer. Those rows MUST be byte-identical in shape to
the held-out side the scorer compares against. This test pins the Python consumer
to the SAME committed golden fixture the TS test pins the CLI to
(`tests/fixtures/extract-errors/polyglot.expected.json`) — so any drift in
`failureSignature`/`errorClass`/`normalizePath` breaks the TS golden test (forcing
a reviewed fixture regen) and this test then confirms the regenerated rows still
satisfy the Python scorer contract. No TS build is required to run this.
"""

import json
from pathlib import Path

import pytest

from membench.grading import (
    RunTrace,
    TraceErrorRef,
    relaxed_signature,
    score_run,
)

# repo_root/memory-bench/tests/this_file -> parents[2] == repo root
_GOLDEN = (
    Path(__file__).parents[2] / "tests" / "fixtures" / "extract-errors" / "polyglot.expected.json"
)


def _golden_rows() -> list[dict]:
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


def test_golden_fixture_exists() -> None:
    assert _GOLDEN.is_file(), f"golden fixture missing at {_GOLDEN}"


def test_from_mapping_accepts_every_cli_row() -> None:
    rows = _golden_rows()
    assert rows, "golden fixture is empty"
    for row in rows:
        ref = TraceErrorRef.from_mapping(row)
        # The five fields the scorer actually keys on round-trip from the CLI row.
        assert ref.tool == row["tool"]
        assert ref.file == row["file"]
        assert ref.line == row["line"]
        assert ref.error_class == row["error_class"]
        assert ref.signature == row["signature"]
        # The canonical signature is tool:file:line:error_class over the
        # NORMALIZED file (architect C1) — recompute the relaxed key to prove the
        # row's file is the normalized one the scorer's basename logic expects.
        assert relaxed_signature(ref) == f"{ref.tool}:{Path(ref.file).name}:{ref.error_class}"


def test_pytest_line_zero_is_int_not_null() -> None:
    # architect C2: pytest emits line 0; from_mapping's int() must not choke and
    # the value must be a real 0, never None.
    pytest_rows = [r for r in _golden_rows() if r["tool"] == "pytest"]
    assert pytest_rows, "fixture should cover pytest"
    for row in pytest_rows:
        assert row["line"] == 0
        assert TraceErrorRef.from_mapping(row).line == 0


def test_scorer_round_trips_cli_rows() -> None:
    """A fresh run whose errors ARE the held-out errors (same CLI rows) and that
    touched the held files scores path_reached=True, not-resolved — the recurrence
    case. Proves the CLI row shape drives the scorer end to end."""
    held = [TraceErrorRef.from_mapping(r) for r in _golden_rows()]
    touched = frozenset(e.file for e in held)

    recurred = score_run(held, RunTrace(errors=tuple(held), files_touched=touched))
    assert recurred.path_reached is True
    assert recurred.trace_error_resolved is False

    # A clean fresh run: reached the files, emitted no errors -> resolved.
    resolved = score_run(held, RunTrace(errors=(), files_touched=touched))
    assert resolved.path_reached is True
    assert resolved.trace_error_resolved is True


def test_empty_held_set_is_a_caller_error() -> None:
    with pytest.raises(ValueError):
        score_run([], RunTrace(errors=(), files_touched=frozenset()))
