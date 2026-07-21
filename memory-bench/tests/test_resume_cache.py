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
import os
import signal
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import ValidationError

from membench.runner.headless_agent import (
    ENV_MODEL,
    CellCalls,
    CellRecorder,
    MemoryChannel,
    result_event,
    serialize_stream,
)
from membench.runner.resume_cache import (
    LEAK,
    SEPARATES,
    WEAK,
    BaseCachedResult,
    BaseCellOutcome,
    BaseRunIdentity,
    CachePlan,
    CorpusRun,
    assert_usable_work_ids,
    corpus_summary,
    digest,
    invocation_digest,
    load_cached,
    pending_tasks,
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
        # The default identity is a DRY RUN, which spawns no binary — so it names none.
        "cli_version": "",
        "protocol": 1,
        "task_fingerprint": "fp-task",
        "invocation_fingerprint": invocation_digest(_calls()),
        "settings_fingerprint": "fp-settings",
    }
    return _Identity(**(fields | overrides))  # type: ignore[arg-type]


def _cells(passes: int = 2, runs: int = 2) -> list[_Cell]:
    return [
        _Cell(arm="a", channel=channel, passes=passes, runs=runs)
        for channel in ("recalled", "trusted")
    ]


def _noop_runner(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), 0, "", "")


def _record(recorder: CellRecorder, goal: str = "goal-prompt", *, extra_leg: bool = False) -> None:
    """Record the stand-in grid's plan THROUGH the recorder — what an honest arm does: open a
    per-cell ``RecordingRunner`` and spawn its ``claude -p`` through it. Doubles RECORD, never
    RETURN, a cycle: after mem-9gvej ``run_cached_corpus`` hashes what the seam SAW, so a double
    that fabricated a ``calls`` value could no longer make the write boundary agree with itself."""
    for channel in ("recalled", "trusted"):
        runner = recorder.cell(_noop_runner, arm="a", channel=MemoryChannel(channel), repeats=1)
        runner(("claude", "-p", f"[{channel}] {goal}"))
        if extra_leg:
            runner(("claude", "-p", "leg 2"))


def _evaluate(_task: _Task, recorder: CellRecorder) -> list[_Cell]:
    _record(recorder)
    return _cells()


def _plan(
    out: Path,
    identity: _Identity | None = None,
    resume: bool = True,
    identity_of=None,
    plan_of=lambda _task: _calls(),
) -> CachePlan[_Task, _Identity, _Cell]:
    ident = identity if identity is not None else _identity()
    return CachePlan(
        out_dir=out,
        result_cls=_Result,
        plan_of=plan_of,
        # The fingerprint CachePlan.lookup derives from `plan_of` is handed in as `_fp`; the
        # default identity carries exactly it (`_calls()` hashed both here and in `_identity`), so
        # the seam guarantee holds and these cases exercise everything downstream of it.
        identity_of=identity_of if identity_of is not None else (lambda _task, _fp: ident),
        summary_name=SUMMARY_NAME,
        resume=resume,
    )


