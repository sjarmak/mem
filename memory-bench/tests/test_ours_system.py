"""Tests for the `ours` arm — retrieval-v1 wired in via an injectable runner.

The runner is injected so these are hermetic: no built CLI, no real store. The
end-to-end path against a real `mem retrieve` is covered by the integration test.
"""

import json

import pytest

from membench.memory_systems.base import RetrievalRequest
from membench.memory_systems.ours_system import OursMemory
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryOperation
from membench.validity import QueryWork


def _ctx():
    return StepContext(trial_id="t", session_id="B", step_id="replay", clock=IdClock())


def _req(scope="cross_rig"):
    return RetrievalRequest(
        query_work=QueryWork(work_id="B", rig="rigA", started="2026-01-10T00:00:00Z"), scope=scope
    )


# A canned retrieval-v1 `data` payload (two items, lessons consumed verbatim).
_DATA = {
    "scope": "cross_rig",
    "work_id": "B",
    "trigger_count": 1,
    "total_matched": 5,
    "near_duplicate_top": True,
    "fts_truncated": False,
    "items": [
        {
            "work_id": "prior-1",
            "rig": "rigB",
            "title": "fix lint",
            "match": "signature",
            "matched_signatures": ["sig-1"],
            "matched_classes": [],
            "citation": {"work_id": "prior-1", "commit_sha": "abc123"},
            "lessons": [{"payload": {"root_cause": "missing import"}}],
        },
        {
            "work_id": "prior-2",
            "rig": "rigC",
            "title": "fix build",
            "match": "error_class",
            "matched_signatures": [],
            "matched_classes": ["tsc:TS2304"],
            "citation": {"work_id": "prior-2"},
            "lessons": [],
        },
    ],
}


def test_retrieve_maps_items_to_payloads_and_event():
    captured = {}

    def runner(query):
        captured["query"] = query
        return _DATA

    arm = OursMemory(store_path="/tmp/store.db", runner=runner)
    result = arm.retrieve(_req(scope="same_rig_temporal"), _ctx())

    # Payload keyed by work_id; lessons consumed (not re-distilled), citation kept.
    assert set(result.payloads) == {"prior-1", "prior-2"}
    decoded = json.loads(result.payloads["prior-1"])
    assert decoded["citation"]["commit_sha"] == "abc123"
    assert decoded["lessons"] == [{"payload": {"root_cause": "missing import"}}]

    # Precision-guard signal passes through.
    assert result.total_matched == 5
    assert result.near_duplicate_top is True

    # The normalized event mirrors §6.2 for a graph search.
    assert result.event.backend is MemoryBackend.KG
    assert result.event.normalized_operation is MemoryOperation.SEARCH
    assert result.event.retrieved_ids == ["prior-1", "prior-2"]

    # The CLI scope spelling is handed to the runner.
    assert captured["query"].scope == "same_rig_temporal"
    assert captured["query"].work_id == "B"
    assert captured["query"].store_path == "/tmp/store.db"


def test_retrieve_requires_query_work_and_scope():
    arm = OursMemory(store_path="x", runner=lambda q: _DATA)
    with pytest.raises(ValueError, match="failure-triggered"):
        arm.retrieve(RetrievalRequest(query_text="hi", requested_ids=["m1"]), _ctx())


def test_retrieve_requires_store_path():
    arm = OursMemory(store_path=None, runner=lambda q: _DATA)
    with pytest.raises(ValueError, match="store_path"):
        arm.retrieve(_req(), _ctx())


def test_runner_resolution_requires_runner_or_bin():
    arm = OursMemory(store_path="x")  # neither runner nor mem_bin
    with pytest.raises(ValueError, match="injected `runner` or a `mem_bin`"):
        arm.retrieve(_req(), _ctx())


def test_write_not_supported():
    arm = OursMemory(store_path="x", runner=lambda q: _DATA)
    assert arm.supports_write is False
    with pytest.raises(NotImplementedError, match="mem-lvp"):
        arm.write("m", "c", _ctx())
