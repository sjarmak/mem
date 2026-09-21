"""The three-arm driver: what it refuses, what it resumes, and when it stops spending.

Nothing here spawns an agent or mints a store — ``run_arm_cell`` is replaced, so what is under
test is the driver's control flow around a spend rather than the spend itself. The tests that
exercise a real mint live in ``test_beads_arm_grid``; the arithmetic is in
``test_beads_arm_plan``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from membench.runner import beads_arm_fire
from membench.runner.bd_build import BdBuild
from membench.runner.beads_arm_fire import (
    admissible_cells,
    fire,
    main,
    resume_identity,
)
from membench.runner.beads_arm_grid import ArmCell
from membench.runner.beads_arm_plan import (
    ArmGridKey,
    cell_key,
    cell_row,
    grid_keys,
    priced_plan,
)
from membench.runner.e1_grid import (
    EXIT_OK,
    EXIT_REFUSED,
    QuotaHaltError,
    ResumeMismatchError,
    RigHaltError,
)
from membench.runner.headless_agent import ENV_OAUTH, HeadlessAgentError
from membench.runner.memory_arm import ARM_BEADS, ARM_NONE
from membench.runner.tool_surface import MemoryToolError
from membench.runner.toolreq_corpus import twin_tasks
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, adapt_sequence
from membench.spawn import with_child
from tests.toolreq_helpers import toolreq_seq

MODEL = "sonnet"


def corpus(n: int = 2) -> list[ToolReqRealAgentTask]:
    return twin_tasks([adapt_sequence(toolreq_seq(f"w-t{i}")) for i in range(n)])


def a_cell(task: ToolReqRealAgentTask, arm: str, *, repeat: int = 0, paid: bool = True) -> ArmCell:
    return ArmCell(
        arm=arm,
        work_id=task.work_id,
        variant=task.variant,
        repeat=repeat,
        passed=True,
        engaged=True,
        leaked=False,
        establish_tool_names=("Bash",),
        endogenous_verbs=(),
        establish_outcomes=("remembered",),
        goal_outcomes=("returned",),
        native_reaches=0,
        pinned_off=True,
        paid=paid,
        status="ok",
    )


BD_BUILD = BdBuild(binary="/opt/bd", sha256="f" * 64, commit="e9d2f1778", version="1.3.0-rc.1")


def identity_of(tasks: list[ToolReqRealAgentTask]) -> dict[str, Any]:
    return resume_identity(
        model=MODEL,
        cli_version="2.1.210",
        corpus="deadbeef",
        work_ids=sorted({task.work_id for task in tasks}),
        bd_build=BD_BUILD,
    )


def artifact(
    tasks: list[ToolReqRealAgentTask], cells: list[ArmCell], **overrides: Any
) -> dict[str, Any]:
    return identity_of(tasks) | {"cells": [cell_row(cell) for cell in cells]} | overrides


def quota_refusal() -> HeadlessAgentError:
    """The CLI's own refusal event, classified off the child's stream rather than the message."""
    child = subprocess.CompletedProcess(
        ["claude"],
        1,
        json.dumps(
            {"type": "result", "is_error": True, "api_error_status": 429, "result": "session limit"}
        ),
        "",
    )
    exc = HeadlessAgentError("claude -p failed (exit 1): <redacted>")
    with_child(exc, child)
    return exc


def timed_out() -> HeadlessAgentError:
    exc = HeadlessAgentError("claude -p timed out")
    exc.__cause__ = subprocess.TimeoutExpired(["claude"], 900)
    return exc


def a_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
    raise AssertionError("no test in this module spawns")


# ---------------------------------------------------------------------------------------
# what a partial artifact is allowed to contribute
# ---------------------------------------------------------------------------------------


def test_an_artifact_from_a_different_arm_registry_is_refused() -> None:
    """The registry IS the experiment. A cell bought when the floor arm still reached the host
    toolchain measured a different machine, and pooling it publishes two rigs as one."""
    tasks = corpus(1)
    grid = grid_keys(tasks)
    identity = identity_of(tasks)
    prior = artifact(tasks, [a_cell(tasks[0], ARM_BEADS)], arm_settings_fingerprint="stale")
    with pytest.raises(ResumeMismatchError, match="different rig"):
        admissible_cells(prior, identity=identity, grid=grid)


def test_a_rig_that_cannot_state_its_own_identity_refuses_to_match_anything() -> None:
    tasks = corpus(1)
    identity = identity_of(tasks) | {"cli_version": ""}
    with pytest.raises(ResumeMismatchError, match="cannot state its own cli_version"):
        admissible_cells(artifact(tasks, []), identity=identity, grid=grid_keys(tasks))


def test_a_cell_written_twice_is_refused_rather_than_deduplicated() -> None:
    """Two fires wrote this file, and neither row can be shown to be the one to keep. Picking
    either one silently chooses which paid measurement survives."""
    tasks = corpus(1)
    cell = a_cell(tasks[0], ARM_BEADS)
    prior = artifact(tasks, [cell, cell])
    with pytest.raises(ResumeMismatchError, match="twice"):
        admissible_cells(prior, identity=identity_of(tasks), grid=grid_keys(tasks))


def test_a_cell_outside_this_fires_grid_is_refused_rather_than_ignored() -> None:
    tasks = corpus(2)
    pilot_grid = grid_keys(tasks, n_tasks=1)
    stranger = a_cell(tasks[2], ARM_BEADS)  # the SECOND work_id, which the pilot does not buy
    prior = artifact(tasks, [stranger])
    with pytest.raises(ResumeMismatchError, match="not a cell of the grid"):
        admissible_cells(prior, identity=identity_of(tasks), grid=pilot_grid)


def test_unpaid_and_unmeasured_cells_are_dropped_so_the_resume_can_buy_them() -> None:
    """A dry-run cell carried into a paid grid publishes a simulation as a measurement; an
    unmeasured cell is a cell still owed."""
    tasks = corpus(1)
    paid = a_cell(tasks[0], ARM_BEADS)
    simulated = a_cell(tasks[0], ARM_NONE, paid=False)
    unmeasured = replace(a_cell(tasks[0], ARM_BEADS, repeat=1), status="timeout")
    prior = artifact(tasks, [paid, simulated, unmeasured])
    kept = admissible_cells(prior, identity=identity_of(tasks), grid=grid_keys(tasks))
    assert [cell_key(cell) for cell in kept] == [cell_key(paid)]


# ---------------------------------------------------------------------------------------
# the fire loop
# ---------------------------------------------------------------------------------------


def test_a_quota_refusal_halts_instead_of_burning_the_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a defect and not a measurement: nothing can be bought until the account resets, so
    filling the rest of the grid with unmeasured cells spends the authorization on nothing."""
    tasks = corpus(1)
    calls: list[str] = []

    def refused(task: ToolReqRealAgentTask, arm: str, **_kw: object) -> ArmCell:
        calls.append(arm)
        raise quota_refusal()

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", refused)
    with pytest.raises(QuotaHaltError, match="refused at cell"):
        fire(tasks, model=MODEL, runner=a_runner)
    assert len(calls) == 1  # stopped on the first refusal rather than walking the grid


