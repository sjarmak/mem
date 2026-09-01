"""The SDK-free records producer: same row contract as ``generate_world_records``.

``generate_world_records`` is the only NeMo-touching step of the world pipeline
(``world_builder``'s module docstring says so); ``records_to_world``,
``materialize_world``, ``write_world`` and the manifest are all pure and CI-tested.
So a deterministic producer of the same flat rows yields the same frozen artifacts
with no endpoint and no SDK, which is what unblocks ``fixtures/worlds-tool``.

These tests pin the two properties that make the substitution safe: the rows satisfy
``records_to_world``'s contract, and the pipeline is byte-reproducible from the seed.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import random
from typing import Any

import pytest

from membench.generators import (
    materialize_world,
    memory_necessity_gate,
    shape_wellformedness_gate,
)
from membench.generators.nemo import records_to_world
from membench.generators.nemo.column_spec import (
    CHANNEL_KINDS,
    DOMAINS,
    ORG_SIZES,
    PERSONA_ROLES,
    REPO_LANGUAGES,
)
from membench.generators.synthetic_records import synthetic_world_records
from membench.memory_systems.semantic_base import DEFAULT_TOP_K

# The columns records_to_world requires; restated here so drift in either direction fails.
REQUIRED = {
    "domain",
    "org_size",
    "org_name",
    "prd_summary",
    "persona_role",
    "channel_kind",
    "repo_language",
    "team_name",
    "persona_name",
}


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def test_rows_carry_every_required_column() -> None:
    for row in synthetic_world_records(num_records=4, seed=0):
        assert set(row) >= REQUIRED, f"missing {sorted(REQUIRED - set(row))}"


def test_sampler_fields_are_in_vocabulary() -> None:
    """records_to_world rejects out-of-vocabulary values; the producer must emit none."""
    vocab = {
        "domain": set(DOMAINS),
        "org_size": set(ORG_SIZES),
        "persona_role": set(PERSONA_ROLES),
        "channel_kind": set(CHANNEL_KINDS),
        "repo_language": set(REPO_LANGUAGES),
    }
    for row in synthetic_world_records(num_records=8, seed=3):
        for field, allowed in vocab.items():
            assert row[field] in allowed, f"{field}={row[field]!r} out of vocabulary"


def test_org_level_fields_are_constant_across_rows() -> None:
    """Rows must describe ONE organization or records_to_world raises."""
    rows = synthetic_world_records(num_records=6, seed=1)
    for field in ("domain", "org_size", "org_name", "prd_summary"):
        assert len({str(r[field]) for r in rows}) == 1, f"{field} varies across rows"


def test_records_are_byte_identical_for_the_same_seed() -> None:
    """The determinism pin: goes RED the moment a field becomes nondeterministic --
    an unseeded org_name, a timestamp, a shuffle off the global RNG."""
    assert _digest(synthetic_world_records(num_records=5, seed=7)) == _digest(
        synthetic_world_records(num_records=5, seed=7)
    )


def test_records_do_not_depend_on_global_rng_state() -> None:
    """A producer reaching for ``random.choice`` instead of its own seeded Random passes
    the repeat-call pin above but drifts whenever anything else touches the global RNG.
    """
    random.seed(1234)
    first = _digest(synthetic_world_records(num_records=5, seed=7))
    random.seed(999)
    [random.random() for _ in range(10)]
    assert _digest(synthetic_world_records(num_records=5, seed=7)) == first


def test_different_seeds_yield_different_worlds() -> None:
    """Non-vacuity: a producer returning one constant world passes the pin above."""
    digests = {_digest(synthetic_world_records(num_records=4, seed=s)) for s in range(6)}
    assert len(digests) > 1, "seed does not vary the generated rows"


def test_personas_are_distinct_within_a_world() -> None:
    """Duplicate persona names make personas indistinguishable to a reader, and the
    fact/distractor surface the materializer builds keys off them."""
    rows = synthetic_world_records(num_records=8, seed=2)
    names = [str(r["persona_name"]) for r in rows]
    assert len(set(names)) == len(names), f"duplicate persona names: {names}"


def test_rows_build_a_world_that_materializes_and_clears_both_gates() -> None:
    """End to end on the real downstream. The substitution is only safe if the artifacts
    a deterministic run freezes are admissible under the same gates
    scripts/generate_worlds.py applies."""
    rows = synthetic_world_records(num_records=4, seed=0)
    world, project = records_to_world(rows, seed=0)
    sequences = materialize_world(world, project, n_tasks=2, facts_per_task=3, tool_requiring=True)
    assert sequences, "no sequences materialized"
    for seq in sequences:
        verdict = memory_necessity_gate(seq).verdict
        assert verdict.accepted, f"{seq.sequence_id} rejected by the necessity gate"
        shape = shape_wellformedness_gate(seq, facts_per_task=3, top_k=DEFAULT_TOP_K)
        assert shape.wellformed, f"{seq.sequence_id} malformed: {shape}"


def test_materialized_sequences_are_byte_identical_for_the_same_seed() -> None:
    """The property verify_world re-checks: seed -> frozen sequences, reproducibly."""

    def build(seed: int) -> str:
        world, project = records_to_world(
            synthetic_world_records(num_records=4, seed=seed), seed=seed
        )
        seqs = materialize_world(world, project, n_tasks=2, facts_per_task=3, tool_requiring=True)
        return _digest([s.model_dump(mode="json") for s in seqs])

    assert build(5) == build(5)


def test_producer_does_not_import_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: no data_designer, no endpoint. A latent SDK dependency --
    including a lazy import inside the call -- fails loudly here."""
    real_import = builtins.__import__

    def guarded(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] == "data_designer":
            raise AssertionError("the deterministic producer imported the data_designer SDK")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert len(synthetic_world_records(num_records=3, seed=4)) == 3
