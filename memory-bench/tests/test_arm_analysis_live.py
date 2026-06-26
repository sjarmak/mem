"""Contract round-trip tests for `scripts/arm_analysis_live.py` (mem-0ut.3).

Asserts the wire-level dispatch contract (`docs/mem-0ut-dispatch-contract.md`)
maps to armcompare's FIXED read-side: the bead-metadata keys `gc.brain_arm` /
`gc.brain_fork_ts` / `gc.brain_parent_sid` must flow through the live bridge into
armcompare's `arm` map + `record.fork.fork_ts` so the warm arm is fork-trimmed.
A field-name drift fails these tests (the mismeasurement guard). No Docker/network.
"""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

_LIVE = Path(__file__).resolve().parents[1] / "scripts" / "arm_analysis_live.py"

# Contract constants — these MUST equal what the dispatch hook stamps.
ARM_KEY = "gc.brain_arm"
FORK_TS_KEY = "gc.brain_fork_ts"
PARENT_SID_KEY = "gc.brain_parent_sid"
FORK_TS = "2026-06-26T04:20:10.683Z"  # the brain builtAt / fork boundary


def _load():
    spec = importlib.util.spec_from_file_location("arm_analysis_live", _LIVE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["arm_analysis_live"] = module
    spec.loader.exec_module(module)
    return module


live = _load()


def _assistant(blocks: list[dict], ts: str) -> str:
    return json.dumps({"type": "assistant", "message": {"content": blocks}, "timestamp": ts})


def _read(path: str) -> dict:
    return {"type": "tool_use", "name": "Read", "input": {"file_path": path}}


def _edit(path: str) -> dict:
    return {
        "type": "tool_use",
        "name": "Edit",
        "input": {"file_path": path, "old_string": "x", "new_string": "y"},
    }


def _warm_transcript() -> str:
    # 3 inherited brain reads (ts <= fork boundary) + 1 fork read then edit (ts >).
    # Raw -> 4 calls before first edit; trimmed at the fork boundary -> 1.
    return (
        "\n".join(
            [
                _assistant([_read("/brain/a.ts")], "2026-06-26T04:20:00.000Z"),
                _assistant([_read("/brain/b.ts")], "2026-06-26T04:20:05.000Z"),
                _assistant([_read("/brain/c.ts")], "2026-06-26T04:20:10.000Z"),
                _assistant([_read("/fork/x.ts")], "2026-06-26T04:25:00.000Z"),
                _assistant([_edit("/fork/x.ts")], "2026-06-26T04:25:10.000Z"),
            ]
        )
        + "\n"
    )


def _cold_transcript() -> str:
    return (
        "\n".join(
            [
                _assistant([_read("/c/a.ts")], "2026-06-26T05:00:00.000Z"),
                _assistant([_read("/c/b.ts")], "2026-06-26T05:00:05.000Z"),
                _assistant([_edit("/c/a.ts")], "2026-06-26T05:00:10.000Z"),
            ]
        )
        + "\n"
    )


def _record(work_id: str, meta: dict) -> str:
    return json.dumps({"work_id": work_id, "trace": {"tool_outcomes": []}, "metadata": meta})


def _live_store(tmp_path: Path, warm_meta: dict, cold_meta: dict) -> Path:
    db = tmp_path / "live.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE work_records (work_id TEXT PRIMARY KEY, rig TEXT, status TEXT, "
        "trace_path TEXT, record TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE record_agents (work_id TEXT, agent_id TEXT, role TEXT, "
        "account TEXT, trace_ref TEXT)"
    )
    warm_tr = tmp_path / "warm.jsonl"
    warm_tr.write_text(_warm_transcript())
    cold_tr = tmp_path / "cold.jsonl"
    cold_tr.write_text(_cold_transcript())
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("bead-warm", "rig", "closed", str(warm_tr), _record("bead-warm", warm_meta)),
    )
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("bead-cold", "rig", "closed", str(cold_tr), _record("bead-cold", cold_meta)),
    )
    # a non-A/B bead (no gc.brain_arm) — must be ignored by the bridge
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("bead-other", "rig", "closed", str(cold_tr), _record("bead-other", {})),
    )
    con.commit()
    con.close()
    return db


def test_contract_metadata_trims_warm_arm_end_to_end(tmp_path: Path) -> None:
    store = _live_store(
        tmp_path,
        warm_meta={ARM_KEY: "warm", FORK_TS_KEY: FORK_TS, PARENT_SID_KEY: "brain-sid-1"},
        cold_meta={ARM_KEY: "cold"},
    )
    out = tmp_path / "live-out.json"
    rc = live.main(
        ["--store", str(store), "--out-json", str(out), "--work-dir", str(tmp_path / "wd")]
    )
    assert rc == 0
    payload = json.loads(out.read_text())
    per = {b["work_id"]: b for b in payload["per_bead"]}
    # warm: the 3 inherited brain reads are trimmed -> only the fork's own read precedes the edit.
    assert per["bead-warm"]["tool_calls_before_first_edit"] == 1
    assert per["bead-warm"]["files_read"] == 1
    # cold: never trimmed -> both its reads precede the edit.
    assert per["bead-cold"]["tool_calls_before_first_edit"] == 2
    # the contract supplied a fork boundary, so no mismeasurement warning.
    assert payload["fork_warnings"] == []
    # non-A/B bead was ignored.
    assert "bead-other" not in per
    assert payload["summary"]["n_per_arm"] == {"warm": 1, "cold": 1}


def test_derive_inputs_injects_fork_from_contract_keys(tmp_path: Path) -> None:
    store = _live_store(
        tmp_path,
        warm_meta={ARM_KEY: "warm", FORK_TS_KEY: FORK_TS, PARENT_SID_KEY: "brain-sid-1"},
        cold_meta={ARM_KEY: "cold"},
    )
    assignment, rows = live.derive_inputs(store)
    assert assignment == {"bead-warm": "warm", "bead-cold": "cold"}
    warm_row = next(r for r in rows if r["work_id"] == "bead-warm")
    # gc.brain_fork_ts -> record.fork.fork_ts (the exact key fork_boundary_for reads).
    assert warm_row["record"]["fork"]["fork_ts"] == FORK_TS
    assert warm_row["record"]["fork"]["parent_sid"] == "brain-sid-1"
    cold_row = next(r for r in rows if r["work_id"] == "bead-cold")
    assert "fork" not in cold_row["record"]


def test_warm_without_fork_ts_surfaces_a_warning_not_silent(tmp_path: Path) -> None:
    # Drift: a warm bead with NO gc.brain_fork_ts must NOT silently invert — it must
    # land in fork_warnings (the mismeasurement guard).
    store = _live_store(
        tmp_path,
        warm_meta={ARM_KEY: "warm"},  # missing fork_ts
        cold_meta={ARM_KEY: "cold"},
    )
    out = tmp_path / "live-out.json"
    rc = live.main(
        ["--store", str(store), "--out-json", str(out), "--work-dir", str(tmp_path / "wd")]
    )
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["fork_warnings"] == [{"work_id": "bead-warm", "reason": "fork_unmeasured"}]
