"""The capture turn's arithmetic: which cells it buys, and what the establish leg is allowed
to claim.

Reads ``docs/prereg-beads-capture.md``. The three-arm grid next door asks whether beads changes
what an agent can DO; this asks the prior question, whether an agent reaches for it at all. Four
pilots have answered no for the shipped text, so the flywheel's turn is a beads build and the
endpoint is the establish leg alone.

Everything here is PURE: a seeded draw, set logic, and a proportion with an interval. Nothing
here spawns an agent or touches an account; the spending is ``beads_capture_fire``, and the split
is the same one the three-arm pair keeps, for the same reason.

ZFC: arithmetic and set logic. No model call, no judgment.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any

from membench.runner.arm_context import ARM_BEADS
from membench.runner.beads_arm_grid import PROTOCOL_CAPTURE, ArmCell
from membench.runner.beads_arm_plan import ArmGridKey, ArmPlanError
from membench.runner.memory_arm import ARM_BUILTIN, ARM_NONE
from membench.runner.toolreq_realagent import VARIANT_NECESSARY, ToolReqRealAgentTask

# §4, frozen for the life of the flywheel. The seed and the count are here rather than at the
# call site because they ARE the registration: a draw whose seed a caller could pass is a draw
# that can be repeated until it flatters a candidate, which is the thing §4 writes the eight
# work_ids down in the document to prevent.
CAPTURE_SEED = 20260921
CAPTURE_SAMPLE_N = 8
CAPTURE_VARIANT = VARIANT_NECESSARY

# §6: 8 tasks x 2 repeats = 16 sessions per arm. One leg per cell, so cells and sessions are the
# same number here -- unlike the three-arm grid, where every cell is a pair.
CAPTURE_REPEATS = 2
CAPTURE_LEGS_PER_CELL = 1

# The arms a capture turn can buy, and what each one costs to re-buy. §6 buys the candidate every
# turn and reuses the other three, because only the treatment depends on the beads build.
CAPTURE_ARMS = (ARM_BEADS, ARM_BUILTIN, ARM_NONE)

# §4.1 of the three-arm registration is not in force here: there is no goal leg, so there is no
# success rate to gate on. What remains is §7, and every condition in it is enforced in the mint
# or the stream by a guard that raises, not by a number compared here.


def capture_work_ids(tasks: Sequence[ToolReqRealAgentTask]) -> list[str]:
    """The eight work_ids §4 draws, in sorted order.

    Drawn from the SORTED full id set with a frozen seed, so the draw is a function of the corpus
    and nothing else. A corpus that no longer carries all eight raises rather than drawing a
    replacement: a turn compared against a previous turn's eight must run the same eight, and a
    silent substitution would publish two different samples under one series."""
    ids = sorted({task.work_id for task in tasks if task.variant == CAPTURE_VARIANT})
    if len(ids) < CAPTURE_SAMPLE_N:
        raise ArmPlanError(
            f"the corpus carries {len(ids)} {CAPTURE_VARIANT!r} work_id(s); the registered sample "
            f"draws {CAPTURE_SAMPLE_N}"
        )
    return sorted(random.Random(CAPTURE_SEED).sample(ids, CAPTURE_SAMPLE_N))


def capture_keys(
    tasks: Sequence[ToolReqRealAgentTask], *, arms: Sequence[str] = (ARM_BEADS,)
) -> list[ArmGridKey]:
    """Every ``(arm, variant, work_id, repeat)`` cell a capture turn runs, in execution order.

    Ordered work_id-major and arm-minor for the reason ``grid_keys`` is: a turn stopped early
    leaves COMPLETE work_ids rather than a fraction of many.

    ``arms`` defaults to the treatment alone because that is what a turn buys. The floor and the
    comparator are bought once per runtime version and reused (§6), which is a separate, named
    invocation rather than something a candidate turn does by default and pays for again."""
    unknown = [one for one in arms if one not in CAPTURE_ARMS]
    if unknown:
        raise ArmPlanError(f"not capture arms: {unknown}; the registration names {CAPTURE_ARMS}")
    if not arms:
        raise ArmPlanError("a capture turn with no arms buys nothing and measures nothing")
    ordered = [one for one in CAPTURE_ARMS if one in arms]
    return [
        (arm_name, CAPTURE_VARIANT, work_id, repeat)
        for work_id in capture_work_ids(tasks)
        for arm_name in ordered
        for repeat in range(CAPTURE_REPEATS)
    ]


def capture_plan(
    tasks: Sequence[ToolReqRealAgentTask], *, arms: Sequence[str] = (ARM_BEADS,)
) -> dict[str, Any]:
    """What a capture turn WOULD spend, counted off the cells it will iterate rather than
    multiplied beside them, so the price authorized and the cells bought cannot disagree."""
    keys = capture_keys(tasks, arms=arms)
    return {
        "protocol": PROTOCOL_CAPTURE,
        "arms": [one for one in CAPTURE_ARMS if one in arms],
        "variant": CAPTURE_VARIANT,
        "work_ids": capture_work_ids(tasks),
        "sample_seed": CAPTURE_SEED,
        "repeats": CAPTURE_REPEATS,
        "legs_per_cell": CAPTURE_LEGS_PER_CELL,
        "cells": len(keys),
        "sessions": len(keys) * CAPTURE_LEGS_PER_CELL,
    }


def reached(cell: ArmCell) -> bool:
    """§3: did this leg call its memory facility at all?

    Read per arm because the facilities are different things. For an arm with a store, an
    execution of `bd` booked in the leg's own receipts (`ArmCell.bd_invocations`), which is bd's
    side of the call and so reads the same on every harness; for the comparator, a write the
    native-memory hook saw. Not a union over both: a `bd` call on the comparator arm would be a
    call to a store it does not have, and a native-memory write on the treatment arm is §7's
    invalidating reach, not a capture. Both are refused upstream, and neither is quietly counted
    as a reach here."""
    if cell.protocol != PROTOCOL_CAPTURE:
        raise ArmPlanError(
            f"cell {cell.arm}/{cell.work_id}#{cell.repeat} was bought under {cell.protocol!r}; "
            "the capture endpoint is defined on the establish leg a capture turn buys"
        )
    if cell.arm == ARM_BUILTIN:
        return cell.native_reaches > 0
    return cell.bd_invocations > 0


def capture_rates(cells: Sequence[ArmCell], *, arm_name: str) -> dict[str, Any]:
    """`reached` and `engaged` for one arm, as counts and proportions over its MEASURED cells.

    Unmeasured cells are excluded and counted, never scored as a zero: a timed-out leg did not
    decline to reach, it was never observed. §8 reports the interval; it is computed by the
    caller from these counts, so this stays arithmetic a person can check by eye."""
    mine = [cell for cell in cells if cell.arm == arm_name]
    ok = [cell for cell in mine if cell.status == "ok"]
    return {
        "arm": arm_name,
        "cells": len(mine),
        "unmeasured": len(mine) - len(ok),
        "measured": len(ok),
        "reached": sum(1 for cell in ok if reached(cell)),
        "engaged": sum(1 for cell in ok if cell.engaged),
        "reached_rate": (sum(1 for cell in ok if reached(cell)) / len(ok)) if ok else None,
        "engaged_rate": (sum(1 for cell in ok if cell.engaged) / len(ok)) if ok else None,
    }


def capture_summary(cells: Sequence[ArmCell], *, arms: Sequence[str]) -> dict[str, Any]:
    """Per-arm rates for the arms this turn bought, in the registration's arm order."""
    ordered = [one for one in CAPTURE_ARMS if one in arms]
    by_arm: Mapping[str, Any] = {one: capture_rates(cells, arm_name=one) for one in ordered}
    return {"protocol": PROTOCOL_CAPTURE, "arms": ordered, "by_arm": dict(by_arm)}


__all__ = [
    "CAPTURE_ARMS",
    "CAPTURE_LEGS_PER_CELL",
    "CAPTURE_REPEATS",
    "CAPTURE_SAMPLE_N",
    "CAPTURE_SEED",
    "CAPTURE_VARIANT",
    "capture_keys",
    "capture_plan",
    "capture_rates",
    "capture_summary",
    "capture_work_ids",
    "reached",
]
