"""mem-eg850 — the OFFLINE half of E1: the guidance ladder, the argv it moves, and the gates.

Nothing here spends anything: every test drives the ladder table, `argv_for`, and the gate
arithmetic directly. The paid halves (the top-rung mechanism preflight and the staged fire) are
wired in `membench.runner.e1_grid` and are the orchestrator's to trigger; what is tested here is
the PLUMBING around them — the argv they will send, the halt logic they will apply to a result,
and the priced plan they disclose.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
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
    LegRecord,
    PreflightHaltError,
    ResumeMismatchError,
    RungCell,
    assert_gates_ride_outside_metrics,
    call_rate_gates,
    corpus_fingerprint,
    discrimination_margins,
    grid_keys,
    guidance_words,
    monotonicity_violations,
    planned_call_count,
    pooled_rates,
    preflight_gate,
    preflight_verdict,
    resume_cells,
    rung_step,
    staged_plan,
    summarize,
)
from membench.runner.headless_agent import (
    HeadlessAgentError,
    HeadlessClaudeAgent,
    MemoryChannel,
    assistant_event,
    result_event,
    serialize_stream,
)
from membench.runner.toolreq_corpus import load_twin_corpus
from membench.runner.toolreq_realagent import VARIANT_NECESSARY, VARIANT_UNNECESSARY
from membench.spawn import with_child
from tests.toolreq_helpers import corpus_one, noop_cli_runner

MODEL = "claude-test-model-1"


def _agent(**kwargs: Any) -> HeadlessClaudeAgent:
    """A render-only agent: `argv_for` never spawns, and the runner is explicit so no test can
    acquire a real, unrecorded `claude -p` by omission."""
    return HeadlessClaudeAgent(model=MODEL, runner=noop_cli_runner, **kwargs)


def _cell(rung: str, variant: str, *, calling: int, runs: int = 4, work_id: str = "") -> RungCell:
    return RungCell(
        rung=rung,
        variant=variant,
        runs=runs,
        calling_runs=calling,
        memory_calls=calling,
        read_calls=calling,
        write_calls=0,
        paid=True,
        work_id=work_id,
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
    with pytest.raises(ValueError, match="calling_runs 5 > measured 4"):
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
    # --staged prices and spends nothing, so it is no longer a paid path; --fire-staged is.
    assert e1_grid.main(["--fire-staged", "--model", MODEL]) == e1_grid.EXIT_REFUSED
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


def _native_reach_runner() -> Any:
    """mem-gj0pc's exact transcript: the agent says it will check memory and Reads MEMORY.md under
    the pinned config dir. The path is read back off the env the cell handed the runner, which is
    the only way a fixture can name a per-repeat tempdir it never chose."""

    def runner(argv: Any, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env") or {}
        config = env["CLAUDE_CONFIG_DIR"]  # type: ignore[index]
        target = f"{config}/projects/-tmp/memory/MEMORY.md"
        events = [
            assistant_event([("Read", {"file_path": target})]),
            result_event(),
        ]
        return subprocess.CompletedProcess(list(argv), 0, serialize_stream(events), "")

    return runner


def test_a_native_memory_reach_counts_as_a_memory_call(tmp_path: Any) -> None:
    """The first paid R4 cycle scored this reach as ZERO because the counter saw bd verbs only.
    Both surfaces count now, so the same stream is one read and no writes."""
    _seqs, tasks = corpus_one(tmp_path)
    cell = e1_grid.run_rung_cell(
        tasks[0], rung="R4", repeats=1, model=MODEL, dry_run=False, runner=_native_reach_runner()
    )
    assert cell.memory_calls == 1
    assert cell.calling_runs == 1
    assert cell.read_calls == 1
    assert cell.write_calls == 0
    assert list(cell.verbs) == ["native_read"]


def test_staged_cells_applies_the_task_cap_per_variant_not_across_the_list(tmp_path: Any) -> None:
    """The priced bill is len(rungs) * n_tasks * repeats * 2, so `n_tasks` is PER VARIANT. Capping
    the flat list instead would run half the grid and report a whole one."""
    _seqs, tasks = corpus_one(tmp_path)
    calls: list[tuple[str, str]] = []

    def counting(argv: Any, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 0, serialize_stream([result_event()]), "")

    cells = e1_grid.staged_cells(
        tasks * 4,
        model=MODEL,
        rungs=("R0",),
        n_tasks=2,
        repeats=1,
        runner=counting,
        on_cell=lambda cell: calls.append((cell.rung, cell.variant)),
    )
    variants = {task.variant for task in tasks * 4}
    assert len(cells) == 2 * len(variants)
    assert calls == [(cell.rung, cell.variant) for cell in cells]


def test_fire_staged_is_a_different_flag_from_the_one_that_prices_it(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--staged still prices and spends nothing. Authorization and execution stay two keystrokes."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    _seqs, _tasks = corpus_one(tmp_path)
    code = e1_grid.main(["--corpus-dir", str(tmp_path / "corpus"), "--staged", "--model", MODEL])
    assert code == e1_grid.EXIT_OK
    assert "PRICED, NOT FIRED" in capsys.readouterr().err


def test_fire_staged_refuses_without_a_token(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert e1_grid.main(["--fire-staged", "--model", MODEL]) == e1_grid.EXIT_REFUSED
    assert "CLAUDE_CODE_OAUTH_TOKEN" in capsys.readouterr().err


# --- timeouts are unmeasured legs, cells pool, and a dead fire resumes -----------------


def _timeout_error() -> HeadlessAgentError:
    """The exact shape ``spawn.run_checked`` raises: a HeadlessAgentError CAUSED BY
    TimeoutExpired."""
    try:
        raise subprocess.TimeoutExpired(cmd=["claude", "-p"], timeout=600.0)
    except subprocess.TimeoutExpired as exc:
        err = HeadlessAgentError("claude -p did not finish within 600.0s")
        err.__cause__ = exc
        return err


def _timing_out_runner(timeout_legs: set[int], *commands: str) -> Any:
    """`_calling_runner`, except the legs numbered in ``timeout_legs`` (0-based, per cell) time out
    the way the first staged fire's cell 13 did."""
    leg = {"n": 0}
    calling = _calling_runner(*commands)

    def runner(argv: Any, **kwargs: object) -> subprocess.CompletedProcess[str]:
        i = leg["n"]
        leg["n"] += 1
        if i in timeout_legs:
            raise _timeout_error()
        result: subprocess.CompletedProcess[str] = calling(argv, **kwargs)
        return result

    return runner


