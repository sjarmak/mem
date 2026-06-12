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


def test_extend_corpus_with_restored_adds_pruned_top_level_only(tmp_path: Path) -> None:
    """mem-qw5 window extension: restored transcripts join the corpus unless the
    live corpus already resolves the uuid (live wins) or the ORIGINAL path was a
    subagent sidecar."""
    from membench.transcript_archive import RestoredTranscript

    live = tmp_path / "proj" / "aaaa-1111.jsonl"
    live.parent.mkdir(parents=True)
    live.write_text("", encoding="utf-8")
    files = [live]
    uuid_map = build_merged_join.uuid_to_path_map(files)

    restored_dir = tmp_path / "archive" / "restored"
    pruned = restored_dir / "d1" / "bbbb-2222.jsonl"
    dup_of_live = restored_dir / "d2" / "aaaa-1111.jsonl"
    sidecar = restored_dir / "d3" / "agent-1.jsonl"
    for p in (pruned, dup_of_live, sidecar):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    restored = [
        RestoredTranscript(path=pruned, source="/gone/proj/bbbb-2222.jsonl"),
        RestoredTranscript(path=dup_of_live, source="/gone/proj/aaaa-1111.jsonl"),
        RestoredTranscript(path=sidecar, source="/gone/proj/x/subagents/agent-1.jsonl"),
    ]

    out_files, out_map, added = build_merged_join.extend_corpus_with_restored(
        files, uuid_map, restored
    )

    assert added == 1
    assert out_files == [live, pruned]
    assert out_map == {"aaaa-1111": str(live), "bbbb-2222": str(pruned)}
    # Pure: the inputs are untouched.
    assert files == [live]
    assert uuid_map == {"aaaa-1111": str(live)}
