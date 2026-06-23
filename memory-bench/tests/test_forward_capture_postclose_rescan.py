"""Post-close value re-scan contract (mem-mor1 D-E — Stephanie's firewall design B).

At in-flight forward-capture the firewall's VALUE scan is necessarily empty: the
capturing work's own outcome SHA does not exist yet, so a raw SHA embedded in a
non-outcome-KEYED field (e.g. `memory_ref`) passes the STRUCTURAL scan unguarded —
the exact surface Codex demonstrated in the validity fork (gc-404620). Stephanie's
decision (AM ledger gc-408361) chose design B: once a work CLOSES and its outcome
identifiers are known, re-scan its captured events against those now-known labels and
QUARANTINE any that leak, BEFORE the memory is ever served to future work.

These tests pin that gate: a clean capture survives, a SHA-in-memory_ref capture that
was structurally clean at capture time is quarantined post-close (with its offenders
surfaced, never silently dropped), and an empty outcome is a no-op (nothing to scan).
"""

from __future__ import annotations

from typing import Any

import pytest

from membench.forward_capture import (
    ForwardCaptureFieldError,
    PostCloseRescan,
    QuarantinedCapture,
    rescan_closed_work,
)

SENTINEL_COMMIT = "SENTINELLEAK0000"
SENTINEL_PR = "SENTINELLEAK0001"


def _event(memory_ref: str, op: str = "write") -> dict[str, Any]:
    """A structurally-clean captured memory-event (the shape `OursLiveMemory.write`
    emits) — no outcome-KEYED field, so it passes the at-capture structural scan."""
    return {
        "session": "sess-1",
        "op": op,
        "backend": "kg",
        "memory_ref": memory_ref,
        "source": "forward-capture",
    }


def test_clean_captures_all_survive_rescan() -> None:
    events = [_event("work:mem-prior"), _event("work:mem-other", op="search")]
    result = rescan_closed_work(events, {"commit_sha": SENTINEL_COMMIT, "pr": SENTINEL_PR})
    assert isinstance(result, PostCloseRescan)
    assert result.leaked is False
    assert result.quarantined == ()
    assert [e["memory_ref"] for e in result.clean] == ["work:mem-prior", "work:mem-other"]


def test_sha_in_memory_ref_quarantined_post_close() -> None:
    """The Codex-demonstrated surface: a SHA value embedded in `memory_ref` is
    structurally clean at capture (no outcome-keyed field) but, once the closing
    work's outcome is known, the post-close value re-scan catches and quarantines it."""
    leaky = _event(f"work:mem-x@{SENTINEL_COMMIT}")
    clean = _event("work:mem-prior")
    result = rescan_closed_work([leaky, clean], {"commit_sha": SENTINEL_COMMIT})
    assert result.leaked is True
    # The clean event still serves; only the leaking one is quarantined.
    assert [e["memory_ref"] for e in result.clean] == ["work:mem-prior"]
    assert len(result.quarantined) == 1
    quarantined = result.quarantined[0]
    assert isinstance(quarantined, QuarantinedCapture)
    assert quarantined.event is leaky
    # The offenders are surfaced (where, label), never a silent drop.
    assert any(label == SENTINEL_COMMIT for _, label in quarantined.offenders)


def test_pr_label_also_scanned() -> None:
    leaky = _event(f"work:mem-x/{SENTINEL_PR}")
    result = rescan_closed_work([leaky], {"pr": SENTINEL_PR})
    assert result.leaked is True
    assert result.clean == ()


def test_empty_outcome_is_noop_everything_clean() -> None:
    """No known identifiers → nothing to scan against → every event is clean. The
    re-scan is dormant until a closing work supplies a label (it never invents one)."""
    events = [_event(f"work:mem-x@{SENTINEL_COMMIT}"), _event("work:mem-prior")]
    result = rescan_closed_work(events, {})
    assert result.leaked is False
    assert len(result.clean) == 2


def test_case_insensitive_match() -> None:
    # A SHA reproduced in a different case must still be quarantined — the scan errs
    # toward over-catching (the safe direction for a validity gate), shared with the
    # leak_guard value scan.
    leaky = _event(f"work:mem-x@{SENTINEL_COMMIT.lower()}")
    result = rescan_closed_work([leaky], {"commit_sha": SENTINEL_COMMIT})
    assert result.leaked is True


def test_structural_violation_still_raises_post_close() -> None:
    """A structurally-bad stored event (outcome identifier KEYED in `payload`) is a
    producer bug at ANY time — the post-close re-scan re-projects each event and lets
    that structural violation RAISE, never quarantining over it silently."""
    rogue = {
        "session": "sess-1",
        "op": "write",
        "backend": "kg",
        "memory_ref": "work:mem-x",
        "source": "forward-capture",
        "payload": {"diff": {"commit_sha": SENTINEL_COMMIT}},
    }
    with pytest.raises(ForwardCaptureFieldError):
        rescan_closed_work([rogue], {"commit_sha": SENTINEL_COMMIT})


def test_label_side_payload_dropped_before_value_scan() -> None:
    """A clean (no outcome-keyed) payload is label-side and DROPPED by the projection
    before the value scan — so a SHA that appears ONLY in the dropped payload is not a
    worker-readable leak and the event is clean (the payload never reaches a worker)."""
    event = {
        "session": "sess-1",
        "op": "write",
        "backend": "kg",
        "memory_ref": "work:mem-prior",
        "source": "forward-capture",
        "payload": {"note": SENTINEL_COMMIT},
    }
    result = rescan_closed_work([event], {"commit_sha": SENTINEL_COMMIT})
    assert result.leaked is False
    assert len(result.clean) == 1
