"""The three-arm grid's arithmetic: what it costs, what it scores, what voids it.

Everything here is PURE — the grid's price, its gate verdicts, its endpoint and the summary it
publishes — and nothing here spawns an agent or touches an account. The spending lives next door
in ``beads_arm_fire``, and the split is the point: the numbers a person authorizes money against,
and the numbers that come back, are computed by code that cannot itself spend.

Reads ``docs/prereg-beads-three-arm.md`` sections 3 (endpoint), 4 (the twelve gates) and 6 (paid
accounting). Where a threshold appears here it is the pre-registered one, frozen before the fire
and carried into the artifact, never re-derived from what the fire returned.

ZFC: arithmetic, set logic, and a seeded bootstrap. No model call, no judgment.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from membench.grading.paired_ci import PairedDeltaCI, paired_delta_ci
from membench.runner.arm_context import ARM_BEADS, ArmContextError
from membench.runner.beads_arm_grid import ArmCell
from membench.runner.e1_grid import EXECUTION_PROTOCOL_VERSION, LEGS_PER_CELL
from membench.runner.memory_arm import (
    ARM_BUILTIN,
    ARM_NAMES,
    ARM_NONE,
    arm,
    arm_context_words,
    arm_settings_fingerprint,
)
from membench.runner.tool_surface import (
    NATIVE_MEMORY_HOOK_MODE_DENY,
    RECOGNIZER_IMPLEMENTATION_VERSION,
    surface_fingerprint,
)
from membench.runner.toolreq_realagent import (
    VARIANT_NECESSARY,
    VARIANT_UNNECESSARY,
    ToolReqRealAgentTask,
)

# Bumped when anything about what a cell MEANS changes. Rides in the resume identity beside the
# corpus and the arm registry, so a cell bought under one protocol cannot be pooled into another.
#
# 2 (2026-09-21, mem-q34kw): the one-task pilot bought a single cell under protocol 1 and the
# receipt showed that cell could not have carried a contrast. Two changes, both of which change
# what a cell MEANS: the scored goal leg now runs the allowlist the shared protocol declares
# instead of the one the corpus authored, and the establish leg now carries an identical recording
# clause on every arm. The bump is what stops the protocol-1 cell being resumed into this grid.
PROTOCOL_VERSION = 2

# Repeats per variant (§6). Asymmetric on purpose: the necessary variant carries the endpoint and
# buys four, the unnecessary twin only has to show the arms do NOT separate and buys two. A table
# rather than a `repeats` argument, because the asymmetry is the design and a caller that could
# pass one number for both would price and spend a grid the pre-registration does not describe.
REPEATS_BY_VARIANT: Mapping[str, int] = {VARIANT_NECESSARY: 4, VARIANT_UNNECESSARY: 2}

# The paid preflight (§6, gate 4): one task, every arm, one repeat.
PREFLIGHT_TASKS = 1
PREFLIGHT_REPEATS = 1

# Void/re-buy headroom over the grid subtotal. A voided work_id costs all three arms, so the
# headroom is a fraction of the whole bill rather than of one arm's share.
VOID_HEADROOM = 0.15

# §6: the pre-registered minimum detectable effect at n = 32 pairs. Published, not enforced — a
# smaller true effect comes back null and the null is uninformative, which is a statement the
# write-up has to carry rather than a number a gate can rescue.
MIN_DETECTABLE_EFFECT = 0.20

# §4 thresholds, frozen before the fire.
NECESSITY_FLOOR = 0.2  # gate 1: `none` on `necessary` must be strictly BELOW this
SPECIFICITY_CEILING = 0.8  # gate 2: `none` on `unnecessary` must be strictly ABOVE this
UNMEASURED_BUDGET = 0.10  # gate 12: unmeasured legs per arm, strictly below

# One cell of the grid. `repeat` is in the key because repeats are bought, persisted and resumed
# one at a time: a key that stopped at `(arm, variant, work_id)` could not say which of four paid
# repeats a partial artifact already holds.
ArmGridKey = tuple[str, str, str, int]


class ArmPlanError(RuntimeError):
    """A grid that cannot be priced or scored as specified."""


def repeats_for(variant: str) -> int:
    """How many repeats ``variant`` buys. Unknown variants raise rather than defaulting: a twin
    the corpus grew that this table has never heard of would otherwise be priced at whatever the
    first entry happens to be, and bought at that price."""
    if variant not in REPEATS_BY_VARIANT:
        raise ArmPlanError(
            f"no repeat count pre-registered for variant {variant!r}; the grid buys "
            f"{dict(REPEATS_BY_VARIANT)}"
        )
    return REPEATS_BY_VARIANT[variant]


def work_ids_of(tasks: Sequence[ToolReqRealAgentTask], *, n_tasks: int | None = None) -> list[str]:
    """The work_ids this fire runs, sorted, capped at ``n_tasks``.

    Sorted and capped in ONE place, because the cap is the staging mechanism (ruling 1(b) buys one
    task first and the rest after) and a resume of the widened fire has to find the pilot's cell
    keys unchanged. A cap applied to an unsorted corpus would re-select a different first task on
    a corpus whose load order moved, and the pilot's money would buy a cell the grid then drops."""
    ids = sorted({task.work_id for task in tasks})
    if n_tasks is None:
        return ids
    if n_tasks < 1:
        raise ArmPlanError(f"--n-tasks must be at least 1, got {n_tasks}")
    return ids[:n_tasks]


