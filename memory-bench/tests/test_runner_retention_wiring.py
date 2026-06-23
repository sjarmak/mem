"""The consolidation factor is non-inert on the LIVE sequence runner.

Gate 0a wires ``record_class`` from a generated factorial cell through the
runner's write path into the retention arm, so the consolidation HURTS condition
(branch-0's goal-required record scheduled for ``destroy``) actually fires when a
cell is run via ``run_sequence`` — not only via the offline
``report/retention_schedule.run_retention_schedule`` path. Without the wiring the
arm never sees a class, every record defaults to the conservative ``permanent``
disposition, and the consolidation factor reads flat (the inertness this guards).

Backward compat: a sequence with no ``record_class`` (the existing fixtures) and a
non-retention arm (filesystem) must be completely unaffected.
"""

from membench.generators.factorial_dag import FactorCell, generate_cell
from membench.memory_systems.retention_scheduled_system import RetentionScheduledMemory
from membench.runner.conditions import run_sequence
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig


def _retention_experiment() -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="retention-wiring-exp",
        agent=AgentConfig(agent_config_id="scripted-ref", runtime="scripted"),
        memory=MemoryConfig(memory_config_id="retention_scheduled", system="retention_scheduled"),
        dataset_id="synthetic-factorial",
        conditions=[Condition.MEMORY_ENABLED],
    )


def _branch_zero_id(width: int, cell: FactorCell, seed: int) -> str:
    prefix = f"factorial-seed{seed}-w{width}-{cell.cell_id}"
    return f"{prefix}-fact0"


def test_consolidation_on_cell_destroys_goal_required_branch_zero_record() -> None:
    """A consolidation-ON cell run via run_sequence with the retention arm fires the
    HURTS condition: branch-0's goal-required record is wrongfully destroyed."""
    seed, width = 7, 3
    cell = FactorCell(interference=False, supersession=False, consolidation=True)
    seq = generate_cell(seed=seed, width=width, cell=cell)

    arm = RetentionScheduledMemory()
    run = run_sequence(
        seq,
        _retention_experiment(),
        memory_system=arm,
        conditions=[Condition.MEMORY_ENABLED],
    )

    # The offline consolidate() sweep ran once after the write loop.
    assert Condition.MEMORY_ENABLED.value in run.consolidations
    branch0 = _branch_zero_id(width, cell, seed)
    # The wiring delivered the destroy-eligible class, so the sweep actually
    # tombstoned the goal-required branch-0 record (the consolidation factor is live).
    assert arm.applied_disposition(branch0) == "destroy"
    assert branch0 not in arm.live_ids()
    # The other branches carry the keep class and stay live.
    for k in range(1, width):
        keep_id = branch0.replace("-fact0", f"-fact{k}")
        assert arm.applied_disposition(keep_id) == "permanent"
        assert keep_id in arm.live_ids()


def test_consolidation_off_cell_retains_branch_zero_record() -> None:
    """A consolidation-OFF cell carries no record_class, so the conservative default
    keeps branch-0 live (permanent) — the factor toggle is the only difference."""
    seed, width = 7, 3
    cell = FactorCell(interference=False, supersession=False, consolidation=False)
    seq = generate_cell(seed=seed, width=width, cell=cell)

    arm = RetentionScheduledMemory()
    run_sequence(
        seq,
        _retention_experiment(),
        memory_system=arm,
        conditions=[Condition.MEMORY_ENABLED],
    )

    branch0 = _branch_zero_id(width, cell, seed)
    assert arm.applied_disposition(branch0) == "permanent"
    assert branch0 in arm.live_ids()


def test_supersession_writes_apply_class_to_each_written_id() -> None:
    """A step may write current_id + a stale v1; the wiring applies the class to EACH
    written id, so neither is silently left unclassified."""
    seed, width = 11, 2
    cell = FactorCell(interference=False, supersession=True, consolidation=True)
    seq = generate_cell(seed=seed, width=width, cell=cell)

    arm = RetentionScheduledMemory()
    run_sequence(
        seq,
        _retention_experiment(),
        memory_system=arm,
        conditions=[Condition.MEMORY_ENABLED],
    )

    prefix = f"factorial-seed{seed}-w{width}-{cell.cell_id}"
    # Branch 0 establish step writes both fact0 (current) and fact0-v1 (stale) under the
    # destroy class; both must carry the swept destroy disposition, not just the current one.
    assert arm.applied_disposition(f"{prefix}-fact0") == "destroy"
    assert arm.applied_disposition(f"{prefix}-fact0-v1") == "destroy"
