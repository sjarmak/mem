"""§11 memory-necessity gate — admit a generated task only if oracle beats no-memory.

The gate runs the real two-condition pilot (NO_MEMORY vs ORACLE_MEMORY) over a
``BenchmarkSequence`` and feeds the arm means to ``pilot_filter``. These tests
cover the three authored blueprints (which must admit) and a deliberately
non-discriminating sequence (which must be rejected) — proving the gate
discriminates rather than rubber-stamping.

Two scopes are covered because two things can make a good task look undiscriminating
(mem-r6yzk B1): averaging the arms over steps that carry no outcome check, which
divides a perfect delta by the sequence LENGTH, and piloting a cross-session task in an
isolated store, where the oracle arm is missing the same memory the no-memory arm is.
"""

from __future__ import annotations

import pytest

from membench.generators.memory_necessity_gate import (
    GateCandidate,
    memory_necessity_gate,
    necessity_gate,
    project_necessity_gate,
)
from membench.generators.synthetic_task import generate_synthetic_sequence
from membench.report.comparison import EPSILON
from membench.schemas.sequence import BenchmarkSequence, OutcomeCheck, SequenceStep

CHARTER_ID = "charter-of-the-project"


def test_each_authored_blueprint_is_admitted() -> None:
    # seeds 0,1,2 select the three distinct blueprints in the bank.
    for seed in (0, 1, 2):
        seq = generate_synthetic_sequence(seed=seed)
        result = memory_necessity_gate(seq)
        assert result.sequence_id == seq.sequence_id
        assert result.verdict.accepted, result.verdict.reason
        assert result.verdict.oracle_reward > result.verdict.no_memory_reward
        assert result.verdict.delta > EPSILON


def test_no_memory_arm_cannot_pass_the_goal() -> None:
    # The discriminating signal: with no memory the goal check fails, so the
    # no-memory arm scores zero mean reward; the oracle arm recalls every fact.
    v = memory_necessity_gate(generate_synthetic_sequence(seed=0)).verdict
    assert v.no_memory_reward == 0.0
    assert v.oracle_reward > 0.0


def test_gate_is_deterministic() -> None:
    seq = generate_synthetic_sequence(seed=1)
    a = memory_necessity_gate(seq).verdict
    b = memory_necessity_gate(seq).verdict
    assert (a.accepted, a.oracle_reward, a.no_memory_reward) == (
        b.accepted,
        b.oracle_reward,
        b.no_memory_reward,
    )


def test_non_discriminating_sequence_is_rejected() -> None:
    # A task whose only check requires NO memory passes statelessly in both arms,
    # so oracle == no_memory and the gate must reject it (DIV-3) rather than admit
    # a task that measures nothing about memory.
    seq = BenchmarkSequence(
        sequence_id="degenerate-no-memory-dep",
        title="needs no memory",
        domain="test",
        goal="answer without recalling anything",
        steps=[
            SequenceStep(
                step_id="s0",
                user_request="answer",
                outcome_checks=[
                    OutcomeCheck(
                        check_id="c0",
                        description="passes without any memory",
                        requires_memory=[],
                    )
                ],
            )
        ],
    )
    result = memory_necessity_gate(seq)
    assert not result.verdict.accepted
    assert result.verdict.delta <= EPSILON


def _long_sequence(n_establishing: int = 20) -> BenchmarkSequence:
    """One graded goal behind a long run of establishing steps — the shape the diluted
    statistic rejected for its LENGTH."""
    memory_ids = [f"m{i}" for i in range(n_establishing)]
    steps = [
        SequenceStep(
            step_id=f"s{i}",
            user_request=f"record decision {i}",
            expected_memory_writes={memory_id: f"decision {i} is value-{i} — by Ada (engineer)"},
        )
        for i, memory_id in enumerate(memory_ids)
    ]
    steps.append(
        SequenceStep(
            step_id="goal",
            user_request="state the current value of every decision recorded above",
            expected_memory_reads=memory_ids,
            outcome_checks=[
                OutcomeCheck(
                    check_id="goal-check",
                    description="every recorded decision must be recalled",
                    requires_memory=memory_ids,
                )
            ],
        )
    )
    return BenchmarkSequence(
        sequence_id="long-establishing-chain",
        title="twenty decisions, one question",
        domain="test",
        goal="recall every decision",
        steps=steps,
    )


def test_a_long_sequence_is_admitted_on_the_step_that_grades_it() -> None:
    """mem-r6yzk B1 item 4 — the statistic used to be a sequence MEAN.

    An establishing step carries no outcome check and scores 0.0 in every arm, so
    averaging over the whole sequence made the oracle arm score 1/n_steps. Past 20
    establishing steps that is inside epsilon, and the task was rejected for its length
    under a reason that said it did not discriminate memory benefit.
    """
    seq = _long_sequence()
    # The old statistic, stated so the regression is explicit rather than remembered.
    assert 1 / len(seq.steps) <= EPSILON

    verdict = memory_necessity_gate(seq).verdict
    assert verdict.accepted, verdict.reason
    assert verdict.oracle_reward == 1.0
    assert verdict.no_memory_reward == 0.0
    assert verdict.delta == 1.0
    assert verdict.scope == f"the 1 graded step(s) of {seq.sequence_id}"


def test_length_alone_does_not_move_the_verdict() -> None:
    short = memory_necessity_gate(_long_sequence(2)).verdict
    long = memory_necessity_gate(_long_sequence(40)).verdict
    assert (short.oracle_reward, short.no_memory_reward) == (
        long.oracle_reward,
        long.no_memory_reward,
    )


