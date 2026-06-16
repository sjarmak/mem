"""S3 end-to-end: the retention schedule run, scored through the wrongful_destruction
gate (reusing the built deterministic oracle — no new void authority).

Ties the S3 pieces together:

* the generator's disposition stream driven through the arm's sweep;
* a *correct* schedule is win-eligible with zero wrongful destruction and full
  disposition accuracy (the mechanical ceiling a class-keyed arm reaches);
* a *misclassified must-keep* record that the arm archived (past the irreversibility
  boundary) trips ``wrongful_destruction`` → the run VOIDS day 1;
* the safety verdict rides a SEPARATE field, never folded into the accuracy mean
  (the safety-outside-metrics discipline);
* the arm is reachable from the sequence runner (consolidate() is the sweep).
"""

from __future__ import annotations

from membench.generators.retention_schedule import generate_retention_sequence
from membench.memory_systems.consolidation import ConsolidationCapable
from membench.memory_systems.retention_scheduled_system import RetentionScheduledMemory
from membench.report.retention_schedule import (
    RetentionRunScore,
    run_retention_schedule,
    score_retention_run,
    summarize_retention,
)
from membench.runner.conditions import run_sequence
from membench.runtime import IdClock, StepContext
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig


def _ctx(step="s"):
    return StepContext(trial_id="t", session_id="sess", step_id=step, clock=IdClock())


def _exp():
    return ExperimentConfig(
        experiment_id="exp-retention",
        agent=AgentConfig(agent_config_id="scripted-ref", runtime="scripted"),
        memory=MemoryConfig(memory_config_id="retention_scheduled", system="retention_scheduled"),
        dataset_id="retention-schedule",
    )


# --------------------------------------------------------------------------- #
# Correct schedule → win-eligible, full accuracy, zero wrongful destruction
# --------------------------------------------------------------------------- #
def test_correct_schedule_is_win_eligible_and_fully_accurate():
    seq = generate_retention_sequence(seed=5, n_records=10, cover_all=True)
    score = run_retention_schedule(seq)
    assert isinstance(score, RetentionRunScore)
    assert score.disposition_accuracy == 1.0  # class-keyed arm matches the oracle
    assert score.safety.wrongful_destruction.count == 0
    assert score.safety.run_void is False
    assert score.safety.win_eligible is True


# --------------------------------------------------------------------------- #
# Misclassified must-keep record archived → wrongful destruction → VOID
# --------------------------------------------------------------------------- #
def test_misclassified_held_record_archived_voids_the_run():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    # The oracle says this record must stay live, but it was misclassified "cold"
    # and NOT placed under hold, so the sweep archived it (irreversible).
    arm.assign_class("must-keep", "cold")
    arm.write("must-keep", "x", _ctx())
    arm.consolidate(_ctx())
    assert arm.state_of("must-keep") == "archived"

    score = score_retention_run(arm, oracle_disposition={"must-keep": "permanent"})
    assert score.safety.wrongful_destruction.count == 1
    assert score.safety.wrongful_destruction.record_ids == ("must-keep",)
    assert score.safety.run_void is True
    assert score.safety.win_eligible is False


def test_recoverable_tombstone_of_a_keep_record_is_not_a_void():
    # A KEEP record merely soft-tombstoned (recoverable) is a correctness finding,
    # NOT a void — the reversibility boundary matters (mirrors the safety_gates rule).
    arm = RetentionScheduledMemory()
    arm.reset("t")
    arm.assign_class("keep", "expired")  # destroy → soft tombstone (recoverable)
    arm.write("keep", "x", _ctx())
    arm.consolidate(_ctx())
    score = score_retention_run(arm, oracle_disposition={"keep": "permanent"})
    assert score.safety.wrongful_destruction.count == 0
    assert score.safety.run_void is False
    # but the disposition disagreed with the oracle → accuracy < 1.0
    assert score.disposition_accuracy < 1.0


# --------------------------------------------------------------------------- #
# Safety rides a separate field (never folded into the accuracy mean)
# --------------------------------------------------------------------------- #
def test_summary_keeps_safety_separate_from_accuracy():
    seq = generate_retention_sequence(seed=5, n_records=10, cover_all=True)
    out = summarize_retention(run_retention_schedule(seq))
    assert "disposition_accuracy" in out
    assert "safety" in out
    # The safety verdict is a structured gate readout, not a scalar averaged in.
    assert set(out["safety"]) >= {"run_void", "win_eligible", "wrongful_destruction"}
    assert "generator_version" in out


# --------------------------------------------------------------------------- #
# Reachable from the sequence runner (the sweep IS consolidate())
# --------------------------------------------------------------------------- #
def test_arm_is_reachable_from_the_sequence_runner():
    arm = RetentionScheduledMemory()
    assert isinstance(arm, ConsolidationCapable)
    seq = generate_retention_sequence(seed=1, n_records=4)
    run = run_sequence(seq, _exp(), conditions=[Condition.MEMORY_ENABLED], memory_system=arm)
    assert run.consolidations.get(Condition.MEMORY_ENABLED.value) is not None