def _run(
    tasks: Sequence[_Task],
    out: Path,
    identity: _Identity | None = None,
    resume: bool = True,
    evaluate=_evaluate,
    before_first_spend=None,
    identity_of=None,
    plan_of=lambda _task: _calls(),
):
    return run_cached_corpus(
        _plan(out, identity, resume, identity_of, plan_of),
        tasks,
        evaluate=evaluate,
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

    def _drifted(_task: _Task, recorder: CellRecorder) -> list[_Cell]:
        _record(recorder, goal="a prompt the plan never modelled")
        return _cells()

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

    def _extra_leg(_task: _Task, recorder: CellRecorder) -> list[_Cell]:
        # An undeclared second leg is SPAWNED through the recorder — the runner sees it whether or
        # not the plan declared it, so it lands in the cycle and moves the digest.
        _record(recorder, extra_leg=True)
        return _cells()

    with pytest.raises(ValueError, match="no longer what its arms execute"):
        _run([_Task("w-0")], out, evaluate=_extra_leg)
    assert not (out / "w-0.json").exists()


def test_the_write_boundary_hashes_the_recorders_calls_not_the_evaluators(tmp_path: Path) -> None:
    """THE mem-9gvej bond. The write boundary once hashed ``Evaluation.calls`` — an ordinary value
    ``evaluate`` RETURNED, which nothing bound to the wire; a future author could return the plan's
    own rendering (the check checks the caller's model against itself) or spawn a leg through a bare
    runner the recorder never saw. ``Evaluation`` is gone: ``run_cached_corpus`` OWNS the
    ``CellRecorder`` and hashes what the recorder SAW. The observable proof is that an ``evaluate``
    which scores a full grid but never drives the recorder — records nothing — is refused and
    publishes nothing. A caller structurally cannot supply the hashed invocations."""
    out = tmp_path / "out"

    def _unrecorded(_task: _Task, _recorder: CellRecorder) -> list[_Cell]:
        return _cells()  # scores the rows, but drives NOTHING through the recorder

    with pytest.raises(ValueError, match="no longer what its arms execute"):
        _run([_Task("w-0")], out, evaluate=_unrecorded)
    assert not (out / "w-0.json").exists()


def test_a_matching_measurement_publishes_and_then_resumes(tmp_path: Path) -> None:
    """The honest path: the invocations sent hash to the identity they were measured under, so the
    record publishes — and is then served on resume like any other."""
    out = tmp_path / "out"
    first = _run([_Task("w-0")], out)
    assert (first.executed, first.reused) == (1, 0)
    second = _run([_Task("w-0")], out)
    assert (second.executed, second.reused) == (0, 1)


# --- the fingerprint is owned by the seam, not authored by the grid (mem-3of76) ---------


def test_an_identity_whose_fingerprint_is_not_the_plans_is_refused_before_any_spend(
    tmp_path: Path,
) -> None:
    """The residue mem-swp43 left one construction site up: the identity's
    ``invocation_fingerprint`` used to be built by the grid, on a route of its own, and only the
    write boundary — which fires on a MISS — ever checked it against what the arms sent. A third
    grid, or one refactor, that computed the field by any other route published fine and then served
    stale numbers on resume.

    ``CachePlan.lookup`` now derives the fingerprint from ``plan_of`` and hands it to
    ``identity_of``; an identity that carries any OTHER value is refused at construction, before the
    cache is even consulted, so the field can no longer be authored by a divergent route. A driver
    (``_measuring_evaluate``) that would spend is installed to prove the refusal lands FIRST: it
    must not run."""
    out = tmp_path / "out"

    def _measuring_evaluate(_task: _Task, _recorder: CellRecorder) -> list[_Cell]:
        raise AssertionError("refused for a divergent fingerprint, yet the task still measured")

    def _authors_its_own_fingerprint(_task: _Task, _fp: str) -> _Identity:
        # Ignores the fingerprint the seam handed it and computes one by a route of its own.
        return _identity(invocation_fingerprint=digest("a plan the arms do not send"))

    with pytest.raises(ValueError, match="authored the field by a route the plan does not own"):
        _run(
            [_Task("w-0")],
            out,
            identity_of=_authors_its_own_fingerprint,
            evaluate=_measuring_evaluate,
        )
    assert not (out / "w-0.json").exists()


def test_a_divergent_fingerprint_is_refused_on_a_cache_hit_too(tmp_path: Path) -> None:
    """The half the write boundary structurally cannot reach: a fully cache-served resume never
    calls ``evaluate``, so before this seam nothing re-checked its stored fingerprint against the
    plan. Publish a task honestly, then resume with an ``identity_of`` that authors a divergent
    fingerprint — the run must be refused at the seam, not served as ``reused``."""
    out = tmp_path / "out"
    assert _run([_Task("w-0")], out).executed == 1

    def _authors_its_own_fingerprint(_task: _Task, _fp: str) -> _Identity:
        return _identity(invocation_fingerprint=digest("a plan the arms do not send"))

    with pytest.raises(ValueError, match="authored the field by a route the plan does not own"):
        _run([_Task("w-0")], out, identity_of=_authors_its_own_fingerprint)


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


# --- the instrument the cells were measured ON (mem-lw0j3) -----------------------------


def test_a_paid_identity_must_name_the_binary_it_measured_on() -> None:
    """A paid cell is a measurement made BY a specific claude binary; an identity that cannot name
    it matches every later resume on any binary (argued at `BaseRunIdentity.cli_version`)."""
    with pytest.raises(ValidationError):
        _identity(dry_run=False, cli_version="")


def test_a_dry_run_identity_must_not_claim_a_binary() -> None:
    """The other direction, and not symmetry for its own sake: a dry run spawns NO binary, so a
    version stamped on it is a claim about a process that never started."""
    with pytest.raises(ValidationError):
        _identity(dry_run=True, cli_version="2.1.210")


def test_upgrading_the_cli_between_runs_is_a_miss_not_a_relabel(tmp_path: Path) -> None:
    """THE defect, end to end: measure a paid sweep, upgrade the claude CLI, resume over the same
    --out. The cached cells must MISS and re-measure — never be served as `reused` with the old
    binary's numbers relabelled as the new instrument's."""
    out = tmp_path / "out"
    first = _run(
        [_Task("w-0")], out, _identity(dry_run=False, cli_version="2.1.173", model="sonnet")
    )
    assert (first.executed, first.reused) == (1, 0)

    second = _run(
        [_Task("w-0")], out, _identity(dry_run=False, cli_version="2.1.210", model="sonnet")
    )
    assert (second.executed, second.reused) == (1, 0), "served one binary's numbers as another's"


def _evaluate_on_version(version: str) -> Callable[[_Task, CellRecorder], list[_Cell]]:
    """An ``evaluate`` whose cells each spawn a ``claude -p`` that PRINTS an init event announcing
    ``version`` — the mid-sweep-upgrade artifact the write boundary checks against the identity's
    pinned ``cli_version`` (mem-z32zu). The argv sent is the stand-in plan's, so the fingerprint
    check upstream passes and the stream check is what fires."""
    stream = serialize_stream(
        [{"type": "system", "subtype": "init", "claude_code_version": version}, result_event()]
    )

    def runner(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 0, stream, "")

    def evaluate(_task: _Task, recorder: CellRecorder) -> list[_Cell]:
        for channel in ("recalled", "trusted"):
            rr = recorder.cell(runner, arm="a", channel=MemoryChannel(channel), repeats=1)
            rr(("claude", "-p", f"[{channel}] goal-prompt"))
        return _cells()

    return evaluate


def test_a_cell_whose_stream_names_a_different_cli_version_is_refused(tmp_path: Path) -> None:
    """The WITHIN-sweep residue of the between-runs drift above (mem-z32zu): ``cli_version`` is
    resolved once, at the top of a sweep, off a `claude --version` claim about a different process —
    so a CLI upgraded mid-sweep tags a later cell with the pre-sweep version. The cell's OWN stream
    reports the version the binary that ran it actually was; the write boundary refuses the
    mislabel, and writes nothing."""
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="upgraded mid-sweep"):
        _run(
            [_Task("w-0")],
            out,
            _identity(dry_run=False, cli_version="2.1.210", model="sonnet"),
            evaluate=_evaluate_on_version("2.1.230"),
        )
    assert not (out / "w-0.json").exists(), "a mislabelled measurement must not be published"


