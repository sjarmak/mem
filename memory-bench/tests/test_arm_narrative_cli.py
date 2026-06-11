"""CLI tests for `scripts/arm_narrative.py` (mem-0ut): pair resolution, the offline
stub judge, typed skips, JSON output, and the optional markdown report.

Loaded from its file path (the run_gate_probe / arm_analysis test idiom). The store
is a synthetic SQLite file with the real `work_records` layout; traces are synthetic
streams. No Docker, no network, and no real claude — the default judge is the offline
stub."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "arm_narrative.py"


def _load_script():
    # arm_narrative imports arm_analysis by name (both live in scripts/); make that
    # sibling importable before loading the script under test.
    scripts_dir = _SCRIPT.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("arm_narrative", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["arm_narrative"] = module
    spec.loader.exec_module(module)
    return module


arm_narrative = _load_script()


def _stream_text(extra_edits: int = 1) -> str:
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/w/a"}}
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                },
                "timestamp": "2026-06-07T02:00:00Z",
            }
        )
    ]
    for i in range(extra_edits):
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Edit", "input": {"file_path": f"/w/{i}"}}
                        ],
                        "usage": {"input_tokens": 50, "output_tokens": 40},
                    },
                    "timestamp": "2026-06-07T02:01:00Z",
                }
            )
        )
    return "\n".join(lines) + "\n"


def _record_json(work_id: str, *, green: bool) -> str:
    outcomes = [{"runner": "tsc", "status": "fail"}, {"runner": "tsc", "status": "pass"}]
    if not green:
        outcomes = [{"runner": "tsc", "status": "fail"}]
    return json.dumps({"work_id": work_id, "trace": {"tool_outcomes": outcomes}})


@pytest.fixture
def store(tmp_path: Path) -> Path:
    db = tmp_path / "store.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE work_records (work_id TEXT PRIMARY KEY, rig TEXT, status TEXT, "
        "trace_path TEXT, record TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE record_agents (work_id TEXT, agent_id TEXT, role TEXT, "
        "account TEXT, trace_ref TEXT)"
    )
    cold_trace = tmp_path / "cold.jsonl"
    cold_trace.write_text(_stream_text(extra_edits=3), encoding="utf-8")  # more steps, failed
    warm_trace = tmp_path / "warm.jsonl"
    warm_trace.write_text(_stream_text(extra_edits=1), encoding="utf-8")  # fewer steps, green
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("cold-1", "rig", "closed", str(cold_trace), _record_json("cold-1", green=False)),
    )
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("warm-1", "rig", "closed", str(warm_trace), _record_json("warm-1", green=True)),
    )
    con.commit()
    con.close()
    return db


def _pairs_file(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_cli_resolves_pair_judges_and_writes_report(tmp_path: Path, store: Path) -> None:
    pairs = _pairs_file(tmp_path, [{"left": "cold-1", "right": "warm-1"}])
    out = tmp_path / "out.json"
    report = tmp_path / "report.md"
    rc = arm_narrative.main(
        [
            "--pairs",
            str(pairs),
            "--store",
            str(store),
            "--out-json",
            str(out),
            "--report",
            str(report),
        ]
    )
    assert rc == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["n_pairs"] == 1
    assert payload["n_resolved"] == 1
    assert payload["judge_model"] == "stub"
    assert payload["skips"] == []
    pair = payload["per_pair"][0]
    assert pair["left_work_id"] == "cold-1"
    assert pair["right_work_id"] == "warm-1"
    # the stub judge defaults to the warm (B) arm.
    assert pair["winner_arm"] == "warm"
    assert payload["summary"]["wins_by_arm"] == {"warm": 1}

    body = report.read_text(encoding="utf-8")
    assert "Wins by arm: warm: 1" in body
    assert "cold-1 (cold) vs warm-1 (warm)" in body


def test_cli_unresolvable_work_id_is_typed_skip(tmp_path: Path, store: Path) -> None:
    pairs = _pairs_file(tmp_path, [{"left": "cold-1", "right": "ghost"}])
    out = tmp_path / "out.json"
    rc = arm_narrative.main(["--pairs", str(pairs), "--store", str(store), "--out-json", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["n_resolved"] == 0
    assert payload["summary"] is None
    assert payload["skips"] == [
        {"work_id": "ghost", "arm": "warm", "reason": "work_id_not_in_store"}
    ]


def test_load_pairs_csv_and_empty(tmp_path: Path) -> None:
    csv_path = tmp_path / "pairs.csv"
    csv_path.write_text("left,right\nc-1,w-1\nc-2,w-2\n", encoding="utf-8")
    assert arm_narrative.load_pairs(csv_path) == [("c-1", "w-1"), ("c-2", "w-2")]

    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="empty pairs file"):
        arm_narrative.load_pairs(empty)


def test_load_pairs_malformed_row_names_the_row(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"left": "c-1"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="row 0 missing"):
        arm_narrative.load_pairs(bad)


def test_make_judge_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown judge"):
        arm_narrative._make_judge("gpt")
