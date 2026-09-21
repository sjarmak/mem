"""The three-arm grid's arithmetic: the price it quotes, the gates it decides, the endpoint.

Pure, free and hermetic — nothing here mints a store or spawns an agent, which is the point of
the module under test. The tests that need a real mint live in ``test_beads_arm_grid``.
"""

from __future__ import annotations

import pytest

from membench.runner.beads_arm_grid import ArmCell
from membench.runner.beads_arm_plan import (
    ArmPlanError,
    cell_from_row,
    cell_key,
    cell_row,
    discovery,
    endpoint,
    gates,
    grid_keys,
    priced_plan,
    repeats_for,
    success_rates,
    summarize,
    void_work_ids,
    work_ids_of,
)
from membench.runner.memory_arm import ARM_BEADS, ARM_BUILTIN, ARM_NAMES, ARM_NONE
from membench.runner.toolreq_corpus import twin_tasks
from membench.runner.toolreq_realagent import (
    VARIANT_NECESSARY,
    VARIANT_UNNECESSARY,
    VARIANT_UNNECESSARY_BY_ABSENCE,
    ToolReqRealAgentTask,
    adapt_sequence,
)
from tests.toolreq_helpers import toolreq_seq

# The pre-registration's own paid table (§6), typed out so a change to the arithmetic has to
# disagree with the frozen document rather than quietly re-derive a new price.
PREREG_TASKS = 32
PREREG_NECESSARY_SESSIONS = 768
PREREG_UNNECESSARY_SESSIONS = 384
PREREG_PREFLIGHT_SESSIONS = 6
PREREG_SUBTOTAL_SESSIONS = 1158
PREREG_CEILING_SESSIONS = 1332


def corpus(n: int) -> list[ToolReqRealAgentTask]:
    """``n`` work_ids, twinned — the shape ``load_twin_corpus`` returns."""
    return twin_tasks([adapt_sequence(toolreq_seq(f"w-t{i}")) for i in range(n)])


def cell(
    arm: str,
    work_id: str,
    *,
    variant: str = VARIANT_NECESSARY,
    repeat: int = 0,
    passed: bool = False,
    engaged: bool = False,
    verbs: tuple[str, ...] = (),
    reaches: int = 0,
    establish_outcomes: tuple[str, ...] = (),
    goal_outcomes: tuple[str, ...] = (),
    status: str = "ok",
) -> ArmCell:
    return ArmCell(
        arm=arm,
        work_id=work_id,
        variant=variant,
        repeat=repeat,
        passed=passed,
        engaged=engaged,
        leaked=passed and not engaged,
        establish_tool_names=("Bash",),
        endogenous_verbs=verbs,
        establish_outcomes=establish_outcomes,
        goal_outcomes=goal_outcomes,
        native_reaches=reaches,
        pinned_off=arm != ARM_BUILTIN,
        paid=True,
        status=status,
    )


def arm_block(
    arm: str,
    work_id: str,
    *,
    passes: int,
    repeats: int = 4,
    variant: str = VARIANT_NECESSARY,
    engaged: bool = False,
    verbs: tuple[str, ...] = (),
    reaches: int = 0,
    establish_outcomes: tuple[str, ...] = (),
    goal_outcomes: tuple[str, ...] = (),
) -> list[ArmCell]:
    """One arm's repeats on one work_id, ``passes`` of which succeeded."""
    return [
        cell(
            arm,
            work_id,
            variant=variant,
            repeat=i,
            passed=i < passes,
            engaged=engaged,
            verbs=verbs,
            reaches=reaches,
            establish_outcomes=establish_outcomes,
            goal_outcomes=goal_outcomes,
        )
        for i in range(repeats)
    ]


def scored_grid(
    *, beads: int, none: int, builtin: int, unnecessary: int = 2, work_ids: int = 4
) -> list[ArmCell]:
    """A fully bought grid: per work_id, each arm passing a fixed count of its four necessary
    repeats, and every arm passing ``unnecessary`` of the twin's two."""
    cells: list[ArmCell] = []
    for i in range(work_ids):
        work_id = f"w-t{i}"
        cells += arm_block(
            ARM_BEADS,
            work_id,
            passes=beads,
            engaged=True,
            establish_outcomes=("remembered",),
            goal_outcomes=("returned",),
        )
        cells += arm_block(ARM_NONE, work_id, passes=none)
        cells += arm_block(ARM_BUILTIN, work_id, passes=builtin, engaged=True)
        for arm_name in ARM_NAMES:
            cells += arm_block(
                arm_name,
                work_id,
                passes=unnecessary,
                repeats=2,
                variant=VARIANT_UNNECESSARY,
            )
    return cells


