"""mem-eg850 — the OFFLINE half of E1: the guidance ladder, the argv it moves, and the gates.

Nothing here spends anything: every test drives the ladder table, `argv_for`, and the gate
arithmetic directly. The paid halves (the top-rung mechanism preflight and the staged fire) are
wired in `membench.runner.e1_grid` and are the orchestrator's to trigger; what is tested here is
the PLUMBING around them — the argv they will send, the halt logic they will apply to a result,
and the priced plan they disclose.
"""

from __future__ import annotations

import subprocess
from itertools import pairwise
from typing import Any

import pytest

from membench.runner import e1_grid
from membench.runner.e1_grid import (
    HALT_NO_CALL,
    HALT_UNPAID,
    OK_FIRED,
    RUNG_IDS,
    RUNG_TEXT,
    STAGED_REPEATS,
    STAGED_RUNGS,
    STAGED_TASKS,
    PreflightHaltError,
    RungCell,
    assert_gates_ride_outside_metrics,
    call_rate_gates,
    discrimination_margins,
    guidance_words,
    monotonicity_violations,
    planned_call_count,
    preflight_gate,
    preflight_verdict,
    rung_step,
    staged_plan,
    summarize,
)
from membench.runner.headless_agent import (
    HeadlessClaudeAgent,
    MemoryChannel,
    assistant_event,
    result_event,
    serialize_stream,
)
from membench.runner.toolreq_realagent import VARIANT_NECESSARY, VARIANT_UNNECESSARY
from tests.toolreq_helpers import corpus_one, noop_cli_runner

MODEL = "claude-test-model-1"


def _agent(**kwargs: Any) -> HeadlessClaudeAgent:
    """A render-only agent: `argv_for` never spawns, and the runner is explicit so no test can
    acquire a real, unrecorded `claude -p` by omission."""
    return HeadlessClaudeAgent(model=MODEL, runner=noop_cli_runner, **kwargs)


def _cell(rung: str, variant: str, *, calling: int, runs: int = 4) -> RungCell:
    return RungCell(
        rung=rung,
        variant=variant,
        runs=runs,
        calling_runs=calling,
        memory_calls=calling,
        read_calls=calling,
        write_calls=0,
        paid=True,
    )


# --- AC1: the ladder is nested, asserted on the TABLE ---------------------------------


def test_ladder_is_nested() -> None:
    """Every rung's guidance CONTAINS its predecessor's, verbatim.

    Asserted on `RUNG_TEXT` — the table — and never on a rendered prompt: containment is what makes
    a rung difference an ADDED CLAUSE rather than a rewrite that merely reads stronger, and only
    the table can say that."""
    assert len(RUNG_TEXT) == len(RUNG_IDS) == 5
    assert RUNG_TEXT[0] == "", "R0 is the SILENT rung: it carries no guidance text at all"
    for n in range(len(RUNG_TEXT) - 1):
        assert RUNG_TEXT[n] in RUNG_TEXT[n + 1], (
            f"{RUNG_IDS[n]} text is not contained in {RUNG_IDS[n + 1]}: the ladder is no longer "
            "nested, so a rung comparison is a comparison of two different prompts"
        )
        assert len(RUNG_TEXT[n + 1]) > len(RUNG_TEXT[n]), (
            f"{RUNG_IDS[n + 1]} adds nothing to {RUNG_IDS[n]} — containment alone is satisfied by "
            "two identical rungs, which would be one treatment billed twice"
        )
    # The reported cost adjustment moves with the treatment, which is the whole reason it is
    # reported: R4's block is longer than R0's BY CONSTRUCTION.
    assert [guidance_words(rung) for rung in RUNG_IDS] == sorted(
        guidance_words(rung) for rung in RUNG_IDS
    )
    assert guidance_words("R0") == 0 < guidance_words("R4")


# --- AC2: every adjacent rung pair moves the argv --------------------------------------


