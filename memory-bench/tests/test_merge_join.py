"""Unit tests for the merged session<->bead join (mem-75t.4).

Pure-data tests of the merge rules: events-primary ordering, uuid bridging,
dolt cross-check, and the content-overrides-assignee suspect flag.
"""

from membench.events_join import EventPair
from membench.merge_join import merge_bead_sessions, merged_stats, normalize_ts

STORE_IDS = frozenset({"mem-1", "mem-2", "gpk-9"})


def _content_row(
    session_id: str,
    work_id: str,
    transcript_path: str,
    strength: str = "strong",
    t_first: str | None = "2026-06-01T10:00:00.000Z",
    t_last: str | None = "2026-06-01T10:30:00.000Z",
    n_strong: int = 1,
) -> dict:
    return {
        "session_id": session_id,
        "work_id": work_id,
        "transcript_path": transcript_path,
        "strength": strength,
        "t_first": t_first,
        "t_last": t_last,
        "session_start": t_first,
        "session_end": t_last,
        "n_strong": n_strong,
    }


def _merge(**overrides):
    kwargs = {
        "event_pairs": [],
        "session_keys": {},
        "content_rows": [],
        "dolt_sessions": {},
        "assignee_links": {},
        "uuid_to_path": {},
        "store_ids": STORE_IDS,
    }
    kwargs.update(overrides)
    return merge_bead_sessions(**kwargs)


# --- timestamp normalization ---------------------------------------------------


def test_normalize_ts_nanoseconds_and_offset() -> None:
    assert normalize_ts("2026-06-01T22:05:10.566655593-04:00") == (
        "2026-06-02T02:05:10.566655+00:00"
    )


def test_normalize_ts_zulu_milliseconds() -> None:
    assert normalize_ts("2026-06-01T10:00:00.000Z") == "2026-06-01T10:00:00+00:00"


def test_normalize_ts_garbage_is_none() -> None:
    assert normalize_ts("not a time") is None
    assert normalize_ts(None) is None
    assert normalize_ts("") is None


# --- events primary ------------------------------------------------------------


def test_events_pairs_become_ordered_entries() -> None:
    merged = _merge(
        event_pairs=[
            EventPair(
                "mem-1", "gc-200", "2026-06-02T09:00:00.0-04:00", "2026-06-02T10:00:00.0-04:00", 3
            ),
            EventPair(
                "mem-1", "gc-100", "2026-06-01T10:00:00.0-04:00", "2026-06-01T11:00:00.0-04:00", 2
            ),
        ],
        session_keys={"gc-100": "uuid-a", "gc-200": "uuid-b"},
        uuid_to_path={"uuid-a": "/t/a.jsonl"},
    )
    bead = merged["mem-1"]
    assert [e.gc_session_id for e in bead.entries] == ["gc-100", "gc-200"]
    assert bead.entries[0].transcript_path == "/t/a.jsonl"
    assert bead.entries[1].transcript_path is None  # uuid-b not on disk
    assert bead.entries[0].sources == ["events"]


def test_out_of_store_beads_are_dropped() -> None:
    merged = _merge(
        event_pairs=[
            EventPair(
                "other-1", "gc-100", "2026-06-01T10:00:00.0-04:00", "2026-06-01T10:00:00.0-04:00", 1
            )
        ],
    )
    assert merged == {}


# --- content scan bridging -----------------------------------------------------


def test_content_row_merges_into_event_entry_via_uuid() -> None:
    merged = _merge(
        event_pairs=[
            EventPair(
                "mem-1", "gc-100", "2026-06-01T14:00:00.0+00:00", "2026-06-01T15:00:00.0+00:00", 1
            )
        ],
        session_keys={"gc-100": "uuid-a"},
        content_rows=[
            _content_row(
                "uuid-a",
                "mem-1",
                "/t/a.jsonl",
                t_first="2026-06-01T13:00:00.000Z",
                t_last="2026-06-01T16:00:00.000Z",
            )
        ],
    )
    (entry,) = merged["mem-1"].entries
    assert sorted(entry.sources) == ["content-scan", "events"]
    assert entry.transcript_path == "/t/a.jsonl"
    # content widened the window on both sides
    assert entry.t_first == "2026-06-01T13:00:00+00:00"
    assert entry.t_last == "2026-06-01T16:00:00+00:00"


def test_strong_content_row_creates_entry_weak_does_not() -> None:
    merged = _merge(
        content_rows=[
            _content_row("uuid-a", "mem-1", "/t/a.jsonl", strength="strong"),
            _content_row("uuid-b", "mem-2", "/t/b.jsonl", strength="weak"),
        ],
    )
    assert "mem-1" in merged
    assert "mem-2" not in merged


def test_subagent_sidecar_loses_to_top_level_transcript() -> None:
    merged = _merge(
        content_rows=[
            _content_row("uuid-a", "mem-1", "/t/p/subagents/agent-1.jsonl", n_strong=5),
            _content_row("uuid-a", "mem-1", "/t/p/a.jsonl", n_strong=1),
        ],
    )
    (entry,) = merged["mem-1"].entries
    assert entry.transcript_path == "/t/p/a.jsonl"


def test_ambiguous_uuid_does_not_guess_gc_identity() -> None:
    # Two respawned seats share the resumed Claude conversation; a content-only
    # entry must not pick one arbitrarily.
    merged = _merge(
        session_keys={"gc-100": "uuid-a", "gc-200": "uuid-a"},
        content_rows=[_content_row("uuid-a", "mem-1", "/t/a.jsonl")],
    )
    (entry,) = merged["mem-1"].entries
    assert entry.gc_session_id is None


# --- dolt cross-check ----------------------------------------------------------