def test_three_consecutive_unmeasured_cells_halt_the_rig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tolerance applied everywhere is a grid that measured nothing while reporting a rate."""
    tasks = corpus(2)
    kept: list[ArmCell] = []

    def always_times_out(task: ToolReqRealAgentTask, arm: str, **_kw: object) -> ArmCell:
        raise timed_out()

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", always_times_out)
    with pytest.raises(RigHaltError, match="consecutive"):
        fire(tasks, model=MODEL, runner=a_runner, on_cell=kept.append)
    assert len(kept) == 3
    assert {cell.status for cell in kept} == {"timeout"}


def test_a_timed_out_cell_lands_unmeasured_and_never_as_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 12's granularity: ``run_arm_cell`` is atomic, so a failure inside it leaves a cell
    that bought legs and measured nothing — not a scorable zero."""
    tasks = corpus(1)
    seen: list[tuple[str, str]] = []

    def flaky(task: ToolReqRealAgentTask, arm: str, *, repeat: int = 0, **_kw: object) -> ArmCell:
        seen.append((arm, task.variant))
        if len(seen) == 1:
            raise timed_out()
        return a_cell(task, arm, repeat=repeat, paid=False)

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", flaky)
    cells = fire(tasks, model=MODEL, runner=a_runner)
    unmeasured = [cell for cell in cells if cell.status != "ok"]
    assert len(unmeasured) == 1
    assert unmeasured[0].status == "timeout"
    assert unmeasured[0].passed is False
    assert unmeasured[0].engaged is False
    # The streak reset on the next measured cell, so one timeout did not stop the fire.
    assert len(cells) == len(grid_keys(tasks))