def test_argv_diverges_per_rung(tmp_path: Any) -> None:
    """No two rungs send the same command line, so the resume cache cannot serve a neighbour's
    measurement as this rung's."""
    _seqs, tasks = corpus_one(tmp_path)
    task = tasks[0]
    agent = _agent()
    argvs = {rung: agent.argv_for(rung_step(task, rung), {}) for rung in RUNG_IDS}
    for lower, upper in pairwise(RUNG_IDS):
        assert argvs[lower] != argvs[upper], (
            f"{lower} and {upper} send the SAME argv — the cache identity cannot tell them apart, "
            "so one rung's cells would be served as the other's"
        )
    # And not merely adjacent-distinct: all five are pairwise distinct.
    assert len({tuple(a) for a in argvs.values()}) == len(RUNG_IDS)


def test_channel_axis_is_dropped_because_it_moves_nothing(tmp_path: Any) -> None:
    """The design note, enforced: on a BARE arm the two trust channels render byte-identical argv,
    which is why E1 pins one channel instead of sweeping them and billing twice."""
    _seqs, tasks = corpus_one(tmp_path)
    step = rung_step(tasks[0], "R4")
    recalled = _agent(memory_channel=MemoryChannel.RECALLED).argv_for(step, {})
    trusted = _agent(memory_channel=MemoryChannel.TRUSTED).argv_for(step, {})
    assert recalled == trusted
    assert e1_grid.CHANNEL is MemoryChannel.RECALLED


# --- AC3: the monotonicity detector actually fires -------------------------------------


def test_monotonicity_detector_fires() -> None:
    """An INVERTED fixture curve must be reported as a violation, WITH THE PAIR NAMED.

    The sharp one. A detector that stays green on inversion fails the bead, so the fixture is a
    curve that rises, falls, and rises again — the shape a mid-ladder clause that BACKFIRES
    produces, and the shape a "is the maximum at the top" check would call fine."""
    inverted = {"R0": 0.10, "R1": 0.60, "R2": 0.20, "R3": 0.30, "R4": 0.90}
    violations = monotonicity_violations(inverted)
    assert violations, "the detector stayed GREEN on an inverted curve"
    assert [f"{v.lower}->{v.upper}" for v in violations] == ["R1->R2"]
    (violation,) = violations
    assert violation.lower_rate == pytest.approx(0.60)
    assert violation.upper_rate == pytest.approx(0.20)
    assert violation.drop == pytest.approx(0.40)
    assert "R1->R2" in violation.describe()

    # The top-vs-bottom endpoints alone are monotone here (0.10 -> 0.90), so a detector reading
    # only the ends would report this curve clean. The named pair is what distinguishes them.
    assert inverted["R4"] > inverted["R0"]

    # A monotone curve is NOT reported: a detector that fires on everything names nothing.
    assert monotonicity_violations({"R0": 0.1, "R1": 0.1, "R2": 0.4, "R3": 0.4, "R4": 0.9}) == []

    # The violation reaches the emitted gate block, named — a detection nobody can read is not one.
    cells = [
        _cell(rung, VARIANT_NECESSARY, calling=int(rate * 10), runs=10)
        for rung, rate in inverted.items()
    ]
    gates = call_rate_gates(cells)
    assert gates["monotonicity"]["monotone"] is False
    assert gates["monotonicity"]["violation_pairs"] == ["R1->R2"]
    assert "R1->R2" in gates["monotonicity"]["reason"]


def test_monotonicity_tolerance_is_an_explicit_dead_band() -> None:
    """A drop inside the tolerance is not reported; the same drop at the default tolerance is.
    Softening the detector must be a visible argument, never the default."""
    curve = {"R0": 0.5, "R1": 0.45}
    assert monotonicity_violations(curve) != []
    assert monotonicity_violations(curve, tolerance=0.1) == []


def test_monotonicity_is_over_the_rungs_actually_measured() -> None:
    """The staged fire measures only the two ENDS, and those are adjacent in that run."""
    assert monotonicity_violations({"R0": 0.8, "R4": 0.2})[0].upper == "R4"


