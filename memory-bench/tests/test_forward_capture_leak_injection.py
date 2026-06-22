"""Phase-0 leak-injection contract test (PRD docs/prd-openrath-incorporation.md §Phase 0).

The forward-capture path projects a runtime `Session`-shaped dict into mem's
field-separated types. This test makes the firewall contract EXECUTABLE and
failing-first (TDD red): it pins that the projection routes outcome identifiers
ONLY into the label-side `record['outcome']` and NEVER into the worker-readable
`MemoryEvent`, that feeding the projection through the agent-readable path RAISES
`OutcomeLeakError` on a sentinel leak, that a known-good event projects (so a
rejection is distinguishable from a parse failure), and that a novel sentinel
field RAISES under the strict allow-list rather than being silently dropped.

It reuses the two existing guards as the contract's enforcement layer:
`grading.leak_guard` (`OutcomeLeakError`, `assert_no_outcome_leak`,
`_IDENTIFYING_KEYS`) and `validity` (`loo_bounded` / `assert_no_leak`).
"""

from __future__ import annotations

from typing import Any

import pytest

from membench.forward_capture import (
    ForwardCaptureFieldError,
    project_memory_event,
    project_session_to_record,
    worker_readable_text,
)
from membench.grading.leak_guard import (
    OutcomeLeakError,
    assert_no_outcome_leak,
    outcome_labels,
)

SENTINEL_COMMIT = "SENTINELLEAK0000"
SENTINEL_PR = "SENTINELLEAK0001"
SENTINEL_BASE = "SENTINELLEAK0002"


def _leaky_session() -> dict[str, Any]:
    """An OpenRath-shaped Session dict carrying outcome identifiers in its lineage
    AND a memory-event payload. The outcome values are sentinels so a leak into any
    worker-readable text is detectable by substring scan."""
    return {
        "work_id": "mem-zzz9",
        "rig": "scix_experiments",
        "started": "2026-06-01T00:00:00Z",
        "closed": "2026-06-02T00:00:00Z",
        "outcome": {
            "pr": SENTINEL_PR,
            "commit_sha": SENTINEL_COMMIT,
            "base_commit": SENTINEL_BASE,
        },
        "memory_event": {
            "session": "sess-1",
            "work_id": "mem-zzz9",
            "op": "read",
            "backend": "filesystem",
            "memory_ref": ".mem/lessons/lvp.md",
            "source": "forward-capture",
            "occurred_at": "2026-06-01T12:00:00Z",
        },
    }


def test_outcome_routes_only_into_record_outcome() -> None:
    record = project_session_to_record(_leaky_session())
    assert record["outcome"]["commit_sha"] == SENTINEL_COMMIT
    assert record["outcome"]["pr"] == SENTINEL_PR
    assert record["outcome"]["base_commit"] == SENTINEL_BASE


def test_memory_event_drops_every_outcome_identifier() -> None:
    session = _leaky_session()
    event = project_memory_event(session["memory_event"])
    flat = " ".join(str(v) for v in event.values())
    assert SENTINEL_COMMIT not in flat
    assert SENTINEL_PR not in flat
    assert SENTINEL_BASE not in flat
    assert event["op"] == "read"
    assert event["source"] == "forward-capture"


def test_worker_readable_text_raises_on_sentinel_leak() -> None:
    """If a (hypothetically buggy) projection let the sentinel reach the worker-
    readable text, the firewall must RAISE — never silently strip."""
    record = project_session_to_record(_leaky_session())
    # The agent-readable surface for the captured memory, plus a planted leak to
    # prove the guard fires (a real projection would never produce this).
    leaked = worker_readable_text(record) + f"\nbase={SENTINEL_COMMIT}"
    with pytest.raises(OutcomeLeakError):
        assert_no_outcome_leak(leaked, outcome_labels(record))


def test_clean_worker_text_passes_the_guard() -> None:
    record = project_session_to_record(_leaky_session())
    # No raise: the projected worker-readable text carries no outcome identifier.
    assert_no_outcome_leak(worker_readable_text(record), outcome_labels(record))


def test_positive_known_good_event_projects() -> None:
    """A rejection-only test cannot tell 'rejected a leak' from 'failed to parse'.
    A known-good memory-event must project successfully."""
    good = {
        "session": "sess-2",
        "work_id": "mem-aaa1",
        "op": "search",
        "backend": "kg",
        "memory_ref": "work:mem-prior",
        "source": "forward-capture",
        "occurred_at": "2026-06-01T09:00:00Z",
    }
    event = project_memory_event(good)
    assert event["op"] == "search"
    assert event["backend"] == "kg"
    assert event["memory_ref"] == "work:mem-prior"


def test_novel_sentinel_field_raises_strict_allow_list() -> None:
    """The allow-list (not deny-list) principle: a producer that grows a novel
    field RAISES rather than smuggling an unscanned column past the firewall."""
    rogue = {
        "session": "sess-3",
        "op": "read",
        "backend": "filesystem",
        "source": "forward-capture",
        "occurred_at": "2026-06-01T09:00:00Z",
        "sneaky_outcome_sha": SENTINEL_COMMIT,
    }
    with pytest.raises(ForwardCaptureFieldError):
        project_memory_event(rogue)
