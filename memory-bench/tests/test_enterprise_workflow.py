"""§11 enterprise-workflow materialiser — memory-dependent worlds with Confusion+Staleness.

Mirrors ``test_synthetic_task`` but over a world: each materialised sequence must be
memory-dependent (clears ``memory_necessity_gate``), carry populated
``distractor_memories`` (Confusion) and ``superseded_memory_ids`` (Staleness), and be
byte-reproducible from its seed. The oracle pool must stay conflict-free (supersession
uses distinct v1/v2 ids).
"""

from __future__ import annotations

import pytest

from membench.generators.enterprise_workflow import SUPERSESSION_DEPTH, materialize_world
from membench.generators.memory_necessity_gate import memory_necessity_gate
from membench.report.comparison import EPSILON
from membench.runner.agent import ScriptedAgent
from membench.runner.conditions import _assert_superseded_written, _oracle_pool
from membench.runtime import IdClock, StepContext
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
    # Staleness: every superseding step annotates its predecessor, and each stale id
    # IS a real earlier write (modeled as distinct version ids).
    superseding = [s for s in seq.steps if s.superseded_memory_ids]
    assert superseding, "expected a superseding step"
    for stale_id in goal.superseded_memory_ids:
        assert stale_id in written
        assert stale_id not in goal.expected_memory_reads  # goal depends on the final version


def test_supersession_chain_has_depth_and_satisfies_runner_contract() -> None:
    # mem-z3gi: the chain is v1..vD with D >= 3 — each superseding step marks its
    # predecessor, the goal marks every earlier version stale, and the runner's
    # prior-write assertion accepts the whole chain.
    assert SUPERSESSION_DEPTH >= 3
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        goal = seq.steps[-1]
        assert len(goal.superseded_memory_ids) == SUPERSESSION_DEPTH - 1
        chain_steps = [s for s in seq.steps[:-1] if s.superseded_memory_ids]
        assert len(chain_steps) == SUPERSESSION_DEPTH - 1
        _assert_superseded_written(seq)


def test_superseded_subject_position_varies_by_seed() -> None:
    # A fixed chain position (the old i==0) would let position stand in for the
    # staleness label; across seeds the superseding steps must not all sit at one
    # index. Step ids are harness-side, so reading the position off them is safe.
    def chain_start_index(seed: int) -> int:
        seq = materialize_world(_world(seed), _project(seed), n_tasks=1)[0]
        return next(i for i, s in enumerate(seq.steps) if s.superseded_memory_ids) - 1

    assert len({chain_start_index(seed) for seed in range(12)}) > 1


def test_goal_forbids_the_superseded_values_and_staleness_is_reward_bearing() -> None:
    seq = materialize_world(_world(), _project(), n_tasks=1, facts_per_task=3)[0]
    goal = seq.steps[-1]
    check = goal.outcome_checks[0]
    # The authored stale values ride the check (mem-z3gi item 4).
    assert len(check.forbidden_values) == SUPERSESSION_DEPTH - 1
    pool = _oracle_pool(seq)
    required = {mid: pool[mid] for mid in check.requires_memory}
    stale = {mid: pool[mid] for mid in goal.superseded_memory_ids}
    ctx = StepContext(trial_id="t", session_id="s", step_id=goal.step_id, clock=IdClock())
    # Exact recall (the oracle surface) passes.
    clean = ScriptedAgent().run_step(goal, required, ctx)
    assert clean.check_results[check.check_id] is True
    # Surfacing a stale version FAILS the goal — reward-bearing, not just diagnostic.
    confused = ScriptedAgent().run_step(goal, {**required, **stale}, ctx)
    assert confused.check_results[check.check_id] is False


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


def test_tool_requiring_variant_moves_staleness_onto_the_tool_action() -> None:
    # mem-31vl: the tool-requiring goal demands a tool call carrying the CURRENT
    # value; staleness moves off the text answer (forbidden_values cleared) onto the
    # action's OWN forbidden_values, so the tool is the sole reward-bearing channel.
    seq = materialize_world(_world(), _project(), n_tasks=1, facts_per_task=3, tool_requiring=True)[
        0
    ]
    goal = seq.steps[-1]
    assert goal.available_tools == ["apply_config"]
    check = goal.outcome_checks[0]
    assert check.forbidden_values == []  # text clause cleared
    assert len(check.requires_action) == 1
    action = check.requires_action[0]
    assert action.tool == "apply_config"
    assert action.arg_values and all(action.arg_values)  # a real current value
    assert action.forbidden_values  # stale values live on the action, not the prose
    # the current value is never itself a forbidden (stale) value
    assert not set(action.arg_values) & set(action.forbidden_values)


def test_tool_requiring_variant_is_still_memory_dependent() -> None:
    # Necessity survives the shape change: oracle (surfaces current-only -> valid tool
    # arg) beats no-memory (empty arg -> current value absent).
    for seq in materialize_world(_world(), _project(), n_tasks=3, tool_requiring=True):
        verdict = memory_necessity_gate(seq).verdict
        assert verdict.accepted, f"{seq.sequence_id}: {verdict.reason}"
        assert verdict.delta > EPSILON


def test_default_variant_stays_text_answer() -> None:
    # Backward compat: default (tool_requiring=False) keeps the text-answer goal.
    goal = materialize_world(_world(), _project(), n_tasks=1, facts_per_task=3)[0].steps[-1]
    assert goal.available_tools == []
    assert goal.outcome_checks[0].requires_action == []
    assert goal.outcome_checks[0].forbidden_values  # text staleness still enforced
