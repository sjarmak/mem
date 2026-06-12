"""Unit tests for the gc-events join source (mem-75t.4, PRIMARY source).

All event lines are synthetic — no dependence on the real .gc dir. The
mechanism under test is structural log parsing (ZFC: no semantic judgment).
"""

import gzip
import json
from pathlib import Path

import pytest

from membench.events_join import (
    EventPair,
    actor_session,
    bead_payload,
    collect_events_join,
    event_paths,
    is_session_bead,
)


def _event(
    seq: int,
    type_: str,
    subject: str,
    actor: str,
    ts: str,
    payload: dict | None = None,
) -> str:
    return json.dumps(
        {
            "seq": seq,
            "type": type_,
            "ts": ts,
            "actor": actor,
            "subject": subject,
            "payload": payload if payload is not None else {"id": subject},
        }
    )


# --- actor session extraction -------------------------------------------------


def test_actor_session_prefixed_and_bare() -> None:
    assert actor_session("polecat-gc-223433") == "gc-223433"
    assert actor_session("mem-worker-gc-340057") == "gc-340057"
    assert actor_session("gc-351468") == "gc-351468"


def test_actor_session_symbolic_actors_yield_none() -> None:
    for actor in (
        "mayor",
        "controller",
        "cache-reconcile",
        "human",
        "order:bead-janitor",
        "/home/ds/gascity/polecat-1",
    ):
        assert actor_session(actor) is None


# --- payload shapes ------------------------------------------------------------


def test_bead_payload_nested_and_flat() -> None:
    nested = {"payload": {"bead": {"id": "gc-4ac4o", "title": "x"}}}
    flat = {"payload": {"id": "gc-4ac4o", "title": "x"}}
    assert bead_payload(nested) == {"id": "gc-4ac4o", "title": "x"}
    assert bead_payload(flat) == {"id": "gc-4ac4o", "title": "x"}
    assert bead_payload({"payload": "not-a-dict"}) is None
    assert bead_payload({}) is None


def test_is_session_bead_by_type_and_label() -> None:
    assert is_session_bead({"issue_type": "session"})
    assert is_session_bead({"labels": ["gc:session", "agent:/x"]})
    assert not is_session_bead({"issue_type": "task", "labels": ["bug"]})


# --- collection ----------------------------------------------------------------


