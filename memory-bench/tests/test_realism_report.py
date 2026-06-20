"""Tests for the combined realism report (three axes -> defensible decision).

Drives the gated ``assess_realism`` entry point with synthetic sequences and a
stub judge — no real corpus, no model, no held numbers.
"""

import json

import pytest

from membench.bbon.comparative_judge import StubComparativeJudge
from membench.realism.features import TaskFeatures, features_from_sequence
from membench.realism.report import assess_realism
from membench.schemas.sequence import BenchmarkSequence, SequenceStep


def _seq(seq_id, n_steps, *, tools=("read",)):
    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"task {seq_id}",
        goal="done",
        steps=[
            SequenceStep(
                step_id=f"{seq_id}-{i}",
                user_request=f"do step {i}",
                available_tools=list(tools),
            )
            for i in range(n_steps)
        ],
    )


def _judge(realism, reads_real):
    return StubComparativeJudge(
        fn=lambda prompt: json.dumps(
            {"realism": realism, "reads_real": reads_real, "rationale": "stub"}
        )
    )


def _real_features_like(synthetic):
    """Real reference = the synthetic corpus's own features, so structural KS=0
    (the structural axis is isolated from the others in these tests)."""
    return [features_from_sequence(s) for s in synthetic]


def test_defensible_when_structural_and_semantic_hold_no_construct():
    syn = [_seq("a", 2), _seq("b", 2), _seq("c", 2)]
    report = assess_realism(syn, _real_features_like(syn), _judge(0.9, True))
    assert report.structural.passes
    assert report.semantic.passes
    assert report.construct is None
    assert report.defensible
    assert len(report.per_task) == 3
    assert report.per_task[0].task_id == "a"


def test_not_defensible_when_semantic_fails():
    syn = [_seq("a", 2), _seq("b", 2)]
    report = assess_realism(syn, _real_features_like(syn), _judge(0.1, False))
    assert report.structural.passes
    assert not report.semantic.passes
    assert not report.defensible
    assert "semantic FAIL" in report.verdict_reason


def test_not_defensible_when_structural_fails():
    syn = [_seq("a", 1) for _ in range(3)]
    # Real corpus is structurally far: many more steps on every task.
    real = [features_from_sequence(_seq("r", 9)) for _ in range(3)]
    report = assess_realism(syn, real, _judge(0.9, True))
    assert not report.structural.passes
    assert not report.defensible
    assert report.structural.worst_feature == "n_steps"


def test_construct_contradiction_vetoes_defensible_at_adequate_n():
    syn = [_seq("a", 2), _seq("b", 2)]
    report = assess_realism(
        syn,
        _real_features_like(syn),
        _judge(0.9, True),
        synthetic_arm_perf={"none": 0.0, "oracle": 1.0, "lexical": 0.6},
        real_arm_perf={"none": 1.0, "oracle": 0.0, "lexical": 0.2},  # inverted ranking
        construct_min_n=2,  # treat the 3 shared arms as adequate for this test
    )
    assert report.construct is not None
    assert report.construct.contradicts
    assert not report.defensible
    assert "construct CONTRADICTS" in report.verdict_reason


def test_three_arms_at_default_min_n_is_flagged_never_vetoes():
    # Honesty property: the canonical none/oracle/lexical setup is only 3 arms, so
    # at the default min_n construct is N-flagged — even an inverted ranking cannot
    # veto a structurally+semantically sound corpus.
    syn = [_seq("a", 2), _seq("b", 2)]
    report = assess_realism(
        syn,
        _real_features_like(syn),
        _judge(0.9, True),
        synthetic_arm_perf={"none": 0.0, "oracle": 1.0, "lexical": 0.6},
        real_arm_perf={"none": 1.0, "oracle": 0.0, "lexical": 0.2},  # inverted
    )
    assert report.construct is not None
    assert report.construct.n_flagged
    assert not report.construct.contradicts
    assert report.defensible


def test_construct_corroborates_without_blocking():
    syn = [_seq("a", 2), _seq("b", 2)]
    report = assess_realism(
        syn,
        _real_features_like(syn),
        _judge(0.9, True),
        synthetic_arm_perf={"none": 0.0, "oracle": 1.0, "lexical": 0.6},
        real_arm_perf={"none": 0.05, "oracle": 0.9, "lexical": 0.5},  # same ranking
    )
    assert report.construct is not None
    assert not report.construct.contradicts
    assert report.defensible


def test_per_task_carries_features_and_semantic():
    syn = [_seq("a", 3, tools=("read", "bash"))]
    report = assess_realism(syn, _real_features_like(syn), _judge(0.7, True))
    pt = report.per_task[0]
    assert isinstance(pt.features, TaskFeatures)
    assert pt.features.n_steps == 3
    assert pt.semantic.realism == 0.7


def test_assess_rejects_empty_synthetic():
    with pytest.raises(ValueError, match="at least one"):
        assess_realism([], [TaskFeatures(1, 1, 1, 1, 1, 1, 1)], _judge(0.9, True))
