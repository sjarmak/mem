"""Tests for the structural distance axis (KS statistic + per-feature report).

Pure two-sample arithmetic — no network, no model.
"""

import pytest

from membench.realism.distance import (
    ks_statistic,
    structural_realism,
)
from membench.realism.features import FEATURE_NAMES, TaskFeatures


def _feat(**kw):
    base = {
        "n_steps": 1,
        "n_tool_calls": 1,
        "tool_diversity": 1,
        "dependency_depth": 1,
        "n_memory_writes": 1,
        "n_memory_reads": 1,
        "task_text_length": 1,
    }
    base.update(kw)
    return TaskFeatures(**base)


def test_ks_identical_samples_is_zero():
    assert ks_statistic([1, 2, 3], [1, 2, 3]) == 0.0


def test_ks_disjoint_supports_is_one():
    assert ks_statistic([0, 0, 0], [9, 9, 9]) == 1.0


def test_ks_half_shifted():
    # CDF gap maxes at 0.5 when half of b sits beyond all of a.
    assert ks_statistic([0, 0], [0, 0, 1, 1]) == pytest.approx(0.5)


def test_ks_is_symmetric():
    a, b = [1, 2, 3, 4], [2, 2, 5, 9]
    assert ks_statistic(a, b) == pytest.approx(ks_statistic(b, a))


@pytest.mark.parametrize("a,b", [([], [1]), ([1], []), ([], [])])
def test_ks_rejects_empty_sample(a, b):
    with pytest.raises(ValueError, match="non-empty"):
        ks_statistic(a, b)


def test_structural_identical_corpora_pass():
    corpus = [_feat(), _feat(n_steps=3)]
    report = structural_realism(corpus, list(corpus))
    assert report.aggregate == 0.0
    assert report.passes
    assert set(report.per_feature) == set(FEATURE_NAMES)
    assert all(v == 0.0 for v in report.per_feature.values())


def test_structural_isolates_the_diverging_feature():
    # Synthetic always has 1 step; real always has 9. Only n_steps diverges (KS=1);
    # every other feature is identical (KS=0). Aggregate = 1/7, and worst_feature
    # names the culprit instead of burying it.
    syn = [_feat(n_steps=1) for _ in range(4)]
    real = [_feat(n_steps=9) for _ in range(4)]
    report = structural_realism(syn, real)
    assert report.per_feature["n_steps"] == 1.0
    assert report.worst_feature == "n_steps"
    assert report.aggregate == pytest.approx(1.0 / len(FEATURE_NAMES))
    others = [v for k, v in report.per_feature.items() if k != "n_steps"]
    assert all(v == 0.0 for v in others)


def test_structural_threshold_gate():
    syn = [_feat(n_steps=1) for _ in range(3)]
    real = [_feat(n_steps=9) for _ in range(3)]
    # aggregate = 1/7 ~= 0.143
    assert structural_realism(syn, real, max_distance=0.20).passes
    assert not structural_realism(syn, real, max_distance=0.10).passes


@pytest.mark.parametrize("syn,real", [([], [_feat()]), ([_feat()], [])])
def test_structural_rejects_empty_corpus(syn, real):
    with pytest.raises(ValueError, match="non-empty"):
        structural_realism(syn, real)