def grid_keys(
    tasks: Sequence[ToolReqRealAgentTask], *, n_tasks: int | None = None
) -> list[ArmGridKey]:
    """Every ``(arm, variant, work_id, repeat)`` cell this fire will run, in execution order.

    Ordered work_id-major and arm-minor so a fire stopped early leaves COMPLETE work_ids rather
    than a third of many: the endpoint is paired per work_id, and a work_id observed on two arms
    contributes nothing to it. That ordering is what makes ruling 1(b)'s pilot a readable result
    instead of a fragment.

    This is the single derivation of the grid. ``priced_plan`` counts these keys rather than
    multiplying four numbers beside them, so the price a person authorizes and the cells the fire
    buys cannot disagree."""
    by_id: dict[str, set[str]] = {}
    for task in tasks:
        by_id.setdefault(task.work_id, set()).add(task.variant)
    keys: list[ArmGridKey] = []
    for work_id in work_ids_of(tasks, n_tasks=n_tasks):
        for variant in sorted(by_id[work_id]):
            for arm_name in ARM_NAMES:
                keys.extend(
                    (arm_name, variant, work_id, repeat) for repeat in range(repeats_for(variant))
                )
    return keys


def priced_plan(
    tasks: Sequence[ToolReqRealAgentTask], *, n_tasks: int | None = None
) -> dict[str, Any]:
    """What the fire WOULD spend, priced off the cells it will actually iterate.

    Sessions, not cells, is the unit a person authorizes: every cell is an establish/goal pair
    (``LEGS_PER_CELL``), and a plan quoting cells quotes half the bill."""
    keys = grid_keys(tasks, n_tasks=n_tasks)
    by_variant: dict[str, int] = {}
    for _arm, variant, _work_id, _repeat in keys:
        by_variant[variant] = by_variant.get(variant, 0) + LEGS_PER_CELL
    grid_sessions = sum(by_variant.values())
    preflight = PREFLIGHT_TASKS * len(ARM_NAMES) * PREFLIGHT_REPEATS * LEGS_PER_CELL
    subtotal = grid_sessions + preflight
    # Rounded UP, matching the pre-registration's own table (§6: 1158 + 174 = 1332).
    # Truncation would quote a ceiling one session below the authorized one, and the
    # number in the frozen document is the one the spend is authorized against.
    headroom = math.ceil(subtotal * VOID_HEADROOM)
    return {
        "arms": list(ARM_NAMES),
        "work_ids": work_ids_of(tasks, n_tasks=n_tasks),
        "n_tasks": len(work_ids_of(tasks, n_tasks=n_tasks)),
        "repeats_by_variant": dict(REPEATS_BY_VARIANT),
        "legs_per_cell": LEGS_PER_CELL,
        "cells": len(keys),
        "sessions_by_variant": by_variant,
        "preflight_sessions": preflight,
        "grid_sessions": grid_sessions,
        "subtotal_sessions": subtotal,
        "void_headroom_sessions": headroom,
        "authorization_ceiling_sessions": subtotal + headroom,
        "scored_goal_legs": len(keys),
        "min_detectable_effect": MIN_DETECTABLE_EFFECT,
        "n_pairs": len(work_ids_of(tasks, n_tasks=n_tasks)),
    }