def test_a_resumed_cell_is_not_bought_again(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = corpus(1)
    landed = [a_cell(tasks[0], ARM_BEADS)]
    bought: list[ArmGridKey] = []

    def buy(task: ToolReqRealAgentTask, arm: str, *, repeat: int = 0, **_kw: object) -> ArmCell:
        cell = a_cell(task, arm, repeat=repeat, paid=False)
        bought.append(cell_key(cell))
        return cell

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", buy)
    cells = fire(tasks, model=MODEL, runner=a_runner, landed=landed)
    assert cell_key(landed[0]) not in bought
    assert sorted([*bought, cell_key(landed[0])]) == sorted(grid_keys(tasks))
    # And it is in the result exactly once — kept, not kept and re-bought.
    assert sum(1 for cell in cells if cell_key(cell) == cell_key(landed[0])) == 1


def test_a_cell_named_by_the_grid_with_no_task_behind_it_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grid and the corpus are derived from the same tasks, so a disagreement means one of
    them was re-derived. Buying the arms the grid names against a task it guessed is worse."""
    tasks = corpus(1)
    monkeypatch.setattr(
        beads_arm_fire, "grid_keys", lambda *a, **k: [(ARM_BEADS, "necessary", "w-nope", 0)]
    )
    monkeypatch.setattr(
        beads_arm_fire, "run_arm_cell", lambda *a, **k: pytest.fail("should not spend")
    )
    with pytest.raises(beads_arm_fire.ArmPlanError, match="carries no"):
        fire(tasks, model=MODEL, runner=a_runner)


# ---------------------------------------------------------------------------------------
# the CLI's refusal ladder
# ---------------------------------------------------------------------------------------


def test_preflight_and_fire_are_two_different_spends() -> None:
    with pytest.raises(SystemExit) as exit_code:
        main(["--preflight", "--fire"])
    assert exit_code.value.code == 2


def test_preflight_will_not_take_a_task_count_it_does_not_buy() -> None:
    """§6 fixes the preflight at one task. ``--n-tasks`` here prices one grid and buys another."""
    with pytest.raises(SystemExit) as exit_code:
        main(["--preflight", "--n-tasks", "4"])
    assert exit_code.value.code == 2


def test_a_paid_run_without_a_pinned_model_is_refused_before_the_corpus_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A paid run whose model resolves empty executes under the CLI's own default, which no
    identity records — a resume across a CLI upgrade would then serve one model's numbers as
    another's. Refused before the corpus loads, so it costs nothing."""
    monkeypatch.delenv("MEMBENCH_AGENT_MODEL", raising=False)
    monkeypatch.setenv(ENV_OAUTH, "token")
    monkeypatch.setattr(
        beads_arm_fire, "load_twin_corpus", lambda *a, **k: pytest.fail("loaded the corpus")
    )
    assert main(["--fire", "--out", str(tmp_path / "out.json")]) == EXIT_REFUSED


def test_a_fire_without_an_out_artifact_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--out`` is the resume artifact, the lock and the parent of the per-cell evidence. A halt
    without one loses every cell bought so far and the re-run buys the whole grid again."""
    monkeypatch.setenv(ENV_OAUTH, "token")
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], corpus(1)))
    assert main(["--fire", "--model", MODEL]) == EXIT_REFUSED


def test_pricing_the_grid_spends_nothing_and_needs_no_account(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pricing and spending are never the same keystroke."""
    tasks = corpus(3)
    monkeypatch.delenv(ENV_OAUTH, raising=False)
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], tasks))
    monkeypatch.setattr(
        beads_arm_fire, "run_arm_cell", lambda *a, **k: pytest.fail("priced and then spent")
    )
    assert main(["--plan"]) == EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    assert printed == priced_plan(tasks)