def test_a_timed_out_leg_is_unmeasured_not_a_non_calling_run(tmp_path: Any) -> None:
    """Three legs, the middle one times out, the other two call: the rate is 2/2, not 2/3, and
    the timeout is REPORTED on the cell rather than folded into the zero side of the rate."""
    _seqs, tasks = corpus_one(tmp_path)
    cell = e1_grid.run_rung_cell(
        tasks[0],
        rung="R0",
        repeats=3,
        model=MODEL,
        dry_run=False,
        runner=_timing_out_runner({1}, "bd recall k"),
    )
    assert cell.runs == 3
    assert cell.timed_out_runs == 1
    assert cell.measured_runs == 2
    assert cell.calling_runs == 2
    assert cell.call_rate == pytest.approx(1.0)
    assert cell.work_id == tasks[0].work_id
    assert cell.metrics()["timed_out_runs"] == 1


def test_an_isolated_failed_leg_is_unmeasured_and_kept_apart_from_a_timeout(
    tmp_path: Any,
) -> None:
    """One leg fails, the others return: the failure leaves the DENOMINATOR the way a timeout does,
    and is reported on its own field so the diagnosis is not folded into 'it timed out'."""
    _seqs, tasks = corpus_one(tmp_path)
    calls = {"n": 0}

    def flaky(argv: Any, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise HeadlessAgentError("claude -p failed (exit 1): transient")
        return _calling_runner("bd recall k")(argv)

    cell = e1_grid.run_rung_cell(
        tasks[0], rung="R0", repeats=3, model=MODEL, dry_run=False, runner=flaky
    )
    assert (cell.runs, cell.errored_runs, cell.timed_out_runs) == (3, 1, 0)
    assert cell.measured_runs == 2
    assert cell.calling_runs == 2
    assert cell.call_rate == pytest.approx(1.0)
    assert cell.metrics()["errored_runs"] == 1


def test_consecutive_failed_legs_halt_rather_than_filling_the_grid(tmp_path: Any) -> None:
    """A broken rig fails EVERY leg. Tolerating that leg by leg buys a whole grid of cells that
    measured nothing, so a run of failures is a halt — and the run has to be CONSECUTIVE, or a
    flake in each of three cells would halt a fire that is working."""
    _seqs, tasks = corpus_one(tmp_path)

    def broken(argv: Any, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise HeadlessAgentError("claude -p failed (exit 1): rig is broken")

    with pytest.raises(e1_grid.RigHaltError, match="3 consecutive"):
        e1_grid.run_rung_cell(
            tasks[0], rung="R0", repeats=5, model=MODEL, dry_run=False, runner=broken
        )

    interleaved = {"n": 0}

    def every_other(argv: Any, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        interleaved["n"] += 1
        if interleaved["n"] % 2 == 0:
            raise HeadlessAgentError("claude -p failed (exit 1): flake")
        return _calling_runner("bd recall k")(argv)

    cell = e1_grid.run_rung_cell(
        tasks[0], rung="R0", repeats=5, model=MODEL, dry_run=False, runner=every_other
    )
    assert (cell.errored_runs, cell.measured_runs, cell.calling_runs) == (2, 3, 3)


def test_a_quota_refusal_halts_the_fire_and_is_read_off_the_stream_not_the_message(
    tmp_path: Any,
) -> None:
    """The account refusing is not a defect and not a measurement: every further leg would fail the
    same way and be billed as nothing.

    Classified on the CLI's own ``api_error_status`` field, carried on the exception by
    ``run_checked``. Not on the message — ``run_checked`` redacts and truncates that by design, so
    a fire that matched prose there would keep spending against an exhausted account whenever the
    wording moved."""
    _seqs, tasks = corpus_one(tmp_path)
    refusal = subprocess.CompletedProcess(
        ["claude"],
        1,
        json.dumps(
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 429,
                "result": "session limit",
            }
        ),
        "",
    )

    def refused(argv: Any, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise with_child(HeadlessAgentError("claude -p failed (exit 1): <redacted>"), refusal)

    with pytest.raises(e1_grid.QuotaHaltError, match="refused the call"):
        e1_grid.run_rung_cell(
            tasks[0], rung="R0", repeats=5, model=MODEL, dry_run=False, runner=refused
        )
    # The message says nothing about a quota; only the stream field does.
    assert e1_grid.is_quota_halt(HeadlessAgentError("You've hit your session limit")) is False
    served = subprocess.CompletedProcess(["claude"], 1, '{"type":"result","is_error":true}', "")
    assert e1_grid.is_quota_halt(with_child(HeadlessAgentError("boom"), served)) is False
    # A non-result event carrying a status is NOT the account refusing.
    tool = subprocess.CompletedProcess(
        ["claude"], 1, '{"type":"assistant","api_error_status":429}', ""
    )
    assert e1_grid.is_quota_halt(with_child(HeadlessAgentError("boom"), tool)) is False


def test_the_timeout_classifier_reads_the_cause_chain(tmp_path: Any) -> None:
    assert e1_grid.is_spawn_timeout(_timeout_error()) is True
    assert e1_grid.is_spawn_timeout(HeadlessAgentError("did not finish within 600.0s")) is False


def test_a_cell_whose_every_leg_timed_out_has_no_rate() -> None:
    """Measured nothing is not measured zero: no ``call_rate``, and it contributes NOTHING to a
    pooled rate rather than a 0 to its numerator and denominator."""
    cell = RungCell(
        rung="R4",
        variant=VARIANT_NECESSARY,
        runs=5,
        calling_runs=0,
        memory_calls=0,
        read_calls=0,
        write_calls=0,
        paid=True,
        work_id="w-dead",
        timed_out_runs=5,
    )
    with pytest.raises(ValueError, match="no leg returned a stream"):
        _ = cell.call_rate
    assert cell.metrics()["call_rate"] is None
    assert pooled_rates([cell], VARIANT_NECESSARY) == {}
    with pytest.raises(ValueError, match="timed_out_runs 6"):
        RungCell(
            rung="R4",
            variant=VARIANT_NECESSARY,
            runs=5,
            calling_runs=0,
            memory_calls=0,
            read_calls=0,
            write_calls=0,
            paid=True,
            timed_out_runs=6,
        )


def test_rates_pool_over_task_cells_not_last_wins_and_not_a_mean() -> None:
    """The first staged fire's gate block read R0/necessary as 0.8 — the LAST task cell's 4/5 —
    while the pooled rate over eight cells was 16/40 = 0.4. Three cells here: 4/5, 1/5, and 1/2
    (three of its legs timed out). Pooled = 6/12 = 0.5. Last-wins says 0.5 too by accident of
    order, so the cells are listed with the 4/5 LAST; a mean of cell rates says 0.5 as well, so
    the 1/2 cell's denominator is what separates the three readings: pooled 6/12, last 0.8,
    mean (0.8 + 0.2 + 0.5) / 3 = 0.5. The 1/5 cell is therefore weighted 5:2 against the 1/2."""
    cells = [
        _cell("R0", VARIANT_NECESSARY, calling=1, runs=5),
        RungCell(
            rung="R0",
            variant=VARIANT_NECESSARY,
            runs=5,
            calling_runs=1,
            memory_calls=1,
            read_calls=1,
            write_calls=0,
            paid=True,
            timed_out_runs=3,
        ),
        _cell("R0", VARIANT_NECESSARY, calling=4, runs=5),
        _cell("R0", VARIANT_UNNECESSARY, calling=1, runs=5),
        _cell("R0", VARIANT_UNNECESSARY, calling=2, runs=5),
    ]
    assert pooled_rates(cells, VARIANT_NECESSARY) == {"R0": pytest.approx(6 / 12)}
    assert pooled_rates(cells, VARIANT_UNNECESSARY) == {"R0": pytest.approx(3 / 10)}
    assert discrimination_margins(cells) == {"R0": pytest.approx(6 / 12 - 3 / 10)}
    gates = call_rate_gates(cells)
    assert gates["monotonicity"]["call_rate_by_rung"] == {"R0": pytest.approx(6 / 12)}


def test_a_cell_row_round_trips_through_the_artifact() -> None:
    cell = RungCell(
        rung="R4",
        variant=VARIANT_UNNECESSARY,
        runs=5,
        calling_runs=2,
        memory_calls=7,
        read_calls=5,
        write_calls=2,
        paid=True,
        verbs=("native_read", "recall"),
        work_id="w-7",
        timed_out_runs=1,
    )
    assert RungCell.from_row(cell.row()) == cell
    assert cell.key == ("R4", VARIANT_UNNECESSARY, "w-7")


def test_staged_cells_keeps_landed_cells_and_buys_only_the_rest(tmp_path: Any) -> None:
    """Resume: a cell already keyed ``(rung, variant, work_id)`` in ``landed`` is returned in place
    and its runner is never invoked. The kept cell is distinguishable from a re-run (its counts are
    ones the runner could not produce), so a resume that quietly re-bought it would show."""
    _seqs, tasks = corpus_one(tmp_path)
    spawned: list[str] = []

    def counting(argv: Any, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        spawned.append(" ".join(argv))
        return subprocess.CompletedProcess(list(argv), 0, serialize_stream([result_event()]), "")

    prior = RungCell(
        rung="R0",
        variant=tasks[0].variant,
        runs=1,
        calling_runs=1,
        memory_calls=9,
        read_calls=9,
        write_calls=0,
        paid=True,
        work_id=tasks[0].work_id,
    )
    cells = e1_grid.staged_cells(
        tasks,
        model=MODEL,
        rungs=("R0", "R4"),
        n_tasks=1,
        repeats=1,
        runner=counting,
        landed=[prior],
    )
    key = (tasks[0].variant, tasks[0].work_id)
    assert [c.key for c in cells] == [("R0", *key), ("R4", *key)]
    assert cells[0] is prior
    assert cells[1].memory_calls == 0
    assert len(spawned) == 1


IDENTITY = {"cli_version": "9.9.9", "corpus": "corpus-abc", "repeats": 5}


def _fake_cells(tasks: Any) -> Any:
    """``run_rung_cell`` without the spawn: the CLI paths under test are the resume, the lock, the
    artifact and the halts, and driving those through a real sandbox per leg buys nothing but
    minutes."""

    def make(task: Any, **kwargs: Any) -> RungCell:
        on_leg = kwargs.get("on_leg")
        if on_leg is not None:
            for i in range(int(kwargs["repeats"])):
                on_leg(
                    LegRecord(
                        rung=str(kwargs["rung"]),
                        variant=task.variant,
                        work_id=task.work_id,
                        leg=i,
                        status="ok",
                        memory_calls=1,
                        read_calls=1,
                        stream='{"type":"result"}',
                    )
                )
        return RungCell(
            rung=str(kwargs["rung"]),
            variant=task.variant,
            runs=int(kwargs["repeats"]),
            calling_runs=int(kwargs["repeats"]),
            memory_calls=int(kwargs["repeats"]),
            read_calls=int(kwargs["repeats"]),
            write_calls=0,
            paid=True,
            work_id=task.work_id,
        )

    return make


def _identified(cells: list[RungCell], **over: Any) -> dict[str, Any]:
    return summarize(
        cells,
        model=MODEL,
        dry_run=False,
        repeats=5,
        cli_version=str(IDENTITY["cli_version"]),
        corpus=str(IDENTITY["corpus"]),
        **over,
    )


def test_resume_refuses_another_rigs_artifact_and_drops_unmeasured_cells() -> None:
    dead = RungCell(
        rung="R0",
        variant=VARIANT_NECESSARY,
        runs=5,
        calling_runs=0,
        memory_calls=0,
        read_calls=0,
        write_calls=0,
        paid=True,
        work_id="w-dead",
        timed_out_runs=5,
    )
    live = _cell("R0", VARIANT_UNNECESSARY, calling=2, runs=5, work_id="w-live")
    summary = _identified([dead, live])
    assert resume_cells(summary, model=MODEL, **IDENTITY) == [live]
    with pytest.raises(ResumeMismatchError, match="model"):
        resume_cells(summary, model="claude-other-model-2", **IDENTITY)
    with pytest.raises(ResumeMismatchError, match="surface_fingerprint"):
        resume_cells({**summary, "surface_fingerprint": "stale"}, model=MODEL, **IDENTITY)


def test_resume_refuses_every_identity_field_it_cannot_match() -> None:
    """Each field names something that changes what a leg MEASURES, so each one alone is enough to
    refuse: a different binary, a different corpus, a different number of legs per cell."""
    summary = _identified([_cell("R0", VARIANT_NECESSARY, calling=2, runs=5, work_id="w-0")])
    with pytest.raises(ResumeMismatchError, match="cli_version"):
        resume_cells(summary, model=MODEL, **{**IDENTITY, "cli_version": "9.9.10"})
    with pytest.raises(ResumeMismatchError, match="corpus_fingerprint"):
        resume_cells(summary, model=MODEL, **{**IDENTITY, "corpus": "corpus-xyz"})
    with pytest.raises(ResumeMismatchError, match="repeat"):
        resume_cells(summary, model=MODEL, **{**IDENTITY, "repeats": 3})
    # A blank field on the RIG is a refusal too: an identity it cannot state cannot be matched.
    with pytest.raises(ResumeMismatchError, match="cannot state its own"):
        resume_cells(summary, model=MODEL, **{**IDENTITY, "cli_version": ""})


def test_resume_drops_unpaid_and_unkeyed_rows_and_refuses_duplicates_and_strangers() -> None:
    """The filters and the refusals are different answers to different problems, and which is
    which is load-bearing.

    An unpaid row is a FIXTURE's call rate and a row with no ``work_id`` keys to a cell no task
    has: both are dropped, and the real cells get bought. A duplicate key or a cell outside this
    grid means the artifact was written by a fire this one is not continuing — nothing here can
    pick the right row, so it refuses rather than publishing half of each."""
    paid = _cell("R0", VARIANT_NECESSARY, calling=2, runs=5, work_id="w-0")
    unpaid = RungCell(
        rung="R0",
        variant=VARIANT_UNNECESSARY,
        runs=5,
        calling_runs=5,
        memory_calls=5,
        read_calls=5,
        write_calls=0,
        paid=False,
        work_id="w-0",
    )
    unkeyed = _cell("R4", VARIANT_NECESSARY, calling=3, runs=5)
    assert resume_cells(_identified([paid, unpaid, unkeyed]), model=MODEL, **IDENTITY) == [paid]

    dup = _identified([paid, paid])
    with pytest.raises(ResumeMismatchError, match="twice"):
        resume_cells(dup, model=MODEL, **IDENTITY)

    with pytest.raises(ResumeMismatchError, match="not a cell of the grid"):
        resume_cells(
            _identified([paid]), model=MODEL, grid=[("R0", VARIANT_NECESSARY, "w-9")], **IDENTITY
        )


def test_grid_keys_names_exactly_the_cells_the_fire_buys(tmp_path: Any) -> None:
    """The set a resume is checked against is built by the same per-variant slice the fire
    executes, so a row can never be refused as a stranger to a grid that will in fact buy it."""
    _seqs, tasks = corpus_one(tmp_path)
    keys = grid_keys(tasks, rungs=("R0", "R4"), n_tasks=1)
    spawned: list[Any] = []

    def counting(argv: Any, **kwargs: object) -> subprocess.CompletedProcess[str]:
        spawned.append(argv)
        return _calling_runner()(argv, **kwargs)

    cells = e1_grid.staged_cells(
        tasks, model=MODEL, rungs=("R0", "R4"), n_tasks=1, repeats=1, runner=counting
    )
    assert [cell.key for cell in cells] == keys


def test_the_corpus_fingerprint_moves_when_a_task_does(tmp_path: Any) -> None:
    """Order is not a measured input; the task set is. A twin whose request changed is a different
    measurement, and this is the field that stops a resume pooling it with the old one."""
    _seqs, tasks = corpus_one(tmp_path)
    twin = replace(tasks[0], variant=VARIANT_UNNECESSARY)
    both = [tasks[0], twin]
    assert corpus_fingerprint(both) == corpus_fingerprint(list(reversed(both)))
    assert corpus_fingerprint(both) != corpus_fingerprint(tasks)
    reworded = replace(
        twin,
        goal_step=twin.goal_step.model_copy(
            update={"user_request": f"{twin.goal_step.user_request} (and the other two subjects)"}
        ),
    )
    assert corpus_fingerprint([tasks[0], reworded]) != corpus_fingerprint(both)


def test_every_leg_is_persisted_with_the_stream_it_was_counted_from(tmp_path: Any) -> None:
    """The cell is a count; the leg is the evidence under it.

    Every counter change this rig has made changed what a leg SCORES, and a scoring fix with no
    stream to re-score costs the whole grid again in real money. The stream is redacted on the way
    out: an evidence file outlives the reason it was kept, and the env this runs under carries an
    OAuth token."""
    _seqs, tasks = corpus_one(tmp_path)
    legs: list[LegRecord] = []
    cell = e1_grid.run_rung_cell(
        tasks[0],
        rung="R4",
        repeats=3,
        model=MODEL,
        dry_run=False,
        runner=_timing_out_runner({1}, "bd recall k", "bd remember k=v"),
        on_leg=legs.append,
    )
    assert [leg.status for leg in legs] == ["ok", "timeout", "ok"]
    assert [leg.leg for leg in legs] == [0, 1, 2]
    assert {leg.work_id for leg in legs} == {tasks[0].work_id}
    ok = [leg for leg in legs if leg.status == "ok"]
    # The legs RECONSTRUCT the cell: same calls, same read/write split. A per-leg record that
    # cannot be summed back to the cell it came from is not evidence for that cell.
    assert sum(leg.memory_calls for leg in ok) == cell.memory_calls
    assert sum(leg.read_calls for leg in ok) == cell.read_calls
    assert sum(leg.write_calls for leg in ok) == cell.write_calls
    assert all("bd recall k" in leg.stream for leg in ok)
    timed_out = next(leg for leg in legs if leg.status == "timeout")
    assert timed_out.stream == "" and timed_out.detail
    assert legs[0].filename == f"R4__{tasks[0].variant}__{tasks[0].work_id}__0.json"


def test_a_secret_in_a_leg_stream_is_redacted_before_it_is_persisted(tmp_path: Any) -> None:
    """The stream is written to a file that outlives the run. Redaction happens where the record is
    BUILT, not where it is written, so a caller that persists it some other way cannot leak."""
    _seqs, tasks = corpus_one(tmp_path)
    legs: list[LegRecord] = []

    def leaky(argv: Any, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        events = [
            assistant_event([("Bash", {"command": "bd recall k"})]),
            result_event("token sk-ant-oat01-DEADBEEFdeadbeefDEADBEEFdeadbeef0123456789ab"),
        ]
        return subprocess.CompletedProcess(list(argv), 0, serialize_stream(events), "")

    e1_grid.run_rung_cell(
        tasks[0], rung="R0", repeats=1, model=MODEL, dry_run=False, runner=leaky, on_leg=legs.append
    )
    assert "DEADBEEFdeadbeef" not in legs[0].stream
    assert "bd recall k" in legs[0].stream


def test_the_out_file_is_written_atomically(tmp_path: Any) -> None:
    """A truncate-then-write leaves a window where the file that makes a resume possible is a
    partial file, and a kill inside it costs every cell the fire has bought.

    Content and a clean tmp are NOT enough to assert here: a copy-over-the-top passes both while
    still writing in place. What separates a rename from a copy is observable, so assert THAT — the
    destination gets a new inode, and a reader holding the old file keeps reading the old bytes
    rather than watching them change underneath it (an isolated-revert mutant that swapped
    ``os.replace`` for ``shutil.copyfile`` survived the weaker assertions)."""
    out = tmp_path / "summary.json"
    out.write_text('{"cells": []}', encoding="utf-8")
    before = out.stat().st_ino
    held = out.open("rb")
    try:
        e1_grid.atomic_write_json(out, {"cells": [1, 2, 3]})
        assert json.loads(out.read_text(encoding="utf-8")) == {"cells": [1, 2, 3]}
        assert list(tmp_path.glob("*.tmp-*")) == []
        assert out.stat().st_ino != before
        assert json.loads(held.read().decode("utf-8")) == {"cells": []}
    finally:
        held.close()


def test_two_fires_cannot_share_one_out_file(tmp_path: Any) -> None:
    """Both would resume from it, both would re-buy the cells the other is buying, and the last
    writer would publish its own half as the grid."""
    out = tmp_path / "summary.json"
    with e1_grid.out_lock(out):
        assert (tmp_path / "summary.json.lock").exists()
        with pytest.raises(ResumeMismatchError, match="another fire holds"), e1_grid.out_lock(out):
            pass
    assert not (tmp_path / "summary.json.lock").exists()
    # Released on the way out of a FAILING fire too, or one crash locks the artifact forever.
    with pytest.raises(RuntimeError, match="boom"), e1_grid.out_lock(out):
        raise RuntimeError("boom")
    assert not (tmp_path / "summary.json.lock").exists()


def test_fire_staged_resumes_from_its_own_out_file(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end through the CLI: a --fire-staged pointed at an --out written by a rig with a
    different surface fingerprint is REFUSED, so a mid-run upgrade cannot land in the old grid."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setattr(e1_grid, "resolve_cli_version", lambda: "9.9.9")
    _seqs, _tasks = corpus_one(tmp_path)
    out = tmp_path / "partial.json"
    cell = _cell("R0", VARIANT_NECESSARY, calling=1, work_id="w-0")
    summary = _identified([cell])
    out.write_text(json.dumps({**summary, "surface_fingerprint": "stale"}), encoding="utf-8")
    argv = ["--corpus-dir", str(tmp_path / "corpus"), "--fire-staged", "--model", MODEL]
    code = e1_grid.main([*argv, "--out", str(out)])
    assert code == e1_grid.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().err


def test_a_fire_publishes_the_grid_it_ran_and_the_legs_under_it(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """What --out holds when the fire finishes is what stdout printed. They diverged before: --out
    was last written by the per-cell accumulator, so a resumed fire published the accumulator's
    order and the reader of the file got a different grid from the reader of the pipe."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setattr(e1_grid, "resolve_cli_version", lambda: "9.9.9")
    monkeypatch.setattr(e1_grid, "STAGED_REPEATS", 1)
    _seqs, tasks = corpus_one(tmp_path)
    monkeypatch.setattr(e1_grid, "run_rung_cell", _fake_cells(tasks))
    out = tmp_path / "summary.json"
    code = e1_grid.main(
        [
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "--fire-staged",
            "--model",
            MODEL,
            "--out",
            str(out),
        ]
    )
    assert code == e1_grid.EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text(encoding="utf-8")) == printed
    assert printed["cli_version"] == "9.9.9"
    _, twins = load_twin_corpus(tmp_path / "corpus")
    assert printed["corpus_fingerprint"] == corpus_fingerprint(twins)
    assert printed["rungs"] == ["R0", "R4"]
    # Both halves of the twin, at both ends of the ladder: the grid the fire actually buys.
    assert [(row["rung"], row["variant"]) for row in printed["cells"]] == [
        (rung, variant)
        for rung in ("R0", "R4")
        for variant in (VARIANT_NECESSARY, VARIANT_UNNECESSARY)
    ]
    assert not (tmp_path / "summary.json.lock").exists()
    legs = sorted(path.name for path in (tmp_path / "summary.json.legs").iterdir())
    assert legs == sorted(
        f"{rung}__{variant}__{tasks[0].work_id}__0.json"
        for rung in ("R0", "R4")
        for variant in (VARIANT_NECESSARY, VARIANT_UNNECESSARY)
    )


def test_a_resumed_fire_publishes_the_grid_order_not_the_order_it_bought_them_in(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The per-cell accumulator writes ``--out`` as the fire goes, and the accumulator is ordered
    RESUMED-FIRST, then newly bought. The grid is ordered by (rung, variant, task). Those coincide
    on a fresh fire, which is why a mutant that dropped the final rewrite survived the fresh-fire
    test. Resume the LAST two cells of the grid so the two orders genuinely disagree, and pin that
    --out publishes the grid."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setattr(e1_grid, "resolve_cli_version", lambda: "9.9.9")
    monkeypatch.setattr(e1_grid, "STAGED_REPEATS", 1)
    _seqs, tasks = corpus_one(tmp_path)
    monkeypatch.setattr(e1_grid, "run_rung_cell", _fake_cells(tasks))
    _, twins = load_twin_corpus(tmp_path / "corpus")
    work_id = tasks[0].work_id

    # R4 is the TAIL of the grid; resuming it and buying R0 puts the accumulator at R4,R4,R0,R0.
    out = tmp_path / "summary.json"
    resumed = [
        _cell("R4", VARIANT_NECESSARY, calling=1, runs=1, work_id=work_id),
        _cell("R4", VARIANT_UNNECESSARY, calling=0, runs=1, work_id=work_id),
    ]
    out.write_text(
        json.dumps(
            summarize(
                resumed,
                model=MODEL,
                dry_run=False,
                repeats=1,
                cli_version="9.9.9",
                corpus=corpus_fingerprint(twins),
            )
        ),
        encoding="utf-8",
    )

    code = e1_grid.main(
        [
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "--fire-staged",
            "--model",
            MODEL,
            "--out",
            str(out),
        ]
    )
    assert code == e1_grid.EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    grid_order = [
        (rung, variant)
        for rung in ("R0", "R4")
        for variant in (VARIANT_NECESSARY, VARIANT_UNNECESSARY)
    ]
    assert [(row["rung"], row["variant"]) for row in printed["cells"]] == grid_order
    # The whole point: the file and the pipe agree, and both hold the grid.
    assert json.loads(out.read_text(encoding="utf-8")) == printed
    # Only the two R0 cells were bought; the R4 pair came back from the artifact untouched.
    assert sum(1 for row in printed["cells"] if row["rung"] == "R4") == 2


def test_a_halt_on_the_very_first_cell_still_leaves_an_artifact(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every completed cell persists ``--out`` on its way out, so a halt LATER in the fire is
    already covered by the last cell's write. The uncovered case is a halt before any cell
    completes: without the halt handler's own write there is no artifact at all, and the operator
    is left with a legs directory and no record of which rig produced it."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setattr(e1_grid, "resolve_cli_version", lambda: "9.9.9")
    monkeypatch.setattr(e1_grid, "STAGED_REPEATS", 1)
    _seqs, _tasks = corpus_one(tmp_path)

    def refuse_immediately(task: Any, **kwargs: Any) -> RungCell:
        raise e1_grid.QuotaHaltError("the account refused the call")

    monkeypatch.setattr(e1_grid, "run_rung_cell", refuse_immediately)
    out = tmp_path / "summary.json"
    code = e1_grid.main(
        [
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "--fire-staged",
            "--model",
            MODEL,
            "--out",
            str(out),
        ]
    )
    assert code == e1_grid.EXIT_HALT
    assert out.exists()
    kept = json.loads(out.read_text(encoding="utf-8"))
    assert kept["cells"] == []
    # The identity is the part worth keeping: it says which rig halted, so the next fire can tell
    # a resume from a restart.
    assert kept["cli_version"] == "9.9.9"
    assert kept["model"] == MODEL
    assert not (tmp_path / "summary.json.lock").exists()


def test_a_quota_refusal_mid_fire_keeps_what_it_bought(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second staged fire died here and lost the in-flight cell's paid legs. A quota halt is a
    clean stop: the artifact holds every cell already bought, and the same command resumes."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setattr(e1_grid, "resolve_cli_version", lambda: "9.9.9")
    monkeypatch.setattr(e1_grid, "STAGED_REPEATS", 1)
    _seqs, tasks = corpus_one(tmp_path)
    made = _fake_cells(tasks)

    def quota_on_the_second(task: Any, **kwargs: Any) -> RungCell:
        if kwargs["rung"] == "R4":
            raise e1_grid.QuotaHaltError("the account refused the call")
        return made(task, **kwargs)

    monkeypatch.setattr(e1_grid, "run_rung_cell", quota_on_the_second)
    out = tmp_path / "summary.json"
    code = e1_grid.main(
        [
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "--fire-staged",
            "--model",
            MODEL,
            "--out",
            str(out),
        ]
    )
    assert code == e1_grid.EXIT_HALT
    assert "HALT" in capsys.readouterr().err
    kept = json.loads(out.read_text(encoding="utf-8"))
    assert [row["rung"] for row in kept["cells"]] == ["R0", "R0"]
    assert kept["cli_version"] == "9.9.9"
    assert not (tmp_path / "summary.json.lock").exists()