# ---------------------------------------------------------------------------------------
# reading the cells back
# ---------------------------------------------------------------------------------------


def cell_key(cell: ArmCell) -> ArmGridKey:
    return (cell.arm, cell.variant, cell.work_id, cell.repeat)


def cell_row(cell: ArmCell) -> dict[str, Any]:
    """One cell as it is persisted. Tuples become lists so a reload compares equal to a row that
    made the round trip through JSON."""
    return {
        "arm": cell.arm,
        "work_id": cell.work_id,
        "variant": cell.variant,
        "repeat": cell.repeat,
        "passed": cell.passed,
        "engaged": cell.engaged,
        "leaked": cell.leaked,
        "establish_tool_names": list(cell.establish_tool_names),
        "endogenous_verbs": list(cell.endogenous_verbs),
        "establish_out_of_sandbox_operands": list(cell.establish_out_of_sandbox_operands),
        "establish_outcomes": list(cell.establish_outcomes),
        "goal_outcomes": list(cell.goal_outcomes),
        "goal_tool_names": list(cell.goal_tool_names),
        "native_reaches": cell.native_reaches,
        "pinned_off": cell.pinned_off,
        "paid": cell.paid,
        "status": cell.status,
        "detail": cell.detail,
    }


def cell_from_row(row: Mapping[str, Any]) -> ArmCell:
    """A persisted row back to a cell. A missing field is an error, not a default: a row that
    cannot say whether it was paid for, or which repeat it is, is a row a resume must not pool."""
    try:
        return ArmCell(
            arm=str(row["arm"]),
            work_id=str(row["work_id"]),
            variant=str(row["variant"]),
            repeat=int(row["repeat"]),
            passed=bool(row["passed"]),
            engaged=bool(row["engaged"]),
            leaked=bool(row["leaked"]),
            establish_tool_names=tuple(row["establish_tool_names"]),
            endogenous_verbs=tuple(row["endogenous_verbs"]),
            establish_out_of_sandbox_operands=tuple(
                row.get("establish_out_of_sandbox_operands", ())
            ),
            establish_outcomes=tuple(row["establish_outcomes"]),
            goal_outcomes=tuple(row["goal_outcomes"]),
            # Required, not defaulted: a row bought before the goal leg was instrumented is the
            # black box mem-0wpq8.1 opened, and pooling it would put unread cells in a read grid.
            goal_tool_names=tuple(row["goal_tool_names"]),
            native_reaches=int(row["native_reaches"]),
            pinned_off=bool(row["pinned_off"]),
            paid=bool(row["paid"]),
            status=str(row["status"]),
            detail=str(row.get("detail", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ArmPlanError(f"not a readable cell row: {exc}") from exc


def measured(cells: Sequence[ArmCell]) -> list[ArmCell]:
    """The cells that returned a measurement. A timed-out or errored cell is UNMEASURED and is
    never a scored zero (gate 12): it is absent from every rate and counted in the budget."""
    return [cell for cell in cells if cell.status == "ok"]


def success_rates(
    cells: Sequence[ArmCell], *, arm_name: str, variant: str, exclude: Sequence[str] = ()
) -> dict[str, float]:
    """Per-work_id goal-leg success rate for one arm on one variant.

    The rate is over that work_id's REPEATS, which is what makes the per-task delta a value on the
    lattice ``{-1, -0.75, ..., 1}`` rather than a 0/1 coin flip; §6's power statement is computed
    on exactly this. Voided work_ids are dropped before the rate exists, not after."""
    voided = set(exclude)
    by_id: dict[str, list[bool]] = {}
    for cell in measured(cells):
        if cell.arm != arm_name or cell.variant != variant or cell.work_id in voided:
            continue
        by_id.setdefault(cell.work_id, []).append(cell.passed)
    return {work_id: sum(passes) / len(passes) for work_id, passes in by_id.items() if passes}


def void_work_ids(cells: Sequence[ArmCell]) -> dict[str, str]:
    """The work_ids this run may not score, and why. Voided ACROSS ALL THREE ARMS (§4 gates 5
    and 7): a channel or a substrate leak found on one arm's cell is open on that work_id's other
    arms too, and keeping their deltas would pair a clean arm against a contaminated one.

    Two triggers, both mechanical:

    * a memory verb on an arm that has no memory substrate (gate 7) — the floor or the comparator
      reached the real bd, so the arms are not the machines the registry says they are;
    * a recorded reach into ``$CLAUDE_CONFIG_DIR`` on an arm whose hook DENIES them (gate 5) — the
      reach was blocked, so nothing is known to have leaked, and that is exactly why it voids
      rather than fails: an agent that went looking for the session transcript found a channel the
      recognizer may only partly see, and the conservative reading is to buy the work_id again.

    A floor-arm PASS is deliberately not a trigger. It is the necessity floor's own measurement
    (gate 1), and voiding on it would drive that gate's rate to zero by construction — the gate
    would then pass on every corpus, including one that needs no memory at all."""
    reasons: dict[str, str] = {}
    for cell in measured(cells):
        one = arm(cell.arm)
        if cell.endogenous_verbs and not one.verbs:
            reasons.setdefault(
                cell.work_id,
                f"{cell.arm}: memory verb(s) {sorted(set(cell.endogenous_verbs))} on an arm with "
                "no memory substrate (gate 7, substrate exclusivity)",
            )
        elif cell.native_reaches and one.hook_mode == NATIVE_MEMORY_HOOK_MODE_DENY:
            reasons.setdefault(
                cell.work_id,
                f"{cell.arm}: {cell.native_reaches} reach(es) into the config dir on a denied arm "
                "(gate 5, leak gate)",
            )
    return reasons


def endpoint(
    cells: Sequence[ArmCell],
    *,
    treatment: str,
    baseline: str,
    variant: str = VARIANT_NECESSARY,
    exclude: Sequence[str] = (),
    seed: int = 0,
) -> dict[str, Any]:
    """The pre-registered contrast (§3): paired per-work_id goal-leg rate deltas.

    ``matched`` is what the endpoint is READ on and ``itt`` is published beside it as the
    conservative bound; both statistics are computed, and the median is the primary. Everything is
    emitted together so no reading is chosen after seeing the others.

    A population with no pairs is reported as such rather than raising: a pilot that bought one
    work_id and voided it has an empty endpoint, and that is a result the artifact should state."""
    base = success_rates(cells, arm_name=baseline, variant=variant, exclude=exclude)
    treat = success_rates(cells, arm_name=treatment, variant=variant, exclude=exclude)
    readings: dict[str, Any] = {}
    for population in ("matched", "itt"):
        for statistic in ("median", "mean"):
            label = f"{population}_{statistic}"
            try:
                result = paired_delta_ci(
                    base,
                    treat,
                    population=population,
                    statistic=statistic,
                    seed=seed,
                )
            except ValueError as exc:
                readings[label] = {"unavailable": str(exc)}
                continue
            readings[label] = _ci_row(result)
    return {
        "contrast": f"{treatment} - {baseline}",
        "variant": variant,
        "primary": "matched_median",
        "readings": readings,
        "per_work_id": {"baseline": base, "treatment": treat},
    }


def _ci_row(result: PairedDeltaCI) -> dict[str, Any]:
    return {
        "delta": result.delta,
        "ci_low": result.ci_low,
        "ci_high": result.ci_high,
        "n_pairs": result.n_pairs,
        "n_imputed_zero": result.n_imputed_zero,
        "population": result.population,
        "statistic": result.statistic,
        "excludes_zero": result.ci_low > 0.0 or result.ci_high < 0.0,
    }


def establish_mechanism(cells: Sequence[ArmCell]) -> dict[str, Any]:
    """What the establish legs DID, reported separately from the endpoint (§3).

    Kept out of the endpoint because the establish leg is instructed to acknowledge state, so its
    memory action is not a choice: pooling it halves the measured effect, which mem-eg850 already
    paid for. Here it answers the different question of whether the mechanism fired at all."""
    block: dict[str, Any] = {}
    for arm_name in ARM_NAMES:
        rows = [cell for cell in measured(cells) if cell.arm == arm_name]
        outcomes: dict[str, int] = {}
        for cell in rows:
            for outcome in cell.establish_outcomes:
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
        block[arm_name] = {
            "cells": len(rows),
            "engaged_cells": sum(1 for cell in rows if cell.engaged),
            "establish_outcomes": dict(sorted(outcomes.items())),
            "establish_tool_names": sorted(
                {name for cell in rows for name in cell.establish_tool_names}
            ),
            "goal_tool_names": sorted({name for cell in rows for name in cell.goal_tool_names}),
        }
    return block


# The bd answers that mean the establish leg WROTE and the goal leg READ (gate 4).
_WROTE = frozenset({"remembered", "updated"})
_READ = frozenset({"returned", "recalled"})


def discovery(cells: Sequence[ArmCell]) -> dict[str, Any]:
    """Gate 4: did the mechanism get reached for at all, on either arm that has one.

    The gate this experiment exists to keep honest. Without it a grid of zeros reads as "memory
    did not help" when it means "memory was never called", which is the mem-lvp.24 null this
    family of gates was built after."""
    # The NECESSARY variant only. The twin is authored so the goal needs no memory, so an agent
    # that never reached there behaved correctly; counting its cells in the denominator would
    # dilute the reach rate with cells where a reach was not called for, and the diluted number
    # is the one a reader would quote.
    scored = [cell for cell in measured(cells) if cell.variant == VARIANT_NECESSARY]
    beads = [cell for cell in scored if cell.arm == ARM_BEADS]
    builtin = [cell for cell in scored if cell.arm == ARM_BUILTIN]
    wrote = sum(1 for cell in beads if _WROTE & set(cell.establish_outcomes))
    read = sum(1 for cell in beads if _READ & set(cell.goal_outcomes))
    engaged = sum(1 for cell in builtin if cell.engaged)
    return {
        "variant": VARIANT_NECESSARY,
        "beads_establish_write_cells": wrote,
        "beads_goal_read_cells": read,
        "builtin_engaged_cells": engaged,
        "beads_goal_reach_rate": (read / len(beads)) if beads else None,
        "passed": bool(wrote and read and engaged),
        "verdict": (
            "reached"
            if wrote and read and engaged
            else "UNMEASURED: the mechanism was never reached for, so a zero here is not a null"
        ),
    }


def _rate(
    cells: Sequence[ArmCell], *, arm_name: str, variant: str, exclude: Sequence[str]
) -> float | None:
    rates = success_rates(cells, arm_name=arm_name, variant=variant, exclude=exclude)
    return (sum(rates.values()) / len(rates)) if rates else None


def gates(
    cells: Sequence[ArmCell], *, voided: Mapping[str, str], bd_capability: str | None = None
) -> dict[str, Any]:
    """Every gate of §4 this artifact can decide from the cells themselves.

    Gates 3, 8, 10 and 11 are enforced where they can refuse a SPEND — at mint, at leg dispatch
    and in the separation report — and appear here only as what the cells observed, because a gate
    that first fails in a post-hoc summary has already been paid for. Each entry says which it is.

    A gate with nothing to judge reports ``null``, never ``true``. An unbought grid must not read
    as a grid that passed."""
    scored = measured(cells)
    floor = _rate(cells, arm_name=ARM_NONE, variant=VARIANT_NECESSARY, exclude=tuple(voided))
    ceiling = _rate(cells, arm_name=ARM_NONE, variant=VARIANT_UNNECESSARY, exclude=tuple(voided))
    specificity = endpoint(
        cells,
        treatment=ARM_BEADS,
        baseline=ARM_NONE,
        variant=VARIANT_UNNECESSARY,
        exclude=tuple(voided),
    )
    separated = specificity["readings"].get("matched_median", {}).get("excludes_zero")
    unmeasured = {arm_name: _unmeasured_share(cells, arm_name=arm_name) for arm_name in ARM_NAMES}
    # The arms whose hook DENIES a config-dir reach: the two the pin and the leak gate judge.
    pinned = [cell for cell in scored if arm(cell.arm).hook_mode == NATIVE_MEMORY_HOOK_MODE_DENY]
    return {
        "necessity_floor": {
            "threshold": NECESSITY_FLOOR,
            "observed": floor,
            "passed": None if floor is None else floor < NECESSITY_FLOOR,
        },
        "specificity_ceiling": {
            "threshold": SPECIFICITY_CEILING,
            "observed": ceiling,
            "arms_separate_on_unnecessary": separated,
            "passed": (
                None
                if ceiling is None or separated is None
                else ceiling > SPECIFICITY_CEILING and not separated
            ),
        },
        "pin_held": {
            "enforced_at": "mint and every leg; here as observed",
            "cells": len(pinned),
            "passed": all(cell.pinned_off for cell in pinned) if pinned else None,
        },
        "discovery": discovery(cells),
        "leak": {
            "denied_arm_reaches": sum(cell.native_reaches for cell in pinned),
            "voided_work_ids": dict(voided),
            "passed": not any(cell.native_reaches for cell in pinned) if pinned else None,
        },
        "pairing_intact": _pairing(cells, voided=voided),
        "substrate_exclusivity": {
            "verbs_on_storeless_arms": sorted(
                {
                    verb
                    for cell in scored
                    if not arm(cell.arm).verbs
                    for verb in cell.endogenous_verbs
                }
            ),
            "passed": (
                not any(cell.endogenous_verbs for cell in scored if not arm(cell.arm).verbs)
                if scored
                else None
            ),
        },
        "presentation_parity": _presentation_parity(bd_capability),
        "one_instrument": {
            "enforced_at": "per-leg cli_version check and the resume identity digest",
            "arm_settings_fingerprint": arm_settings_fingerprint(),
        },
        "instrument_agreement": {
            "enforced_at": "beads_arm_grid.engagement_of, argv counter vs shim receipts",
        },
        "unmeasured_budget": {
            "threshold": UNMEASURED_BUDGET,
            "observed": unmeasured,
            "passed": (
                None
                if all(share is None for share in unmeasured.values())
                else all(
                    share is None or share < UNMEASURED_BUDGET for share in unmeasured.values()
                )
            ),
        },
        # §3's assertion, computed rather than promised: nothing in this artifact scores an
        # establish leg, so there is no halved number for a reader to pick up.
        "n_establish_legs_scored": 0,
    }


def _presentation_parity(bd_capability: str | None) -> dict[str, Any]:
    """Gate 9 as OBSERVED. The enforcement is in ``render_arm_separation``, which refuses before
    any spend; here the word counts are reported so the artifact carries them.

    The beads arm's paragraph is captured from what ``bd init`` shipped, so a summary written
    without it reports the counts as unavailable rather than raising. Scoring cells already
    bought must not fail on a presentation input that only the pre-spend path needs — the
    artifact for money already spent is the last thing that should be unwritable."""
    try:
        words = {name: arm_context_words(name, bd_capability=bd_capability) for name in ARM_NAMES}
    except ArmContextError as exc:
        return {
            "enforced_at": "arm_separation.render_arm_separation, before any spend",
            "arm_context_words": None,
            "unavailable": str(exc),
            "length_confound_is_unconditional": True,
        }
    return {
        "enforced_at": "arm_separation.render_arm_separation, before any spend",
        "arm_context_words": words,
        "length_confound_is_unconditional": True,
    }


def _unmeasured_share(cells: Sequence[ArmCell], *, arm_name: str) -> float | None:
    rows = [cell for cell in cells if cell.arm == arm_name]
    if not rows:
        return None
    return sum(1 for cell in rows if cell.status != "ok") / len(rows)


def _pairing(cells: Sequence[ArmCell], *, voided: Mapping[str, str]) -> dict[str, Any]:
    """Gate 6: the endpoint is read on ``matched``, where imputation cannot happen."""
    reading = endpoint(cells, treatment=ARM_BEADS, baseline=ARM_NONE, exclude=tuple(voided))[
        "readings"
    ]["matched_median"]
    imputed = reading.get("n_imputed_zero")
    return {
        "population": "matched",
        "n_pairs": reading.get("n_pairs"),
        "n_imputed_zero": imputed,
        "passed": None if imputed is None else imputed == 0,
    }


def summarize(
    cells: Sequence[ArmCell],
    *,
    model: str,
    dry_run: bool,
    n_tasks: int | None = None,
    work_ids: Sequence[str] = (),
    cli_version: str = "",
    corpus: str = "",
    bd_capability: str | None = None,
) -> dict[str, Any]:
    """The whole artifact: identity, plan, gates, endpoint, mechanism, cells.

    The identity fields come first because they are what a resume matches against, and a reader
    who cannot tell which rig produced a number should not have to scroll to find out."""
    voided = void_work_ids(cells)
    scored = measured(cells)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "execution_protocol": EXECUTION_PROTOCOL_VERSION,
        "model": model,
        "dry_run": dry_run,
        "cli_version": cli_version,
        "corpus_fingerprint": corpus,
        "arm_settings_fingerprint": arm_settings_fingerprint(),
        "surface_fingerprint": surface_fingerprint(),
        "recognizer_version": RECOGNIZER_IMPLEMENTATION_VERSION,
        "n_tasks": n_tasks,
        "work_ids": list(work_ids),
        "repeats_by_variant": dict(REPEATS_BY_VARIANT),
        "n_cells": len(cells),
        "n_measured_cells": len(scored),
        "n_paid_cells": sum(1 for cell in cells if cell.paid),
        "voided_work_ids": voided,
        "endpoint": endpoint(cells, treatment=ARM_BEADS, baseline=ARM_NONE, exclude=tuple(voided)),
        "second_contrast": endpoint(
            cells, treatment=ARM_BEADS, baseline=ARM_BUILTIN, exclude=tuple(voided)
        ),
        "gates": gates(cells, voided=voided, bd_capability=bd_capability),
        "establish_mechanism": establish_mechanism(cells),
        "min_detectable_effect": MIN_DETECTABLE_EFFECT,
        "cells": [cell_row(cell) for cell in cells],
    }


__all__ = [
    "MIN_DETECTABLE_EFFECT",
    "NECESSITY_FLOOR",
    "PREFLIGHT_REPEATS",
    "PREFLIGHT_TASKS",
    "PROTOCOL_VERSION",
    "REPEATS_BY_VARIANT",
    "SPECIFICITY_CEILING",
    "UNMEASURED_BUDGET",
    "VOID_HEADROOM",
    "ArmGridKey",
    "ArmPlanError",
    "cell_from_row",
    "cell_key",
    "cell_row",
    "discovery",
    "endpoint",
    "establish_mechanism",
    "gates",
    "grid_keys",
    "measured",
    "priced_plan",
    "repeats_for",
    "success_rates",
    "summarize",
    "void_work_ids",
    "work_ids_of",
]