def test_an_empty_corpus_is_reported_as_missing_and_never_as_a_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], []))
    assert main(["--plan"]) != EXIT_OK
    assert "NOT a result" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------
# the goal leg is no longer a black box (mem-0wpq8.1)
# ---------------------------------------------------------------------------------------


def test_the_driver_keeps_each_legs_stream_beside_the_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The re-fired pilot scored 0/18 on the goal leg with nothing to read: the cell row says
    what the establish leg called and nothing about the goal leg, and neither stream survived
    the mint's teardown. Each leg's verbatim stream lands beside the cell file under the cell's
    own key, or the next diagnosis costs another paid round."""
    tasks = corpus(1)

    def buy(
        task: ToolReqRealAgentTask,
        arm: str,
        *,
        repeat: int = 0,
        keep_stream: Any = None,
        **_kw: object,
    ) -> ArmCell:
        keep_stream("establish", f'{{"leg": "establish", "arm": "{arm}"}}\n')
        keep_stream("goal", f'{{"leg": "goal", "arm": "{arm}"}}\n')
        return a_cell(task, arm, repeat=repeat, paid=False)

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", buy)
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], tasks))
    monkeypatch.setattr(beads_arm_fire, "resolve_cli_version", lambda *a, **k: "2.1.210")
    monkeypatch.setattr(beads_arm_fire, "resolve_bd_build", lambda *a, **k: BD_BUILD)
    out = tmp_path / "out.json"
    assert main(["--dry-run", "--out", str(out), "--model", MODEL]) == EXIT_OK

    cells_dir = tmp_path / "out.json.cells"
    cell_files = sorted(cells_dir.glob("*.json"))
    assert len(cell_files) == len(grid_keys(tasks))
    for cell_file in cell_files:
        key = cell_file.name[: -len(".json")]
        establish = cells_dir / f"{key}.establish.jsonl"
        goal = cells_dir / f"{key}.goal.jsonl"
        assert establish.is_file() and goal.is_file(), key
        assert json.loads(establish.read_text(encoding="utf-8"))["leg"] == "establish"
        assert json.loads(goal.read_text(encoding="utf-8"))["leg"] == "goal"
        assert json.loads(goal.read_text(encoding="utf-8"))["arm"] == key.split("-")[0]


def test_an_empty_stream_is_an_absence_and_never_an_empty_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stand-in runner hands back no stream. Writing a zero-byte file for it would let a
    dry-run cell look, on disk, like a paid cell whose agent said nothing."""
    tasks = corpus(1)

    def buy(
        task: ToolReqRealAgentTask,
        arm: str,
        *,
        repeat: int = 0,
        keep_stream: Any = None,
        **_kw: object,
    ) -> ArmCell:
        keep_stream("establish", "")
        keep_stream("goal", "")
        return a_cell(task, arm, repeat=repeat, paid=False)

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", buy)
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], tasks))
    monkeypatch.setattr(beads_arm_fire, "resolve_cli_version", lambda *a, **k: "2.1.210")
    monkeypatch.setattr(beads_arm_fire, "resolve_bd_build", lambda *a, **k: BD_BUILD)
    out = tmp_path / "out.json"
    assert main(["--dry-run", "--out", str(out), "--model", MODEL]) == EXIT_OK
    assert list((tmp_path / "out.json.cells").glob("*.jsonl")) == []