def test_a_cell_whose_stream_names_the_pinned_cli_version_publishes(tmp_path: Path) -> None:
    """The other side of the gate: when a cell's own init event agrees with the identity's pinned
    version, the artifact confirms the pre-flight claim and the measurement publishes."""
    out = tmp_path / "out"
    run = _run(
        [_Task("w-0")],
        out,
        _identity(dry_run=False, cli_version="2.1.210", model="sonnet"),
        evaluate=_evaluate_on_version("2.1.210"),
    )
    assert (run.executed, run.reused) == (1, 0)
    assert (out / "w-0.json").exists()


def test_a_dry_run_does_not_check_stream_versions(tmp_path: Path) -> None:
    """A dry run names no binary (``cli_version == ""``), so the artifact check is skipped even when
    the simulated stream reports one — there is no pinned instrument for it to contradict."""
    out = tmp_path / "out"
    run = _run([_Task("w-0")], out, _identity(), evaluate=_evaluate_on_version("2.1.230"))
    assert run.executed == 1
    assert (out / "w-0.json").exists()


def test_a_stream_that_names_no_version_is_left_unchecked(tmp_path: Path) -> None:
    """A stream carrying no init event names no instrument, so a paid run over it publishes: a
    drifted REAL `claude -p` always prints the new version's init event, so the mid-sweep threat is
    covered without refusing every version-less stream (which would import dead-run detection into
    the grids). The default ``_evaluate`` records through ``_noop_runner``, whose stdout is empty.
    """
    out = tmp_path / "out"
    run = _run([_Task("w-0")], out, _identity(dry_run=False, cli_version="2.1.210", model="sonnet"))
    assert run.executed == 1
    assert (out / "w-0.json").exists()


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


