"""The capture turn: which cells it draws, what a capture cell may claim, and what it refuses.

Nothing here spawns an agent or mints a store. ``run_arm_cell`` is replaced where the driver is
under test, and the arithmetic is exercised directly.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from membench.runner import beads_capture_fire
from membench.runner.bd_build import BdBuild
from membench.runner.beads_arm_fire import admissible_cells, resume_identity
from membench.runner.beads_arm_grid import PROTOCOL_CAPTURE, PROTOCOL_THREE_ARM, ArmCell, legs_for
from membench.runner.beads_arm_plan import ArmPlanError, cell_from_row, cell_row, grid_keys
from membench.runner.beads_capture import (
    CAPTURE_REPEATS,
    CAPTURE_SAMPLE_N,
    capture_keys,
    capture_plan,
    capture_rates,
    capture_work_ids,
    reached,
)
from membench.runner.beads_capture_fire import capture_identity, main
from membench.runner.e1_grid import EXIT_OK, EXIT_REFUSED, ResumeMismatchError
from membench.runner.memory_arm import ARM_BEADS, ARM_BUILTIN, ARM_NONE
from membench.runner.toolreq_corpus import twin_tasks
from membench.runner.toolreq_realagent import (
    VARIANT_NECESSARY,
    ToolReqRealAgentTask,
    adapt_sequence,
)
from tests.toolreq_helpers import toolreq_seq

MODEL = "sonnet"
BD_BUILD = BdBuild(binary="/opt/bd", sha256="a" * 64, commit="7786d93", version="1.3.0")

# The eight work_ids docs/prereg-beads-capture.md §4 writes down. Spelled out here rather than
# recomputed from the seed, because a test that re-derives the draw the same way the code does
# cannot catch the draw moving.
REGISTERED_SAMPLE = (
    "world-seed1-task0",
    "world-seed10-task0",
    "world-seed13-task0",
    "world-seed14-task0",
    "world-seed22-task0",
    "world-seed29-task0",
    "world-seed4-task0",
    "world-seed8-task0",
)

JEV32 = Path("fixtures/worlds-tool-jev32")


def corpus(n: int = 3) -> list[ToolReqRealAgentTask]:
    return twin_tasks([adapt_sequence(toolreq_seq(f"w-t{i}")) for i in range(n)])


def a_capture_cell(
    arm: str,
    work_id: str = "w-t0",
    *,
    repeat: int = 0,
    engaged: bool = False,
    verbs: tuple[str, ...] = (),
    reaches: int = 0,
    status: str = "ok",
) -> ArmCell:
    return ArmCell(
        arm=arm,
        work_id=work_id,
        variant=VARIANT_NECESSARY,
        repeat=repeat,
        passed=False,
        engaged=engaged,
        leaked=False,
        establish_tool_names=("Bash",) if verbs else (),
        endogenous_verbs=verbs,
        establish_outcomes=(),
        goal_outcomes=(),
        native_reaches=reaches,
        pinned_off=True,
        paid=True,
        status=status,
        protocol=PROTOCOL_CAPTURE,
        legs=1,
    )


# ---------------------------------------------------------------------------------------
# The draw
# ---------------------------------------------------------------------------------------


@pytest.mark.skipif(not JEV32.exists(), reason="the registered corpus is not in this checkout")
def test_the_draw_is_the_eight_work_ids_the_registration_wrote_down() -> None:
    from membench.runner.toolreq_corpus import load_twin_corpus

    _, tasks = load_twin_corpus(JEV32)
    assert tuple(capture_work_ids(tasks)) == REGISTERED_SAMPLE


def test_a_corpus_too_small_for_the_registered_sample_is_refused() -> None:
    with pytest.raises(ArmPlanError, match=str(CAPTURE_SAMPLE_N)):
        capture_work_ids(corpus(2))


def test_the_draw_does_not_move_when_the_corpus_grows_around_it() -> None:
    """The draw is a function of the id set, so a corpus that GREW re-draws -- but the same corpus
    read twice must give the same eight, or two turns of one series ran different samples."""
    tasks = corpus(12)
    assert capture_work_ids(tasks) == capture_work_ids(list(reversed(tasks)))


def test_the_draw_takes_only_the_necessary_variant() -> None:
    tasks = corpus(12)
    necessary = {task.work_id for task in tasks if task.variant == VARIANT_NECESSARY}
    assert set(capture_work_ids(tasks)) <= necessary


# ---------------------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------------------


def test_a_candidate_turn_buys_the_treatment_alone() -> None:
    keys = capture_keys(corpus(12))
    assert {arm for arm, _v, _w, _r in keys} == {ARM_BEADS}
    assert len(keys) == CAPTURE_SAMPLE_N * CAPTURE_REPEATS


def test_the_reused_arms_are_a_named_invocation_not_a_default() -> None:
    keys = capture_keys(corpus(12), arms=(ARM_NONE, ARM_BUILTIN))
    assert {arm for arm, _v, _w, _r in keys} == {ARM_BUILTIN, ARM_NONE}
    assert ARM_BEADS not in {arm for arm, _v, _w, _r in keys}


def test_the_grid_is_work_id_major_so_an_early_stop_leaves_complete_tasks() -> None:
    keys = capture_keys(corpus(12), arms=(ARM_BEADS, ARM_BUILTIN))
    seen: list[str] = []
    for _arm, _variant, work_id, _repeat in keys:
        if not seen or seen[-1] != work_id:
            seen.append(work_id)
    assert len(seen) == len(set(seen))


def test_an_arm_outside_the_registration_is_refused() -> None:
    with pytest.raises(ArmPlanError, match="not capture arms"):
        capture_keys(corpus(12), arms=("oracle",))


def test_a_turn_with_no_arms_is_refused() -> None:
    with pytest.raises(ArmPlanError, match="buys nothing"):
        capture_keys(corpus(12), arms=())


def test_the_plan_counts_the_cells_it_will_iterate() -> None:
    tasks = corpus(12)
    plan = capture_plan(tasks)
    assert plan["cells"] == len(capture_keys(tasks))
    # One leg per cell, so cells and sessions agree -- unlike the three-arm grid, where they
    # cannot. A plan that quoted the pair here would quote twice the bill.
    assert plan["sessions"] == plan["cells"]


def test_the_capture_grid_and_the_three_arm_grid_are_different_derivations() -> None:
    """Neither may be substituted for the other: the three-arm grid buys both variants, four
    repeats on the necessary one and all three arms, and pricing a capture turn off it would
    authorize several times the spend."""
    tasks = corpus(12)
    contrast = grid_keys(tasks)
    # The three-arm grid buys both variants, four repeats on the necessary one and all three
    # arms; a capture turn buys one variant, two repeats and one arm over eight drawn tasks.
    assert len(capture_keys(tasks)) * 4 < len(contrast)
    assert {variant for _a, variant, _w, _r in capture_keys(tasks)} == {VARIANT_NECESSARY}
    assert len({variant for _a, variant, _w, _r in contrast}) == 2


# ---------------------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------------------


def test_a_bd_verb_is_a_reach_on_an_arm_with_a_store() -> None:
    assert reached(a_capture_cell(ARM_BEADS, verbs=("remember",))) is True
    assert reached(a_capture_cell(ARM_BEADS)) is False


def test_the_comparator_reaches_through_its_native_path_not_through_bd() -> None:
    assert reached(a_capture_cell(ARM_BUILTIN, reaches=2)) is True
    # A bd verb on the comparator would be a call to a store it does not have; it is refused in
    # the mint, and it is not quietly counted as a capture here either.
    assert reached(a_capture_cell(ARM_BUILTIN, verbs=("remember",))) is False


def test_a_three_arm_cell_cannot_be_read_for_the_capture_endpoint() -> None:
    contrast = replace(a_capture_cell(ARM_BEADS, verbs=("remember",)), protocol=PROTOCOL_THREE_ARM)
    with pytest.raises(ArmPlanError, match="capture endpoint"):
        reached(contrast)


def test_an_unmeasured_cell_is_excluded_and_counted_never_scored_zero() -> None:
    cells = [
        a_capture_cell(ARM_BEADS, "a", verbs=("remember",), engaged=True),
        a_capture_cell(ARM_BEADS, "b", status="timeout"),
    ]
    rates = capture_rates(cells, arm_name=ARM_BEADS)
    assert rates["cells"] == 2
    assert rates["unmeasured"] == 1
    assert rates["measured"] == 1
    # 1/1, not 1/2: the timed-out leg was never observed declining to reach.
    assert rates["reached_rate"] == 1.0
    assert rates["engaged_rate"] == 1.0


def test_an_arm_with_nothing_measured_reports_no_rate_rather_than_zero() -> None:
    rates = capture_rates([a_capture_cell(ARM_NONE, status="error")], arm_name=ARM_NONE)
    assert rates["measured"] == 0
    assert rates["reached_rate"] is None


# ---------------------------------------------------------------------------------------
# What a row may be pooled into
# ---------------------------------------------------------------------------------------


def test_a_capture_cell_records_that_it_bought_one_leg() -> None:
    row = cell_row(a_capture_cell(ARM_BEADS, verbs=("remember",)))
    assert row["protocol"] == PROTOCOL_CAPTURE
    assert row["legs"] == 1
    assert cell_from_row(row) == a_capture_cell(ARM_BEADS, verbs=("remember",))


def test_a_row_that_cannot_say_which_registration_bought_it_is_refused() -> None:
    row = cell_row(a_capture_cell(ARM_BEADS))
    del row["protocol"]
    with pytest.raises(ArmPlanError, match="not a readable cell row"):
        cell_from_row(row)


def test_the_leg_count_a_protocol_buys_is_registered_not_guessed() -> None:
    assert legs_for(PROTOCOL_THREE_ARM) == 2
    assert legs_for(PROTOCOL_CAPTURE) == 1
    with pytest.raises(Exception, match="no leg count registered"):
        legs_for("half-arm")


def test_a_three_arm_artifact_cannot_be_resumed_into_a_capture_turn() -> None:
    tasks = corpus(12)
    work_ids = capture_work_ids(tasks)
    mine = capture_identity(
        model=MODEL,
        cli_version="2.1.278",
        corpus="deadbeef",
        arms=[ARM_BEADS],
        work_ids=work_ids,
        bd_build=BD_BUILD,
    )
    theirs = resume_identity(
        model=MODEL, cli_version="2.1.278", corpus="deadbeef", work_ids=work_ids, bd_build=BD_BUILD
    )
    with pytest.raises(ResumeMismatchError):
        admissible_cells(dict(theirs) | {"cells": []}, identity=mine, grid=capture_keys(tasks))


def test_a_turn_that_bought_the_treatment_alone_cannot_pass_for_one_that_bought_all_three() -> None:
    """§6 reuses the floor and comparator across candidates. An artifact that could not say which
    arms it holds would let a candidate-only file be resumed as though it already had them."""
    tasks = corpus(12)
    work_ids = capture_work_ids(tasks)

    def ident(arms: list[str]) -> dict[str, Any]:
        return capture_identity(
            model=MODEL,
            cli_version="2.1.278",
            corpus="deadbeef",
            arms=arms,
            work_ids=work_ids,
            bd_build=BD_BUILD,
        )

    candidate = ident([ARM_BEADS])
    with pytest.raises(ResumeMismatchError):
        admissible_cells(
            dict(candidate) | {"cells": []},
            identity=ident([ARM_BEADS, ARM_BUILTIN, ARM_NONE]),
            grid=capture_keys(tasks, arms=(ARM_BEADS, ARM_BUILTIN, ARM_NONE)),
        )


def test_the_identity_names_the_bd_build_the_turn_measured() -> None:
    ident = capture_identity(
        model=MODEL,
        cli_version="2.1.278",
        corpus="deadbeef",
        arms=[ARM_BEADS],
        work_ids=["w-t0"],
        bd_build=BD_BUILD,
    )
    assert ident["bd_commit"] == "7786d93"
    assert ident["protocol"] == PROTOCOL_CAPTURE


# ---------------------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------------------


def test_the_plan_spends_nothing_and_prints_the_turn(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        beads_capture_fire, "load_twin_corpus", lambda _dir: (None, corpus(12)), raising=True
    )
    assert main(["--plan", "--model", MODEL]) == EXIT_OK
    plan = json.loads(capsys.readouterr().out)
    assert plan["protocol"] == PROTOCOL_CAPTURE
    assert plan["cells"] == CAPTURE_SAMPLE_N * CAPTURE_REPEATS


def test_a_paid_turn_without_an_out_is_refused_before_it_spends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        beads_capture_fire, "load_twin_corpus", lambda _dir: (None, corpus(12)), raising=True
    )
    assert main(["--fire", "--model", MODEL]) == EXIT_REFUSED


# ---------------------------------------------------------------------------------------
# What a capture cell actually buys
# ---------------------------------------------------------------------------------------


def _counting_runner(spawns: list[list[str]]) -> Any:
    """A CLI that answers nothing and records every argv it was spawned with.

    The count is the point. §6 prices a capture turn at 16 sessions for 16 cells, and that price
    is only true if a capture cell spawns the agent ONCE. A cell that quietly ran the goal leg
    and discarded it would return the same row and cost twice the authorized amount."""
    import subprocess as _sp

    def run(argv: Any, **kwargs: Any) -> Any:
        spawns.append(list(argv))
        return _sp.CompletedProcess(
            list(argv), 0, json.dumps({"type": "result", "result": "noted"}), ""
        )

    return run


def test_a_capture_cell_spawns_the_agent_once_and_never_mints_a_goal_leg() -> None:
    from membench.runner.beads_arm_grid import run_arm_cell
    from membench.runner.headless_agent import MemoryChannel

    task = adapt_sequence(toolreq_seq("cap-t0"))
    spawns: list[list[str]] = []
    cell = run_arm_cell(
        task,
        ARM_NONE,
        repeat=0,
        model=MODEL,
        channel=MemoryChannel.TRUSTED,
        runner=_counting_runner(spawns),
        protocol=PROTOCOL_CAPTURE,
    )
    assert len(spawns) == 1
    assert cell.protocol == PROTOCOL_CAPTURE
    assert cell.legs == 1
    assert cell.goal_tool_names == ()
    assert cell.goal_outcomes == ()
    # No goal leg was bought, so there is no pass to claim and nothing to call a leak.
    assert cell.passed is False
    assert cell.leaked is False
    assert cell.status == "ok"


def test_the_same_cell_under_the_contrast_protocol_buys_both_legs() -> None:
    """The control for the test above: the one-leg result has to be the protocol's doing and not
    a stand-in runner that only ever gets called once."""
    from membench.runner.beads_arm_grid import run_arm_cell
    from membench.runner.headless_agent import MemoryChannel

    task = adapt_sequence(toolreq_seq("cap-t0"))
    spawns: list[list[str]] = []
    cell = run_arm_cell(
        task,
        ARM_NONE,
        repeat=0,
        model=MODEL,
        channel=MemoryChannel.TRUSTED,
        runner=_counting_runner(spawns),
    )
    assert len(spawns) == 2
    assert cell.protocol == PROTOCOL_THREE_ARM
    assert cell.legs == 2
