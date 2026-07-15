"""Tests for the `ours` arm — retrieval-v1 wired in via an injectable runner.

The runner is injected so these are hermetic: no built CLI, no real store. The
end-to-end path against a real `mem retrieve` is covered by the integration test.

The harness-side bulk resolver over the same runner (`resolve_payloads`) is not the
arm — it lives in `harbor.bundle_grid` and is tested in `test_bundle_grid`.
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
    assert result.fts_truncated is False

    # The normalized event mirrors §6.2 for a graph search.
    assert result.event.backend is MemoryBackend.KG
    assert result.event.normalized_operation is MemoryOperation.SEARCH
    assert result.event.retrieved_ids == ["prior-1", "prior-2"]

    # The CLI scope spelling is handed to the runner.
    assert captured["query"].scope == "same_rig_temporal"
    assert captured["query"].work_id == "B"
    assert captured["query"].store_path == "/tmp/store.db"


def test_retrieve_surfaces_fts_truncation():
    def runner(query):
        return {**_DATA, "fts_truncated": True}

    arm = OursMemory(store_path="/tmp/store.db", runner=runner)
    result = arm.retrieve(_req(), _ctx())
    # The substrate's FTS cap fired — never silently dropped (Decision 10).
    assert result.fts_truncated is True


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


# --- mem-tnyo: trigger labeling + the issue-text control arm ------------------------


def test_ours_arm_is_labeled_oracle_triggered():
    """Replay resolution goes through queryFromRecord over the held record's OWN
    stored trace errors -- an oracle trigger, labeled explicitly (mem-tnyo)."""
    assert OursMemory.trigger == "oracle"


def test_default_runner_appends_no_trace_query_flag(monkeypatch):
    import membench.memory_systems.ours_system as mod
    from membench.memory_systems.ours_system import OursQuery, _default_runner

    captured: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        captured.append(argv)
        return {"items": []}

    monkeypatch.setattr(mod, "run_mem_json", fake_run)
    run = _default_runner("/bin/mem")
    run(OursQuery(work_id="B", scope="cross_rig", store_path="/s.db"))
    run(OursQuery(work_id="B", scope="cross_rig", store_path="/s.db", no_trace_query=True))

    assert "--no-trace-query" not in captured[0]
    assert captured[1][-1] == "--no-trace-query"


def test_issue_trigger_arm_queries_without_trace_errors():
    from membench.memory_systems.ours_system import OursIssueTriggerMemory, OursQuery

    seen: list[OursQuery] = []

    def runner(query: OursQuery):
        seen.append(query)
        return dict(_DATA)

    arm = OursIssueTriggerMemory(store_path="/s.db", runner=runner)
    assert arm.name == "ours-issue-trigger"
    assert arm.trigger == "issue-text"

    result = arm.retrieve(_req(), _ctx())

    assert seen[0].no_trace_query is True
    assert set(result.payloads) == {"prior-1", "prior-2"}


def test_oracle_triggered_arm_keeps_trace_query():
    from membench.memory_systems.ours_system import OursQuery

    seen: list[OursQuery] = []

    def runner(query: OursQuery):
        seen.append(query)
        return dict(_DATA)

    OursMemory(store_path="/s.db", runner=runner).retrieve(_req(), _ctx())
    assert seen[0].no_trace_query is False


def test_issue_trigger_arm_is_wired_in_the_registry():
    from membench.memory_systems import build_memory_system, wired_memory_systems

    assert "ours-issue-trigger" in wired_memory_systems()
    arm = build_memory_system("ours-issue-trigger", store_path="/s.db", runner=lambda q: _DATA)
    assert arm.name == "ours-issue-trigger"