# --- AC4: R0 is not the none-arm cell --------------------------------------------------


def test_r0_argv_differs_from_none_arm(tmp_path: Any) -> None:
    """R0 carries no guidance TEXT, but it is not the existing none-arm cell: it hands the agent
    the memory tool surface, and that reaches the argv through `--allowedTools`. If the two
    collided, the none-arm's cached cells would be served as E1's tool-affordance floor."""
    _seqs, tasks = corpus_one(tmp_path)
    task = tasks[0]
    agent = _agent()
    none_arm = agent.argv_for(task.goal_step, {})
    r0 = agent.argv_for(rung_step(task, "R0"), {})
    assert r0 != none_arm
    assert "Bash" in r0[r0.index("--allowedTools") + 1]
    assert (
        "--allowedTools" not in none_arm
        or "Bash" not in none_arm[none_arm.index("--allowedTools") + 1]
    )
    # The step ids differ too, so the two cells cannot share a result identity even where a driver
    # keys on the step rather than the argv.
    assert rung_step(task, "R0").step_id != task.goal_step.step_id


# --- AC6: the gates ride on the summary, outside metrics() -----------------------------


def _summary_fixture() -> dict[str, Any]:
    cells = [
        _cell("R0", VARIANT_NECESSARY, calling=1),
        _cell("R0", VARIANT_UNNECESSARY, calling=0),
        _cell("R4", VARIANT_NECESSARY, calling=4),
        _cell("R4", VARIANT_UNNECESSARY, calling=1),
    ]
    return summarize(cells, model=MODEL, dry_run=False, repeats=4)


def test_gates_ride_outside_metrics() -> None:
    """`jq '.call_rate_gates'` is non-empty AND `jq '.cells[0].metrics.call_rate_gates'` is null —
    the acceptance criterion, on the object the driver writes to `summary-e1.json`."""
    summary = _summary_fixture()
    assert summary["call_rate_gates"]
    assert summary["cells"]
    for row in summary["cells"]:
        assert "call_rate_gates" not in row["metrics"]
    assert summary["cells"][0]["metrics"]["call_rate"] == pytest.approx(0.25)


def test_gate_placement_guard_refuses_a_smuggled_block() -> None:
    """The guard is checked at the WRITE boundary, so a summary that flattened the verdict into a
    metric vector cannot be published. Driven against a hand-built summary: the guard must reject
    the shape, not merely fail to produce it."""
    smuggled = _summary_fixture()
    smuggled["cells"][0]["metrics"]["call_rate_gates"] = {"monotone": True}
    with pytest.raises(ValueError, match="INSIDE its metrics"):
        assert_gates_ride_outside_metrics(smuggled)
    ungated = _summary_fixture()
    ungated["call_rate_gates"] = {}
    with pytest.raises(ValueError, match="no 'call_rate_gates' block"):
        assert_gates_ride_outside_metrics(ungated)


def test_primary_endpoint_is_the_margin_not_the_rate() -> None:
    """Both are emitted, and the margin is labelled the endpoint — a rung that lifts the raw rate
    with d flat bought nothing, and the block must let a reader see that."""
    summary = _summary_fixture()
    gates = summary["call_rate_gates"]
    assert gates["endpoint"] == "discrimination_margin"
    assert gates["discrimination"]["margin_by_rung"] == {
        "R0": pytest.approx(0.25),
        "R4": pytest.approx(0.75),
    }
    assert gates["monotonicity"]["call_rate_by_rung"] == {
        "R0": pytest.approx(0.25),
        "R4": pytest.approx(1.0),
    }
    # The reported (never applied) cost adjustment, and the affordance floor.
    assert gates["guidance_token_adjustment"]["guidance_words_by_rung"]["R0"] == 0
    assert gates["tool_affordance_floor"]["rung"] == "R0"
    assert gates["tool_affordance_floor"]["call_rate"] == pytest.approx(0.25)