# ---------------------------------------------------------------------------------------
# the price
# ---------------------------------------------------------------------------------------


def test_the_price_counts_the_cells_the_fire_iterates() -> None:
    """One derivation of the grid, not two. A plan multiplied out beside ``grid_keys`` can quote
    a grid the fire does not run — and the quote is what a person authorizes money against."""
    tasks = corpus(3)
    plan = priced_plan(tasks)
    keys = grid_keys(tasks)
    assert plan["cells"] == len(keys)
    assert plan["grid_sessions"] == len(keys) * plan["legs_per_cell"]
    assert sum(plan["sessions_by_variant"].values()) == plan["grid_sessions"]


def test_the_full_corpus_prices_the_pre_registered_paid_shape() -> None:
    plan = priced_plan(corpus(PREREG_TASKS))
    assert plan["n_tasks"] == PREREG_TASKS
    assert plan["sessions_by_variant"][VARIANT_NECESSARY] == PREREG_NECESSARY_SESSIONS
    assert plan["sessions_by_variant"][VARIANT_UNNECESSARY] == PREREG_UNNECESSARY_SESSIONS
    assert plan["preflight_sessions"] == PREREG_PREFLIGHT_SESSIONS
    assert plan["subtotal_sessions"] == PREREG_SUBTOTAL_SESSIONS
    assert plan["authorization_ceiling_sessions"] == PREREG_CEILING_SESSIONS
    assert plan["n_pairs"] == PREREG_TASKS


def test_the_pilot_buys_cells_of_the_widened_grid_and_not_a_fragment() -> None:
    """Ruling 1(b) stages the spend: one task, then the rest against the same artifact. The
    pilot's keys have to BE keys of the full grid, or its money buys cells the resume drops."""
    tasks = corpus(4)
    pilot = grid_keys(tasks, n_tasks=1)
    full = grid_keys(tasks)
    assert set(pilot) < set(full)
    assert len({work_id for _arm, _variant, work_id, _repeat in pilot}) == 1
    assert work_ids_of(tasks, n_tasks=1) == work_ids_of(tasks)[:1]


def test_every_cell_of_a_work_id_is_bought_before_the_next_one_starts() -> None:
    """Execution order, and it is load-bearing: the endpoint pairs per work_id, so a fire stopped
    early has to leave complete work_ids rather than one arm of many."""
    seen: list[str] = []
    for _arm, _variant, work_id, _repeat in grid_keys(corpus(3)):
        if not seen or seen[-1] != work_id:
            assert work_id not in seen
            seen.append(work_id)
    assert len(seen) == 3


def test_a_variant_nobody_pre_registered_a_repeat_count_for_is_refused() -> None:
    """``unnecessary-by-absence`` is a variant this corpus can already produce and this grid does
    not buy. Priced at whatever the table's first entry happens to be, it would be bought."""
    with pytest.raises(ArmPlanError, match="no repeat count pre-registered"):
        repeats_for(VARIANT_UNNECESSARY_BY_ABSENCE)


def test_a_zero_task_fire_is_refused_rather_than_priced_at_nothing() -> None:
    with pytest.raises(ArmPlanError, match="at least 1"):
        work_ids_of(corpus(2), n_tasks=0)


# ---------------------------------------------------------------------------------------
# the round trip a resume reads cells back through
# ---------------------------------------------------------------------------------------


def test_a_cell_survives_the_round_trip_it_is_resumed_through() -> None:
    one = cell(ARM_BEADS, "w-t0", passed=True, engaged=True, goal_outcomes=("returned",))
    assert cell_from_row(cell_row(one)) == one
    assert cell_key(one) == (ARM_BEADS, VARIANT_NECESSARY, "w-t0", 0)


def test_a_row_that_cannot_say_whether_it_was_paid_for_is_refused() -> None:
    row = cell_row(cell(ARM_BEADS, "w-t0"))
    del row["paid"]
    with pytest.raises(ArmPlanError, match="not a readable cell row"):
        cell_from_row(row)


# ---------------------------------------------------------------------------------------
# voiding
# ---------------------------------------------------------------------------------------


