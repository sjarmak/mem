"""mem-mpxie — the shared resumable per-task result cache (``membench.runner.resume_cache``).

The cache both paid toolreq grids run on. Every defect this codebase has shipped in a resume cache
had ONE shape: the identity hashed a MODEL of the executed input, the model was one field short, and
the task then reported ``reused``, spent nothing, did NOT crash, and printed a stale or fabricated
number as a real measurement. A green suite is no evidence against that — so each shape is an
executable case here, at the CORE, once, rather than re-earned per grid.

The suite drives a minimal stand-in grid (``_Result`` over ``_Task``): the core's contract does not
mention arms, so neither does its test. The two real consumers' end-to-end cases live in
``test_toolreq_realagent.py`` and ``test_toolreq_builtin.py``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from membench.runner.headless_agent import ENV_MODEL, CellCalls
from membench.runner.resume_cache import (
    BaseCachedResult,
    BaseCellOutcome,
    BaseRunIdentity,
    Evaluation,
    assert_usable_work_ids,
    digest,
    invocation_digest,
    load_cached,
    run_cached_corpus,
)

SUMMARY_NAME = "summary-test.json"


@dataclass(frozen=True)
class _Task:
    """The least a task can be and still be cacheable: an id. (``CacheableTask`` is structural, so
    nothing here inherits from the core.)"""

    work_id: str


class _Identity(BaseRunIdentity):
    pass


class _Cell(BaseCellOutcome):
    pass


class _Result(BaseCachedResult[_Identity, _Cell]):
    @classmethod
    def expected_cells(cls) -> set[tuple[str, str]]:
        return {("a", "recalled"), ("a", "trusted")}

    @classmethod
    def classify(cls, outcomes: Sequence[_Cell]) -> list[tuple[str, str, str]]:
        return [
            (c.channel, "PASS" if c.passes == c.runs else "FAIL", f"{c.passes}/{c.runs}")
            for c in outcomes
        ]


def _calls(goal: str = "goal-prompt") -> list[CellCalls]:
    """The stand-in grid's plan: one cell per channel, each a single `claude -p` invocation."""
    return [
        CellCalls(arm="a", channel=channel, calls=(("claude", "-p", f"[{channel}] {goal}"),))
        for channel in ("recalled", "trusted")
    ]


def _identity(**overrides: object) -> _Identity:
    fields: dict[str, object] = {
        "repeats": 2,
        "dry_run": True,
        "model": "",
        "protocol": 1,
        "task_fingerprint": "fp-task",
        "invocation_fingerprint": invocation_digest(_calls()),
    }
    return _Identity(**(fields | overrides))  # type: ignore[arg-type]


def _cells(passes: int = 2, runs: int = 2) -> list[_Cell]:
    return [
        _Cell(arm="a", channel=channel, passes=passes, runs=runs)
        for channel in ("recalled", "trusted")
    ]


def _evaluate(_task: _Task) -> Evaluation:
    return Evaluation(outcomes=_cells(), calls=_calls())


def _run(
    tasks: Sequence[_Task],
    out: Path,
    identity: _Identity | None = None,
    resume: bool = True,
    evaluate=_evaluate,
    before_first_spend=None,
):
    ident = identity if identity is not None else _identity()
    return run_cached_corpus(
        tasks,
        out_dir=out,
        result_cls=_Result,
        identity_of=lambda _task: ident,
        evaluate=evaluate,
        summary_name=SUMMARY_NAME,
        resume=resume,
        before_first_spend=before_first_spend,
    )


# --- the digest ------------------------------------------------------------------------


def test_the_digest_sorts_mapping_keys_but_never_reorders_a_list() -> None:
    """The fingerprints hash canonical JSON, and both halves of that are load-bearing.

    Keys SORT: `task_fingerprint` hashes `goal_step.model_dump()`, whose key order is the pydantic
    field-DECLARATION order — so a plain `json.dumps` would turn reordering two fields of
    SequenceStep, a no-op that moves nothing executed and nothing scored, into a total miss that
    re-spends the whole paid grid.

    Lists DON'T: every input whose order IS a measured input (surfaced memory, the ours payload, the
    prompt cells) is passed as a list of pairs, because insertion order is the order the memory
    lines are rendered into the prompt."""
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest([("a", 1), ("b", 2)]) != digest([("b", 2), ("a", 1)])


