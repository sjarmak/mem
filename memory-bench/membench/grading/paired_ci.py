"""Shared paired-delta inference for the grading stack (mem-lp24).

The grid designs are fully paired — every rung replays the same held-out tasks —
so deltas are formed per task and summarized with the paired percentile bootstrap
that already backs the handoff-efficiency metrics
(``membench.handoff_efficiency.bootstrap_median_ci``, reused here rather than
duplicated).

Two populations, always explicitly labeled (never a silent choice):

- ``itt`` (intention-to-treat, the pre-registered PRIMARY): every task admitted to
  either rung contributes a delta; a task missing one rung's observation — e.g. the
  ``ours`` rung fired no retrieval, so the arm degenerates to its counterpart —
  contributes delta 0 rather than silently dropping out. Stable as the corpus grows.
- ``matched`` (per-protocol, the labeled SECONDARY): only tasks observed on BOTH
  rungs. Mechanism-conditional — its selection function (e.g. lesson coverage)
  moves as the corpus grows, so successive reads are different populations.

ZFC: pure mechanism — arithmetic plus seeded resampling, no semantic judgment.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from membench.handoff_efficiency import bootstrap_mean_ci, bootstrap_median_ci

Population = Literal["itt", "matched"]

POPULATION_PRIMARY: Population = "itt"

Statistic = Literal["median", "mean"]

# The paired summary a caller may ask for. The median is the pre-registered primary everywhere
# in this repo; the mean is admissible only ALONGSIDE it, because a median over a coarse
# per-task rate lattice can sit flat while most tasks moved a little. Naming both here, rather
# than letting a caller pass its own callable, keeps "which statistic was this reported at" a
# value that rides in the artifact instead of a closure nobody can read back.
_STATISTICS = {"median": bootstrap_median_ci, "mean": bootstrap_mean_ci}


@dataclass(frozen=True)
class PairedDeltaCI:
    """A paired delta (treatment - baseline) with its bootstrap CI and the
    population it was read on. ``n_pairs`` counts every delta entering the
    bootstrap; ``n_imputed_zero`` says how many were ITT zero-imputations
    (always 0 for ``matched``)."""

    delta: float
    ci_low: float
    ci_high: float
    n_pairs: int
    n_imputed_zero: int
    population: Population
    # Which summary of the deltas ``delta`` is. Defaulted so every existing caller keeps the
    # median it already reports, and carried on the result so a reader of an artifact never has
    # to infer it from the call site that produced it.
    statistic: Statistic = "median"


def paired_deltas(
    baseline: Mapping[str, float],
    treatment: Mapping[str, float],
    *,
    population: Population,
) -> tuple[list[float], int]:
    """Per-task deltas (treatment - baseline) in sorted task order (a deterministic
    bootstrap input). Returns the deltas and the count of ITT zero-imputations —
    tasks observed on only one rung, whose delta is pinned to 0 under ``itt`` and
    dropped under ``matched``."""
    if population == "matched":
        keys = sorted(baseline.keys() & treatment.keys())
        return [treatment[k] - baseline[k] for k in keys], 0
    deltas: list[float] = []
    imputed = 0
    for key in sorted(baseline.keys() | treatment.keys()):
        if key in baseline and key in treatment:
            deltas.append(treatment[key] - baseline[key])
        else:
            deltas.append(0.0)
            imputed += 1
    return deltas, imputed


def paired_delta_ci(
    baseline: Mapping[str, float],
    treatment: Mapping[str, float],
    *,
    population: Population = POPULATION_PRIMARY,
    statistic: Statistic = "median",
    n_resamples: int = 5000,
    conf: float = 0.95,
    seed: int = 0,
) -> PairedDeltaCI:
    """The paired delta + percentile-bootstrap CI on the labeled population.

    An empty population is a caller error — there is nothing to infer from — so it
    raises rather than fabricating a degenerate interval."""
    deltas, imputed = paired_deltas(baseline, treatment, population=population)
    if not deltas:
        raise ValueError(f"paired_delta_ci: no tasks in the {population!r} population")
    if statistic not in _STATISTICS:
        raise ValueError(f"paired_delta_ci: unknown statistic {statistic!r}")
    point, lo, hi = _STATISTICS[statistic](deltas, n_resamples=n_resamples, conf=conf, seed=seed)
    return PairedDeltaCI(
        delta=point,
        ci_low=lo,
        ci_high=hi,
        n_pairs=len(deltas),
        n_imputed_zero=imputed,
        population=population,
        statistic=statistic,
    )
