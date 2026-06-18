"""§11 memory-necessity gate — admit a generated task only if oracle beats no-memory.

The gate runs the real two-condition pilot (NO_MEMORY vs ORACLE_MEMORY) over a
``BenchmarkSequence`` and feeds the arm means to ``pilot_filter``. These tests
cover the three authored blueprints (which must admit) and a deliberately
non-discriminating sequence (which must be rejected) — proving the gate
discriminates rather than rubber-stamping.
"""

from __future__ import annotations

from membench.generators.memory_necessity_gate import memory_necessity_gate
from membench.generators.synthetic_task import generate_synthetic_sequence
from membench.report.comparison import EPSILON
from membench.schemas.sequence import BenchmarkSequence, OutcomeCheck, SequenceStep


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