def test_the_cells_sort_but_a_cells_own_calls_never_reorder() -> None:
    """The two halves of ``invocation_digest``, and each is load-bearing in the OPPOSITE direction.

    CELLS sort: a cell is an independent ``claude -p`` run in its own sandbox, self-labeled by
    (arm, channel), so the order the grid's loop happens to visit them in moves nothing executed and
    nothing scored — sorting keeps a reordered loop a cache HIT rather than a full paid re-spend.

    A cell's CALLS do not: its legs share a cwd and a config dir and run in SEQUENCE (establish,
    then goal), so their order IS a measured input. Swap the two and the builtin arm establishes the
    fact into a directory it then wipes, and measures a guaranteed 0/N. A digest that called that
    the same run as the un-swapped one would serve the pre-swap numbers as post-swap measurements —
    this bead's own defect, reintroduced by its fix."""
    recalled = CellCalls(arm="a", channel="recalled", calls=(("claude", "-p", "x"),))
    trusted = CellCalls(arm="a", channel="trusted", calls=(("claude", "-p", "y"),))
    assert invocation_digest([recalled, trusted]) == invocation_digest([trusted, recalled])

    legs = CellCalls(
        arm="b", channel="recalled", calls=(("claude", "-p", "establish"), ("claude", "-p", "goal"))
    )
    swapped = CellCalls(
        arm="b", channel="recalled", calls=(("claude", "-p", "goal"), ("claude", "-p", "establish"))
    )
    assert invocation_digest([legs]) != invocation_digest([swapped])


def test_two_records_for_one_cell_are_refused() -> None:
    """A cell runs ONE cycle, so two records for it cannot both describe what it did. Merging them
    would let a second, fabricated record silently overwrite the measured one."""
    cell = CellCalls(arm="a", channel="recalled", calls=(("claude", "-p", "x"),))
    with pytest.raises(ValueError, match="two invocation records"):
        invocation_digest([cell, cell])


# --- the write boundary: a measurement may not be published under an identity that does not
# --- describe the invocations it actually made -------------------------------------------


def test_a_task_that_sent_a_prompt_its_identity_does_not_fingerprint_is_refused(
    tmp_path: Path,
) -> None:
    """THE bond this module exists to enforce. ``identity_of`` and ``evaluate`` are injected
    INDEPENDENTLY, so nothing but this check binds the hashed invocation to the sent one: a grid
    whose plan drifts from what its arms execute would otherwise persist a real measurement under a
    fingerprint describing a prompt it never sent, and every later resume would serve it.

    The record must NOT reach disk. A refused measurement that still wrote its file would be served
    by the very next resume — the failure it exists to prevent, one run later."""
    out = tmp_path / "out"

    def _drifted(_task: _Task) -> Evaluation:
        return Evaluation(outcomes=_cells(), calls=_calls(goal="a prompt the plan never modelled"))

    with pytest.raises(ValueError, match="no longer what its arms execute"):
        _run([_Task("w-0")], out, evaluate=_drifted)
    assert not (out / "w-0.json").exists(), "a refused measurement was published anyway"


def test_a_leg_the_plan_never_declared_is_refused(tmp_path: Path) -> None:
    """The failure mode that decides the SEAM. Recording what an arm *says* it sent — a prompt field
    on its step result, a list it appends to — is a MODEL of the wire: add a leg to the arm and
    forget BOTH to declare it in the plan and to record it (one omission, two symptoms, the
    likeliest edit of all) and the sent record still equals the plan. Recording off the CLI
    runner instead sees the call whether or not anyone declared it, so the undeclared leg lands
    in the cycle and moves the digest."""
    out = tmp_path / "out"

    def _extra_leg(_task: _Task) -> Evaluation:
        undeclared = [
            CellCalls(arm=c.arm, channel=c.channel, calls=(*c.calls, ("claude", "-p", "leg 2")))
            for c in _calls()
        ]
        return Evaluation(outcomes=_cells(), calls=undeclared)

    with pytest.raises(ValueError, match="no longer what its arms execute"):
        _run([_Task("w-0")], out, evaluate=_extra_leg)
    assert not (out / "w-0.json").exists()


def test_a_matching_measurement_publishes_and_then_resumes(tmp_path: Path) -> None:
    """The honest path: the invocations sent hash to the identity they were measured under, so the
    record publishes — and is then served on resume like any other."""
    out = tmp_path / "out"
    first = _run([_Task("w-0")], out)
    assert (first.executed, first.reused) == (1, 0)
    second = _run([_Task("w-0")], out)
    assert (second.executed, second.reused) == (0, 1)


# --- the model rule, made structural ---------------------------------------------------


