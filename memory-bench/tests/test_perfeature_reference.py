"""Tests for the R-C interim per-feature realism reference (mem-ovi)."""

from __future__ import annotations

import pytest

from membench.realism.features import TaskFeatures
from membench.realism.perfeature_reference import (
    MATCHABLE_FEATURES,
    MEMORY_OP_FEATURES,
    PerFeatureReference,
    per_feature_reference,
)


def _features(
    *,
    n_steps: int = 1,
    n_tool_calls: int = 1,
    tool_diversity: int = 1,
    dependency_depth: int = 0,
    n_memory_writes: int = 0,
    n_memory_reads: int = 0,
    task_text_length: int = 10,
) -> TaskFeatures:
    return TaskFeatures(
        n_steps=n_steps,
        n_tool_calls=n_tool_calls,
        tool_diversity=tool_diversity,
        dependency_depth=dependency_depth,
        n_memory_writes=n_memory_writes,
        n_memory_reads=n_memory_reads,
        task_text_length=task_text_length,
    )


def test_groups_have_exactly_their_feature_keys() -> None:
    corpus = [_features(), _features(n_steps=2)]
    ref = per_feature_reference(corpus, corpus)
    assert set(ref.matchable) == set(MATCHABLE_FEATURES)
    assert set(ref.memory_op) == set(MEMORY_OP_FEATURES)


def test_no_dependency_depth_anywhere() -> None:
    corpus = [_features(), _features(dependency_depth=5)]
    ref = per_feature_reference(corpus, corpus)
    assert "dependency_depth" not in ref.matchable
    assert "dependency_depth" not in ref.memory_op


def test_no_aggregate_or_passes_attribute() -> None:
    ref = per_feature_reference([_features()], [_features()])
    assert not hasattr(ref, "aggregate")
    assert not hasattr(ref, "passes")
    assert not hasattr(ref, "max_distance")
    # The frozen dataclass exposes only the per-feature dicts + corpus sizes.
    assert set(PerFeatureReference.__dataclass_fields__) == {
        "matchable",
        "memory_op",
        "n_synthetic",
        "n_real",
    }


def test_identical_corpora_all_distances_zero() -> None:
    corpus = [
        _features(n_steps=1, n_tool_calls=2, tool_diversity=1, task_text_length=5),
        _features(n_steps=3, n_tool_calls=4, tool_diversity=2, task_text_length=9),
    ]
    ref = per_feature_reference(corpus, corpus)
    assert all(v == 0.0 for v in ref.matchable.values())
    assert all(v == 0.0 for v in ref.memory_op.values())


def test_single_diverging_feature_is_ks_one_others_zero() -> None:
    # All synthetic n_steps=1, all real n_steps=10 -> disjoint supports on n_steps,
    # identical on every other matchable axis.
    synthetic = [_features(n_steps=1) for _ in range(4)]
    real = [_features(n_steps=10) for _ in range(4)]
    ref = per_feature_reference(synthetic, real)
    assert ref.matchable["n_steps"] == 1.0
    assert ref.matchable["n_tool_calls"] == 0.0
    assert ref.matchable["tool_diversity"] == 0.0
    assert ref.matchable["task_text_length"] == 0.0


def test_memory_op_disjointness_does_not_contaminate_matchable() -> None:
    # Synthetic emits memory writes; real (coding sessions) emit none.
    synthetic = [_features(n_memory_writes=3) for _ in range(4)]
    real = [_features(n_memory_writes=0) for _ in range(4)]
    ref = per_feature_reference(synthetic, real)
    assert ref.memory_op["n_memory_writes"] == 1.0
    # Disjoint memory ops must NOT appear in or perturb the matchable group.
    assert "n_memory_writes" not in ref.matchable
    assert all(v == 0.0 for v in ref.matchable.values())


def test_worst_matchable_is_diagnosis_only() -> None:
    synthetic = [_features(n_steps=1) for _ in range(4)]
    real = [_features(n_steps=10) for _ in range(4)]
    ref = per_feature_reference(synthetic, real)
    name, distance = ref.worst_matchable
    assert name == "n_steps"
    assert distance == 1.0


def test_empty_corpus_raises() -> None:
    with pytest.raises(ValueError):
        per_feature_reference([], [_features()])
    with pytest.raises(ValueError):
        per_feature_reference([_features()], [])


def test_corpus_sizes_reported() -> None:
    ref = per_feature_reference([_features(), _features()], [_features()])
    assert ref.n_synthetic == 2
    assert ref.n_real == 1