# --- the model a paid cell RAN UNDER (mem-bzv2p) ---------------------------------------


def test_a_paid_identity_must_name_the_model_it_ran_under(monkeypatch) -> None:
    """THE defect: a paid cell left unpinned names no model, and "" is not one — it is a RULE the
    CLI evaluates, against inputs this codebase never sees. `ANTHROPIC_MODEL` is the one that
    prompted this (the CLI honours it as a selector; only an explicit `--model` overrides it), but
    naming it is beside the point: `settings.json`'s model key reaches the CLI through a FILE, and
    the alias remaps (`ANTHROPIC_DEFAULT_SONNET_MODEL`) move even a pinned `sonnet`. Enumerating
    them is what `resume_cache`'s whole thesis refuses. So the gate is on the ANSWER, not the
    inputs: two sweeps on genuinely different models both store "", match on every field, and the
    resume publishes one model's numbers as the other's at `executed=0` with nothing raised.

    The env is cleared so this asserts THIS gate and not
    `_model_is_the_one_the_agent_will_actually_run_under` — with `MEMBENCH_AGENT_MODEL` set, "" is
    already unconstructible for a different reason and the test would pass without the gate."""
    monkeypatch.delenv(ENV_MODEL, raising=False)
    with pytest.raises(ValidationError, match="cannot name the model"):
        _identity(dry_run=False, cli_version="2.1.210", model="")


def test_a_dry_run_identity_may_leave_the_model_to_the_cli(monkeypatch) -> None:
    """The asymmetry with `cli_version` is deliberate, not an oversight. That field is REFUSED on a
    dry run (a version stamped on a process that never started is a false claim); this one is
    merely ALLOWED to be empty. A dry run spawns no model to misname and spends nothing on it, and
    a plan for the CLI's default is a legitimate thing to fingerprint for free."""
    monkeypatch.delenv(ENV_MODEL, raising=False)
    assert _identity(dry_run=True, model="").model == ""


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
    # A paid identity names the binary it was measured on; a dry-run one names none (see
    # `_a_paid_measurement_names_the_binary_it_was_made_on`). `dry_run` itself is what this case
    # turns on — the version rides along because a paid identity cannot be built without one.
    out = tmp_path / "out"
    _run([_Task("w-0")], out, _identity(dry_run=True))
    paid = _run(
        [_Task("w-0")], out, _identity(dry_run=False, cli_version="2.1.210", model="sonnet")
    )
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


def _alarm(*_: object) -> None:
    """SIGALRM -> exception, so a blocked publish FAILS the test instead of hanging it."""
    raise TimeoutError("the publish blocked")


@pytest.mark.parametrize("shape", ["symlink", "hardlink"])
def test_a_planted_link_at_the_temp_path_never_reaches_the_victim(
    tmp_path: Path, shape: str
) -> None:
    """The atomic publish must not write THROUGH a planted `<work_id>.json.tmp`.

    BOTH link shapes, because the obvious guard only stops one of them. `O_NOFOLLOW`
    refuses a symlink and opens a HARD link exactly like a regular file -- same victim,
    same clobber, no refusal. The publish creates a fresh inode instead (unlink, then
    `O_EXCL`), which refuses every planted shape by construction rather than by
    enumerating the ones someone thought of.

    The unlink drops the directory ENTRY, so the victim itself is never touched by either
    arm -- which is what these assert.
    """
    out = tmp_path / "out"
    out.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    if shape == "symlink":
        (out / "w-0.json.tmp").symlink_to(victim)
    else:
        os.link(victim, out / "w-0.json.tmp")

    _run([_Task("w-0")], out)

    assert victim.read_text(encoding="utf-8") == "untouched"  # never written through
    assert json.loads((out / "w-0.json").read_text(encoding="utf-8"))["work_id"] == "w-0"


