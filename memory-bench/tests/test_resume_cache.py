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

from membench.runner.headless_agent import ENV_MODEL
from membench.runner.resume_cache import (
    BaseCachedResult,
    BaseCellOutcome,
    BaseRunIdentity,
    assert_usable_work_ids,
    digest,
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


def _identity(**overrides: object) -> _Identity:
    fields: dict[str, object] = {
        "repeats": 2,
        "dry_run": True,
        "model": "",
        "protocol": 1,
        "task_fingerprint": "fp-task",
        "prompt_fingerprint": "fp-prompt",
    }
    return _Identity(**(fields | overrides))  # type: ignore[arg-type]


def _cells(passes: int = 2, runs: int = 2) -> list[_Cell]:
    return [
        _Cell(arm="a", channel=channel, passes=passes, runs=runs)
        for channel in ("recalled", "trusted")
    ]


def _evaluate(_task: _Task) -> list[_Cell]:
    return _cells()


def _run(tasks: Sequence[_Task], out: Path, identity: _Identity | None = None, resume: bool = True):
    ident = identity if identity is not None else _identity()
    return run_cached_corpus(
        tasks,
        out_dir=out,
        result_cls=_Result,
        identity_of=lambda _task: ident,
        evaluate=_evaluate,
        summary_name=SUMMARY_NAME,
        resume=resume,
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