# ---------------------------------------------------------------------------------------
# the bd build is part of the identity (mem-0wpq8.2)
# ---------------------------------------------------------------------------------------


def test_an_artifact_bought_against_another_bd_build_is_refused() -> None:
    """The flywheel's one variable per turn is the beads build. A partial bought against the
    last turn's binary must not be pooled into this turn's grid -- by commit OR by bytes, since a
    rebuilt binary at the same commit is a different instrument."""
    tasks = corpus(1)
    prior = artifact(tasks, [a_cell(tasks[0], ARM_BEADS)], bd_commit="0000000")
    with pytest.raises(ResumeMismatchError, match="bd_commit"):
        admissible_cells(prior, identity=identity_of(tasks), grid=grid_keys(tasks))
    prior = artifact(tasks, [a_cell(tasks[0], ARM_BEADS)], bd_binary_sha256="0" * 64)
    with pytest.raises(ResumeMismatchError, match="bd_binary_sha256"):
        admissible_cells(prior, identity=identity_of(tasks), grid=grid_keys(tasks))


def test_an_artifact_that_never_recorded_its_bd_build_is_refused() -> None:
    """Every artifact bought before this field existed: the bd it wrapped is unknowable now."""
    tasks = corpus(1)
    prior = artifact(tasks, [a_cell(tasks[0], ARM_BEADS)])
    del prior["bd_commit"]
    del prior["bd_binary_sha256"]
    with pytest.raises(ResumeMismatchError, match="different rig"):
        admissible_cells(prior, identity=identity_of(tasks), grid=grid_keys(tasks))


def test_a_run_whose_bd_cannot_be_identified_refuses_before_spending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], corpus(1)))
    monkeypatch.setattr(beads_arm_fire, "resolve_cli_version", lambda *a, **k: "2.1.210")

    def no_bd(*_a: object, **_k: object) -> BdBuild:
        raise MemoryToolError("no executable 'bd'")

    monkeypatch.setattr(beads_arm_fire, "resolve_bd_build", no_bd)
    monkeypatch.setattr(
        beads_arm_fire, "run_arm_cell", lambda *a, **k: pytest.fail("spent without an identity")
    )
    out = tmp_path / "out.json"
    assert main(["--dry-run", "--out", str(out), "--model", MODEL]) == EXIT_REFUSED


def test_the_artifact_records_the_bd_build_it_was_bought_against(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = corpus(1)
    monkeypatch.setattr(
        beads_arm_fire,
        "run_arm_cell",
        lambda task, arm, *, repeat=0, **_kw: a_cell(task, arm, repeat=repeat, paid=False),
    )
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], tasks))
    monkeypatch.setattr(beads_arm_fire, "resolve_cli_version", lambda *a, **k: "2.1.210")
    monkeypatch.setattr(beads_arm_fire, "resolve_bd_build", lambda *a, **k: BD_BUILD)
    out = tmp_path / "out.json"
    assert main(["--dry-run", "--out", str(out), "--model", MODEL]) == EXIT_OK
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["bd_commit"] == "e9d2f1778"
    assert written["bd_binary_sha256"] == "f" * 64


# ---------------------------------------------------------------------------------------
# the pinned beads build reaches the cells, not just the identity (mem-0wpq8.4)
# ---------------------------------------------------------------------------------------


