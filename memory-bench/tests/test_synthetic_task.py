"""§11 synthetic task generator — blueprint → step → deterministic schema.

The generator emits a ``BenchmarkSequence`` (reusing the existing schema, NOT a new
task type): establishing steps that each write one authored fact, plus a goal step
that REQUIRES every fact via an ``OutcomeCheck`` — so the task is memory-dependent by
construction (the oracle passes it, no-memory cannot). The oracle is authored in pure
Python (Tier 0): deterministic, seed-reproducible, no LLM in CI.
"""

from __future__ import annotations

from membench.generators.synthetic_task import (
    GENERATOR_VERSION,
    SHAPE_BLUEPRINTS,
    blueprint_for_seed,
    generate_synthetic_sequence,
)


def test_generator_version_is_recorded() -> None:
    assert GENERATOR_VERSION.startswith("synthetic-task")


def test_blueprint_for_seed_is_deterministic() -> None:
    assert blueprint_for_seed(0) is blueprint_for_seed(0)
    # The seed selects from the bank; seeds one bank-length apart pick the same one.
    assert blueprint_for_seed(0).blueprint_id == blueprint_for_seed(3).blueprint_id


def test_deterministic_seed_is_byte_reproducible() -> None:
    a = generate_synthetic_sequence(seed=7)
    b = generate_synthetic_sequence(seed=7)
    assert a.model_dump_json() == b.model_dump_json()
    # A different seed yields a different sequence (not a constant).
    c = generate_synthetic_sequence(seed=8)
    assert c.model_dump_json() != a.model_dump_json()


def test_goal_step_requires_every_established_fact() -> None:
    seq = generate_synthetic_sequence(seed=1)
    writes = {mid for step in seq.steps[:-1] for mid in step.expected_memory_writes}
    assert writes, "establishing steps must write facts"
    goal = seq.steps[-1]
    # The goal writes nothing and depends on every fact established earlier.
    assert goal.expected_memory_writes == {}
    assert set(goal.expected_memory_reads) == writes
    assert len(goal.outcome_checks) == 1
    assert set(goal.outcome_checks[0].requires_memory) == writes


def test_every_required_memory_is_established_in_an_earlier_step() -> None:
    # Cross-session dependency: nothing the goal requires is invented at the goal —
    # each required id is written by a strictly earlier step (this is what makes the
    # oracle beat no-memory).
    seq = generate_synthetic_sequence(seed=2)
    goal = seq.steps[-1]
    for required in goal.outcome_checks[0].requires_memory:
        established_before = any(required in step.expected_memory_writes for step in seq.steps[:-1])
        assert established_before, f"{required} required at goal but never established"


def test_shape_blueprints_are_a_separate_bank_with_shape_ids() -> None:
    # The shape-grounded blueprints live OUTSIDE the generic seed→blueprint bank, so the
    # existing seed mapping (and byte-reproducibility of existing seeds) is untouched.
    generic_ids = {blueprint_for_seed(s).blueprint_id for s in range(3)}
    for bp in SHAPE_BLUEPRINTS:
        assert bp.shape_id is not None
        assert bp.blueprint_id not in generic_ids
    # The generic bank carries no shape label (it mimics no specific real shape).
    assert blueprint_for_seed(0).shape_id is None


def test_distinct_seeds_do_not_share_a_memory_scope() -> None:
    # Ids are seed-namespaced, so two generated tasks never collide in the store.
    a = generate_synthetic_sequence(seed=1)
    b = generate_synthetic_sequence(seed=4)  # same blueprint (1 % 3 == 4 % 3), distinct seed
    a_ids = {mid for step in a.steps for mid in step.expected_memory_writes}
    b_ids = {mid for step in b.steps for mid in step.expected_memory_writes}
    assert a_ids.isdisjoint(b_ids)
    assert a.sequence_id != b.sequence_id
