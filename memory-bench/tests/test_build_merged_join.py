"""Tests for the pure helpers of the mem-75t.4 merged-join driver (loaded by
file path, the driver-script test idiom). Real-infra plumbing (corpus walk,
events files, dolt client) is exercised by the production run, not here."""

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_merged_join = _load("build_merged_join")


def test_uuid_map_excludes_subagent_sidecars(tmp_path: Path) -> None:
    top = tmp_path / "proj" / "aaaa-bbbb.jsonl"
    side = tmp_path / "proj" / "aaaa-bbbb" / "subagents" / "agent-1.jsonl"
    for p in (top, side):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    mapping = build_merged_join.uuid_to_path_map([top, side])
    assert mapping == {"aaaa-bbbb": str(top)}


def test_beads_to_json_sequences_entries() -> None:
    from membench.events_join import EventPair
    from membench.merge_join import merge_bead_sessions

    merged = merge_bead_sessions(
        event_pairs=[
            EventPair(
                "mem-1", "gc-2", "2026-06-02T10:00:00.0+00:00", "2026-06-02T10:00:00.0+00:00", 1
            ),
            EventPair(
                "mem-1", "gc-1", "2026-06-01T10:00:00.0+00:00", "2026-06-01T10:00:00.0+00:00", 1
            ),
        ],
        session_keys={},
        content_rows=[],
        dolt_sessions={},
        assignee_links={},
        uuid_to_path={},
        store_ids=frozenset({"mem-1"}),
    )
    payload = build_merged_join.beads_to_json(merged)
    assert [e["sequence"] for e in payload["mem-1"]] == [1, 2]
    assert [e["gc_session_id"] for e in payload["mem-1"]] == ["gc-1", "gc-2"]
