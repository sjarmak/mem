"""Gate 0a — realization + recovery on REAL ScriptedAgent scores (not planted).

Two halves, both over scores produced by actually running each generated factorial
cell through the live runner (``run_sequence`` + ``ScriptedAgent`` + the arm), never
fabricated numbers:

* REALIZATION — the factor toggles MOVE the mechanical observable in the right
  direction on the live path: interference raises ``distractor_retrieval_rate``
  (Confusion) under the lexical top-k arm and stays 0 under the id-exact filesystem
  arm; supersession raises ``stale_memory_retrieval_rate`` (Staleness) under lexical;
  consolidation drives the goal-required branch-0 record to a ``destroy`` disposition
  under the retention arm while OFF does not.

* RECOVERY — feeding each per-cell observable into ``factorial_diagnosis.diagnose``
  recovers the MATCHING factor's main effect with the right direction (``hurts``,
  since higher confusion/staleness/destruction is worse and the driver negates the
  rate so "higher response = better" holds) and a CI that clears 0, while an unrelated
  factor stays ``inconclusive``; and ``shuffle_responses`` collapses the recovered
  effect in aggregate (the Gate 0a negative control).

The replicate count (``_SEEDS``) is tuned so the assertions are deterministic and
robust — the runner + scorer are pure functions of (seed, width, cell), so the scores
are byte-stable and the bootstrap CIs are reproducible.
"""

from __future__ import annotations

from statistics import mean

import pytest

from membench.report.factorial_behavioral import (
    CONSOLIDATION,
    INTERFERENCE,
    SUPERSESSION,
    CellObservables,
    observations_for,
    run_family_observables,
)
from membench.report.factorial_diagnosis import diagnose, shuffle_responses

# Eight seeds x eight cells = 64 cell-replicates per arm — enough matched groups that the
# paired-bootstrap CI is tight and the shuffle control is stable, while CI stays fast
# (offline ScriptedAgent, no Docker/API).
_SEEDS: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7)
_WIDTH = 3


@pytest.fixture(scope="module")
def lexical_cells() -> list[CellObservables]:
    """The factorial family run under the lexical top-k arm (Confusion/Staleness probe)."""
    return run_family_observables(_SEEDS, width=_WIDTH, arm="lexical")


@pytest.fixture(scope="module")
def filesystem_cells() -> list[CellObservables]:
    """The same family under the id-exact filesystem arm (never surfaces distractors)."""
    return run_family_observables(_SEEDS, width=_WIDTH, arm="filesystem")


@pytest.fixture(scope="module")
def retention_cells() -> list[CellObservables]:
    """The same family under the retention arm (reports the destroy disposition)."""
    return run_family_observables(_SEEDS, width=_WIDTH, arm="retention_scheduled")


def _split_mean(cells: list[CellObservables], factor: str, observable: str) -> tuple[float, float]:
    """Mean raw observable for the factor-ON vs factor-OFF cells."""
    on = mean(c.raw(observable) for c in cells if c.cell.levels()[factor])
    off = mean(c.raw(observable) for c in cells if not c.cell.levels()[factor])
    return on, off


# --------------------------------------------------------------------------- #
# REALIZATION — the factor toggles move the live observable
# --------------------------------------------------------------------------- #
def test_realization_interference_raises_confusion_under_lexical(
    lexical_cells: list[CellObservables],
) -> None:
    """Interference-ON cells surface more seeded distractors at the goal retrieve than
    OFF cells, under the top-k arm — the Confusion construct actually realizing."""
    on, off = _split_mean(lexical_cells, INTERFERENCE, INTERFERENCE)
    assert off == 0.0  # no distractors planted ⇒ none retrieved
    assert on > off  # ON cells genuinely surface the planted competitors


def test_realization_interference_is_zero_under_filesystem(
    filesystem_cells: list[CellObservables],
) -> None:
    """The id-exact arm only ever returns the ids the harness requested, so it never
    surfaces a distractor — confusion is 0 in EVERY cell regardless of the toggle."""
    on, off = _split_mean(filesystem_cells, INTERFERENCE, INTERFERENCE)
    assert on == 0.0
    assert off == 0.0
    assert all(c.confusion == 0.0 for c in filesystem_cells)


def test_realization_supersession_raises_staleness_under_lexical(
    lexical_cells: list[CellObservables],
) -> None:
    """Supersession-ON cells surface the superseded v1 at the goal retrieve more than
    OFF cells — the Staleness construct realizing on the live path."""
    on, off = _split_mean(lexical_cells, SUPERSESSION, SUPERSESSION)
    assert off == 0.0  # no stale v1 written ⇒ none retrieved
    assert on > off


