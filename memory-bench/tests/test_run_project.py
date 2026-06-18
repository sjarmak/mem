"""§Continuity — run_project shares memory across tasks; cross-task tasks need it.

A project (materialize_project) links every task's goal to a charter established in
task 0. These tests prove: run_project carries that charter across tasks (oracle
union + a real filesystem arm), the same later task FAILS under isolated
run_sequence (genuinely cross-task, not cross-step), and the recovery variant
(dropped charter) models missing context.
"""

from __future__ import annotations

import pytest

from membench.generators import materialize_project
from membench.runner.conditions import run_sequence
from membench.runner.project import run_project
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team


def _world(seed: int = 5) -> EnterpriseWorld:
    return EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Lin", role="qa-engineer", team_id="t1"),
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


def _exp(system: str = "none") -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="proj-exp",
        agent=AgentConfig(agent_config_id="scripted-ref"),
        memory=MemoryConfig(memory_config_id="m", system=system),
        dataset_id="synthetic",
    )


def _goal_trial(run, sequence_id: str):
    return next(
        t
        for t in run.trials
        if t.sequence_id == sequence_id and t.step_id.endswith("-goal")
    )


def _mean_reward(run, condition: Condition) -> float:
    trials = run.by_condition()[condition]
    return sum(t.reward for t in trials) / len(trials)


def test_continuity_is_memory_dependent_at_project_level() -> None:
    seqs = materialize_project(_world(), _project(), n_tasks=3)
    run = run_project(
        seqs, _exp(), conditions=[Condition.NO_MEMORY, Condition.ORACLE_MEMORY], project_id="proj"
    )
    assert _mean_reward(run, Condition.NO_MEMORY) == 0.0
    assert _mean_reward(run, Condition.ORACLE_MEMORY) > _mean_reward(run, Condition.NO_MEMORY)


def test_later_task_requires_the_shared_store() -> None:
    # The cross-task signal: task 1's goal needs the charter task 0 wrote. Isolated
    # (run_sequence) it fails even under ORACLE; under run_project it passes.
    seqs = materialize_project(_world(), _project(), n_tasks=2)
    task1 = seqs[1]
    iso = run_sequence(task1, _exp(), conditions=[Condition.ORACLE_MEMORY])
    assert _goal_trial(iso, task1.sequence_id).reward == 0.0  # charter absent in isolation
    proj = run_project(seqs, _exp(), conditions=[Condition.ORACLE_MEMORY], project_id="proj")
    assert _goal_trial(proj, task1.sequence_id).reward == 1.0  # charter via shared store


def test_filesystem_arm_carries_charter_across_tasks(tmp_path) -> None:
    # A real (non-oracle) arm persists task 0's charter write in the shared scope, so
    # task 1 retrieves it — cross-task continuity end to end.
    seqs = materialize_project(_world(), _project(), n_tasks=2)
    run = run_project(
        seqs,
        _exp(system="filesystem"),
        conditions=[Condition.MEMORY_ENABLED],
        project_id="proj",
        fs_base_dir=tmp_path,
    )
    assert _goal_trial(run, seqs[1].sequence_id).reward == 1.0


def test_recovery_variant_models_missing_context() -> None:
    # drop_charter omits the establishing step: the charter is required but never
    # written, so even the oracle union lacks it and the cross-task goal fails.
    seqs = materialize_project(_world(), _project(), n_tasks=2, drop_charter=True)
    run = run_project(seqs, _exp(), conditions=[Condition.ORACLE_MEMORY], project_id="proj")
    assert _goal_trial(run, seqs[1].sequence_id).reward == 0.0


def test_run_project_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one sequence"):
        run_project([], _exp())
