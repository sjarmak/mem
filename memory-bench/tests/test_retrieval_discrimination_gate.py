"""End-to-end (mem-31vl): the tool-requiring shape is BOTH memory-dependent AND
retrieval-quality-discriminating, proven offline with the ScriptedAgent — no model,
no Docker. The discrimination is the deterministic proxy for ours-vs-builtin.
"""

from __future__ import annotations

import pytest

from membench.generators.enterprise_workflow import materialize_world
from membench.generators.memory_necessity_gate import memory_necessity_gate
from membench.generators.retrieval_discrimination_gate import (
    NAIVE_ARM,
    QUALITY_ARM,
    retrieval_discrimination_gate,
)
from membench.report.comparison import EPSILON
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team


def _world(seed: int = 5) -> EnterpriseWorld:
    return EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(
                persona_id="p2", name="Grace Hopper", role="site-reliability-engineer", team_id="t1"
            ),
        ],
        channels=[Channel(channel_id="c1", name="kernels", kind="chat")],
        seed=seed,
    )


def _project(seed: int = 5) -> Project:
    return Project(
        project_id=f"world-seed{seed}-project",
        world_id=f"world-seed{seed}",
        name="Acme initiative",
        goal="Reconcile the launch config.",
    )


def _tool_requiring_seq(seed: int = 5) -> BenchmarkSequence:
    return materialize_world(
        _world(seed), _project(seed), n_tasks=1, facts_per_task=3, tool_requiring=True
    )[0]


def test_tool_requiring_shape_is_memory_dependent() -> None:
    # Necessity (memory > none): oracle surfaces current-only -> valid tool arg ->
    # passes; no-memory -> empty arg -> current value absent -> fails.
    verdict = memory_necessity_gate(_tool_requiring_seq()).verdict
    assert verdict.accepted, verdict.reason
    assert verdict.delta > EPSILON


def test_tool_requiring_shape_discriminates_retrieval_quality() -> None:
    # Discrimination (quality > naive) — the proxy for ours-vs-builtin: filesystem
    # (id-exact, surfaces current-only) applies the current value and PASSES the
    # goal; lexical (token top-k) surfaces the superseded version too, so the stale
    # value rides into the tool argument and it FAILS.
    result = retrieval_discrimination_gate(_tool_requiring_seq())
    assert result.quality_arm == QUALITY_ARM
    assert result.naive_arm == NAIVE_ARM
    assert result.accepted, result.reason
    assert result.quality_reward == 1.0  # id-exact arm applies the current value
    assert result.naive_reward == 0.0  # naive arm's stale arg fails the action
    assert result.delta > EPSILON


def test_discrimination_holds_across_seeds() -> None:
    # The separation is not a single-seed artifact (chain position + values vary).
    for seed in (5, 11, 23, 42):
        result = retrieval_discrimination_gate(_tool_requiring_seq(seed))
        assert result.accepted, f"seed {seed}: {result.reason}"
        assert result.quality_reward > result.naive_reward


def test_invalid_top_k_propagates_from_lexical_arm() -> None:
    # top_k<1 is not re-validated at this boundary (mem-keqrm): the naive ("lexical")
    # arm is the one system that actually consumes top_k, and its constructor already
    # raises ValueError for an invalid value. This locks in that the error reaches the
    # gate's caller unwrapped and unswallowed, so a future refactor of the arm-building
    # path can't silently absorb it.
    with pytest.raises(ValueError, match="top_k must be >= 1"):
        retrieval_discrimination_gate(_tool_requiring_seq(), top_k=0)
