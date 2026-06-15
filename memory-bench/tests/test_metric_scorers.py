"""Table-driven unit tests for the deterministic §12 scorers.

No network, no model — every case is pure set/ranking arithmetic.
"""

import math

import pytest

from membench.generators.interruption import (
    detect_interruption_points,
    generate_handoff_tasks,
    trajectory_for_seed,
)
from membench.metrics.scorers import (
    InterruptionInputs,
    PrivacyInputs,
    RetentionInputs,
    RetrievalInputs,
    SynthesisInputs,
    score_efficiency,
    score_interruption,
    score_privacy,
    score_retention,
    score_retrieval,
    score_synthesis,
)
from membench.schemas.handoff import (
    FIRST_POST_FAILURE_EDIT,
    FIRST_SOURCE_EDIT,
    FIRST_VALIDATION_RESULT,
    SOURCE_EDIT,
    VALIDATION,
    VALIDATION_FAIL,
    VALIDATION_PASS,
    PredecessorEvent,
)
from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation


def _event(latency: float = 0.0) -> MemoryEvent:
    return MemoryEvent(
        event_id="e",
        trial_id="t",
        session_id="s",
        step_id="st",
        timestamp="0",
        concrete_tool="tool",
        normalized_operation=MemoryOperation.READ,
        backend=MemoryBackend.FILESYSTEM,
        latency_ms=latency,
    )


# --------------------------------------------------------------------------- #
# Retrieval: precision / recall / mrr / nDCG / rank / distractor / stale
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "inp,expected",
    [
        # Perfect: both required ids retrieved at the top, no noise.
        (
            RetrievalInputs(retrieved_ids=["a", "b"], required_ids=["a", "b"]),
            {
                "precision_at_k": 1.0,
                "recall_at_k": 1.0,
                "mrr": 1.0,
                "nDCG": 1.0,
                "retrieval_rank": 1,
                "missed_required_memory_count": 0,
                "relevant_memory_retrieved": True,
            },
        ),
        # Relevant id ranked second behind a non-relevant id -> mrr = 1/2.
        (
            RetrievalInputs(retrieved_ids=["x", "a"], required_ids=["a"]),
            {
                "precision_at_k": 0.5,
                "recall_at_k": 1.0,
                "mrr": 0.5,
                "retrieval_rank": 2,
                "missed_required_memory_count": 0,
            },
        ),
        # Half recall: one of two required ids retrieved.
        (
            RetrievalInputs(retrieved_ids=["a"], required_ids=["a", "b"]),
            {
                "precision_at_k": 1.0,
                "recall_at_k": 0.5,
                "mrr": 1.0,
                "retrieval_rank": 1,
                "missed_required_memory_count": 1,
                "relevant_memory_retrieved": False,
            },
        ),
        # Nothing relevant retrieved.
        (
            RetrievalInputs(retrieved_ids=["x", "y"], required_ids=["a"]),
            {
                "precision_at_k": 0.0,
                "recall_at_k": 0.0,
                "mrr": 0.0,
                "nDCG": 0.0,
                "retrieval_rank": None,
                "relevant_memory_available": False,
            },
        ),
        # Empty retrieval against a required id.
        (
            RetrievalInputs(retrieved_ids=[], required_ids=["a"]),
            {
                "precision_at_k": 0.0,
                "recall_at_k": 0.0,
                "mrr": 0.0,
                "retrieval_rank": None,
                "missed_required_memory_count": 1,
            },
        ),
    ],
)
def test_retrieval_ranking(inp, expected):
    m = score_retrieval(inp)
    for field, value in expected.items():
        assert getattr(m, field) == value, field


def test_retrieval_ndcg_penalizes_rank():
    # required ['a','b']; retrieved ['x','a','b'] -> a@2, b@3.
    m = score_retrieval(RetrievalInputs(retrieved_ids=["x", "a", "b"], required_ids=["a", "b"]))
    dcg = 1 / math.log2(3) + 1 / math.log2(4)  # positions 2 and 3
    idcg = 1 / math.log2(2) + 1 / math.log2(3)  # ideal: positions 1 and 2
    assert m.nDCG == pytest.approx(dcg / idcg)
    assert 0.0 < m.nDCG < 1.0
    assert m.retrieval_rank == 2


def test_retrieval_distractor_and_stale_rates():
    m = score_retrieval(
        RetrievalInputs(
            retrieved_ids=["a", "d1", "s1", "s2"],
            required_ids=["a"],
            distractor_ids=["d1", "d2"],
            stale_ids=["s1", "s2"],
        )
    )
    assert m.distractor_retrieval_rate == pytest.approx(1 / 4)
    assert m.stale_memory_retrieval_rate == pytest.approx(2 / 4)
    assert m.precision_at_k == pytest.approx(1 / 4)


