"""Tests for the per-arm raw 5-axis report (no composite — fork 2)."""

from membench.memory_systems.none_system import NoneMemory
from membench.memory_systems.ours_system import OursMemory
from membench.replay import run_replay
from membench.report.arm_vector import build_arm_vectors, to_dict, to_markdown
from membench.validity import QueryWork, WorkRef


def _run():
    corpus = [WorkRef(work_id="prior-1", rig="rigB", closed="2026-01-01T00:00:00Z")]
    query = QueryWork(work_id="B", rig="rigA", started="2026-01-10T00:00:00Z")
    ours = OursMemory(
        store_path="x",
        runner=lambda q: {
            "total_matched": 1,
            "near_duplicate_top": True,
            "items": [{"work_id": "prior-1", "citation": {"work_id": "prior-1"}, "lessons": []}],
        },
    )
    return run_replay(query, corpus, [NoneMemory(), ours])


def test_axes_are_raw_with_explicit_none():
    vectors = build_arm_vectors(_run())
    ours_vec = next(v for v in vectors if v.arm == "ours")
    task_perf, token_budget, latency, privacy, interruption = ours_vec.axes
    # Agent-dependent / stub axes are explicit None, never zero-filled.
    assert task_perf is None
    assert privacy is None
    assert interruption is None
    # Measured axes carry real values.
    assert token_budget > 0
    assert latency >= 0.0


def test_no_composite_field_exists():
    # fork 2: raw vector only — assert no weighted/aggregate score sneaks in.
    vec = build_arm_vectors(_run())[0]
    field_names = set(vars(vec))
    for forbidden in ("composite", "weighted", "score", "total_axis"):
        assert forbidden not in field_names


def test_to_dict_and_markdown_render():
    run = _run()
    d = to_dict(run)
    assert d["work_id"] == "B"
    assert d["eligible_count"] == 1
    assert len(d["arms"]) == 3  # none (1 track) + ours (2 tracks)

    md = to_markdown(run)
    assert "Replay 5-axis (raw)" in md
    assert "no composite (fork 2)" in md
    assert "ours" in md and "none" in md