def test_the_rejection_reason_names_the_cause_and_the_trial_set() -> None:
    # A rejection that says only "does not discriminate" hides WHICH of the three
    # causes fired. The reason must name the scope it measured and the actual cause.
    seq = BenchmarkSequence(
        sequence_id="degenerate-ties",
        title="needs no memory",
        domain="test",
        goal="answer without recalling anything",
        steps=[
            SequenceStep(
                step_id="s0",
                user_request="answer",
                outcome_checks=[OutcomeCheck(check_id="c0", requires_memory=[])],
            )
        ],
    )
    reason = memory_necessity_gate(seq).verdict.reason
    assert "the 1 graded step(s) of degenerate-ties" in reason
    assert "memory confers no advantage where the task is graded" in reason


def test_a_sequence_with_nothing_graded_raises_rather_than_reporting_a_tie() -> None:
    # A 0.000 delta over no graded step is not a measurement of non-discrimination;
    # it is a malformed candidate, and reporting it as a verdict would launder it.
    seq = BenchmarkSequence(
        sequence_id="ungraded",
        title="nothing is checked",
        domain="test",
        goal="write and stop",
        steps=[
            SequenceStep(
                step_id="s0",
                user_request="record something",
                expected_memory_writes={"m0": "a thing is a value — by Ada (engineer)"},
            )
        ],
    )
    with pytest.raises(ValueError, match="no outcome check"):
        memory_necessity_gate(seq)


def _project_pair() -> tuple[BenchmarkSequence, BenchmarkSequence]:
    """Two sequences of one world: task 0 establishes the charter, task 1 is graded on
    it and never writes it — the cross-session shape."""
    task0 = BenchmarkSequence(
        sequence_id="proj-task0",
        title="establish the charter",
        domain="test",
        goal="record what the project decided",
        tier="project",
        steps=[
            SequenceStep(
                step_id="t0-charter",
                user_request="Record the project charter decision.",
                expected_memory_writes={
                    CHARTER_ID: (
                        "the project charter decision is extend the beta by one quarter "
                        "— by Ada Lovelace (staff-engineer) in #kernels"
                    )
                },
            ),
            SequenceStep(
                step_id="t0-goal",
                user_request="State the current value of: the project charter decision.",
                expected_memory_reads=[CHARTER_ID],
                outcome_checks=[
                    OutcomeCheck(check_id="t0-check", requires_memory=[CHARTER_ID]),
                ],
            ),
        ],
    )
    task1 = BenchmarkSequence(
        sequence_id="proj-task1",
        title="decide against the charter",
        domain="test",
        goal="reconcile this session against what the project decided",
        tier="project",
        steps=[
            SequenceStep(
                step_id="t1-window",
                user_request="Record the data retention window.",
                expected_memory_writes={
                    "t1-window": (
                        "the data retention window is 90 days "
                        "— by Grace Hopper (site-reliability-engineer) in #kernels"
                    )
                },
            ),
            SequenceStep(
                step_id="t1-goal",
                user_request=(
                    "State the current value of: the data retention window, "
                    "the project charter decision."
                ),
                expected_memory_reads=["t1-window", CHARTER_ID],
                outcome_checks=[
                    OutcomeCheck(check_id="t1-check", requires_memory=["t1-window", CHARTER_ID]),
                ],
            ),
        ],
    )
    return task0, task1


def test_the_isolated_gate_cannot_admit_a_cross_session_task() -> None:
    # Not a defect in the task: the isolated oracle pool is missing the charter too,
    # so both arms fail the goal. The verdict is about the SCOPE it was piloted at.
    _, task1 = _project_pair()
    verdict = memory_necessity_gate(task1).verdict
    assert not verdict.accepted
    assert verdict.oracle_reward == verdict.no_memory_reward


def test_the_project_gate_admits_the_cross_session_task() -> None:
    """mem-r6yzk B1 — the gate half. Piloted alongside the sequence that wrote the
    charter, the oracle arm holds it and the no-memory arm does not: the task
    discriminates memory benefit, which is exactly what it was built to do."""
    task0, task1 = _project_pair()
    result = project_necessity_gate(task1, [task0])
    assert result.sequence_id == "proj-task1"
    verdict = result.verdict
    assert verdict.accepted, verdict.reason
    assert verdict.oracle_reward > verdict.no_memory_reward + EPSILON
    assert verdict.oracle_reward == 1.0
    assert verdict.no_memory_reward == 0.0
    assert "shared with 1 earlier sequence(s)" in verdict.scope


def test_the_project_gate_grades_only_the_target() -> None:
    # The context's trials are setup, not evidence: task 0's own goal step must not
    # enter task 1's arm means.
    task0, task1 = _project_pair()
    verdict = project_necessity_gate(task1, [task0]).verdict
    assert verdict.scope.startswith("the 1 graded step(s) of proj-task1")


def test_the_project_gate_refuses_an_empty_context() -> None:
    # An empty prefix IS the isolated pilot; accepting it here would report a project
    # verdict for a run that never shared a store.
    _, task1 = _project_pair()
    with pytest.raises(ValueError, match="no context"):
        project_necessity_gate(task1, [])


def test_necessity_gate_dispatches_on_the_candidates_own_context() -> None:
    task0, task1 = _project_pair()
    alone = necessity_gate(GateCandidate(sequence=task1))
    assert not alone.verdict.accepted
    together = necessity_gate(GateCandidate(sequence=task1, context=(task0,)))
    assert together.verdict.accepted, together.verdict.reason
    # The isolated branch must stay the isolated call, byte for byte.
    assert alone == memory_necessity_gate(task1)
    assert together == project_necessity_gate(task1, [task0])
