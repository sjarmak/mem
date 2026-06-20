"""Tests for the construct-validity axis (Spearman rank-correlation, N-flagged).

Pure numeric arithmetic — no network, no model.
"""

import pytest

from membench.realism.construct import (
    construct_validity,
    construct_validity_from_arms,
    spearman_rho,
)


def test_spearman_perfect_positive():
    assert spearman_rho([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_perfect_negative():
    assert spearman_rho([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_handles_ties():
    # Average-rank handling: a tie block shares the mean rank.
    rho = spearman_rho([1, 1, 2, 3], [5, 5, 6, 7])
    assert rho == pytest.approx(1.0)


def test_spearman_rejects_length_mismatch():
    with pytest.raises(ValueError, match="length mismatch"):
        spearman_rho([1, 2], [1, 2, 3])


def test_spearman_rejects_too_few_points():
    with pytest.raises(ValueError, match="at least two"):
        spearman_rho([1], [1])


def test_spearman_rejects_flat_sample():
    with pytest.raises(ValueError, match="flat"):
        spearman_rho([2, 2, 2], [1, 2, 3])


def test_construct_positive_correlation_does_not_contradict():
    v = construct_validity([0.1, 0.5, 0.9, 1.0], [0.2, 0.4, 0.8, 0.95], min_n=2)
    assert v.rho == pytest.approx(1.0)
    assert not v.flat
    assert not v.n_flagged
    assert not v.contradicts


def test_construct_strong_negative_contradicts():
    v = construct_validity([0.1, 0.5, 0.9, 1.0], [1.0, 0.8, 0.4, 0.1], min_n=2)
    assert v.rho == pytest.approx(-1.0)
    assert v.contradicts


def test_construct_both_vectors_all_ties_is_flat():
    # Every arm performs identically on both corpora -> flat, undefined, not a veto.
    v = construct_validity([0.5, 0.5, 0.5], [0.5, 0.5, 0.5], min_n=2)
    assert v.rho is None
    assert v.flat
    assert not v.contradicts


def test_construct_flat_vector_is_undefined_not_contradiction():
    # bxhh.5 flat-anchor NO-GO shape: a flat real vector -> rho undefined, never a
    # contradiction.
    v = construct_validity([0.1, 0.5, 0.9], [0.5, 0.5, 0.5], min_n=2)
    assert v.rho is None
    assert v.flat
    assert not v.contradicts
    assert "flat" in v.reason


def test_construct_low_n_is_flagged_and_never_contradicts():
    # Strongly negative but below min_n -> reported, flagged, and NOT a veto.
    v = construct_validity([0.1, 0.9, 0.5], [0.9, 0.1, 0.5], min_n=10)
    assert v.n_flagged
    assert not v.contradicts
    assert "below min_n" in v.reason


def test_construct_rejects_arm_count_mismatch():
    with pytest.raises(ValueError, match="mismatch"):
        construct_validity([0.1, 0.2], [0.1, 0.2, 0.3])


def test_construct_from_arms_aligns_on_shared_arms():
    syn = {"none": 0.0, "oracle": 1.0, "lexical": 0.6, "extra": 0.3}
    real = {"none": 0.05, "oracle": 0.9, "lexical": 0.5}  # no "extra"
    v = construct_validity_from_arms(syn, real, min_n=2)
    # shared = {none, oracle, lexical}; same ranking -> rho = 1.
    assert v.n == 3
    assert v.rho == pytest.approx(1.0)


def test_construct_from_arms_rejects_too_few_shared():
    with pytest.raises(ValueError, match="shared arms"):
        construct_validity_from_arms({"a": 1.0}, {"b": 1.0})
