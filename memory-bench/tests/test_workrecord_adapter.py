"""Tests for the WorkRecord -> ablation-ladder Harbor task adapter (mem-apg.2).

One task dir per ablation rung. The task is built ONLY from the record's label-free
title (never metadata, which carries outcome signals; never the outcome), and every
agent-readable file is leak-guarded before write (mem-apg.1 finding C1, ARCHITECTURE
D17 ablation-first).
"""

import pytest
import toml

from membench.grading import AblationSource, OutcomeLeakError
from membench.harbor.workrecord_adapter import WorkRecordLadderAdapter


def _record(
    work_id="w1",
    rig="mem",
    title="Fix the broken parser",
    started="2026-01-10T00:00:00Z",
    **outcome,
):
    rec = {
        "work_id": work_id,
        "rig": rig,
        "title": title,
        "lifecycle": {
            "created": "2026-01-01T00:00:00Z",
            "started": started,
            "closed": "2026-02-01T00:00:00Z",
            "status": "closed",
        },
    }
    if outcome:
        rec["outcome"] = outcome
    return rec


def test_emits_one_task_dir_per_rung(tmp_path):
    created = WorkRecordLadderAdapter(_record(), tmp_path).run()
    assert len(created) == len(AblationSource().rungs)
    for task_dir in created:
        assert (task_dir / "task.toml").is_file()
        assert (task_dir / "instruction.md").is_file()


def test_task_toml_carries_rung_workid_rig_and_loo_boundary(tmp_path):
    created = WorkRecordLadderAdapter(_record(work_id="abc", rig="gascity"), tmp_path).run()
    md = toml.load(created[0] / "task.toml")["metadata"]
    assert md["work_id"] == "abc"
    assert md["rig"] == "gascity"
    assert md["loo_boundary"] == "2026-01-10T00:00:00Z"
    assert md["source"] == "workrecord"


def test_allow_internet_defaults_offline_and_is_opt_in(tmp_path):
    # Offline by default (deterministic scoring); the real-exec spike opts in because
    # Harbor's installed claude-code agent fetches its CLI + deps over the network.
    # Emitted as harbor>=0.13's network_mode, not the deprecated allow_internet field.
    offline = WorkRecordLadderAdapter(_record(), tmp_path / "off").run()
    assert toml.load(offline[0] / "task.toml")["environment"]["network_mode"] == "no-network"
    online = WorkRecordLadderAdapter(_record(), tmp_path / "on", allow_internet=True).run()
    assert toml.load(online[0] / "task.toml")["environment"]["network_mode"] == "public"


def test_each_dir_carries_its_own_rung(tmp_path):
    # Guards against the loop writing the same rung tag into every task.toml: each
    # dir's recorded rung must match its slug suffix, and all rungs are distinct.
    created = WorkRecordLadderAdapter(_record(work_id="abc"), tmp_path).run()
    seen = set()
    for task_dir in created:
        rung = toml.load(task_dir / "task.toml")["metadata"]["rung"]
        assert task_dir.name == f"abc-{rung.replace('+', '-')}"
        seen.add(rung)
    assert seen == set(AblationSource().rungs)


def test_instruction_is_label_free_built_from_title(tmp_path):
    created = WorkRecordLadderAdapter(
        _record(title="Fix parser bug", commit_sha="deadbeefcafe1234"), tmp_path
    ).run()
    for task_dir in created:
        text = (task_dir / "instruction.md").read_text()
        assert "Fix parser bug" in text
        assert "deadbeefcafe1234" not in text  # outcome never reaches the agent


@pytest.mark.parametrize("key", ["commit_sha", "base_commit"])
def test_leak_guard_fires_when_outcome_value_is_in_the_title(key, tmp_path):
    # Mechanical backstop: if any answer-revealing outcome value appears in
    # agent-readable text, the adapter raises rather than writing the task — and
    # leaves no partial output (validate-all-then-write).
    value = "deadbeefcafe1234"
    rec = _record(title=f"port of {value}", **{key: value})
    with pytest.raises(OutcomeLeakError):
        WorkRecordLadderAdapter(rec, tmp_path).run()
    assert list(tmp_path.iterdir()) == []  # nothing written on abort


def test_memory_enabled_rungs_note_memory_and_none_does_not(tmp_path):
    created = WorkRecordLadderAdapter(_record(), tmp_path).run()
    none_dir = next(d for d in created if d.name.endswith("-none"))
    ours_dir = next(d for d in created if d.name.endswith("-ours"))
    assert "## Memory" not in (none_dir / "instruction.md").read_text()
    assert "## Memory" in (ours_dir / "instruction.md").read_text()


def test_overwrite_guard(tmp_path):
    WorkRecordLadderAdapter(_record(), tmp_path).run()
    with pytest.raises(FileExistsError):
        WorkRecordLadderAdapter(_record(), tmp_path).run()
    # explicit overwrite succeeds
    WorkRecordLadderAdapter(_record(), tmp_path, overwrite=True).run()


def test_missing_loo_boundary_raises(tmp_path):
    # No started AND no created -> no leak-safe boundary; must fail, not default.
    rec = {"work_id": "w", "rig": "mem", "title": "t", "lifecycle": {"status": "closed"}}
    with pytest.raises(ValueError):
        WorkRecordLadderAdapter(rec, tmp_path).run()