def _write_events(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_collect_pairs_and_session_keys(tmp_path: Path) -> None:
    lines = [
        # work bead touched twice by one session, once by another
        _event(1, "bead.updated", "mem-1", "polecat-gc-100", "2026-06-01T10:00:00.0-04:00"),
        _event(2, "bead.updated", "mem-1", "polecat-gc-100", "2026-06-01T11:00:00.0-04:00"),
        _event(3, "bead.closed", "mem-1", "mem-worker-gc-200", "2026-06-02T09:00:00.0-04:00"),
        # session-housekeeping bead carries the resolver mapping
        _event(
            4,
            "bead.updated",
            "gc-100",
            "cache-reconcile",
            "2026-06-01T10:01:00.0-04:00",
            payload={
                "id": "gc-100",
                "issue_type": "session",
                "metadata": {"session_key": "aaaa-bbbb"},
            },
        ),
        # symbolic actor: no session id, no pair
        _event(5, "bead.updated", "mem-1", "controller", "2026-06-01T12:00:00.0-04:00"),
        # non-bead event is skipped entirely
        json.dumps({"seq": 6, "type": "mail.sent", "ts": "x", "actor": "human"}),
    ]
    live = tmp_path / "events.jsonl"
    _write_events(live, lines)

    join = collect_events_join([live])
    assert join.session_keys == {"gc-100": "aaaa-bbbb"}
    assert join.pairs == (
        EventPair(
            work_id="mem-1",
            session_id="gc-100",
            t_first="2026-06-01T10:00:00.0-04:00",
            t_last="2026-06-01T11:00:00.0-04:00",
            n_events=2,
            n_actor_events=2,
        ),
        EventPair(
            work_id="mem-1",
            session_id="gc-200",
            t_first="2026-06-02T09:00:00.0-04:00",
            t_last="2026-06-02T09:00:00.0-04:00",
            n_events=1,
            n_actor_events=1,
        ),
    )


def test_collect_pairs_from_payload_assignee(tmp_path: Path) -> None:
    # Symbolic actors (controller/cache-reconcile) write events that carry the
    # worker session only in the payload assignee field.
    lines = [
        _event(
            1,
            "bead.updated",
            "mem-1",
            "cache-reconcile",
            "2026-06-01T10:00:00.0-04:00",
            payload={"id": "mem-1", "assignee": "enterprisebench-worker-gc-333253"},
        ),
        # actor and assignee are the same session: one pair, both channels
        _event(
            2,
            "bead.closed",
            "mem-1",
            "polecat-gc-333253",
            "2026-06-01T11:00:00.0-04:00",
            payload={"id": "mem-1", "assignee": "polecat-gc-333253"},
        ),
    ]
    live = tmp_path / "events.jsonl"
    _write_events(live, lines)
    (pair,) = collect_events_join([live]).pairs
    assert pair.session_id == "gc-333253"
    assert pair.n_events == 2
    assert pair.n_actor_events == 1
    assert pair.n_assignee_events == 2


def test_collect_nested_payload_shape(tmp_path: Path) -> None:
    line = _event(
        1,
        "bead.updated",
        "gc-4ac4o",
        "polecat-gc-300",
        "2026-06-08T18:35:10.0-04:00",
        payload={"bead": {"id": "gc-4ac4o", "title": "work"}},
    )
    live = tmp_path / "events.jsonl"
    _write_events(live, [line])
    join = collect_events_join([live])
    assert [p.work_id for p in join.pairs] == ["gc-4ac4o"]


def test_session_beads_never_become_pairs(tmp_path: Path) -> None:
    line = _event(
        1,
        "bead.updated",
        "gc-100",
        "polecat-gc-100",
        "2026-06-01T10:00:00.0-04:00",
        payload={"id": "gc-100", "labels": ["gc:session"]},
    )
    live = tmp_path / "events.jsonl"
    _write_events(live, [line])
    assert collect_events_join([live]).pairs == ()


def test_archive_live_overlap_dedupes_by_seq(tmp_path: Path) -> None:
    archived = [
        _event(1, "bead.updated", "mem-1", "polecat-gc-100", "2026-05-01T10:00:00.0-04:00"),
        _event(2, "bead.updated", "mem-1", "polecat-gc-100", "2026-05-01T11:00:00.0-04:00"),
    ]
    gz = tmp_path / "events.jsonl.archive-20260519T184356Z-seq-1-2.gz"
    with gzip.open(gz, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(archived) + "\n")
    live_lines = [
        # seq 2 duplicates the archive tail and must be skipped
        _event(2, "bead.updated", "mem-1", "polecat-gc-100", "2026-05-01T11:00:00.0-04:00"),
        _event(3, "bead.closed", "mem-1", "polecat-gc-100", "2026-05-01T12:00:00.0-04:00"),
    ]
    live = tmp_path / "events.jsonl"
    _write_events(live, live_lines)

    join = collect_events_join(event_paths(tmp_path))
    (pair,) = join.pairs
    assert pair.n_events == 3
    assert pair.t_last == "2026-05-01T12:00:00.0-04:00"


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    live = tmp_path / "events.jsonl"
    _write_events(live, ['{"type":"bead.updated", truncated', ""])
    join = collect_events_join([live])
    assert join.pairs == ()


def test_malformed_bead_lines_are_counted(tmp_path: Path) -> None:
    # A bead-shaped line that fails to parse is COUNTED (not silently dropped) so
    # event-log corruption surfaces as a coverage gap (mem-2el item 3). A
    # non-bead malformed line is below the probe and never reaches the parse.
    live = tmp_path / "events.jsonl"
    _write_events(
        live,
        ['{"type":"bead.updated", truncated', '{"type":"bead.x" also broken', "not even json"],
    )
    join = collect_events_join([live])
    assert join.n_malformed_lines == 2
    assert join.pairs == ()


# --- file discovery ------------------------------------------------------------


def test_event_paths_orders_archives_before_live(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")
    a2 = tmp_path / "events.jsonl.archive-20260603T050726Z-seq-890190-1334289.gz"
    a1 = tmp_path / "events.jsonl.archive-20260519T184356Z-seq-1-890189.gz"
    for p in (a1, a2):
        with gzip.open(p, "wt", encoding="utf-8") as handle:
            handle.write("")
    assert event_paths(tmp_path) == [a1, a2, tmp_path / "events.jsonl"]


def test_event_paths_requires_live_log(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        event_paths(tmp_path)
