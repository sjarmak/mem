"""Tests for `membench.bbon.aggregate`: comparison assembly + the win-by-arm
readout. Pure tally over already-decided verdicts."""

import pytest

from membench.bbon.aggregate import Comparison, build_comparison, summarize_comparisons
from membench.bbon.comparative_judge import StubComparativeJudge, compare_attempts
from membench.bbon.models import Attempt, deterministic_id
from membench.bbon.narrative_diff import generate_narrative_diff


def _attempt(arm: str, status: str = "completed", **result: object) -> Attempt:
    return Attempt(
        id=deterministic_id({"arm": arm}),
        work_id=f"w-{arm}",
        arm=arm,
        status=status,  # type: ignore[arg-type]
        result=result,
    )


def _comparison(winner: str, confidence: float) -> Comparison:
    left, right = _attempt("cold", total_tokens=900), _attempt("warm", total_tokens=300)
    diff = generate_narrative_diff(left, right, [], [])
    judgment = compare_attempts(
        left, right, diff, StubComparativeJudge(winner=winner, confidence=confidence)
    )
    return build_comparison(left, right, diff, judgment)


def test_build_comparison_reads_winner_arm() -> None:
    comparison = _comparison("B", 0.8)
    assert comparison.winner_arm == "warm"
    assert comparison.left_arm == "cold"
    assert comparison.confidence == 0.8


def test_build_comparison_rejects_foreign_winner() -> None:
    left, right = _attempt("cold"), _attempt("warm")
    diff = generate_narrative_diff(left, right, [], [])
    # winner=B -> the judgment points at the warm attempt's id; re-pairing against an
    # attempt that is neither left nor that warm attempt must raise.
    judgment = compare_attempts(left, right, diff, StubComparativeJudge(winner="B", confidence=0.5))
    other = _attempt("other")
    with pytest.raises(ValueError, match="matches neither"):
        build_comparison(left, other, diff, judgment)


def test_summarize_counts_wins_and_mean_confidence() -> None:
    comparisons = [_comparison("B", 0.8), _comparison("B", 0.6), _comparison("A", 1.0)]
    summary = summarize_comparisons(comparisons)
    assert summary["n_pairs"] == 3
    assert summary["wins_by_arm"] == {"warm": 2, "cold": 1}
    assert summary["mean_confidence"] == pytest.approx((0.8 + 0.6 + 1.0) / 3)


def test_summarize_empty_raises() -> None:
    with pytest.raises(ValueError, match="no comparisons"):
        summarize_comparisons([])
