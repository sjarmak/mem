"""The leg plans a cell can spend: which sessions run, in which order, on one shared store.

A plan is a tuple of role names. The PAIR is the E1 ladder's cell and the adoption harness's
default: establish a fact in one session, act on it in a fresh one. The TRIAL adds the two
sessions the Beads version-history work (beads#6154, mem-605zn) asks an agent to be measured on
between and after those: a session told the fact CHANGED, and a session that only ever knew the
old version and tries to write after the new one landed.

This module holds nothing but the names. It exists so the grid (``e1_grid``), the scorer
(``e1_reliability``) and the scheduler (``bd_experiment``) can all import the same plan without
importing each other; the grid already imports the scorer, so the plan could not live in either.

Order is the hypothesis. Each role is a session on a store that carries whatever the roles before
it wrote, with the cwd wiped between neighbours, so a plan read out of order is a different
experiment with the same names.
"""

from __future__ import annotations

PAIR_ROLES: tuple[str, ...] = ("establish", "goal")
TRIAL_ROLES: tuple[str, ...] = ("establish", "revise", "goal", "stale_writer")

LEG_PLANS: dict[str, tuple[str, ...]] = {"pair": PAIR_ROLES, "trial": TRIAL_ROLES}

# The one role every plan shares and the one whose action is scored: the fresh session that must
# act on the current version. Named once so nothing indexes a plan by a literal position.
GOAL_ROLE = "goal"


def plan_name(plan: tuple[str, ...]) -> str:
    """The name a plan is frozen under, or a refusal for a tuple that is not a known plan."""
    for name, roles in LEG_PLANS.items():
        if roles == plan:
            return name
    raise ValueError(f"unknown leg plan {plan!r}; the plans are {sorted(LEG_PLANS)}")