def test_the_raw_cli_default_model_is_unconstructible_when_the_env_pins_one(monkeypatch) -> None:
    """`--model` defaults to "" and the agent then reads MEMBENCH_AGENT_MODEL, so an identity that
    stores the RAW flag makes the driver's primary independent variable invisible. Caching "" while
    the env points at a model is the defect in its purest form — and it is now unconstructible, so a
    caller who forgets to resolve gets a ValidationError, not a silent mislabelling."""
    monkeypatch.setenv(ENV_MODEL, "claude-haiku-4-5")
    with pytest.raises(ValidationError):
        _identity(model="")


def test_the_legitimate_cli_default_model_still_validates(monkeypatch) -> None:
    """No pin, no env: "" IS what the agent runs under (the CLI's own default), so it is its own
    fixed point. The rule must not refuse the honest case it exists to protect."""
    monkeypatch.delenv(ENV_MODEL, raising=False)
    assert _identity(model="").model == ""


def test_an_explicit_pin_validates_even_when_the_env_points_elsewhere(monkeypatch) -> None:
    """An explicit `--model` wins over the env (resolve_model), so a pinned model is a fixed point
    regardless of what the env says — the pin is what the agent will actually run under."""
    monkeypatch.setenv(ENV_MODEL, "claude-haiku-4-5")
    assert _identity(model="claude-opus-4-8").model == "claude-opus-4-8"


def test_repointing_the_env_model_between_runs_is_a_miss_not_a_relabel(
    tmp_path: Path, monkeypatch
) -> None:
    """THE defect, end to end: run a sweep under one model, repoint MEMBENCH_AGENT_MODEL, resume.
    The cached cells must MISS and re-measure — never be served as `reused` with the first model's
    numbers relabelled as the second's."""
    out = tmp_path / "out"
    monkeypatch.setenv(ENV_MODEL, "model-one")
    first = _run([_Task("w-0")], out, _identity(model="model-one"))
    assert (first.executed, first.reused) == (1, 0)

    monkeypatch.setenv(ENV_MODEL, "model-two")
    second = _run([_Task("w-0")], out, _identity(model="model-two"))
    assert (second.executed, second.reused) == (1, 0), "served one model's numbers as another's"


def test_a_record_written_with_an_unresolved_model_is_unloadable(
    tmp_path: Path, monkeypatch
) -> None:
    """The fail-safe direction for a record an OLDER (forgetful) writer left behind: with the env
    pinned, a stored raw "" is not a model anything ran under, so the record cannot even parse. A
    miss re-measures; an accept would publish a number under the wrong model's name."""
    out = tmp_path / "out"
    monkeypatch.delenv(ENV_MODEL, raising=False)
    _run([_Task("w-0")], out, _identity(model=""))  # written honestly: no pin, no env

    monkeypatch.setenv(ENV_MODEL, "model-two")
    result_path = out / "w-0.json"
    assert load_cached(result_path, _identity(model="model-two"), _Result) is None


# --- whole-object identity -------------------------------------------------------------


def test_an_identity_carrying_an_unknown_field_is_a_miss(tmp_path: Path) -> None:
    """A NEWER writer's record, read by an older binary. The predecessor compared the identity as a
    SUBSET (`any(loaded.get(k) != v for k, v in identity.items())`), so a record carrying a field
    the reader did not know about matched on the fields they happened to share and was served as
    `reused`. `extra="forbid"` turns that into a miss — the fail-safe direction."""
    out = tmp_path / "out"
    _run([_Task("w-0")], out)

    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["identity"]["a_field_a_newer_writer_added"] = "surely harmless"
    result_path.write_text(json.dumps(record), encoding="utf-8")

    assert load_cached(result_path, _identity(), _Result) is None
    resumed = _run([_Task("w-0")], out)
    assert (resumed.executed, resumed.reused) == (1, 0)


def test_a_changed_fingerprint_is_a_miss(tmp_path: Path) -> None:
    """The regenerated-corpus case in its general form: work ids are POSITIONAL, so a re-run over
    the same `--out` reuses them. Only the task fingerprint distinguishes the previous world's
    numbers from this one's."""
    out = tmp_path / "out"
    first = _run([_Task("w-0")], out, _identity(task_fingerprint="world-one"))
    assert (first.executed, first.reused) == (1, 0)

    second = _run([_Task("w-0")], out, _identity(task_fingerprint="world-two"))
    assert (second.executed, second.reused) == (1, 0), "served the previous world's numbers"


