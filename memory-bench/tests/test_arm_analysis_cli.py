"""CLI tests for `scripts/arm_analysis.py` (mem-0ut): store resolution, typed
skips, JSON output, optional markdown report.

Loaded from its file path (the run_gate_probe test idiom). The store is a
synthetic SQLite file with the real `work_records` / `record_agents` column
layout; traces are synthetic stream files. No Docker, no network, and the
script opens the store strictly read-only (pinned by a test).
"""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "arm_analysis.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("arm_analysis", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["arm_analysis"] = module
    spec.loader.exec_module(module)
    return module


arm_analysis = _load_script()


def _assistant_line(blocks: list[dict], ts: str, usage: dict | None = None) -> str:
    message: dict = {"content": blocks}
    if usage is not None:
        message["usage"] = usage
    return json.dumps({"type": "assistant", "message": message, "timestamp": ts})


def _stream_text() -> str:
    return (
        _assistant_line(
            [{"type": "tool_use", "name": "Read", "input": {"file_path": "/w/src/a.ts"}}],
            "2026-06-07T02:00:00Z",
            usage={"input_tokens": 100, "output_tokens": 10},
        )
        + "\n"
        + _assistant_line(
            [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "/w/src/a.ts", "old_string": "x", "new_string": "y"},
                }
            ],
            "2026-06-07T02:01:00Z",
            usage={"input_tokens": 50, "output_tokens": 40},
        )
        + "\n"
    )


def _record_json(work_id: str) -> str:
    return json.dumps(
        {
            "work_id": work_id,
            "trace": {
                "tool_outcomes": [
                    {"runner": "tsc", "command": "tsc", "status": "fail", "errors": []},
                    {"runner": "tsc", "command": "tsc", "status": "pass", "errors": []},
                ]
            },
        }
    )


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A synthetic store: two resolvable beads (one via work_records.trace_path,
    one via record_agents.trace_ref), one bead with no trace anywhere."""
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
    direct_trace = tmp_path / "direct.jsonl"
    direct_trace.write_text(_stream_text(), encoding="utf-8")
    agent_trace = tmp_path / "agent.jsonl"
    agent_trace.write_text(_stream_text(), encoding="utf-8")

    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("r-direct", "rig", "closed", str(direct_trace), _record_json("r-direct")),
    )
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("r-agent", "rig", "closed", None, _record_json("r-agent")),
    )
    con.execute(
        "INSERT INTO record_agents VALUES (?,?,?,?,?)",
        ("r-agent", "gc-1", "worker", None, str(agent_trace)),
    )
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("r-notrace", "rig", "closed", None, _record_json("r-notrace")),
    )
    con.commit()
    con.close()
    return db


def _arms_file(tmp_path: Path, assignment: dict[str, str]) -> Path:
    path = tmp_path / "arms.json"
    path.write_text(json.dumps(assignment), encoding="utf-8")
    return path


def test_cli_extracts_summarizes_and_records_typed_skips(tmp_path: Path, store: Path) -> None:
    arms = _arms_file(
        tmp_path,
        {"r-direct": "warm", "r-agent": "cold", "r-notrace": "warm", "r-ghost": "cold"},
    )
    out = tmp_path / "out.json"
    rc = arm_analysis.main(["--arms", str(arms), "--store", str(store), "--out-json", str(out)])
    assert rc == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["n_per_arm"] == {"warm": 1, "cold": 1}
    per_bead = {row["work_id"]: row for row in payload["per_bead"]}
    assert per_bead["r-direct"]["arm"] == "warm"
    assert per_bead["r-direct"]["total_tokens"] == 200
    assert per_bead["r-agent"]["arm"] == "cold"

    skips = {row["work_id"]: row["reason"] for row in payload["skips"]}
    assert skips == {"r-notrace": "no_trace_path", "r-ghost": "work_id_not_in_store"}


def test_cli_missing_trace_file_is_typed_skip(tmp_path: Path, store: Path) -> None:
    con = sqlite3.connect(store)
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?,?)",
        ("r-gone", "rig", "closed", str(tmp_path / "missing.jsonl"), _record_json("r-gone")),
    )
    con.commit()
    con.close()
    arms = _arms_file(tmp_path, {"r-gone": "warm", "r-direct": "cold"})
    out = tmp_path / "out.json"
    rc = arm_analysis.main(["--arms", str(arms), "--store", str(store), "--out-json", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["skips"] == [
        {
            "work_id": "r-gone",
            "arm": "warm",
            "reason": "trace_file_missing",
            "detail": str(tmp_path / "missing.jsonl"),
        }
    ]


def test_cli_scope_manifest_enables_distractor_rate(tmp_path: Path, store: Path) -> None:
    manifest = tmp_path / "brain.json"
    manifest.write_text(json.dumps({"fileHashes": {"src/a.ts": "h"}}), encoding="utf-8")
    arms = _arms_file(tmp_path, {"r-direct": "warm", "r-agent": "cold"})
    out = tmp_path / "out.json"
    rc = arm_analysis.main(
        [
            "--arms",
            str(arms),
            "--store",
            str(store),
            "--out-json",
            str(out),
            "--scope-manifest",
            str(manifest),
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    rates = {row["work_id"]: row["distractor_read_rate"] for row in payload["per_bead"]}
    assert rates == {"r-direct": 0.0, "r-agent": 0.0}


def test_cli_without_scope_manifest_distractor_rate_is_none(tmp_path: Path, store: Path) -> None:
    arms = _arms_file(tmp_path, {"r-direct": "warm", "r-agent": "cold"})
    out = tmp_path / "out.json"
    arm_analysis.main(["--arms", str(arms), "--store", str(store), "--out-json", str(out)])
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert all(row["distractor_read_rate"] is None for row in payload["per_bead"])


def test_cli_writes_markdown_report(tmp_path: Path, store: Path) -> None:
    arms = _arms_file(tmp_path, {"r-direct": "warm", "r-agent": "cold"})
    out = tmp_path / "out.json"
    report = tmp_path / "report.md"
    argv = ["--arms", str(arms), "--store", str(store), "--out-json", str(out)]
    arm_analysis.main([*argv, "--report", str(report)])
    text = report.read_text(encoding="utf-8")
    assert "warm" in text and "cold" in text
    assert "total_tokens" in text
    assert "unpaired" in text


def test_cli_all_skips_still_writes_output_without_summary(tmp_path: Path, store: Path) -> None:
    arms = _arms_file(tmp_path, {"r-ghost": "warm", "r-notrace": "cold"})
    out = tmp_path / "out.json"
    rc = arm_analysis.main(["--arms", str(arms), "--store", str(store), "--out-json", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"] is None
    assert len(payload["skips"]) == 2


def test_store_is_opened_read_only(tmp_path: Path, store: Path) -> None:
    con = arm_analysis.open_store_readonly(store)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("INSERT INTO record_agents VALUES ('x','y',NULL,NULL,NULL)")
    con.close()