def test_margin_needs_both_halves() -> None:
    """A rung with only one half measured gets NO margin — a one-sided margin is a call rate
    wearing the endpoint's name."""
    assert discrimination_margins([_cell("R2", VARIANT_NECESSARY, calling=2)]) == {}


def test_a_cell_cannot_claim_impossible_counts() -> None:
    with pytest.raises(ValueError, match="calling_runs 5 > runs 4"):
        _cell("R1", VARIANT_NECESSARY, calling=5)
    with pytest.raises(ValueError, match="cannot cover"):
        RungCell(
            rung="R1",
            variant=VARIANT_NECESSARY,
            runs=4,
            calling_runs=3,
            memory_calls=1,
            read_calls=1,
            write_calls=0,
            paid=True,
        )


# --- the paid plumbing: halt logic and the priced plan, driven by fixtures --------------


def test_preflight_halts_on_zero_calls() -> None:
    """AC5's halt rule, as a pure function over a FIXTURE result row: zero memory calls at the TOP
    rung is a HALT, and no interior rung is paid for."""
    kind, line = preflight_verdict({"rung": "R4", "memory_calls": 0, "paid": True})
    assert kind == HALT_NO_CALL
    assert "ZERO memory calls" in line
    with pytest.raises(PreflightHaltError) as excinfo:
        preflight_gate({"rung": "R4", "memory_calls": 0, "paid": True})
    assert excinfo.value.kind == HALT_NO_CALL


def test_preflight_halts_on_an_unpaid_row() -> None:
    """A fixture runner's calls are the fixture's. An unpaid row halts however many calls it
    shows — the mechanism claim is only ever about a real `claude -p`."""
    kind, _line = preflight_verdict({"rung": "R4", "memory_calls": 7, "paid": False})
    assert kind == HALT_UNPAID


def test_preflight_passes_a_fired_paid_row() -> None:
    kind, line = preflight_verdict({"rung": "R4", "memory_calls": 2, "paid": True})
    assert kind == OK_FIRED and "fired" in line
    assert preflight_gate({"rung": "R4", "memory_calls": 2, "paid": True})["kind"] == OK_FIRED


def test_preflight_runs_at_the_top_rung_and_has_no_free_path() -> None:
    """The preflight is deliberately NOT simulated (the `toolreq_builtin_grid.preflight` stance):
    it takes no `dry_run` and no `runner`, so there is no way to satisfy the mechanism check with
    a cooperating stand-in."""
    assert e1_grid.PREFLIGHT_RUNG == RUNG_IDS[-1] == "R4"
    params = e1_grid.preflight.__code__.co_varnames[
        : e1_grid.preflight.__code__.co_argcount + e1_grid.preflight.__code__.co_kwonlyargcount
    ]
    assert "dry_run" not in params and "runner" not in params


def test_staged_plan_is_priced_not_quoted() -> None:
    """T=8, R=5, the two ends of the ladder, both corpus halves: 160 real calls."""
    assert STAGED_RUNGS == ("R0", "R4")
    plan = staged_plan(64)
    assert plan == {
        "rungs": ["R0", "R4"],
        "n_tasks": STAGED_TASKS,
        "repeats": STAGED_REPEATS,
        "n_variants": 2,
        "calls": 160,
        "halt_rule": plan["halt_rule"],
    }
    assert "ZERO" in plan["halt_rule"] or "zero" in plan["halt_rule"]
    # Priced by product over what is actually run, so adding a rung moves the disclosed cost.
    assert planned_call_count(rungs=RUNG_IDS, n_tasks=8, repeats=5) == 400
    # A corpus smaller than the staged size is priced at the corpus, never at the constant.
    assert staged_plan(3)["calls"] == 60


def test_cli_refuses_to_spend_without_a_pinned_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The paid entrypoint refuses BEFORE the corpus loads, so a refused run costs nothing."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    assert e1_grid.main(["--preflight", "--rung", "R4"]) == e1_grid.EXIT_REFUSED
    assert "model" in capsys.readouterr().err.lower()


