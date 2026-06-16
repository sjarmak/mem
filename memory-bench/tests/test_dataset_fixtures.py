"""Validation gate for the benchmark-sequence dataset (mem-lvp.8, spec §9/§10/§15).

Deterministic, no network, no model calls: globs every fixture under
``fixtures/sequences/``, parses it into a typed :class:`BenchmarkSequence`, and
asserts the dataset-level invariants the harness depends on:

* every sequence is *memory-sensitive* — at least one outcome check requires a
  memory an earlier step established (the oracle-beats-no_memory premise, §15);
* every step carries at least one outcome check and one memory probe (§15);
* the §10 stressor axes A,B,C,E,F,G are each covered by >=1 fixture, and the
  STRUCTURAL axes (A,B,C,E,F) are re-derived from the data and cross-checked
  against each fixture's self-declared ``axes_covered`` manifest so the manifest
  cannot over-claim a structural axis it does not actually exhibit.

The existing dashboard reference fixture predates the ``axes_covered`` manifest;
it is still required to parse and be memory-sensitive, but is exempt from the
manifest cross-check (no manifest => nothing to verify against).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from membench.schemas.sequence import BenchmarkSequence

SEQ_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sequences"

# Axes the corpus as a whole must exercise (spec §10). D (compaction) and the
# backend-spread half of F are out of scope for hand-authored fixtures and are
# not asserted here.
REQUIRED_AXES = {"A", "B", "C", "E", "F", "G"}

# write->read gap (in steps) at or above which a dependency counts as a
# temporal-distance (axis A) stressor.
TEMPORAL_GAP_MIN = 3

# environment_state keys that name a memory *scope* (axis E). Two steps with
# different values under any of these keys means the sequence spans scopes.
SCOPE_KEYS = ("repo", "clone", "project", "epic", "trace")


def _fixture_paths() -> list[Path]:
    paths = sorted(SEQ_DIR.glob("*.json"))
    assert paths, f"no sequence fixtures found under {SEQ_DIR}"
    return paths


def _load(path: Path) -> BenchmarkSequence:
    return BenchmarkSequence.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _required_memory_checks(seq: BenchmarkSequence) -> list[list[str]]:
    """Lists of required-memory ids for every memory-sensitive outcome check."""
    return [
        check.requires_memory
        for step in seq.steps
        for check in step.outcome_checks
        if check.requires_memory
    ]


def _has_cross_session_dependency(seq: BenchmarkSequence) -> bool:
    """A later step requires a memory an EARLIER step established."""
    written_by: dict[str, int] = {}
    for idx, step in enumerate(seq.steps):
        for required in _step_required_ids(step):
            origin = written_by.get(required)
            if origin is not None and origin < idx:
                return True
        for mid in step.expected_memory_writes:
            written_by.setdefault(mid, idx)
    return False


def _step_required_ids(step) -> set[str]:
    return {mid for check in step.outcome_checks for mid in check.requires_memory}


# --- structural axis derivation (independent of the declared manifest) -------


def _axis_a_temporal(seq: BenchmarkSequence) -> bool:
    write_step: dict[str, int] = {}
    for idx, step in enumerate(seq.steps):
        for required in _step_required_ids(step):
            origin = write_step.get(required)
            if origin is not None and idx - origin >= TEMPORAL_GAP_MIN:
                return True
        for mid in step.expected_memory_writes:
            write_step.setdefault(mid, idx)
    return False


def _axis_b_interference(seq: BenchmarkSequence) -> bool:
    return any(step.distractor_memories for step in seq.steps)


def _axis_c_staleness(seq: BenchmarkSequence) -> bool:
    return any(step.superseded_memory_ids for step in seq.steps)


def _axis_e_scope(seq: BenchmarkSequence) -> bool:
    for key in SCOPE_KEYS:
        values = {
            step.environment_state[key] for step in seq.steps if key in step.environment_state
        }
        if len(values) >= 2:
            return True
    return False


def _axis_f_synthesis(seq: BenchmarkSequence) -> bool:
    return any(len(ids) >= 2 for ids in _required_memory_checks(seq))


# Structural axes => the predicate that must hold if a fixture declares the axis.
STRUCTURAL_AXES = {
    "A": _axis_a_temporal,
    "B": _axis_b_interference,
    "C": _axis_c_staleness,
    "E": _axis_e_scope,
    "F": _axis_f_synthesis,
}


def _declared_axes(seq: BenchmarkSequence) -> set[str]:
    return set(seq.final_goal_check.get("axes_covered", []))


# --- per-fixture invariants ---------------------------------------------------


@pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: p.stem)
def test_fixture_parses_and_is_memory_sensitive(path: Path) -> None:
    seq = _load(path)
    assert seq.steps, f"{path.name}: sequence has no steps"
    assert 3 <= len(seq.steps) <= 8, f"{path.name}: §15 wants 3-8 steps, got {len(seq.steps)}"

    sensitive = _required_memory_checks(seq)
    assert sensitive, (
        f"{path.name}: not memory-sensitive — no outcome_check has non-empty "
        f"requires_memory, so oracle cannot beat no_memory"
    )
    assert _has_cross_session_dependency(seq), (
        f"{path.name}: every required memory is established in or after its own "
        f"step — there is no cross-session dependency"
    )


@pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: p.stem)
def test_every_step_has_check_and_probe(path: Path) -> None:
    seq = _load(path)
    for step in seq.steps:
        assert step.outcome_checks, f"{path.name}/{step.step_id}: no outcome_checks (§15)"
        assert step.memory_probes, f"{path.name}/{step.step_id}: no memory_probes (§15)"


@pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: p.stem)
def test_declared_structural_axes_are_real(path: Path) -> None:
    """A fixture may not claim a structural axis it does not exhibit in its data."""
    seq = _load(path)
    for axis in _declared_axes(seq) & STRUCTURAL_AXES.keys():
        assert STRUCTURAL_AXES[axis](
            seq
        ), f"{path.name}: declares axis {axis} but the data does not exhibit it"


# --- corpus-level invariants --------------------------------------------------


def _corpus() -> list[BenchmarkSequence]:
    return [_load(p) for p in _fixture_paths()]


def test_corpus_covers_every_required_axis() -> None:
    union: set[str] = set()
    for seq in _corpus():
        union |= _declared_axes(seq)
    missing = REQUIRED_AXES - union
    assert not missing, f"§10 axes not covered by any fixture: {sorted(missing)}"


def test_reference_dashboard_fixture_still_valid() -> None:
    """The pre-existing dashboard fixture must keep parsing under the schema."""
    ref = SEQ_DIR / "gascity_backend_conventions.json"
    seq = _load(ref)
    assert seq.sequence_id == "gascity-backend-conventions"
    assert _required_memory_checks(seq), "reference fixture lost its memory-sensitivity"


def test_dataset_mvp_ratios() -> None:
    """§15 Dataset MVP ratio targets, measured over the seed corpus."""
    corpus = _corpus()
    n = len(corpus)
    assert n >= 10, f"§15 Dataset MVP wants >=10 multi-session sequences; corpus has {n}"

    synthesis = sum(1 for seq in corpus if _axis_f_synthesis(seq))
    stale_or_interf = sum(
        1 for seq in corpus if _axis_b_interference(seq) or _axis_c_staleness(seq)
    )
    # >=50% synthesis and >=30% staleness/interference (§15). Reported as a hard
    # gate; the final message records the exact achieved fractions.
    assert synthesis / n >= 0.50, f"synthesis ratio {synthesis}/{n} < 0.50 (§15)"
    assert (
        stale_or_interf / n >= 0.30
    ), f"staleness/interference ratio {stale_or_interf}/{n} < 0.30 (§15)"
