"""Score-vs-information curve — the .3c headline artifact.

Aggregates per-rung ``combined_reward`` across the held-out set into the curve the
mem-apg headline is read off (ARCHITECTURE.md D17). Pure deterministic aggregation
(ZFC): grouping, arithmetic means, and a normal-approximation confidence interval —
no semantic judgment.

**What the live 3-rung ladder supports (architect H2).** With the combinatorial rungs
(`builtin`, `ours+builtin`) deferred to mem-whi, the executable ladder is only
``none < ours < oracle`` — three points with no interior resolution and no
*combination* axis. Three points cannot LOCATE a saturation point or a
minimum-useful-information COMBINATION, so this module reports what they genuinely
support:

- ``floor_lift`` — how far ``ours`` lifts reward above the zero-memory ``none`` floor.
- ``ceiling_gap`` — how far ``ours`` still trails the ``oracle`` ceiling.

``saturation_point`` and ``min_useful_combo`` are the D17 readouts that need the full
ladder; they REFUSE (raise ``InsufficientLadderError``) below four rungs rather than
fabricate a vacuous "saturation at the only interior rung". They become meaningful
once mem-whi lands the builtin rungs.

Repeats collapse within task (architect M2) before the across-task mean + CI: ``k``
repeats of a task are correlated, not independent observations, so each task
contributes one mean-reward value to its rung's sample.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from math import fsum, sqrt
from statistics import NormalDist

from membench.grading.ablation import DEFAULT_RUNGS
from membench.grading.trace_score import RewardRecord

# Below four rungs the curve has no interior resolution, so the saturation /
# minimum-useful-combination readouts cannot be computed (architect H2).
MIN_LADDER_FOR_SATURATION = 4

# Reward-scale tolerance for "stopped improving" / "reached the ceiling". A
# calibrated-threshold ZFC exception (transparent arithmetic against a documented
# constant): two rungs within 5% of the [0, 1] reward scale are treated as
# indistinguishable for the saturation/min-useful readouts.
DEFAULT_SATURATION_TOL = 0.05

# Absolute slack on the tolerance comparison so a reward difference that is exactly
# DEFAULT_SATURATION_TOL is not excluded by IEEE-754 rounding (e.g. 0.55 - 0.5
# evaluates to 0.0500000000000000444). Tiny relative to the 0.05 tolerance.
_TOL_EPS = 1e-9


@dataclass(frozen=True)
class RungReward:
    """One rung's aggregate reward over the held-out set. ``lower_bound`` /
    ``upper_bound`` are the normal-approximation CI clamped to ``[0, 1]``; for a single
    task they collapse to the mean (no across-task spread to estimate)."""

    rung: str
    mean_reward: float
    lower_bound: float
    upper_bound: float
    n_tasks: int


@dataclass(frozen=True)
class ScoreInformationCurve:
    """The per-rung reward curve in ladder order. ``floor_lift`` and ``ceiling_gap``
    are the two readouts the 3-rung ladder supports; both are ``None`` when the
    rung they need is absent (a partial run), surfaced rather than guessed."""

    rungs: tuple[RungReward, ...]

    def rung(self, name: str) -> RungReward | None:
        """The aggregate for ``name``, or None if that rung is not in the curve."""
        for r in self.rungs:
            if r.rung == name:
                return r
        return None

    @property
    def floor_lift(self) -> float | None:
        """``ours`` mean minus ``none`` mean — the lift our memory buys over zero
        memory. None unless both rungs are present."""
        none, ours = self.rung("none"), self.rung("ours")
        if none is None or ours is None:
            return None
        return ours.mean_reward - none.mean_reward

    @property
    def ceiling_gap(self) -> float | None:
        """``oracle`` mean minus ``ours`` mean — how far our memory still trails the
        oracle ceiling. None unless both rungs are present."""
        ours, oracle = self.rung("ours"), self.rung("oracle")
        if ours is None or oracle is None:
            return None
        return oracle.mean_reward - ours.mean_reward


class InsufficientLadderError(Exception):
    """Raised when a readout needs more rungs than the curve has (architect H2):
    saturation / minimum-useful-combination require the full ladder, not the live
    3-rung subset."""


def _mean_ci(values: list[float], conf: float) -> tuple[float, float, float]:
    """Mean and normal-approximation CI of ``values``, clamped to ``[0, 1]``.

    A single value has no across-task variance to interval, so its bounds collapse to
    the mean. Normal approximation (mean ± z·sem) is deliberate: transparent arithmetic
    over a small sample, no scipy dependency for an inverse-t."""
    n = len(values)
    if n == 0:
        # build_curve never calls this with an empty rung, but make the invariant
        # explicit so a future refactor can't reach a ZeroDivisionError here.
        raise ValueError("_mean_ci requires at least one value")
    mean = fsum(values) / n
    if n < 2:
        return mean, mean, mean
    variance = fsum((x - mean) ** 2 for x in values) / (n - 1)
    sem = sqrt(variance / n)
    z = NormalDist().inv_cdf(0.5 + conf / 2.0)
    half = z * sem
    return mean, max(0.0, mean - half), min(1.0, mean + half)


def _ladder_order(present: set[str], rungs: Sequence[str] | None) -> list[str]:
    """The rungs to report, in order. Explicit ``rungs`` are honored (restricted to
    those with data); auto-detect falls back to canonical ladder order with any
    non-canonical rungs appended deterministically (sorted), so the order is never
    LLM- or dict-iteration-dependent."""
    if rungs is not None:
        return [r for r in rungs if r in present]
    canonical = [r for r in DEFAULT_RUNGS if r in present]
    extra = sorted(present - set(DEFAULT_RUNGS))
    return canonical + extra


def build_curve(
    records: Sequence[RewardRecord],
    *,
    rungs: Sequence[str] | None = None,
    conf: float = 0.95,
) -> ScoreInformationCurve:
    """Build the score-vs-information curve from reward records across rungs.

    Per-rung value = mean over tasks of each task's mean ``combined_reward`` (repeats
    collapsed within task first, M2). Rung order follows the canonical ladder unless
    ``rungs`` is given. An empty input is a caller error — there is no curve to build
    from no observations; likewise a ``rungs`` filter that selects nothing present."""
    if not records:
        raise ValueError("build_curve needs at least one reward record")
    if not 0.0 < conf < 1.0:
        raise ValueError(f"conf must be in (0, 1), got {conf}")

    # (rung, work_id) -> the task's per-repeat rewards.
    by_task: dict[tuple[str, str], list[float]] = defaultdict(list)
    present: set[str] = set()
    for record in records:
        by_task[(record.rung, record.work_id)].append(record.reward)
        present.add(record.rung)

    # rung -> per-task mean rewards (one value per task, repeats collapsed).
    task_means: dict[str, list[float]] = defaultdict(list)
    for (rung, _work_id), rewards in by_task.items():
        task_means[rung].append(fsum(rewards) / len(rewards))

    order = _ladder_order(present, rungs)
    if not order:
        # An explicit rungs= filter that matches nothing present would otherwise
        # return an empty curve silently (silent_fallbacks) — fail loudly instead.
        raise ValueError(f"rungs filter {rungs!r} selected no rungs present in records")

    built: list[RungReward] = []
    for rung in order:
        values = task_means[rung]
        mean, lower, upper = _mean_ci(values, conf)
        built.append(
            RungReward(
                rung=rung,
                mean_reward=mean,
                lower_bound=lower,
                upper_bound=upper,
                n_tasks=len(values),
            )
        )
    return ScoreInformationCurve(rungs=tuple(built))


def _require_full_ladder(curve: ScoreInformationCurve) -> None:
    if len(curve.rungs) < MIN_LADDER_FOR_SATURATION:
        raise InsufficientLadderError(
            f"need ≥{MIN_LADDER_FOR_SATURATION} rungs to read this off, have "
            f"{len(curve.rungs)} ({[r.rung for r in curve.rungs]}); the builtin / "
            f"ours+builtin rungs (mem-whi) must land first"
        )


def saturation_point(
    curve: ScoreInformationCurve, *, tol: float = DEFAULT_SATURATION_TOL
) -> RungReward:
    """The earliest rung past which more information stops adding reward (within
    ``tol``) — i.e. no later rung's mean exceeds this rung's mean by more than ``tol``.
    The last rung trivially qualifies, so a value is always returned. Refuses below the
    full ladder (architect H2)."""
    _require_full_ladder(curve)
    rungs = curve.rungs
    for i, rung in enumerate(rungs):
        if all(later.mean_reward - rung.mean_reward <= tol + _TOL_EPS for later in rungs[i + 1 :]):
            return rung
    return rungs[-1]  # unreachable: rungs[-1] satisfies the predicate vacuously (all([]))


def min_useful_combo(
    curve: ScoreInformationCurve, *, tol: float = DEFAULT_SATURATION_TOL
) -> RungReward:
    """The cheapest (earliest-on-the-ladder) rung whose mean reward reaches the curve's
    ceiling within ``tol`` — the minimum information that buys essentially the best
    score. Refuses below the full ladder (architect H2)."""
    _require_full_ladder(curve)
    ceiling = max(r.mean_reward for r in curve.rungs)
    for rung in curve.rungs:
        if ceiling - rung.mean_reward <= tol + _TOL_EPS:
            return rung
    return curve.rungs[-1]  # unreachable: the ceiling rung itself always qualifies