def test_cli_refuses_to_spend_without_an_oauth_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert e1_grid.main(["--staged", "--model", MODEL]) == e1_grid.EXIT_REFUSED
    assert "CLAUDE_CODE_OAUTH_TOKEN" in capsys.readouterr().err


def test_cli_plan_path_spends_nothing_and_names_the_paid_commands(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default path prices the ladder and fires nothing; the paid commands are printed so the
    orchestrator has them verbatim."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seqs, _tasks = corpus_one(tmp_path)
    assert e1_grid.main(["--corpus-dir", str(tmp_path / "corpus"), "--json"]) == e1_grid.EXIT_OK
    captured = capsys.readouterr()
    plan = __import__("json").loads(captured.out)
    assert plan["rungs"] == list(RUNG_IDS)
    assert plan["staged_plan"]["repeats"] == STAGED_REPEATS


def test_cli_reports_a_missing_corpus_as_infrastructure_not_a_result(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "empty").mkdir()
    assert e1_grid.main(["--corpus-dir", str(tmp_path / "empty")]) == e1_grid.EXIT_NO_CORPUS


# --------------------------------------------------------------------------------------
# run_rung_cell end-to-end (pre-spend smoke, mem-eg850)
#
# Every other test in this file exercises run_rung_cell's PARTS — rung_step, the counter, the
# gates — and none of them execute the function itself. That left the first paid keystroke as the
# first end-to-end execution of the cell, which is the one place a wiring defect costs money to
# find. These two tests drive the real function with an injected runner: the store provisioning,
# the sandbox, the argv render, the stream parse and the counter all run for real, and only the
# `claude -p` spawn is replaced.
# --------------------------------------------------------------------------------------


def _calling_runner(*commands: str) -> Any:
    """A `claude -p` stand-in whose stream carries `commands` as Bash tool_use blocks.

    Deliberately UNCONDITIONAL: it emits the same calls at every rung, so it can prove the cell
    counts what the stream contains and can never reproduce a rung effect. `_silent_runner` is the
    zero end of the same fixture."""

    def runner(argv: Any, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        events = [
            assistant_event([("Bash", {"command": command}) for command in commands]),
            result_event(),
        ]
        return subprocess.CompletedProcess(list(argv), 0, serialize_stream(events), "")

    return runner


def test_run_rung_cell_counts_the_memory_calls_its_stream_carries(tmp_path: Any) -> None:
    """The whole cell, executed: provision a store, render the rung's step, parse the stream, count.

    Three calls per repeat over two repeats is six, split two reads / one write per repeat. The
    read and write counts are deliberately UNEQUAL and neither equals the total: a fixture with
    reads == writes passes whether or not the two filters are swapped, which is the geometry this
    test was first written with and which let a swapped-filter mutant survive."""
    _seqs, tasks = corpus_one(tmp_path)
    cell = e1_grid.run_rung_cell(
        tasks[0],
        rung="R4",
        repeats=2,
        model=MODEL,
        dry_run=False,
        runner=_calling_runner("bd remember --key k 'a value'", "bd recall k", "bd recall other"),
    )
    assert cell.runs == 2
    assert cell.calling_runs == 2
    assert cell.memory_calls == 6
    assert cell.read_calls == 4
    assert cell.write_calls == 2
    assert set(cell.verbs) == {"remember", "recall"}
    assert cell.paid is False


def test_run_rung_cell_is_unpaid_and_empty_on_the_dry_run_path(tmp_path: Any) -> None:
    """The free path proves the plumbing and measures zero, and says so in `paid` — the pairing that
    keeps a green smoke from reading as a mechanism result."""
    _seqs, tasks = corpus_one(tmp_path)
    cell = e1_grid.run_rung_cell(tasks[0], rung="R4", repeats=1, model=MODEL, dry_run=True)
    assert cell.memory_calls == 0
    assert cell.calling_runs == 0
    assert cell.paid is False