def test_a_planted_fifo_at_the_temp_path_does_not_hang_the_publish(tmp_path: Path) -> None:
    """A FIFO at the temp path must not block the publish.

    `O_WRONLY` on a FIFO with no reader blocks FOREVER, and it would block HERE -- after the
    paid `claude -p` calls are already made and before anything is written. The sweep would
    neither publish nor halt; it would just stop, holding the spend. Creating a fresh inode
    sidesteps the open-what-is-there problem entirely.
    """
    out = tmp_path / "out"
    out.mkdir()
    os.mkfifo(out / "w-0.json.tmp")

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(5)
    try:
        _run([_Task("w-0")], out)
    finally:
        signal.alarm(0)

    assert json.loads((out / "w-0.json").read_text(encoding="utf-8"))["work_id"] == "w-0"


def test_an_unclearable_temp_path_is_diagnosed_not_tracebacked(tmp_path: Path) -> None:
    """A shape the unlink cannot clear must still fail CLOSED with the publish's own message.

    A directory at `<work_id>.json.tmp` raises `IsADirectoryError` from the unlink, not from
    the create -- so it escapes any handler wrapped around the create alone, which is where a
    raw traceback would come from on a paid sweep.
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / "w-0.json.tmp").mkdir()

    with pytest.raises(OSError, match="refusing to publish"):
        _run([_Task("w-0")], out)

    assert not (out / "w-0.json").exists()  # nothing published on the refused path


def test_an_unclearable_temp_path_is_not_diagnosed_as_a_race(tmp_path: Path) -> None:
    """The two failure shapes get their own diagnosis, because they have different remedies.

    A directory at the tmp name fails the UNLINK -- nothing raced anything, and telling the
    operator it did sends them hunting an attacker for what is a stale directory. Only a
    failure of the O_EXCL CREATE means something appeared in the window after the unlink.
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / "w-0.json.tmp").mkdir()

    with pytest.raises(OSError) as caught:
        _run([_Task("w-0")], out)

    assert "could not be cleared" in str(caught.value)
    assert "raced" not in str(caught.value)