# --------------------------------------------------------------------------- #
# Retention: hit / miss / over-retention / scope / supersession
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "inp,expected",
    [
        # Clean write of exactly the expected ids.
        (
            RetentionInputs(written_ids=["a", "b"], expected_writes=["a", "b"]),
            {
                "write_hit_rate": 1.0,
                "write_miss_rate": 0.0,
                "over_retention_rate": 0.0,
                "noise_write_rate": 0.0,
                "expected_memory_written": True,
                "correct_scope_rate": 1.0,
                "correct_backend_rate": 1.0,
            },
        ),
        # Missed one expected write.
        (
            RetentionInputs(written_ids=["a"], expected_writes=["a", "b"]),
            {
                "write_hit_rate": 0.5,
                "write_miss_rate": 0.5,
                "over_retention_rate": 0.0,
                "expected_memory_written": False,
            },
        ),
        # Wrote a noise id alongside the expected one -> over-retention.
        (
            RetentionInputs(written_ids=["a", "noise"], expected_writes=["a"]),
            {
                "write_hit_rate": 1.0,
                "over_retention_rate": 0.5,
                "noise_write_rate": 0.5,
                "expected_memory_written": True,
            },
        ),
        # No expected writes and none written -> all clean.
        (
            RetentionInputs(written_ids=[], expected_writes=[]),
            {"write_hit_rate": 0.0, "write_miss_rate": 0.0, "over_retention_rate": 0.0},
        ),
    ],
)
def test_retention(inp, expected):
    m = score_retention(inp)
    for field, value in expected.items():
        assert getattr(m, field) == pytest.approx(value), field


def test_retention_supersession():
    # superseded id removed -> correct.
    ok = score_retention(
        RetentionInputs(
            written_ids=["a"],
            expected_writes=["a"],
            removed_ids=["old"],
            superseded_expected_ids=["old"],
        )
    )
    assert ok.stale_memory_removed is True
    assert ok.supersession_correct is True

    # superseded id NOT removed -> incorrect.
    bad = score_retention(
        RetentionInputs(
            written_ids=["a"],
            expected_writes=["a"],
            removed_ids=[],
            superseded_expected_ids=["old"],
        )
    )
    assert bad.stale_memory_removed is False
    assert bad.supersession_correct is False

    # nothing to supersede -> trivially correct, not "removed".
    none = score_retention(RetentionInputs(written_ids=["a"], expected_writes=["a"]))
    assert none.supersession_correct is True
    assert none.stale_memory_removed is False


def test_retention_correct_scope_subset():
    # 2 expected written; only one in correct scope -> 0.5.
    m = score_retention(
        RetentionInputs(
            written_ids=["a", "b"],
            expected_writes=["a", "b"],
            correct_scope_ids=["a"],
        )
    )
    assert m.correct_scope_rate == pytest.approx(0.5)
    assert m.correct_backend_rate == pytest.approx(1.0)  # defaults to all expected-written


# --------------------------------------------------------------------------- #
# Synthesis: supporting counts + cross-session dependency
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "inp,required,used,success",
    [
        (SynthesisInputs(["a", "b"], ["a", "b"]), 2, 2, True),
        (SynthesisInputs(["a", "b"], ["a"]), 2, 1, False),
        (SynthesisInputs(["a"], ["a", "extra"]), 1, 1, True),
        (SynthesisInputs([], ["a"]), 0, 0, False),  # no dependency -> not "success"
    ],
)
def test_synthesis(inp, required, used, success):
    m = score_synthesis(inp)
    assert m.supporting_memories_required == required
    assert m.supporting_memories_used == used
    assert m.cross_session_dependency_success is success
    # Judge seams stay None.
    assert m.multi_backend_synthesis_success is None
    assert m.contradiction_resolution_success is None


# --------------------------------------------------------------------------- #
# Efficiency arithmetic
# --------------------------------------------------------------------------- #
def test_efficiency_sums_tokens_and_calls():
    events = [_event(latency=5.0), _event(latency=2.0)]
    m = score_efficiency(
        input_tokens=100,
        output_tokens=30,
        non_memory_tool_calls=3,
        memory_events=events,
        non_memory_tool_latency_ms=4.0,
        turns=2,
    )
    assert m.total_tokens == 130
    assert m.memory_tool_calls == 2
    assert m.non_memory_tool_calls == 3
    assert m.tool_calls_total == 5
    assert m.tool_latency_ms == pytest.approx(11.0)  # 5 + 2 + 4
    assert m.turns == 2
    # Model-path-only fields stay zero in the deterministic path.
    assert m.cost_usd == 0.0
    assert m.model_latency_ms == 0.0


