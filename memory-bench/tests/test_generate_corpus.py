"""Seed-loop corpus wrapper: seed-spec parsing + cross-seed admission aggregation.

All SDK-free / NIM-free — exercises the pure control logic of ``scripts/generate_corpus.py``
and the ``WorldResult`` aggregation added to ``scripts/generate_worlds.py``. The model-
calling ``generate_and_freeze`` body is operator tooling, not covered here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from generate_corpus import _summarise, parse_seed_spec  # noqa: E402
from generate_worlds import SequenceAdmission, WorldResult  # noqa: E402


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("0-19", list(range(20))),
        ("0,2,5", [0, 2, 5]),
        ("0-4,10,12-14", [0, 1, 2, 3, 4, 10, 12, 13, 14]),
        ("3", [3]),
        (" 0 , 1 ", [0, 1]),
        ("5-5", [5]),  # inclusive single-element range
        ("2,2,1", [1, 2]),  # de-duplicated + sorted
    ],
)
def test_parse_seed_spec_valid(spec: str, expected: list[int]) -> None:
    assert parse_seed_spec(spec) == expected


@pytest.mark.parametrize("spec", ["5-2", "", "x", "1-", ","])
def test_parse_seed_spec_rejects_garbage(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_seed_spec(spec)


def _admission(seq_id: str, accepted: bool) -> SequenceAdmission:
    return SequenceAdmission(
        sequence_id=seq_id,
        accepted=accepted,
        delta=0.2 if accepted else 0.0,
        oracle_reward=0.2 if accepted else 0.0,
        no_memory_reward=0.0,
    )


def _world(seed: int, accepts: list[bool]) -> WorldResult:
    return WorldResult(
        seed=seed,
        org_name=f"Org{seed}",
        domain="data-infrastructure",
        out_dir=Path(f"fixtures/worlds/{seed}"),
        admissions=tuple(_admission(f"s{seed}-t{i}", a) for i, a in enumerate(accepts)),
    )


def test_world_result_counts() -> None:
    w = _world(0, [True, False, True])
    assert w.total == 3
    assert w.admitted == 2


def test_summarise_aggregates_across_seeds() -> None:
    results = [_world(0, [True, False]), _world(1, [True, True])]
    summary = _summarise(results, failures=[])

    assert summary["seeds_requested"] == 2
    assert summary["seeds_generated"] == 2
    assert summary["seeds_failed"] == []
    assert summary["tasks_generated"] == 4
    assert summary["tasks_admitted"] == 3
    assert summary["tasks_rejected"] == 1
    worlds = summary["worlds"]
    assert isinstance(worlds, list)
    assert len(worlds) == 2


def test_summarise_counts_failed_seeds_in_denominator() -> None:
    results = [_world(0, [True, True])]
    summary = _summarise(results, failures=[(1, "RuntimeError: NIM 503")])

    assert summary["seeds_requested"] == 2
    assert summary["seeds_generated"] == 1
    assert summary["seeds_failed"] == [{"seed": 1, "error": "RuntimeError: NIM 503"}]
    # a failed seed contributes no tasks to the corpus totals
    assert summary["tasks_generated"] == 2
    assert summary["tasks_admitted"] == 2