def test_a_free_dry_run_never_satisfies_a_paid_run(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _run([_Task("w-0")], out, _identity(dry_run=True))
    paid = _run([_Task("w-0")], out, _identity(dry_run=False))
    assert (paid.executed, paid.reused) == (1, 0)


def test_a_record_measured_at_a_different_repeat_count_is_a_miss(tmp_path: Path) -> None:
    """The forged-repeat-count shape on the LOAD path: a record SELF-consistent at repeats=3
    (rows at runs=3, so the grid check passes and runs > 0 keeps it out of reach of the ge=1
    row bound) must still miss for a repeats=2 run. The whole-object identity ``==`` is the
    only guard left standing — delete it and a sweep measured at one repeat count is served,
    at executed=0, as a measurement of another."""
    out = tmp_path / "out"
    out.mkdir()
    record = _Result.of("w-0", _identity(repeats=3), _cells(passes=3, runs=3))
    result_path = out / "w-0.json"
    result_path.write_text(record.model_dump_json(), encoding="utf-8")

    assert load_cached(result_path, _identity(repeats=2), _Result) is None
    resumed = _run([_Task("w-0")], out, _identity(repeats=2))
    assert (resumed.executed, resumed.reused) == (
        1,
        0,
    ), "served a repeats=3 measurement for a repeats=2 run"


# --- the parse boundary: every rejection is a MISS, never a crash -----------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"{ half-written not json", id="malformed-json"),
        pytest.param(b"[]", id="non-object-top-level"),
        pytest.param(b'"a string"', id="scalar-top-level"),
        pytest.param(b"\xff\xfe\x00corrupt", id="invalid-utf8"),
        pytest.param(b'{"work_id": "w-0"}', id="missing-identity-and-rows"),
        pytest.param(b"[" * 200_000, id="deeply-nested-json"),
    ],
)
def test_a_hostile_cache_file_is_a_miss_not_a_crash(tmp_path: Path, payload: bytes) -> None:
    """These files are written by a sweep that can be killed mid-run and re-read by a PAID resume,
    so an exception escaping the loader kills the whole sweep on one bad file. Deeply-nested JSON is
    the one the builtin driver's `except (OSError, ValueError)` did NOT catch: `json.loads` raises
    RecursionError, which is not a ValueError, so it escaped and killed the sweep."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "w-0.json").write_bytes(payload)
    run = _run([_Task("w-0")], out)
    assert (run.executed, run.reused) == (1, 0)


def test_a_type_hostile_row_is_a_miss(tmp_path: Path) -> None:
    """A cache that parses, matches the identity, and would construct a plain dataclass happily —
    but with STRING counts. Unvalidated it survives the load and detonates LATER inside a verdict
    rule (`passes > 0` -> TypeError), an unhandled exception escaping mid-resume. Strict mode
    rejects it at the parse boundary, where a bad row is still just a cache miss."""
    out = tmp_path / "out"
    _run([_Task("w-0")], out)

    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    for row in record["outcomes"]:
        row["passes"] = str(row["passes"])
    result_path.write_text(json.dumps(record), encoding="utf-8")

    assert load_cached(result_path, _identity(), _Result) is None


def test_a_bool_row_count_is_a_miss(tmp_path: Path) -> None:
    """`bool` is an `int` subclass, so `isinstance(True, int)` is True and a hand-rolled type check
    waves it through. Strict mode does not."""
    with pytest.raises(ValidationError):
        _Cell(arm="a", channel="recalled", passes=True, runs=2)  # type: ignore[arg-type]


# --- the schema's own bounds -----------------------------------------------------------


def test_zero_repeats_and_zero_runs_are_unconstructible() -> None:
    """`--repeats 0` evaluates nothing and persists 0/0 rows that a verdict rule reads as a
    confident finding for a task that was never run. The drivers refuse it at the flag; this is the
    structural backstop, and both are deliberate."""
    with pytest.raises(ValidationError):
        _identity(repeats=0)
    with pytest.raises(ValidationError):
        _Cell(arm="a", channel="recalled", passes=0, runs=0)


def test_more_passes_than_runs_is_unconstructible() -> None:
    with pytest.raises(ValidationError):
        _Cell(arm="a", channel="recalled", passes=3, runs=2)


def test_rows_must_be_measured_at_the_identitys_repeats() -> None:
    with pytest.raises(ValidationError):
        _Result.of("w-0", _identity(repeats=2), _cells(passes=3, runs=3))


# --- the grid must be covered EXACTLY --------------------------------------------------


def test_a_short_grid_is_unconstructible_and_unloadable(tmp_path: Path) -> None:
    """A record missing a channel would credit full coverage for a channel no `claude -p` call ever
    ran."""
    with pytest.raises(ValidationError):
        _Result.of("w-0", _identity(), _cells()[:1])

    out = tmp_path / "out"
    _run([_Task("w-0")], out)
    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["outcomes"] = record["outcomes"][:1]
    result_path.write_text(json.dumps(record), encoding="utf-8")
    assert load_cached(result_path, _identity(), _Result) is None


def test_a_duplicated_cell_is_unconstructible(tmp_path: Path) -> None:
    """The dual of the short grid, and NOT caught by an arity check alone: two copies of one cell
    has the right COUNT and the wrong coverage — the other channel was never measured, yet a verdict
    keyed by (arm, channel) reads full coverage off one channel counted twice."""
    duplicated = [_cells()[0], _cells()[0]]
    with pytest.raises(ValidationError):
        _Result.of("w-0", _identity(), duplicated)


def test_a_forged_verdict_is_a_miss(tmp_path: Path) -> None:
    """The verdict is DERIVED, so a persisted one may only be the one its rows produce. Without this
    it is the single unchecked value in the record — and the summary a human reads is built from
    these strings, not re-derived from the rows."""
    out = tmp_path / "out"
    _run([_Task("w-0")], out)

    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["verdict"] = "[recalled] 99/99"
    result_path.write_text(json.dumps(record), encoding="utf-8")

    assert load_cached(result_path, _identity(), _Result) is None


# --- work ids are filenames ------------------------------------------------------------


def test_duplicate_work_ids_are_refused() -> None:
    """A duplicate silently aliases two different tasks onto one cache file: the second overwrites
    the first and, on resume, that single record is served for both."""
    with pytest.raises(ValueError, match="duplicate work_id"):
        assert_usable_work_ids([_Task("w-0"), _Task("w-0")], SUMMARY_NAME)


@pytest.mark.parametrize(
    "bad_id",
    [
        pytest.param("../escape", id="traversal"),
        pytest.param("nested/id", id="separator"),
        pytest.param("", id="empty"),
        pytest.param(SUMMARY_NAME.removesuffix(".json"), id="claims-the-summarys-name"),
    ],
)
def test_unsafe_work_ids_are_refused(bad_id: str) -> None:
    """Corpus data used to build a filesystem path before being checked as one: a separator or
    traversal writes outside `--out`, and the summary's name is overwritten by the summary the
    driver writes AFTERWARDS — so that task's result misses forever."""
    with pytest.raises(ValueError, match="unsafe work_id"):
        assert_usable_work_ids([_Task(bad_id)], SUMMARY_NAME)


