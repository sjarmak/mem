"""§11 pilot filter — the synthetic-task validity gate.

A synthetic task is only a valid eval task if it actually discriminates memory
benefit: the oracle arm (memory available) must beat the no-memory arm. A task
where the two tie measures nothing about memory and is rejected.

This is the same oracle-vs-no_memory relationship the §4 interpretation calls the
task-validity gate (DIV-3, see ``report.comparison``); the gate's ``EPSILON`` is
reused here so generation and reporting agree on what "beats" means. The decision
is pure arithmetic over rewards a pilot run produced — running the pilot is the
caller's job (runner + ``build_comparison``); this module only judges the result.
"""

from __future__ import annotations

from dataclasses import dataclass

from membench.report.comparison import EPSILON

# What the two rewards are means OVER, when the caller does not say. The phrase rides
# the verdict's ``reason``, because a delta is only readable against the set of trials it
# was averaged over: the same task reads as "does not discriminate" or "discriminates
# perfectly" depending on whether ungraded steps were in the denominator (mem-r6yzk B1).
DEFAULT_SCOPE = "the pilot run"


@dataclass(frozen=True)
class PilotVerdict:
    """The admission decision for one synthetic task. ``delta`` is
    ``oracle_reward - no_memory_reward``; ``accepted`` is true only when it exceeds
    ``epsilon`` (the task discriminates memory benefit). ``scope`` names the trial set
    the two rewards are means over, so the verdict cannot be read against the wrong
    denominator."""

    accepted: bool
    oracle_reward: float
    no_memory_reward: float
    delta: float
    epsilon: float
    reason: str
    scope: str = DEFAULT_SCOPE


def pilot_filter(
    *,
    oracle_reward: float,
    no_memory_reward: float,
    epsilon: float = EPSILON,
    scope: str = DEFAULT_SCOPE,
) -> PilotVerdict:
    """Decide whether a synthetic task is admitted, given the mean reward its oracle
    and no-memory arms scored in a pilot run.

    Admit only when ``oracle_reward`` beats ``no_memory_reward`` by more than
    ``epsilon``. A non-positive or within-``epsilon`` delta means the task does not
    discriminate memory benefit (DIV-3) and is rejected — never silently kept.

    ``scope`` names what the rewards average over and is stated in ``reason``. The two
    rejection causes are reported SEPARATELY: an oracle that gains nothing at all is a
    different defect from one that gains a little but less than the ≈ tolerance, and a
    single blended sentence hid a third cause entirely — a diluted denominator, which
    was reported as "does not discriminate" for tasks that discriminate perfectly where
    they are graded (mem-r6yzk B1)."""
    delta = oracle_reward - no_memory_reward
    accepted = delta > epsilon
    if accepted:
        reason = f"oracle beats no_memory over {scope} by {delta:.3f} > epsilon {epsilon:.3f}"
    elif delta <= 0:
        reason = (
            f"oracle does not beat no_memory over {scope} (delta {delta:.3f} <= 0): "
            f"memory confers no advantage where the task is graded, so it does not "
            f"discriminate memory benefit (DIV-3)"
        )
    else:
        reason = (
            f"oracle beats no_memory over {scope} by only {delta:.3f}, within epsilon "
            f"{epsilon:.3f}: the advantage is inside the ≈ tolerance, too small to "
            f"discriminate memory benefit (DIV-3)"
        )
    return PilotVerdict(
        accepted=accepted,
        oracle_reward=oracle_reward,
        no_memory_reward=no_memory_reward,
        delta=delta,
        epsilon=epsilon,
        reason=reason,
        scope=scope,
    )