def test_a_memory_verb_on_a_storeless_arm_voids_the_work_id_across_all_three_arms() -> None:
    cells = [
        *arm_block(ARM_BEADS, "w-t0", passes=4),
        *arm_block(ARM_NONE, "w-t0", passes=0, verbs=("recall",)),
        *arm_block(ARM_BUILTIN, "w-t0", passes=1),
        *arm_block(ARM_BEADS, "w-t1", passes=4),
        *arm_block(ARM_NONE, "w-t1", passes=0),
        *arm_block(ARM_BUILTIN, "w-t1", passes=1),
    ]
    voided = void_work_ids(cells)
    assert set(voided) == {"w-t0"}
    assert "substrate exclusivity" in voided["w-t0"]
    # Across all three arms, not only the one that reached: the clean arms of a contaminated
    # work_id cannot be paired against the contaminated one.
    for arm_name in ARM_NAMES:
        rates = success_rates(
            cells, arm_name=arm_name, variant=VARIANT_NECESSARY, exclude=tuple(voided)
        )
        assert set(rates) == {"w-t1"}


def test_a_blocked_config_dir_reach_voids_on_a_denied_arm_and_not_on_the_comparator() -> None:
    """Gate 5 voids conservatively. The reach WAS blocked, so nothing is known to have leaked —
    and that is why it voids rather than fails: an agent that went looking for the session
    transcript found a channel the recognizer may only partly see. The comparator's reaches are
    into its own memory, which is the thing it is the comparator for."""
    denied = arm_block(ARM_NONE, "w-t0", passes=0, reaches=1)
    assert set(void_work_ids(denied)) == {"w-t0"}
    comparator = arm_block(ARM_BUILTIN, "w-t1", passes=1, reaches=3)
    assert void_work_ids(comparator) == {}


def test_a_floor_pass_does_not_void_because_it_is_the_necessity_measurement() -> None:
    """The regression guard for a void rule that would eat its own gate. Voiding on a floor pass
    drives the necessity floor to zero by construction, so gate 1 would then pass on every
    corpus — including one that needs no memory at all."""
    cells = arm_block(ARM_NONE, "w-t0", passes=4)
    assert void_work_ids(cells) == {}
    assert success_rates(cells, arm_name=ARM_NONE, variant=VARIANT_NECESSARY) == {"w-t0": 1.0}


# ---------------------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------------------


def test_a_corpus_the_floor_can_solve_fails_the_necessity_gate() -> None:
    passing = gates(scored_grid(beads=4, none=0, builtin=1), voided={})
    assert passing["necessity_floor"]["observed"] == 0.0
    assert passing["necessity_floor"]["passed"] is True

    leaky = gates(scored_grid(beads=4, none=2, builtin=1), voided={})
    assert leaky["necessity_floor"]["observed"] == 0.5
    assert leaky["necessity_floor"]["passed"] is False


def test_a_twin_the_floor_cannot_solve_fails_the_specificity_gate() -> None:
    passing = gates(scored_grid(beads=4, none=0, builtin=1, unnecessary=2), voided={})
    assert passing["specificity_ceiling"]["observed"] == 1.0
    assert passing["specificity_ceiling"]["arms_separate_on_unnecessary"] is False
    assert passing["specificity_ceiling"]["passed"] is True

    broken = gates(scored_grid(beads=4, none=0, builtin=1, unnecessary=0), voided={})
    assert broken["specificity_ceiling"]["observed"] == 0.0
    assert broken["specificity_ceiling"]["passed"] is False


def test_an_unbought_grid_reads_as_undecided_and_never_as_passed() -> None:
    """A gate with nothing to judge reports null. An artifact of an unbought grid whose gates all
    read ``true`` would authorize the fire it has not run."""
    empty = gates([], voided={})
    assert empty["necessity_floor"]["passed"] is None
    assert empty["specificity_ceiling"]["passed"] is None
    assert empty["pin_held"]["passed"] is None
    assert empty["leak"]["passed"] is None
    assert empty["substrate_exclusivity"]["passed"] is None
    assert empty["unmeasured_budget"]["passed"] is None
    # Discovery is the exception and says so: never having reached for memory is not a pass.
    assert empty["discovery"]["passed"] is False


def test_the_endpoint_scores_no_establish_leg() -> None:
    """§3, asserted rather than promised: pooling the instructed establish leg halves the effect,
    which mem-eg850 already paid for (+0.425 on goal legs became +0.2125 pooled)."""
    summary = summarize(scored_grid(beads=4, none=0, builtin=1), model="sonnet", dry_run=True)
    assert summary["gates"]["n_establish_legs_scored"] == 0
    assert "pooled_rate" not in summary
    # The establish legs are still REPORTED — as mechanism, beside the endpoint, not inside it.
    assert summary["establish_mechanism"][ARM_BEADS]["cells"] > 0
    assert summary["establish_mechanism"][ARM_BEADS]["establish_outcomes"] == {"remembered": 16}