def test_dolt_annotates_event_entry_and_adds_fallback() -> None:
    merged = _merge(
        event_pairs=[
            EventPair(
                "mem-1", "gc-100", "2026-06-01T10:00:00.0+00:00", "2026-06-01T10:00:00.0+00:00", 1
            )
        ],
        dolt_sessions={"mem-1": ["gc-100", "gc-300"]},
    )
    entries = merged["mem-1"].entries
    by_gc = {e.gc_session_id: e for e in entries}
    assert sorted(by_gc["gc-100"].sources) == ["dolt-history", "events"]
    assert by_gc["gc-300"].sources == ["dolt-history"]
    # timeless dolt fallback sorts after the timestamped event entry
    assert entries[0].gc_session_id == "gc-100"


def test_dolt_bridges_into_content_entry_via_uuid() -> None:
    merged = _merge(
        session_keys={"gc-100": "uuid-a"},
        content_rows=[_content_row("uuid-a", "mem-1", "/t/a.jsonl")],
        dolt_sessions={"mem-1": ["gc-100"]},
    )
    (entry,) = merged["mem-1"].entries
    assert sorted(entry.sources) == ["content-scan", "dolt-history"]
    assert entry.gc_session_id == "gc-100"


# --- assignee + the suspect flag ------------------------------------------------


def test_assignee_annotates_matching_entry() -> None:
    merged = _merge(
        content_rows=[_content_row("uuid-a", "mem-1", "/t/a.jsonl")],
        assignee_links={"mem-1": "/t/a.jsonl"},
    )
    (entry,) = merged["mem-1"].entries
    assert "assignee" in entry.sources
    assert not entry.suspect


def test_contradicted_assignee_is_suspect() -> None:
    # The store points mem-1 at a transcript that never mentions mem-1 and
    # strongly worked gpk-9 — the gc-01wm wrong-conversation case.
    merged = _merge(
        content_rows=[_content_row("uuid-z", "gpk-9", "/t/z.jsonl")],
        assignee_links={"mem-1": "/t/z.jsonl"},
    )
    (entry,) = merged["mem-1"].entries
    assert entry.sources == ["assignee"]
    assert entry.suspect


def test_unscanned_assignee_transcript_is_kept_not_suspect() -> None:
    merged = _merge(assignee_links={"mem-1": "/t/never-scanned.jsonl"})
    (entry,) = merged["mem-1"].entries
    assert entry.sources == ["assignee"]
    assert not entry.suspect


# --- alias collapse (mem-75t.10) -----------------------------------------------


def test_same_uuid_via_two_namespaces_collapses_to_one_entry() -> None:
    # Events resolved the homes-namespace path; the store assignee link carries
    # the bare path. Same uuid stem => ONE session, not two record_agents rows.
    homes = "/home/ds/.claude-homes/acct/.claude/projects/p/sess-uuid.jsonl"
    bare = "/home/ds/.claude/projects/p/sess-uuid.jsonl"
    merged = _merge(
        event_pairs=[
            EventPair(
                "mem-1", "gc-100", "2026-06-01T10:00:00.0+00:00", "2026-06-01T11:00:00.0+00:00", 4
            )
        ],
        session_keys={"gc-100": "sess-uuid"},
        uuid_to_path={"sess-uuid": homes},
        assignee_links={"mem-1": bare},
    )
    (entry,) = merged["mem-1"].entries
    assert sorted(entry.sources) == ["assignee", "events"]
    assert entry.session_key == "sess-uuid"
    assert entry.n_events == 4
    # the surviving entry adopts the on-disk corpus path, not the stale alias
    assert entry.transcript_path == homes
    assert not entry.suspect


def test_alias_clears_spurious_suspect_when_events_confirm_same_uuid() -> None:
    # The assignee path looks contradicted on its own (the scan strongly tied
    # that transcript to gpk-9), but events (PRIMARY) place the same uuid on
    # mem-1 — collapse clears the spurious suspect flag.
    homes = "/home/ds/.claude-homes/acct/.claude/projects/p/sess-uuid.jsonl"
    bare = "/home/ds/.claude/projects/p/sess-uuid.jsonl"
    merged = _merge(
        event_pairs=[
            EventPair(
                "mem-1", "gc-100", "2026-06-01T10:00:00.0+00:00", "2026-06-01T11:00:00.0+00:00", 2
            )
        ],
        session_keys={"gc-100": "sess-uuid"},
        uuid_to_path={"sess-uuid": homes},
        content_rows=[_content_row("other-uuid", "gpk-9", bare)],
        assignee_links={"mem-1": bare},
    )
    (entry,) = merged["mem-1"].entries
    assert sorted(entry.sources) == ["assignee", "events"]
    assert not entry.suspect


# --- stats ----------------------------------------------------------------------


def test_merged_stats_counts() -> None:
    merged = _merge(
        event_pairs=[
            EventPair(
                "mem-1", "gc-100", "2026-06-01T10:00:00.0+00:00", "2026-06-01T10:00:00.0+00:00", 1
            ),
            EventPair(
                "mem-1", "gc-200", "2026-06-02T10:00:00.0+00:00", "2026-06-02T10:00:00.0+00:00", 1
            ),
        ],
        session_keys={"gc-100": "uuid-a"},
        uuid_to_path={"uuid-a": "/t/a.jsonl"},
        content_rows=[_content_row("uuid-b", "mem-2", "/t/b.jsonl")],
    )
    stats = merged_stats(merged)
    assert stats["beads"] == 2
    assert stats["multi_session_beads"] == 1
    assert stats["session_entries"] == 3
    assert stats["entries_with_transcript"] == 2
    assert stats["entries_by_source"] == {"content-scan": 1, "events": 2}
    assert stats["iterations_histogram"] == {"1": 1, "2": 1}
