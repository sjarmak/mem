"""Tests for the replay runner — failure-triggered arms under the LOO guard."""

import pytest

from membench.memory_systems.none_system import NoneMemory
from membench.memory_systems.ours_system import OursMemory
from membench.replay import TRACKS, replay_arm, run_replay
from membench.validity import LeakageError, QueryWork, WorkRef


def _corpus():
    return [
        WorkRef(work_id="prior-1", rig="rigB", closed="2026-01-01T00:00:00Z"),
        WorkRef(work_id="prior-2", rig="rigC", closed="2026-01-02T00:00:00Z"),
        WorkRef(work_id="future", rig="rigB", closed="2026-02-01T00:00:00Z"),
    ]


def _query():
    return QueryWork(work_id="B", rig="rigA", started="2026-01-10T00:00:00Z")


def _ours_returning(ids):
    items = [{"work_id": i, "citation": {"work_id": i}, "lessons": []} for i in ids]
    data = {"total_matched": len(ids), "near_duplicate_top": False, "items": items}
    return OursMemory(store_path="x", runner=lambda q: data)


def test_run_replay_runs_none_once_and_ours_both_tracks():
    arms = [NoneMemory(), _ours_returning(["prior-1"])]
    run = run_replay(_query(), _corpus(), arms)

    none_results = [r for r in run.results if r.arm == "none"]
    ours_results = [r for r in run.results if r.arm == "ours"]
    # none is scope-independent → one trial; ours runs under both D7 tracks.
    assert len(none_results) == 1
    assert none_results[0].scope is None
    assert {r.scope for r in ours_results} == set(TRACKS)


def test_eligible_count_excludes_future_record():
    run = run_replay(_query(), _corpus(), [NoneMemory()])
    # prior-1 + prior-2 are before the boundary; future is not.
    assert run.eligible_count == 2


def test_none_arm_retrieves_nothing():
    result = replay_arm(NoneMemory(), _query(), _corpus(), scope=None)
    assert result.retrieved_ids == []
    assert result.injected_context_chars == 0


def test_ours_within_bound_passes_guard():
    result = replay_arm(_ours_returning(["prior-1"]), _query(), _corpus(), scope="cross_rig")
    assert result.retrieved_ids == ["prior-1"]
    assert result.injected_context_chars > 0
    assert result.eligible_count == 2


def test_leaked_retrieval_raises():
    # An arm that returns a record closed after the boundary is a validity bug;
    # the harness must raise, never silently drop it.
    leaky = _ours_returning(["future"])
    with pytest.raises(LeakageError):
        replay_arm(leaky, _query(), _corpus(), scope="cross_rig")


def test_replay_emits_otel_spans():
    from membench.telemetry.otel_spans import replay_to_spans

    run = run_replay(_query(), _corpus(), [NoneMemory(), _ours_returning(["prior-1"])])
    spans = replay_to_spans(run)

    roots = [s for s in spans if s["name"] == "memory_eval.replay"]
    # one root per (arm, track): none ×1 + ours ×2 tracks.
    assert len(roots) == 3
    ours_cross = next(
        s
        for s in roots
        if s["attributes"]["membench.arm"] == "ours"
        and s["attributes"]["membench.scope"] == "cross_rig"
    )
    assert ours_cross["attributes"]["membench.retrieval.total_matched"] == 1
    assert ours_cross["attributes"]["membench.storage_tier"] == "kg"
    # each root has a child memory-event span.
    child_parents = {s["parent_span_id"] for s in spans if s["name"].startswith("membench.memory.")}
    assert ours_cross["span_id"] in child_parents