# --- the accounting a paid run is read through -----------------------------------------


def test_a_completed_task_is_persisted_and_then_reused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    first = _run([_Task("w-0"), _Task("w-1")], out)
    assert (first.executed, first.reused) == (2, 0)
    assert (out / "w-0.json").is_file() and (out / "w-1.json").is_file()

    second = _run([_Task("w-0"), _Task("w-1")], out)
    assert (second.executed, second.reused) == (0, 2)


def test_resume_false_re_measures_everything(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _run([_Task("w-0")], out)
    forced = _run([_Task("w-0")], out, resume=False)
    assert (forced.executed, forced.reused) == (1, 0)


def test_a_cache_hit_does_not_rewrite_the_result_file(tmp_path: Path) -> None:
    """A reused record already passed every validator, so a rewrite could only reproduce it — and a
    fully cache-served resume then does zero writes, leaving the results' mtimes an honest record of
    which tasks this run actually measured."""
    out = tmp_path / "out"
    _run([_Task("w-0")], out)
    result_path = out / "w-0.json"
    before = result_path.stat().st_mtime_ns

    reused = _run([_Task("w-0")], out)
    assert reused.reused == 1
    assert result_path.stat().st_mtime_ns == before


def test_a_leftover_temp_file_is_never_read_as_a_result(tmp_path: Path) -> None:
    """The atomic publish writes a sibling `.json.tmp` then renames, so a kill mid-write leaves
    either the old result or the new one — never a half-written JSON the next resume trips on."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "w-0.json.tmp").write_bytes(b"{ half-written")
    run = _run([_Task("w-0")], out)
    assert (run.executed, run.reused) == (1, 0)
    assert not (out / "w-0.json.tmp").exists()  # the publish replaced it


# --- a paid warm-up fires with the SPEND, not with the invocation ----------------------


def test_before_first_spend_fires_once_immediately_before_the_first_measured_task(
    tmp_path: Path,
) -> None:
    """The hook's whole contract in one order assertion: it fires ONCE, it fires LATE (after the
    cached task is served, not at the top of the loop), and it fires BEFORE the first task that
    spends — never between the later ones."""
    out = tmp_path / "out"
    _run([_Task("w-0")], out)  # w-0 is now cached at this identity; w-1 and w-2 are not

    order: list[str] = []

    def _recording_evaluate(task: _Task) -> Evaluation:
        order.append(f"measure:{task.work_id}")
        return _evaluate(task)

    run = _run(
        [_Task("w-0"), _Task("w-1"), _Task("w-2")],
        out,
        evaluate=_recording_evaluate,
        before_first_spend=lambda: order.append("warm-up"),
    )
    assert (run.executed, run.reused) == (2, 1)
    assert order == ["warm-up", "measure:w-1", "measure:w-2"]


def test_before_first_spend_never_fires_on_a_fully_cache_served_resume(tmp_path: Path) -> None:
    """The property the hook exists for (mem-dblue). A driver's paid warm-up — a preflight, a
    mechanism check — costs real `claude -p` calls, and a resume that measures NOTHING has nothing
    for it to protect. The waste it replaces was proportional to the number of RESUME ATTEMPTS,
    which is precisely the axis this cache exists to zero out."""
    out = tmp_path / "out"
    _run([_Task("w-0"), _Task("w-1")], out)

    def _boom() -> None:
        raise AssertionError("the warm-up must NOT fire when the run measures nothing")

    run = _run([_Task("w-0"), _Task("w-1")], out, before_first_spend=_boom)
    assert (run.executed, run.reused) == (0, 2)


def test_raising_from_before_first_spend_aborts_before_anything_is_measured(
    tmp_path: Path,
) -> None:
    """A warm-up that refuses is how a driver halts a sweep it has diagnosed as not worth spending
    on, so the raise must land BEFORE the first `evaluate` — not after the call it was meant to
    prevent."""
    out = tmp_path / "out"

    def _gate() -> None:
        raise RuntimeError("diagnosed: refusing to spend")

    def _evaluate_boom(_task: _Task) -> Evaluation:
        raise AssertionError("the warm-up refused; nothing may be measured")

    with pytest.raises(RuntimeError, match="refusing to spend"):
        _run([_Task("w-0")], out, evaluate=_evaluate_boom, before_first_spend=_gate)
    assert not (out / "w-0.json").exists()


def test_before_first_spend_fires_after_the_work_id_check(tmp_path: Path) -> None:
    """Ordering, and the reason the hook is injected into the LOOP rather than run by the driver
    ahead of it. A driver that computed the miss set itself would fire its paid warm-up first and
    only THEN reach this refusal — spending real calls on a corpus that was never going to be
    measured. Inside the loop, the free structural check always speaks first."""

    def _boom() -> None:
        raise AssertionError("a corpus that cannot be measured must cost nothing")

    with pytest.raises(ValueError, match="duplicate work_id"):
        _run([_Task("w-0"), _Task("w-0")], tmp_path / "out", before_first_spend=_boom)


def test_the_verdict_string_and_the_counted_kinds_come_from_one_ladder() -> None:
    """`classify` yields (channel, kind, line) from a single branch: the summary COUNTS the kinds
    and the human READS the lines, so the two cannot desync.

    The shape this replaces derived the headline by substring-matching the rendered verdict
    (`"LEAK" in verdict`, `verdict.count("SEPARATES")`). Under that shape, rewording a line — or
    adding an arm whose NAME contains a kind word — silently moves the headline with no schema
    change and nothing failing. Here the line is not an input to any count."""
    record = _Result.of("w-0", _identity(), _cells(passes=2, runs=2))
    assert record.kinds == ["PASS", "PASS"]
    assert record.verdict == "[recalled] 2/2 | [trusted] 2/2"

    mixed = [
        _Cell(arm="a", channel="recalled", passes=2, runs=2),
        _Cell(arm="a", channel="trusted", passes=0, runs=2),
    ]
    assert _Result.of("w-1", _identity(), mixed).kinds == ["PASS", "FAIL"]
    # The kinds are not recoverable from the rendered line by any string match — that is the point.
    assert "PASS" not in _Result.of("w-1", _identity(), mixed).verdict
