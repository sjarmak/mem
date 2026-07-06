"""CLI tests for `scripts/classify_task_types.py` (mem-hv9l): the driver builds its
model runner through the mem-9ld4 isolation seam and stamps the RUN-LEVEL
``judge_isolation`` marker into the artifact. Loaded from its file path (the
arm_narrative test idiom); the runner factory and isolation prep are monkeypatched
on the loaded module -- no real claude, no shared temp state."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from membench.judge_config import prepare_isolated_judge

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "classify_task_types.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("classify_task_types", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_task_types"] = module
    spec.loader.exec_module(module)
    return module


ctt = _load_script()


def _store(tmp_path: Path) -> Path:
    db = tmp_path / "store.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE work_records (work_id TEXT PRIMARY KEY, rig TEXT, title TEXT, "
        "record TEXT NOT NULL)"
    )
    con.execute(
        "INSERT INTO work_records VALUES (?,?,?,?)",
        ("mem-1", "mem", "fix the dashboard", json.dumps({"metadata": {}})),
    )
    con.commit()
    con.close()
    return db


def test_main_classifies_through_isolated_runner_and_stamps_marker(
    tmp_path: Path, monkeypatch
) -> None:
    seen: dict = {}

    def fake_prepare(base=None, *, label="graded"):
        seen["label"] = label
        return prepare_isolated_judge(base=tmp_path / "iso")

    def fake_factory(model: str, isolation, **kwargs):
        seen["factory_model"] = model
        seen["factory_isolation"] = isolation
        return lambda prompt: '{"mem-1": "bugfix"}'

    monkeypatch.setattr(ctt, "prepare_isolated_judge", fake_prepare)
    monkeypatch.setattr(ctt, "claude_model_runner", fake_factory)

    out = tmp_path / "task-types.json"
    rc = ctt.main(["--store", str(_store(tmp_path)), "--out", str(out)])
    assert rc == 0

    # The runner was built THROUGH the isolation seam, labelled for this callsite.
    assert seen["label"] == "task-types"
    assert seen["factory_model"] == "haiku"

    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["entries"]["mem-1"]["task_type"] == "bugfix"
    # RUN-LEVEL marker (the TS loadTaskTypes reads only entries{}): the run that
    # wrote this artifact is attributable to a clean-config classifier.
    marker = artifact["judge_isolation"]
    assert marker["isolated_config"] is True
    assert marker["config_dir"] == str(seen["factory_isolation"].config_dir)


def test_main_dry_run_never_touches_isolation_or_model(tmp_path: Path, monkeypatch) -> None:
    def exploding_prepare(*args, **kwargs):
        raise AssertionError("dry-run must not materialize an isolation surface")

    monkeypatch.setattr(ctt, "prepare_isolated_judge", exploding_prepare)

    out = tmp_path / "task-types.json"
    rc = ctt.main(["--store", str(_store(tmp_path)), "--out", str(out), "--dry-run"])
    assert rc == 0
    assert not out.exists()  # dry-run reports counts only, writes nothing