def test_an_unmeasured_cell_is_counted_against_the_budget_and_never_scored_as_a_zero() -> None:
    cells = [
        *arm_block(ARM_BEADS, "w-t0", passes=3, repeats=3),
        cell(ARM_BEADS, "w-t0", repeat=3, status="timeout"),
    ]
    assert success_rates(cells, arm_name=ARM_BEADS, variant=VARIANT_NECESSARY) == {"w-t0": 1.0}
    budget = gates(cells, voided={})["unmeasured_budget"]
    assert budget["observed"][ARM_BEADS] == pytest.approx(0.25)
    assert budget["passed"] is False


def test_a_grid_that_never_reached_for_memory_is_unmeasured_not_a_null() -> None:
    """The gate this experiment exists to keep honest: a grid of zeros reads as "memory did not
    help" when it means "memory was never called" (the mem-lvp.24 null)."""
    silent = discovery(
        [
            *arm_block(ARM_BEADS, "w-t0", passes=0),
            *arm_block(ARM_NONE, "w-t0", passes=0),
            *arm_block(ARM_BUILTIN, "w-t0", passes=0),
        ]
    )
    assert silent["passed"] is False
    assert "UNMEASURED" in silent["verdict"]
    assert silent["beads_goal_reach_rate"] == 0.0

    reached = discovery(scored_grid(beads=0, none=0, builtin=0))
    assert reached["passed"] is True
    assert reached["beads_goal_reach_rate"] == 1.0


def test_a_write_the_goal_leg_never_read_back_does_not_pass_discovery() -> None:
    """Both halves, because a mechanism that wrote and was never read is the failure the gate is
    for: the arm looks instrumented and the endpoint still measures nothing."""
    write_only = discovery(
        [
            *arm_block(
                ARM_BEADS, "w-t0", passes=0, establish_outcomes=("remembered",), engaged=True
            ),
            *arm_block(ARM_BUILTIN, "w-t0", passes=0, engaged=True),
        ]
    )
    assert write_only["beads_establish_write_cells"] == 4
    assert write_only["beads_goal_read_cells"] == 0
    assert write_only["passed"] is False


# ---------------------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------------------


def test_the_endpoint_is_a_paired_per_work_id_delta_with_both_populations_and_statistics() -> None:
    reading = endpoint(
        scored_grid(beads=3, none=0, builtin=1), treatment=ARM_BEADS, baseline=ARM_NONE
    )
    assert reading["primary"] == "matched_median"
    assert set(reading["readings"]) == {
        "matched_median",
        "matched_mean",
        "itt_median",
        "itt_mean",
    }
    primary = reading["readings"]["matched_median"]
    assert primary["delta"] == pytest.approx(0.75)
    assert primary["n_pairs"] == 4
    # Gate 6: `matched` cannot impute, which is the whole reason the endpoint is read on it.
    assert primary["n_imputed_zero"] == 0
    assert reading["per_work_id"]["baseline"]["w-t0"] == 0.0
    assert reading["variant"] == VARIANT_NECESSARY


def test_an_endpoint_with_nothing_left_to_pair_says_so_rather_than_fabricating_one() -> None:
    """A pilot that bought one work_id and voided it has no endpoint. That is a result the
    artifact states; a degenerate interval would read as a measurement."""
    cells = arm_block(ARM_NONE, "w-t0", passes=0, verbs=("recall",))
    voided = void_work_ids(cells)
    reading = endpoint(cells, treatment=ARM_BEADS, baseline=ARM_NONE, exclude=tuple(voided))
    assert "unavailable" in reading["readings"]["matched_median"]


def test_the_second_contrast_is_beads_against_the_comparator() -> None:
    summary = summarize(scored_grid(beads=4, none=0, builtin=2), model="sonnet", dry_run=True)
    assert summary["endpoint"]["contrast"] == f"{ARM_BEADS} - {ARM_NONE}"
    assert summary["second_contrast"]["contrast"] == f"{ARM_BEADS} - {ARM_BUILTIN}"
    assert summary["second_contrast"]["readings"]["matched_median"]["delta"] == pytest.approx(0.5)


def test_the_artifact_names_the_rig_that_produced_it() -> None:
    summary = summarize(
        scored_grid(beads=4, none=0, builtin=1),
        model="sonnet",
        dry_run=True,
        n_tasks=4,
        work_ids=["w-t0"],
        cli_version="2.1.210",
        corpus="deadbeef",
    )
    for field in (
        "protocol_version",
        "execution_protocol",
        "arm_settings_fingerprint",
        "surface_fingerprint",
        "recognizer_version",
        "cli_version",
        "corpus_fingerprint",
    ):
        assert summary[field], field
    assert summary["n_paid_cells"] == summary["n_cells"]
