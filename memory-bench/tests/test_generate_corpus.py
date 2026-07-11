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
from generate_worlds import (  # noqa: E402
    SequenceAdmission,
    SequenceWellformedness,
    WorldResult,
)


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


def _world(seed: int, accepts: list[bool], *, wellformed: list[bool] | None = None) -> WorldResult:
    shapes: tuple[SequenceWellformedness, ...] = ()
    if wellformed is not None:
        shapes = tuple(
            SequenceWellformedness(
                sequence_id=f"s{seed}-t{i}",
                wellformed=w,
                reason="well-formed" if w else "M1 truncation: facts_per_task > top_k",
            )
            for i, w in enumerate(wellformed)
        )
    return WorldResult(
        seed=seed,
        org_name=f"Org{seed}",
        domain="data-infrastructure",
        out_dir=Path(f"fixtures/worlds/{seed}"),
        admissions=tuple(_admission(f"s{seed}-t{i}", a) for i, a in enumerate(accepts)),
        wellformedness=shapes,
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


def test_shape_wellformedness_block_absent_when_no_tool_requiring() -> None:
    # Text-answer worlds carry no graded shape, so the summary must omit the block
    # rather than report a vacuous 0/0 well-formedness (mem-rk41.2).
    results = [_world(0, [True, False]), _world(1, [True, True])]
    summary = _summarise(results, failures=[])
    assert "shape_wellformedness" not in summary


def test_shape_wellformedness_block_aggregates_across_seeds() -> None:
    # Two tool-requiring worlds, one malformed task among them: the block reports the
    # corpus-wide graded/well-formed/malformed counts and names the malformed task.
    results = [
        _world(0, [True, True], wellformed=[True, False]),
        _world(1, [True], wellformed=[True]),
    ]
    summary = _summarise(results, failures=[])

    block = summary["shape_wellformedness"]
    assert isinstance(block, dict)
    assert "NOT arm discrimination" in block["label"]
    assert block["tasks_graded"] == 3
    assert block["tasks_wellformed"] == 2
    assert block["tasks_malformed"] == 1
    assert block["malformed"] == [
        {"sequence_id": "s0-t1", "reason": "M1 truncation: facts_per_task > top_k"}
    ]