# --------------------------------------------------------------------------- #
# Privacy (DIV-4) — deterministic leakage check + model-classified passthrough
# --------------------------------------------------------------------------- #
def test_privacy_cross_rig_same_rig_injection_is_flagged_with_ids():
    # A cross_rig run injecting content from the task's OWN rig defeats the cross-rig
    # isolation → each offending id is flagged; an other-rig id is clean.
    m = score_privacy(
        PrivacyInputs(
            run_scope="cross_rig",
            task_rig="gascity",
            injected_rigs={"m1": "gascity", "m2": "codeprobe", "m3": "gascity"},
        )
    )
    assert m.leakage_flags == [
        "cross_rig_same_rig_injection:m1",
        "cross_rig_same_rig_injection:m3",
    ]


def test_privacy_same_rig_temporal_run_never_flags_same_rig_injection():
    # same_rig_temporal is SUPPOSED to inject same-rig content — never a leak.
    m = score_privacy(
        PrivacyInputs(
            run_scope="same_rig_temporal",
            task_rig="gascity",
            injected_rigs={"m1": "gascity"},
        )
    )
    assert m.leakage_flags == []


def test_privacy_unmeasured_provenance_reports_no_flags_not_a_fabricated_clean_bill():
    # No scope / no provenance threaded → honest empty, like distractor/stale defaults.
    assert score_privacy(PrivacyInputs()).leakage_flags == []
    assert score_privacy(PrivacyInputs(run_scope="cross_rig")).leakage_flags == []


@pytest.mark.parametrize("cls", ["none", "internal", "sensitive", None])
def test_privacy_class_passes_through_valid_buckets(cls):
    assert score_privacy(PrivacyInputs(privacy_class=cls)).privacy_class == cls


def test_privacy_class_out_of_bucket_raises():
    # An off-vocabulary class is a producer bug, surfaced loudly (DIV-4 is frozen).
    with pytest.raises(ValueError, match="DIV-4"):
        score_privacy(PrivacyInputs(privacy_class="secret"))


# --------------------------------------------------------------------------- #
# Interruption (DIV-4) — inject_timing wired against the mem-dsu generator
# --------------------------------------------------------------------------- #
def _inputs_at(events, point):
    """Wire the scorer against the generator: detect the point's trigger index, then
    feed the checkpoint-inclusive prefix the successor inherits."""
    idx = detect_interruption_points(events)[point]
    return InterruptionInputs(point=point, checkpoint_prefix=events[: idx + 1])


def test_interruption_timing_classifies_from_validation_outcomes():
    # edit -> failing validation -> post-failure edit. Source edit is pre-failure
    # (off), the failing validation and the post-failure edit are on-failure.
    events = (
        PredecessorEvent(kind=SOURCE_EDIT, target="a.py", detail="edit"),
        PredecessorEvent(
            kind=VALIDATION, target="pytest", outcome=VALIDATION_FAIL, detail="1 failed"
        ),
        PredecessorEvent(kind=SOURCE_EDIT, target="a.py", detail="fix"),
    )
    assert score_interruption(_inputs_at(events, FIRST_SOURCE_EDIT)).inject_timing == "off_failure"
    assert (
        score_interruption(_inputs_at(events, FIRST_VALIDATION_RESULT)).inject_timing
        == "on_failure"
    )
    assert (
        score_interruption(_inputs_at(events, FIRST_POST_FAILURE_EDIT)).inject_timing
        == "on_failure"
    )


def test_interruption_first_validation_passing_is_off_failure_not_hardcoded():
    # A trajectory whose first post-edit validation PASSES must classify off_failure —
    # timing reads the actual outcome, never the point name.
    events = (
        PredecessorEvent(kind=SOURCE_EDIT, target="a.py"),
        PredecessorEvent(kind=VALIDATION, target="pytest", outcome=VALIDATION_PASS),
    )
    m = score_interruption(_inputs_at(events, FIRST_VALIDATION_RESULT))
    assert m.inject_timing == "off_failure"


def test_interruption_derailment_signal_is_left_a_judge_seam():
    events = (PredecessorEvent(kind=SOURCE_EDIT, target="a.py"),)
    m = score_interruption(_inputs_at(events, FIRST_SOURCE_EDIT))
    assert m.derailment_signal is None  # magnitude is the model's call, not the scorer's


def test_interruption_unknown_point_raises():
    with pytest.raises(ValueError, match="not one of"):
        score_interruption(InterruptionInputs(point="halfway", checkpoint_prefix=()))


def test_interruption_end_to_end_against_generator_views_share_timing():
    # Wire the real generator: the 4 view arms of one interruption point are a matched
    # set, so they MUST share one inject_timing (timing is a property of the point).
    traj = trajectory_for_seed(seed=0)
    tasks = generate_handoff_tasks(seed=0)
    by_point: dict[str, set[str]] = {}
    for task in tasks:
        timing = score_interruption(_inputs_at(traj.events, task.point)).inject_timing
        by_point.setdefault(task.point, set()).add(timing)
    # Every point resolved to exactly one timing across its 4 views.
    assert by_point  # the generator emitted at least one point
    assert all(len(timings) == 1 for timings in by_point.values())
