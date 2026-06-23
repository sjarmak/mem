"""Gate 0a apparatus — factorial isolation-DAG generator.

Verifies the INSTRUMENT-validity properties the PRD's Gate 0a needs WITHOUT any agent
run or real anchor: single-factor isolation (antichain width invariant across non-depth
toggles), depth as the skeleton axis, per-factor realisation (each toggle moves a
distinct trace observable the runner/scorer consume), balance, structural
memory-dependence (``pilot_filter`` admits), the runner's supersession contract, and
byte-reproducibility.
"""

from __future__ import annotations

import pytest

from membench.generators.factorial_dag import (
    DISTRACTOR_ON_COUNT,
    GENERATOR_VERSION,
    FactorCell,
    all_factor_cells,
    antichain_width,
    generate_cell,
    generate_factorial_family,
)
from membench.generators.pilot_filter import pilot_filter
from membench.runner.conditions import _assert_superseded_written
from membench.schemas.sequence import BenchmarkSequence, SequenceStep

_ALL_OFF = FactorCell(False, False, False)


def _establish_steps(seq: BenchmarkSequence) -> list[SequenceStep]:
    return [st for st in seq.steps if "-establish" in st.step_id]


def _total_distractors(seq: BenchmarkSequence) -> int:
    return sum(len(st.distractor_memories) for st in seq.steps)


def _total_superseded(seq: BenchmarkSequence) -> int:
    return sum(len(st.superseded_memory_ids) for st in seq.steps)


def _structural_rewards(seq: BenchmarkSequence) -> tuple[float, float]:
    """Oracle (all memory present) vs no-memory, scored on the deterministic
    ``requires_memory`` checks — the property ``pilot_filter`` admits on."""
    checks = [c for st in seq.steps for c in st.outcome_checks]
    assert checks, "generated sequence must carry at least one outcome check"
    oracle = 1.0
    no_mem = sum(1 for c in checks if not c.requires_memory) / len(checks)
    return oracle, no_mem


def test_generator_version_recorded() -> None:
    assert GENERATOR_VERSION.startswith("factorial-dag")


def test_family_is_full_factorial_of_eight_unique_cells() -> None:
    fam = generate_factorial_family(seed=1, width=4)
    assert len(fam) == 8
    assert len({s.sequence_id for s in fam}) == 8
    assert {c.cell_id for c in all_factor_cells()} == {
        "i0s0c0",
        "i1s0c0",
        "i0s1c0",
        "i0s0c1",
        "i1s1c0",
        "i1s0c1",
        "i0s1c1",
        "i1s1c1",
    }


def test_antichain_width_invariant_across_nondepth_toggles() -> None:
    # Isolation guarantee: every cell at a frozen K has the SAME topology width == K.
    for width in (3, 4, 5):
        widths = {antichain_width(s.steps) for s in generate_factorial_family(seed=2, width=width)}
        assert widths == {width}


def test_depth_is_the_only_axis_that_moves_width() -> None:
    assert antichain_width(generate_cell(seed=0, width=3, cell=_ALL_OFF).steps) == 3
    assert antichain_width(generate_cell(seed=0, width=6, cell=_ALL_OFF).steps) == 6


def test_interference_toggles_distractor_observable_without_moving_topology() -> None:
    off = generate_cell(seed=3, width=4, cell=_ALL_OFF)
    on = generate_cell(seed=3, width=4, cell=FactorCell(True, False, False))
    assert _total_distractors(off) == 0
    assert _total_distractors(on) == DISTRACTOR_ON_COUNT
    assert antichain_width(off.steps) == antichain_width(on.steps)


def test_supersession_toggles_stale_observable_without_moving_topology() -> None:
    off = generate_cell(seed=3, width=4, cell=_ALL_OFF)
    on = generate_cell(seed=3, width=4, cell=FactorCell(False, True, False))
    assert _total_superseded(off) == 0
    assert _total_superseded(on) == 4  # one stale v1 per branch
    assert antichain_width(off.steps) == antichain_width(on.steps)


def test_consolidation_toggles_retention_labels_with_a_hurts_condition() -> None:
    off = generate_cell(seed=3, width=4, cell=_ALL_OFF)
    on = generate_cell(seed=3, width=4, cell=FactorCell(False, False, True))
    assert all(st.record_class is None and st.disposition is None for st in off.steps)
    est = _establish_steps(on)
    assert est and all(st.record_class is not None for st in est)
    # the HURTS condition: a goal-required record scheduled for destruction.
    assert "destroy" in {st.disposition for st in est}
    assert antichain_width(off.steps) == antichain_width(on.steps)


def test_full_factorial_is_balanced() -> None:
    cells = all_factor_cells()
    for name in ("interference", "supersession", "consolidation"):
        assert sum(1 for c in cells if c.levels()[name]) == 4


def test_memory_dependent_by_construction_admitted_by_pilot_filter() -> None:
    for cell in all_factor_cells():
        seq = generate_cell(seed=5, width=4, cell=cell)
        oracle, no_mem = _structural_rewards(seq)
        verdict = pilot_filter(oracle_reward=oracle, no_memory_reward=no_mem)
        assert verdict.accepted, f"cell {cell.cell_id} not admitted: {verdict.reason}"


def test_supersession_satisfies_runner_contract() -> None:
    # The real runner assertion must accept every cell: a superseded v1 is written by an
    # EARLIER step than the goal that marks it stale.
    for cell in all_factor_cells():
        _assert_superseded_written(generate_cell(seed=6, width=4, cell=cell))


def test_deterministic_byte_reproducible() -> None:
    a = generate_factorial_family(seed=9, width=4)
    b = generate_factorial_family(seed=9, width=4)
    assert [s.model_dump_json() for s in a] == [s.model_dump_json() for s in b]
    c = generate_factorial_family(seed=10, width=4)
    assert [s.model_dump_json() for s in c] != [s.model_dump_json() for s in a]


def test_cost_grounding_is_optional_and_resampled_from_the_pool() -> None:
    pool = [(140, 12), (150, 9), (160, 20)]
    grounded = generate_cell(seed=1, width=4, cell=FactorCell(True, True, True), cost_pool=pool)
    real_turns = {t for t, _ in pool}
    for st in grounded.steps:
        assert st.environment_state["real_cost_turns"] in real_turns
    # absent a pool, no cost keys leak into the trace (CI stays IO-free).
    plain = generate_cell(seed=1, width=4, cell=FactorCell(True, True, True))
    assert all("real_cost_turns" not in st.environment_state for st in plain.steps)


def test_width_must_be_positive() -> None:
    with pytest.raises(ValueError):
        generate_cell(seed=0, width=0, cell=_ALL_OFF)