def test_the_fd_is_not_leaked_when_the_wrapper_around_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.open` hands back a RAW fd that nothing owns until `os.fdopen` adopts it.

    If the adoption itself raises, the `with` block never runs and the fd is orphaned. This
    publishes once per task inside a long paid sweep, so a systematic trigger (memory
    pressure) would exhaust the table mid-sweep rather than once.
    """
    out = tmp_path / "out"
    out.mkdir()
    captured: dict[str, int] = {}

    def refuse_to_wrap(fd: int, *args: object, **kwargs: object) -> NoReturn:
        captured["fd"] = fd
        raise MemoryError("no memory for the TextIOWrapper")

    monkeypatch.setattr(os, "fdopen", refuse_to_wrap)

    with pytest.raises(MemoryError):
        _run([_Task("w-0")], out)

    monkeypatch.undo()
    with pytest.raises(OSError):  # EBADF -- the fd was closed on the way out
        os.fstat(captured["fd"])


def test_the_out_dir_is_not_created_group_or_world_writable(tmp_path: Path) -> None:
    """out_dir holds PAID results, and a bare `mkdir` takes 0o777 & ~umask.

    Under this rig's own 0o002 umask that is 0o775 -- group-writable, i.e. the precondition
    for planting anything at the predictable temp name above. Pinned under a deliberately
    permissive umask, since the default one would hide the bug.
    """
    out = tmp_path / "made-by-the-run"
    old = os.umask(0o002)
    try:
        _run([_Task("w-0")], out)
    finally:
        os.umask(old)

    mode = stat.S_IMODE(out.stat().st_mode)
    assert not mode & stat.S_IWGRP, f"out_dir is group-writable: {oct(mode)}"
    assert not mode & stat.S_IWOTH, f"out_dir is world-writable: {oct(mode)}"


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

    def _recording_evaluate(task: _Task, recorder: CellRecorder) -> list[_Cell]:
        order.append(f"measure:{task.work_id}")
        return _evaluate(task, recorder)

    run = _run(
        [_Task("w-0"), _Task("w-1"), _Task("w-2")],
        out,
        evaluate=_recording_evaluate,
        before_first_spend=lambda: order.append("warm-up"),
    )
    assert (run.executed, run.reused) == (2, 1)
    assert order == ["warm-up", "measure:w-1", "measure:w-2"]


def test_before_first_spend_never_fires_on_a_fully_cache_served_resume(tmp_path: Path) -> None:
    """The property the hook exists for (mem-dblue). A driver's paid warm-up costs real `claude -p`
    calls, and a resume that measures NOTHING has nothing for it to protect."""
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

    def _evaluate_boom(_task: _Task, _recorder: CellRecorder) -> list[_Cell]:
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


# --- corpus_summary is the one owner of the cross-grid headline keys (mem-d9v8k) -------


class _VerdictResult(BaseCachedResult[_Identity, _Cell]):
    """A stand-in whose ``classify`` emits the SHARED verdict kinds, so ``corpus_summary``'s
    ``separates_all_channels`` / ``leaked`` are exercised at the CORE that now owns them rather than
    only end-to-end through a grid: ``passes == runs`` -> SEPARATES, ``passes == 0`` -> WEAK, else
    LEAK."""

    @classmethod
    def expected_cells(cls) -> set[tuple[str, str]]:
        return {("a", "recalled"), ("a", "trusted")}

    @classmethod
    def classify(cls, outcomes: Sequence[_Cell]) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for c in outcomes:
            kind = SEPARATES if c.passes == c.runs else WEAK if c.passes == 0 else LEAK
            out.append((c.channel, kind, f"{kind}: {c.passes}/{c.runs}"))
        return out


def _verdict_result(work_id: str, recalled: int, trusted: int) -> _VerdictResult:
    cells = [
        _Cell(arm="a", channel="recalled", passes=recalled, runs=2),
        _Cell(arm="a", channel="trusted", passes=trusted, runs=2),
    ]
    return _VerdictResult.of(work_id, _identity(), cells)


def test_corpus_summary_owns_separates_all_channels_and_leaked() -> None:
    """The two headline keys EVERY grid emits live in ``corpus_summary`` — the one owner — never
    copied into each grid's ``run_corpus`` where the two could rename a kind and drift a headline
    apart with nothing failing (mem-d9v8k). LEAK/SEPARATES/WEAK are the shared vocabulary the grids
    EXTEND, declared beside the summary that counts them.

    The count is against ``n_channels``, NEVER ``all()`` over whatever kinds a result happens to
    hold: a result that SEPARATES on one channel but not the other must not be credited as
    separating on all of them, and an empty grid must credit nothing."""
    results = [
        _verdict_result("all-sep", recalled=2, trusted=2),  # SEPARATES, SEPARATES
        _verdict_result("one-sep", recalled=2, trusted=0),  # SEPARATES, WEAK  -> not all channels
        _verdict_result("leaks", recalled=1, trusted=2),  # LEAK, SEPARATES
    ]
    run = CorpusRun(results=results, executed=3, reused=0)
    summary = corpus_summary(
        [_Task(r.work_id) for r in results], run, dry_run=False, repeats=2, n_channels=2
    )
    assert summary["separates_all_channels"] == 1  # only "all-sep"
    assert summary["leaked"] == ["leaks"]
    # the accounting keys ride along from the same owner
    assert summary["n_tasks"] == 3
    assert (summary["executed"], summary["reused"]) == (3, 0)

    empty = corpus_summary(
        [], CorpusRun(results=[], executed=0, reused=0), dry_run=False, repeats=2, n_channels=2
    )
    assert (
        empty["separates_all_channels"] == 0
    )  # all() over nothing is vacuously true; a count is not
    assert empty["leaked"] == []


# --- what the fire will COST: pending_tasks (mem-u9nu2) --------------------------------


def _evaluate_goal(goal: str):
    """An ``evaluate`` whose arm sends ``goal`` — so a case can move the INVOCATION and keep the
    recording honest (the write boundary hashes what the seam saw)."""

    def _evaluate_it(_task: _Task, recorder: CellRecorder) -> list[_Cell]:
        _record(recorder, goal)
        return _cells()

    return _evaluate_it


def _evaluate_runs(runs: int):
    """An ``evaluate`` that measures ``runs`` repeats — so the `repeats` case below can move that
    knob and still PUBLISH: a row's `runs` must equal the identity's `repeats`, which is the schema
    refusing a cell measured at a different repeat count than the identity filing it claims."""

    def _evaluate_it(_task: _Task, recorder: CellRecorder) -> list[_Cell]:
        _record(recorder)
        return _cells(passes=runs, runs=runs)

    return _evaluate_it


def _measured(tasks: Sequence[_Task], out: Path, evaluate, **plan_kw) -> set[str]:
    """The work_ids ``run_cached_corpus`` ACTUALLY measured — recorded off ``evaluate`` itself, the
    one place a task's spend is unfakeable. Not `executed`, which is a count: the differential below
    needs to know WHICH tasks, or a probe that named the wrong ones by the right number would pass.
    """
    seen: list[str] = []

    def _evaluate_recording(task: _Task, recorder: CellRecorder) -> list[_Cell]:
        seen.append(task.work_id)
        return evaluate(task, recorder)

    _run(tasks, out, evaluate=_evaluate_recording, **plan_kw)
    return set(seen)


_PAID = {"dry_run": False, "model": "model-one", "cli_version": "2.1.210"}

# Each case is (BASELINE plan kwargs, PERTURBED plan kwargs, evaluate). The baseline run persists
# `w-0`; the perturbed plan then asks about `[w-0, w-1]`. `unperturbed` is the resume the disclosure
# exists for — `w-0` hits, `w-1` remains. Every other case moves ONE knob off its own baseline,
# which must miss `w-0` and re-measure it.
#
# The baseline is per-case and not one shared dry run, so a knob is genuinely ISOLATED:
# `cli_version` only exists on a PAID identity (a dry run is refused one), so perturbing it from a
# dry baseline would move `dry_run` and `cli_version` together and isolate neither — a case that
# passes while naming a field it never varied.
_PERTURBATIONS = [
    pytest.param({}, {}, _evaluate, id="unperturbed"),
    pytest.param({}, {"identity": _identity(repeats=3)}, _evaluate_runs(3), id="repeats"),
    pytest.param({}, {"identity": _identity(protocol=2)}, _evaluate, id="protocol"),
    pytest.param({}, {"identity": _identity(task_fingerprint="fp-other")}, _evaluate, id="task"),
    pytest.param({}, {"identity": _identity(model="model-two")}, _evaluate, id="model"),
    pytest.param({}, {"resume": False}, _evaluate, id="resume-off"),
    # dry -> paid: a paid run must not be served a free run's simulated numbers.
    pytest.param({}, {"identity": _identity(**_PAID)}, _evaluate, id="dry_run"),
    # paid -> paid on an UPGRADED binary, off a paid baseline so only `cli_version` moves.
    pytest.param(
        {"identity": _identity(**_PAID)},
        {"identity": _identity(**(_PAID | {"cli_version": "2.1.999"}))},
        _evaluate,
        id="cli_version",
    ),
    pytest.param(
        {},
        {
            "identity": _identity(invocation_fingerprint=invocation_digest(_calls("other-goal"))),
            "plan_of": lambda _task: _calls("other-goal"),
        },
        _evaluate_goal("other-goal"),
        id="invocation",
    ),
]


@pytest.mark.parametrize(("baseline_kw", "plan_kw", "evaluate"), _PERTURBATIONS)
def test_pending_names_exactly_the_tasks_the_run_measures_under_every_identity_knob(
    tmp_path: Path, baseline_kw: dict, plan_kw: dict, evaluate
) -> None:
    """THE probe defect, end to end, and the ONLY shape that catches it. `pending_tasks` exists so a
    driver's refuse-to-spend disclosure can price the work that REMAINS instead of the whole corpus
    (mem-u9nu2) — a human authorizes real money against that number. It is a SECOND answer to the
    question `run_cached_corpus` answers by running, and two answers that agree today is exactly
    this module's defect family (see its docstring), so 'call both on a happy path and compare'
    proves nothing.

    A PERTURBATION differential instead: for every knob of the identity, the two answers must move
    TOGETHER. A probe that modelled the decision — an `is_file` check, a hand-rolled identity, a
    forgotten `resume` — passes the unperturbed case and fails here, in the UNDER-report direction
    that costs money: it calls a task cached, the fire measures it anyway, and the spend exceeds
    what was authorized.

    `pending_tasks` is asked BEFORE the run, since the run publishes and would answer its own
    question."""
    out = tmp_path / "out"
    tasks = [_Task("w-0"), _Task("w-1")]
    # The baseline is the run being RESUMED, so it measures the default way; `evaluate` belongs to
    # the perturbed run, which is the one whose arm may send a different cycle or repeat count.
    first = _run([_Task("w-0")], out, **baseline_kw)  # only w-0 is measured and persisted
    assert (first.executed, first.reused) == (1, 0)

    predicted = {task.work_id for task in pending_tasks(_plan(out, **plan_kw), tasks)}
    measured = _measured(tasks, out, evaluate, **plan_kw)

    assert predicted == measured, "the probe priced a different fire than the run made"


def test_pending_is_a_read_and_publishes_nothing(tmp_path: Path) -> None:
    """It runs on the REFUSE path, which spends nothing and writes nothing, and its answer must not
    become true by being asked: a probe that created `--out`, rewrote a result, or touched an mtime
    would corrupt the record of which tasks a run actually measured (`run_cached_corpus` leaves
    mtimes as exactly that record)."""
    out = tmp_path / "out"
    _run([_Task("w-0")], out)
    before = {path: path.stat().st_mtime_ns for path in sorted(out.iterdir())}

    assert [t.work_id for t in pending_tasks(_plan(out), [_Task("w-0"), _Task("w-1")])] == ["w-1"]

    assert {path: path.stat().st_mtime_ns for path in sorted(out.iterdir())} == before
    # And it does not conjure an --out that a run has not created yet.
    fresh = tmp_path / "never"
    assert len(pending_tasks(_plan(fresh), [_Task("w-0")])) == 1
    assert not fresh.exists()


def test_pending_over_a_fully_served_corpus_is_empty(tmp_path: Path) -> None:
    """The bead's headline scenario at its limit: every task cached, so the fire spends NOTHING.
    The drivers print their own branch for this rather than a `0 calls` line, because zero is also
    what a preflight costs here (`before_first_spend` never fires) and that is worth saying."""
    out = tmp_path / "out"
    tasks = [_Task("w-0"), _Task("w-1")]
    _run(tasks, out)
    assert pending_tasks(_plan(out), tasks) == []


def test_pending_refuses_a_corpus_the_run_would_refuse(tmp_path: Path) -> None:
    """A duplicate work_id aliases two tasks onto one result file, so the probe would answer for the
    wrong one — and the run refuses such a corpus outright (`assert_usable_work_ids`), so a
    disclosure that priced it would be pricing a fire that cannot start."""
    with pytest.raises(ValueError, match="duplicate work_id"):
        pending_tasks(_plan(tmp_path / "out"), [_Task("w-0"), _Task("w-0")])


def test_pending_never_softens_a_fingerprint_the_plan_does_not_own_into_a_cost(
    tmp_path: Path,
) -> None:
    """The loudest structural defect this cache has — an identity carrying a fingerprint its plan
    did not compute — must surface as itself from the probe too, never be swallowed into a number.
    A driver catches the ONE diagnosis that means 'no honest answer exists' (an unidentifiable
    claude binary) and prints a labeled ceiling; a grid whose plan and identity disagree is not that
    diagnosis, and a disclosure that printed a cost for it would be pricing a run that cannot
    execute."""
    with pytest.raises(ValueError, match="authored the field by a route the plan does not own"):
        pending_tasks(
            # `_fp` — the digest of this plan's own `plan_of` — is handed in and DROPPED, which is
            # the whole defect: the identity names an invocation the plan never described.
            _plan(
                tmp_path / "out",
                identity_of=lambda _task, _fp: _identity(invocation_fingerprint="authored-here"),
            ),
            [_Task("w-0")],
        )
