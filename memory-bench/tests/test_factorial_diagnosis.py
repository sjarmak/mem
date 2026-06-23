"""Gate 0a diagnosis reporter — factorial main-effect / interaction recovery.

The instrument-validity half of Gate 0a (no agent run, no real anchor): given graded
responses with a PLANTED effect, the reporter must recover it with the correct sign
and a CI that clears 0, leave non-planted factors inconclusive, and — the negative
control — collapse every effect once the cell→response link is shuffled.
"""

from __future__ import annotations

import random
from collections.abc import Callable

import pytest

from membench.generators.factorial_dag import NON_DEPTH_FACTORS, FactorCell, all_factor_cells
from membench.report.factorial_diagnosis import (
    Observation,
    benefit_observations,
    diagnose,
    shuffle_responses,
)

_BASE = 0.5


def _planted(
    effect_fn: Callable[[FactorCell], float],
    *,
    replicates: int = 8,
    noise: float = 0.0,
    seed: int = 0,
) -> list[Observation]:
    """A full 2^3 family across ``replicates`` seeds, response = base + planted effect
    (+ optional deterministic noise)."""
    rng = random.Random(seed)
    obs: list[Observation] = []
    for rep in range(replicates):
        for cell in all_factor_cells():
            jitter = rng.uniform(-noise, noise) if noise else 0.0
            obs.append(
                Observation(
                    levels=cell.levels(),
                    replicate=str(rep),
                    response=_BASE + effect_fn(cell) + jitter,
                )
            )
    return obs


def test_recovers_planted_main_effect_with_correct_sign() -> None:
    # interference HURTS by 0.3; the other two factors do nothing.
    obs = _planted(lambda c: -0.3 if c.interference else 0.0)
    diag = diagnose(obs)
    interference = diag.effect_for("interference")
    assert interference.direction == "hurts"
    assert interference.ci_high < 0.0
    assert interference.effect == pytest.approx(-0.3)
    # untouched factors stay inconclusive (CI spans 0).
    assert diag.effect_for("supersession").direction == "inconclusive"
    assert diag.effect_for("consolidation").direction == "inconclusive"


def test_recovers_planted_interaction() -> None:
    # memory hurts ONLY under interference AND supersession together.
    obs = _planted(lambda c: -0.4 if (c.interference and c.supersession) else 0.0)
    inter = diagnose(obs).effect_for("interference", "supersession")
    assert inter.direction == "hurts"
    assert inter.ci_high < 0.0
    assert inter.effect == pytest.approx(-0.2)  # Yates interaction = planted/2


def test_positive_effect_reads_as_helps() -> None:
    obs = _planted(lambda c: 0.25 if c.consolidation else 0.0)
    eff = diagnose(obs).effect_for("consolidation")
    assert eff.direction == "helps"
    assert eff.ci_low > 0.0


def test_shuffle_control_collapses_a_real_effect_in_aggregate() -> None:
    # A strong real main effect with noise so the shuffle has range to destroy.
    obs = _planted(lambda c: -0.4 if c.interference else 0.0, noise=0.05, seed=1)
    true = diagnose(obs).effect_for("interference")
    assert true.direction == "hurts"
    # The negative control is an AGGREGATE claim: any single shuffle is one random
    # draw, but destroying the cell→response link must collapse the effect across
    # shuffles. (Observed 38/40; assert with margin so it is not seed-cherry-picked.)
    mags: list[float] = []
    inconclusive = 0
    for s in range(40):
        eff = diagnose(shuffle_responses(obs, seed=s)).effect_for("interference")
        inconclusive += eff.direction == "inconclusive"
        mags.append(abs(eff.effect))
    assert inconclusive >= 32
    median_shuffled = sorted(mags)[len(mags) // 2]
    assert median_shuffled < abs(true.effect) / 4  # shuffled effect is a fraction of the real one


def test_all_seven_effects_are_estimated_for_a_full_factorial() -> None:
    diag = diagnose(_planted(lambda c: 0.0))
    labels = {e.label for e in diag.effects}
    assert len(diag.effects) == 7  # 3 main + 3 two-way + 1 three-way
    assert "interference" in labels
    assert "interference*supersession*consolidation" in labels
    assert diag.skipped_incomplete_groups == 0


def test_benefit_observations_pairs_arm_minus_baseline() -> None:
    arm = _planted(lambda c: 0.8, replicates=2)  # arm scores high
    baseline = _planted(lambda c: 0.3, replicates=2)  # baseline scores low
    benefit = benefit_observations(arm, baseline)
    assert len(benefit) == len(arm)
    assert all(o.response == pytest.approx(0.5) for o in benefit)  # 0.8 - 0.3


def test_benefit_observations_raises_on_unmatched_cell() -> None:
    arm = _planted(lambda c: 0.5, replicates=1)
    baseline = [o for o in _planted(lambda c: 0.0, replicates=1) if not o.levels["interference"]]
    with pytest.raises(ValueError):
        benefit_observations(arm, baseline)


def test_benefit_observations_raises_on_duplicate_arm_cell() -> None:
    baseline = _planted(lambda c: 0.0, replicates=1)
    arm = _planted(lambda c: 0.5, replicates=1)
    arm.append(arm[0])  # a duplicated arm cell must raise, not silently overwrite
    with pytest.raises(ValueError):
        benefit_observations(arm, baseline)


def test_incomplete_group_is_skipped_and_counted() -> None:
    # Drop one corner from one replicate: its main-effect group is incomplete.
    obs = _planted(lambda c: 0.0, replicates=2)
    dropped = [
        o
        for o in obs
        if not (o.replicate == "0" and o.levels == FactorCell(True, True, True).levels())
    ]
    diag = diagnose(dropped)
    # one dropped corner leaves each of the 7 effects missing exactly one matched group.
    assert diag.skipped_incomplete_groups == 7


def test_diagnose_raises_on_missing_factor_level() -> None:
    bad = [Observation(levels={"interference": True}, replicate="0", response=0.5)]
    with pytest.raises(ValueError):
        diagnose(bad)


def test_diagnose_raises_on_empty() -> None:
    with pytest.raises(ValueError):
        diagnose([])


def test_duplicate_observation_raises() -> None:
    cell = FactorCell(False, False, False)
    dup = [
        Observation(levels=cell.levels(), replicate="0", response=0.5),
        Observation(levels=cell.levels(), replicate="0", response=0.6),
    ]
    # only one factor subset is needed to trip the duplicate guard
    with pytest.raises(ValueError):
        diagnose(dup, factors=NON_DEPTH_FACTORS)