def test_every_cell_mints_against_the_build_the_artifact_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect this wiring exists to make unrepresentable. `--bd-ref` records a commit and a
    binary hash in the resume identity, while `provision_memory_tool` would resolve bd off PATH --
    so a turn could publish an identity naming a prototype build that no cell ever ran, and the
    ambient binary on this machine is a different build from any commit a turn pins.

    Asked of every cell, not the first: a grid that straddled two builds would pass a check on
    one of them."""
    tasks = corpus(1)
    minted: list[str | None] = []

    def buy(
        task: ToolReqRealAgentTask,
        arm: str,
        *,
        repeat: int = 0,
        bd_binary: str | None = None,
        **_kw: object,
    ) -> ArmCell:
        minted.append(bd_binary)
        return a_cell(task, arm, repeat=repeat, paid=False)

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", buy)
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], tasks))
    monkeypatch.setattr(beads_arm_fire, "resolve_cli_version", lambda *a, **k: "2.1.210")
    monkeypatch.setattr(beads_arm_fire, "resolve_bd_build", lambda *a, **k: BD_BUILD)

    out = tmp_path / "out.json"
    assert main(["--dry-run", "--out", str(out), "--model", MODEL]) == EXIT_OK

    assert minted, "no cell ran"
    assert set(minted) == {
        BD_BUILD.binary
    }, "a cell minted against a bd other than the one the artifact's identity names"


def test_a_pinned_ref_is_built_before_any_cell_is_bought(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--bd-ref` has to resolve to a binary BEFORE the first cell, and a failure to build it has
    to refuse rather than fall through to the ambient bd. A build failure discovered mid-grid
    would leave a half-bought artifact whose cells straddle two instruments."""
    tasks = corpus(1)
    asked: list[tuple[str, str]] = []

    def built(remote: str, sha: str, **_kw: object) -> Any:
        asked.append((remote, sha))
        return BD_BUILD

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", lambda t, a, **k: a_cell(t, a, paid=False))
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], tasks))
    monkeypatch.setattr(beads_arm_fire, "resolve_cli_version", lambda *a, **k: "2.1.210")
    monkeypatch.setattr(beads_arm_fire, "build_bd_ref", built)

    out = tmp_path / "out.json"
    code = main(
        ["--dry-run", "--out", str(out), "--model", MODEL, "--bd-ref", "origin", "5b9e938b5"]
    )

    assert code == EXIT_OK
    assert asked == [("origin", "5b9e938b5")], "the pinned ref was not the build that was used"


def test_a_ref_that_cannot_be_built_refuses_before_spending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = corpus(1)
    bought: list[str] = []

    def never(task: Any, arm: str, **_kw: object) -> ArmCell:
        bought.append(arm)
        raise AssertionError("a cell was bought after the build failed")

    def refuse(remote: str, sha: str, **_kw: object) -> Any:
        raise MemoryToolError("no toolchain")

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", never)
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], tasks))
    monkeypatch.setattr(beads_arm_fire, "resolve_cli_version", lambda *a, **k: "2.1.210")
    monkeypatch.setattr(beads_arm_fire, "build_bd_ref", refuse)

    out = tmp_path / "out.json"
    code = main(
        ["--dry-run", "--out", str(out), "--model", MODEL, "--bd-ref", "origin", "5b9e938b5"]
    )

    assert code == EXIT_REFUSED
    assert bought == []


def test_a_dry_run_identifies_the_bd_build_through_the_real_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The stand-in runner answers every spawn with a fixed agent result, so handing it
    ``bd version --json`` makes the binary unidentifiable and refuses the run. ``--dry-run`` did
    exactly that from the moment the build entered the identity, and the CLI could not be
    dry-run at all. Identifying the build is free and is part of what a dry run proves."""
    tasks = corpus(1)
    seen: list[object] = []

    def record(*_a: object, runner: object = None, **_kw: object) -> BdBuild:
        seen.append(runner)
        return BD_BUILD

    def buy(task: ToolReqRealAgentTask, arm: str, *, repeat: int = 0, **_kw: object) -> ArmCell:
        return a_cell(task, arm, repeat=repeat, paid=False)

    monkeypatch.setattr(beads_arm_fire, "run_arm_cell", buy)
    monkeypatch.setattr(beads_arm_fire, "load_twin_corpus", lambda *a, **k: ([], tasks))
    monkeypatch.setattr(beads_arm_fire, "resolve_cli_version", lambda *a, **k: "2.1.210")
    monkeypatch.setattr(beads_arm_fire, "resolve_bd_build", record)
    out = tmp_path / "out.json"
    assert main(["--dry-run", "--out", str(out), "--model", MODEL]) == EXIT_OK
    assert seen == [subprocess.run]
