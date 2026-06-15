"""M7 — the provenance/reversibility gate (reachability, NOT shape).

The decisive premortem finding (lens 4): a gate that checks the *shape* of
``source_trace_ids`` is a lie once ``consolidate -> tombstone -> GC`` reaps a cited
trace — "re-derivable" is true at write time and false at read time. So the gate
DEREFERENCES: every cited trace id must resolve to a live row, and the negative
test is a *reaped* citation, not a hand-fabricated one. A consolidated item with
no provenance at all is also a failure (a claim with no source).
"""

from __future__ import annotations

from membench.grading.provenance_gate import (
    ProvenanceItem,
    cited_trace_ids,
    provenance_gate,
)


def _live(ids):
    s = set(ids)
    return lambda t: t in s


def test_rederivable_item_passes():
    items = [ProvenanceItem(memory_id="schema-1", source_trace_ids=("ep-1", "ep-2"))]
    res = provenance_gate(items, is_live=_live({"ep-1", "ep-2", "ep-3"}))
    assert res.valid is True
    assert res.checked == 1
    assert res.reachable == 1
    assert res.dangling == ()


def test_gc_reaped_citation_fails_the_gate():
    # The cited trace existed at consolidation time; GC then reaped ep-2.
    items = [ProvenanceItem(memory_id="schema-1", source_trace_ids=("ep-1", "ep-2"))]
    res = provenance_gate(items, is_live=_live({"ep-1"}))  # ep-2 reaped
    assert res.valid is False
    assert res.reachable == 0
    assert len(res.dangling) == 1
    d = res.dangling[0]
    assert d.memory_id == "schema-1"
    assert d.missing_trace_ids == ("ep-2",)
    assert d.reason == "dangling_citation"


def test_item_with_no_provenance_fails():
    items = [ProvenanceItem(memory_id="fabricated", source_trace_ids=())]
    res = provenance_gate(items, is_live=_live({"ep-1"}))
    assert res.valid is False
    assert res.dangling[0].reason == "no_provenance"


def test_empty_item_set_is_vacuously_valid_but_distinct():
    res = provenance_gate([], is_live=_live(set()))
    assert res.valid is True
    assert res.checked == 0
    assert "no consolidated items" in res.reason


def test_cited_trace_ids_is_the_gc_keep_set():
    items = [
        ProvenanceItem(memory_id="s1", source_trace_ids=("ep-1", "ep-2")),
        ProvenanceItem(memory_id="s2", source_trace_ids=("ep-2", "ep-9")),
    ]
    # The union of every citation — any GC MUST keep these or it breaks reachability.
    assert cited_trace_ids(items) == frozenset({"ep-1", "ep-2", "ep-9"})


def test_reachability_uses_the_predicate_not_the_shape():
    # Both items are well-shaped; only the predicate distinguishes them.
    items = [
        ProvenanceItem(memory_id="ok", source_trace_ids=("ep-1",)),
        ProvenanceItem(memory_id="bad", source_trace_ids=("ep-2",)),
    ]
    res = provenance_gate(items, is_live=lambda t: t == "ep-1")
    assert res.checked == 2
    assert res.reachable == 1
    assert {d.memory_id for d in res.dangling} == {"bad"}


def test_retrieve_result_carries_source_trace_ids_field():
    # M7 substrate: a retrieve of consolidated items can carry provenance through to
    # the gate. Additive + default-empty so every existing arm stays valid.
    from membench.memory_systems.base import RetrieveResult
    from membench.runtime import IdClock
    from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation

    ev = MemoryEvent(
        event_id="ev-1",
        trial_id="t",
        session_id="s",
        step_id="st",
        timestamp=IdClock().timestamp(),
        concrete_tool="x",
        normalized_operation=MemoryOperation.SEARCH,
        backend=MemoryBackend.FILESYSTEM,
    )
    default = RetrieveResult(payloads={}, event=ev)
    assert default.source_trace_ids == {}
    carried = RetrieveResult(
        payloads={"schema-1": "merged lesson"},
        event=ev,
        source_trace_ids={"schema-1": ("ep-1", "ep-2")},
    )
    assert carried.source_trace_ids["schema-1"] == ("ep-1", "ep-2")
