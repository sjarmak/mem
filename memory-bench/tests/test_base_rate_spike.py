"""Base-rate spike driver wiring (mem-apg.3.1).

Exercises the whole driver under the StubRunner -- store loaders, run_grid fan-out, and
the gate -- with no Docker, git, or subscription. The store loaders themselves are
checked against a real temp SQLite store shaped like the production tables.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from membench.grading import RunTrace, TraceErrorRef
from membench.grading.base_rate import GateDecision
from membench.harbor.base_rate_spike import (
    BeadOutcome,
    load_held_errors_from_store,
    load_record_from_store,
    run_base_rate_spike,
)
from membench.harbor.grid import StubRunner


def _held(file: str = "foo_test.go") -> TraceErrorRef:
    return TraceErrorRef(
        tool="go",
        file=file,
        line=42,
        error_class="assert",
        signature=f"go:{file}:42:assert",
    )


def _record(work_id: str) -> dict:
    return {
        "work_id": work_id,
        "rig": "gascity",
        "title": f"work {work_id}",
        "lifecycle": {"started": "2026-06-07T00:00:00", "created": "2026-06-07T00:00:00"},
    }


def _recurring_none_trace() -> RunTrace:
    # Reached the held file AND the known failure recurred -> deterministic_term 0.0.
    return RunTrace(errors=(_held(),), files_touched=frozenset({"/app/x/foo_test.go"}))


# --- store loaders against a real temp SQLite store -----------------------------


@pytest.fixture
def store(tmp_path: Path) -> Path:
    db = tmp_path / "store.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE work_records (work_id TEXT, record TEXT)")
    con.execute(
        "CREATE TABLE trace_errors (id INTEGER PRIMARY KEY, work_id TEXT, tool TEXT, "
        "file TEXT, line INTEGER, error_class TEXT, signature TEXT)"
    )
    con.execute(
        "INSERT INTO work_records VALUES (?,?)",
        ("gc-1", json.dumps(_record("gc-1"))),
    )
    con.execute(
        "INSERT INTO trace_errors (work_id, tool, file, line, error_class, signature) "
        "VALUES (?,?,?,?,?,?)",
        ("gc-1", "go", "foo_test.go", 42, "assert", "go:foo_test.go:42:assert"),
    )
    con.commit()
    con.close()
    return db


def test_load_record_returns_canonical_record(store: Path):
    rec = load_record_from_store(store, "gc-1")
    assert rec["work_id"] == "gc-1"
    assert rec["rig"] == "gascity"
    assert rec["lifecycle"]["started"] == "2026-06-07T00:00:00"


def test_load_record_missing_raises(store: Path):
    with pytest.raises(KeyError):
        load_record_from_store(store, "nope")


def test_load_held_errors_maps_rows(store: Path):
    held = load_held_errors_from_store(store, "gc-1")
    assert held == [_held()]


def test_load_held_errors_empty_raises(store: Path):
    with pytest.raises(ValueError, match="no trace_errors"):
        load_held_errors_from_store(store, "absent")


# --- driver: full none-rung fan-out + gate under StubRunner ---------------------


def test_spike_gate_go_when_recurrence_high(tmp_path: Path):
    work_ids = ["gc-1", "gc-2", "gc-3", "gc-4", "gc-5"]
    records = {w: _record(w) for w in work_ids}
    held = {w: [_held()] for w in work_ids}
    runner = StubRunner({"none": _recurring_none_trace()})

    result = run_base_rate_spike(
        work_ids,
        output_dir=tmp_path,
        runner=runner,
        load_record=lambda w: records[w],
        load_held_errors=lambda w: held[w],
        reconstruct=False,
    )

    # 5 applicable tasks, all recurred -> the avoid-axis has dynamic range -> GO.
    assert result.verdict.decision is GateDecision.GO
    assert len(result.records) == 5
    assert all(o.path_reached for o in result.outcomes)
    assert all(o.deterministic == 0.0 for o in result.outcomes)


def test_spike_insufficient_power_when_path_never_reached(tmp_path: Path):
    # A none-rung run that touched nothing of the held file -> path_reached False ->
    # the bead is not applicable -> the gate reports INSUFFICIENT_POWER, not NO_GO.
    work_ids = ["gc-1", "gc-2", "gc-3", "gc-4", "gc-5"]
    no_reach = RunTrace(errors=(), files_touched=frozenset({"/app/unrelated.go"}))
    runner = StubRunner({"none": no_reach})

    result = run_base_rate_spike(
        work_ids,
        output_dir=tmp_path,
        runner=runner,
        load_record=lambda w: _record(w),
        load_held_errors=lambda w: [_held()],
        reconstruct=False,
    )
    assert result.verdict.decision is GateDecision.INSUFFICIENT_POWER
    assert result.outcomes == tuple(
        BeadOutcome(work_id=w, path_reached=False, deterministic=None) for w in work_ids
    )


def test_spike_empty_work_ids_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="at least one work_id"):
        run_base_rate_spike(
            [],
            output_dir=tmp_path,
            runner=StubRunner({"none": _recurring_none_trace()}),
            load_record=lambda w: _record(w),
            load_held_errors=lambda w: [_held()],
            reconstruct=False,
        )
