"""§11 enterprise-workflow materialiser — memory-dependent worlds with Confusion+Staleness.

Mirrors ``test_synthetic_task`` but over a world: each materialised sequence must be
memory-dependent (clears ``memory_necessity_gate``), carry populated
``distractor_memories`` (Confusion) and ``superseded_memory_ids`` (Staleness), and be
byte-reproducible from its seed. The oracle pool must stay conflict-free (supersession
uses distinct v1/v2 ids).
"""

from __future__ import annotations

import pytest

from membench.generators.enterprise_workflow import materialize_world
from membench.generators.memory_necessity_gate import memory_necessity_gate
from membench.report.comparison import EPSILON
from membench.runner.conditions import _oracle_pool
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


def test_materializes_requested_number_of_tasks() -> None:
    seqs = materialize_world(_world(), _project(), n_tasks=3, facts_per_task=3)
    assert len(seqs) == 3
    assert all(s.sequence_id.startswith("world-seed5-task") for s in seqs)


def test_every_task_is_memory_dependent() -> None:
    # The construct-validity bar: each generated sequence must clear the gate
    # (oracle beats no-memory) — otherwise the materialiser produced a task that
    # does not require memory.
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        verdict = memory_necessity_gate(seq).verdict
        assert verdict.accepted, f"{seq.sequence_id}: {verdict.reason}"
        assert verdict.delta > EPSILON


def test_confusion_and_staleness_fields_are_populated() -> None:
    seq = materialize_world(_world(), _project(), n_tasks=1, facts_per_task=3)[0]
    goal = seq.steps[-1]
    # Confusion: one distractor per required subject, none colliding with a real id.
    assert goal.distractor_memories
    written = {mid for step in seq.steps for mid in step.expected_memory_writes}
    assert set(goal.distractor_memories).isdisjoint(written)
    # Staleness: the superseding step annotates the stale v1 id, and that id IS a
    # real earlier write (modeled as distinct v1/v2 ids).
    superseding = [s for s in seq.steps if s.superseded_memory_ids]
    assert superseding, "expected a superseding step"
    stale_id = superseding[0].superseded_memory_ids[0]
    assert stale_id in written
    assert stale_id not in goal.expected_memory_reads  # goal depends on v2, not v1


def test_oracle_pool_has_no_conflict() -> None:
    # Supersession must use distinct ids; _oracle_pool raises on same-id/diff-content.
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        pool = _oracle_pool(seq)
        assert pool  # facts were established


def test_is_byte_reproducible() -> None:
    a = materialize_world(_world(7), _project(7), n_tasks=2, seed=7)
    b = materialize_world(_world(7), _project(7), n_tasks=2, seed=7)
    assert [s.model_dump_json() for s in a] == [s.model_dump_json() for s in b]
    # A different seed yields different content (not a constant).
    c = materialize_world(_world(7), _project(7), n_tasks=2, seed=8)
    assert [s.model_dump_json() for s in c] != [s.model_dump_json() for s in a]


def test_distinct_tasks_do_not_share_a_memory_scope() -> None:
    seqs = materialize_world(_world(), _project(), n_tasks=2)
    ids0 = {mid for step in seqs[0].steps for mid in step.expected_memory_writes}
    ids1 = {mid for step in seqs[1].steps for mid in step.expected_memory_writes}
    assert ids0 and ids1 and ids0.isdisjoint(ids1)


def test_rejects_world_without_personas() -> None:
    empty = _world()
    empty = empty.model_copy(update={"personas": []})
    with pytest.raises(ValueError, match="no personas"):
        materialize_world(empty, _project())
