"""S3 — the retention disposition-stream generator (Tier 0, pure Python).

A retention sequence is N write steps, each establishing ONE record carrying its
``record_class`` and the schedule's ground-truth ``disposition`` oracle. The oracle
and the whole structure are authored here deterministically: given a seed the
sequence is byte-reproducible (no LLM, no network — the gate this feeds voids
precisely because the oracle is deterministic, not judged).
"""

from __future__ import annotations

from membench.generators.retention_schedule import (
    GENERATOR_VERSION,
    RETENTION_POLICY,
    generate_retention_sequence,
)


def test_seed_is_byte_reproducible():
    a = generate_retention_sequence(seed=7, n_records=6)
    b = generate_retention_sequence(seed=7, n_records=6)
    assert a.model_dump() == b.model_dump()


def test_distinct_seeds_differ():
    a = generate_retention_sequence(seed=1, n_records=6)
    b = generate_retention_sequence(seed=2, n_records=6)
    assert a.model_dump() != b.model_dump()


def test_every_write_step_carries_class_and_disposition_oracle():
    seq = generate_retention_sequence(seed=3, n_records=8)
    write_steps = [s for s in seq.steps if s.expected_memory_writes]
    assert write_steps, "the sequence must establish records"
    for step in write_steps:
        assert step.record_class in RETENTION_POLICY
        # The oracle disposition is the policy applied to the class.
        assert step.disposition == RETENTION_POLICY[step.record_class]


def test_generator_version_is_tagged_on_the_sequence_domain():
    seq = generate_retention_sequence(seed=1, n_records=4)
    assert seq.domain == "retention-schedule"
    assert GENERATOR_VERSION.startswith("retention-schedule")


def test_covers_every_disposition_class_when_asked():
    # With enough records the generator exercises every policy class, so a scorer run
    # touches permanent / review / archive / destroy / legal_hold — not one corner.
    seq = generate_retention_sequence(seed=5, n_records=10, cover_all=True)
    classes = {s.record_class for s in seq.steps if s.expected_memory_writes}
    assert classes == set(RETENTION_POLICY)