def test_realization_consolidation_destroys_goal_record_only_when_on(
    retention_cells: list[CellObservables],
) -> None:
    """Consolidation-ON drives the goal-required branch-0 record to a ``destroy``
    disposition under the retention arm; OFF leaves it kept (destruction 0)."""
    on, off = _split_mean(retention_cells, CONSOLIDATION, CONSOLIDATION)
    assert on == 1.0  # every ON cell wrongfully destroys the needed record
    assert off == 0.0  # every OFF cell keeps it
    assert all(c.destruction == 1.0 for c in retention_cells if c.cell.consolidation)
    assert all(c.destruction == 0.0 for c in retention_cells if not c.cell.consolidation)


# --------------------------------------------------------------------------- #
# RECOVERY — diagnose() recovers the matching main effect; controls collapse it
# --------------------------------------------------------------------------- #
def test_recovery_interference_effect_hurts_with_ci_clearing_zero(
    lexical_cells: list[CellObservables],
) -> None:
    """diagnose() recovers the interference main effect as ``hurts`` (negated confusion,
    so higher confusion = worse = a negative effect) with a CI strictly below 0, while
    the unrelated consolidation factor (inert under lexical) stays inconclusive."""
    diag = diagnose(observations_for(lexical_cells, INTERFERENCE))
    eff = diag.effect_for(INTERFERENCE)
    assert eff.direction == "hurts"
    assert eff.ci_high < 0.0  # the whole CI clears 0 on the worse side
    assert diag.effect_for(CONSOLIDATION).direction == "inconclusive"


def test_recovery_supersession_effect_hurts_with_ci_clearing_zero(
    lexical_cells: list[CellObservables],
) -> None:
    """diagnose() recovers the supersession main effect as ``hurts`` with a CI below 0;
    the unrelated consolidation factor stays inconclusive."""
    diag = diagnose(observations_for(lexical_cells, SUPERSESSION))
    eff = diag.effect_for(SUPERSESSION)
    assert eff.direction == "hurts"
    assert eff.ci_high < 0.0
    assert diag.effect_for(CONSOLIDATION).direction == "inconclusive"


def test_recovery_consolidation_effect_hurts_with_ci_clearing_zero(
    retention_cells: list[CellObservables],
) -> None:
    """diagnose() recovers the consolidation main effect as ``hurts`` (the destroy of a
    needed record) with a CI below 0; the unrelated interference + supersession factors
    (no Confusion/Staleness signal in the destruction observable) stay inconclusive."""
    diag = diagnose(observations_for(retention_cells, CONSOLIDATION))
    eff = diag.effect_for(CONSOLIDATION)
    assert eff.direction == "hurts"
    assert eff.ci_high < 0.0
    assert diag.effect_for(INTERFERENCE).direction == "inconclusive"
    assert diag.effect_for(SUPERSESSION).direction == "inconclusive"


@pytest.mark.parametrize(
    ("fixture_name", "observable"),
    [
        ("lexical_cells", INTERFERENCE),
        ("lexical_cells", SUPERSESSION),
        ("retention_cells", CONSOLIDATION),
    ],
)
def test_shuffle_control_collapses_recovered_effect(
    fixture_name: str,
    observable: str,
    request: pytest.FixtureRequest,
) -> None:
    """The Gate 0a negative control: permuting responses destroys the cell→response
    link, so across many shuffles the recovered effect collapses — the aggregate mean
    absolute shuffled effect is a small fraction of the true effect, and no shuffle
    reads a robust same-direction effect. Judged in aggregate over seeds since any
    single shuffle is one random draw."""
    cells: list[CellObservables] = request.getfixturevalue(fixture_name)
    obs = observations_for(cells, observable)
    true_effect = abs(diagnose(obs).effect_for(observable).effect)
    assert true_effect > 0.0  # there IS a real effect to collapse

    shuffled_effects = [
        abs(diagnose(shuffle_responses(obs, seed=s)).effect_for(observable).effect)
        for s in range(40)
    ]
    # The link is gone, so the aggregate shuffled effect is a small fraction of the real
    # one (a tenth is generous — empirically it is < 5%).
    assert mean(shuffled_effects) < 0.1 * true_effect
    # And no shuffle recovers the same-direction effect robustly (CI clears 0 the wrong way).
    same_direction = sum(
        1
        for s in range(40)
        if diagnose(shuffle_responses(obs, seed=s)).effect_for(observable).direction == "hurts"
    )
    assert same_direction <= 2  # a handful of spurious draws is allowed; a recovered effect is not
