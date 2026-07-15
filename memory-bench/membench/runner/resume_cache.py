"""The resumable per-task result cache, shared by every paid toolreq grid.

THE CACHE INVARIANT — stated once here, for every consumer, not re-argued at each check. A resumed
paid run may reuse a persisted cell only when that cell's identity is a total function of what was
measured: the invocations sent, the inputs the scorer grades against, and the run knobs. Every cache
defect this codebase has shipped had ONE shape — the identity hashed a MODEL of the executed input,
the model was one field short, and the task then reported ``reused``, spent nothing, did NOT crash,
and printed a stale or fabricated number as a real measurement. A green suite is not evidence
against that; each such shape is an executable case in ``tests/test_resume_cache.py``.

So the identity does not model the executed input, it CARRIES it: ``invocation_fingerprint`` hashes
the exact ``claude -p`` command lines the task's cells spawn, and ``run_cached_corpus`` refuses to
publish a measurement whose RECORDED invocations do not hash to it. The two halves are what make it
structural rather than a convention — hash the artifact, and check the artifact against the hash.

Every defense below is STRUCTURAL — a property of the schema, not of the caller — and each is
stated at its own definition. A consumer subclasses ``BaseRunIdentity`` / ``BaseCellOutcome`` /
``BaseCachedResult`` to add its own measured inputs and cell fields, and supplies ``expected_cells``
+ ``classify``. All of it then stays in force by construction: a grid that does not cover its cells
exactly, or whose stored verdict is not the one its own rows imply, cannot be constructed, let
alone loaded.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from membench.bbon.models import deterministic_id
from membench.runner.headless_agent import CellCalls, resolve_model


def digest(payload: object) -> str:
    """The one hash used by every fingerprint in every grid — same encoding, same width.

    Canonical (``deterministic_id`` -> RFC8785-style: object keys SORTED, list order KEPT). Sorting
    keys is what makes a field REORDER — which moves nothing executed and nothing scored — a cache
    hit rather than a full re-spend, and it is safe only because every input whose ORDER IS a
    measured input (surfaced memory, a retrieval payload, a cell's legs) is passed as a LIST."""
    return deterministic_id(payload)[:16]


def invocation_digest(cells: Iterable[CellCalls]) -> str:
    """The value ``BaseRunIdentity.invocation_fingerprint`` carries: every ``claude -p`` a task's
    cells will spawn — argv and all, prompt included.

    Its two halves pull in OPPOSITE directions and both are load-bearing.

    The CELLS sort. Each is an independent run in its own sandbox carrying its own ``(arm,
    channel)`` label, so the order a grid's loop happens to visit them in moves nothing executed and
    nothing scored — sorting keeps a reordered loop a cache HIT rather than a full paid re-spend.

    A cell's CALLS do not. Its legs share a cwd and a config dir and run in SEQUENCE, so their order
    IS a measured input: swap the builtin arm's establish and goal legs and it establishes the fact
    into a directory it then wipes, measuring a guaranteed 0/N. A digest that read that as the same
    run as the un-swapped one would serve the pre-swap numbers as post-swap measurements — this
    module's whole defect family, so the list stays a list. (Same two halves, same reasons, as
    ``digest``'s canonical-JSON contract.)

    Two records for one cell is refused, never merged: a cell runs ONE cycle, so they cannot both
    describe what it did, and merging would let a fabricated record overwrite the measured one."""
    by_cell: dict[tuple[str, str], list[list[str]]] = {}
    for cell in cells:
        key = (cell.arm, cell.channel)
        if key in by_cell:
            raise ValueError(f"two invocation records for cell {key} — a cell runs ONE cycle")
        by_cell[key] = [list(argv) for argv in cell.calls]
    return digest([[arm, channel, by_cell[(arm, channel)]] for arm, channel in sorted(by_cell)])


class CacheableTask(Protocol):
    """What the cache needs of a task: an id that keys both its identity and its result file.

    Structural, so this module never imports a task type and no consumer must inherit anything to
    be cacheable."""

    @property
    def work_id(self) -> str: ...


TaskT = TypeVar("TaskT", bound=CacheableTask)


class Cell(Protocol):
    """The four fields every grid's verdict rule reads.

    Structural, so one rule applies both to a persisted row (``BaseCellOutcome``) and to a raw
    in-memory measurement (``ArmOutcome``) — including a DEGENERATE one the schema refuses to
    persist (``runs=0``), which is how the rules that refuse it stay testable at all."""

    @property
    def arm(self) -> str: ...

    @property
    def channel(self) -> str: ...

    @property
    def passes(self) -> int: ...

    @property
    def runs(self) -> int: ...


def render_verdict(classified: Sequence[tuple[str, str, str]]) -> str:
    """The one renderer, so every grid's verdict string is built from its kind ladder rather than
    authored beside it."""
    return " | ".join(f"[{channel}] {line}" for channel, _kind, line in classified)


class BaseRunIdentity(BaseModel):
    """What a persisted cell was measured under: the run's knobs, and every measured input a grid
    has in common. A consumer subclasses this to add the inputs only it has.

    ``repeats`` is bounded at 1: ``--repeats 0`` evaluates nothing and persists 0/0 rows that a
    verdict rule reads as a confident "no separation" for a task that was never run. The drivers
    reject it at the flag too; this is the structural backstop, and both are deliberate.

    ``protocol`` covers what the fingerprints structurally CANNOT: the executing and scoring CODE. A
    change to the stream-json parser, the scorer, the engagement check or the sandbox firewall moves
    a result without touching any task field, so every fingerprint stays identical across it and a
    resumed sweep would serve pre-change answers as if they measured the new protocol. Each grid
    owns its own constant and BUMPS it on such a change. It is a MANUAL gate and that is its
    weakness: the alternative (hashing those modules' source) would invalidate the whole paid grid
    on any comment edit and re-spend real money. It cannot cover the ``claude`` binary itself
    (version, PATH, account config) — see the drivers' docstrings.

    ``invocation_fingerprint`` hashes the COMMAND LINES THEMSELVES — every ``claude -p`` argv every
    cell will spawn, prompt included — rather than a model of what goes into them, and so it cannot
    be incomplete *about the invocation*, because it IS the invocation. Its other half is the write
    boundary: ``run_cached_corpus`` refuses to publish a measurement whose RECORDED invocations do
    not hash to it (``invocation_digest``), so the two cannot be things that merely agree today.
    Hashing the whole argv rather than just the prompt is what keeps ``--allowedTools``, ``--model``
    and ``--strict-mcp-config`` out of ``protocol``'s manual surface: unclamping ``--allowedTools``
    frees the scored goal leg while moving no task field, no payload and no prompt.

    It does NOT subsume ``task_fingerprint`` and is carried ALONGSIDE it, never in place of it: the
    scorer grades fields no command line mentions."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    repeats: int = Field(ge=1)
    dry_run: bool
    model: str
    protocol: int
    task_fingerprint: str
    invocation_fingerprint: str

    @model_validator(mode="after")
    def _model_is_the_one_the_agent_will_actually_run_under(self) -> Self:
        """``model`` must be a FIXED POINT of ``resolve_model`` — the rule made structural rather
        than left to caller discipline (see this module's docstring)."""
        resolved = resolve_model(self.model)
        if self.model != resolved:
            raise ValueError(
                f"model {self.model!r} is not the one the agent would run under ({resolved!r}): a "
                "cache identity must store the RESOLVED model (headless_agent.resolve_model), or a "
                "repointed MEMBENCH_AGENT_MODEL serves one model's numbers as another's"
            )
        return self


class BaseCellOutcome(BaseModel):
    """One persisted ``(arm, channel)`` row — the schema the in-memory measurement dataclasses
    cannot enforce.

    ``ArmOutcome`` and friends are plain frozen dataclasses that type-check nothing, so a
    hand-edited ``{"passes": "0"}`` constructs happily and only detonates LATER inside a verdict
    rule
    (``passes > 0`` -> TypeError), an unhandled exception escaping mid-resume and killing a paid
    sweep. Strict mode rejects the string, the float, and the bool (an ``int`` subclass that would
    otherwise sail straight through an ``isinstance`` check) structurally, at the parse boundary,
    where a bad row is still just a cache miss.

    ``runs`` is bounded at 1 for the reason ``BaseRunIdentity.repeats`` is; ``passes`` is bounded
    above by ``runs`` because a row claiming more passes than runs is a fabricated ceiling."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    arm: str
    channel: str
    passes: int = Field(ge=0)
    runs: int = Field(ge=1)

    @model_validator(mode="after")
    def _passes_cannot_exceed_runs(self) -> Self:
        if self.passes > self.runs:
            raise ValueError(f"{self.arm}/{self.channel}: passes {self.passes} > runs {self.runs}")
        return self


IdentityT = TypeVar("IdentityT", bound=BaseRunIdentity)
CellT = TypeVar("CellT", bound=BaseCellOutcome)


class BaseCachedResult(BaseModel, Generic[IdentityT, CellT]):
    """One task's persisted result — the ONLY shape ``<work_id>.json`` may take, on write and on
    read alike, so a writer cannot emit a record its reader would have to reject.

    The identity is NESTED, not spread across the top level: nesting is what makes acceptance a
    whole-object ``==`` against this run's identity instead of a field-by-field walk that can only
    check the fields the reader thought to enumerate.

    The two cross-row invariants below are schema, not caller discipline. A subclass adds its own by
    declaring another ``model_validator``; it does not re-implement these."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    work_id: str
    identity: IdentityT
    outcomes: list[CellT]
    verdict: str

    @classmethod
    def expected_cells(cls) -> set[tuple[str, str]]:
        """The full ``(arm, channel)`` grid one task must cover to be scored as complete."""
        raise NotImplementedError

    @classmethod
    def classify(cls, outcomes: Sequence[CellT]) -> list[tuple[str, str, str]]:
        """This grid's verdict rule as ONE ladder: ``(channel, kind, line)``, one entry per channel.

        The kind a summary COUNTS and the line a human READS come out of the same branch, so they
        cannot desync. Deriving the counts the other way — substring-matching the rendered line, as
        ``"LEAK" in verdict`` — is what this shape refuses: reword a line and the headline silently
        changes, with no schema change and nothing failing."""
        raise NotImplementedError

    @classmethod
    def implied_verdict(cls, outcomes: Sequence[CellT]) -> str:
        """The verdict these rows produce — rendered from ``classify``, never authored beside it.
        The stored one may only ever be this one."""
        return render_verdict(cls.classify(outcomes))

    @property
    def kinds(self) -> list[str]:
        """This record's per-channel verdict kinds — what a summary counts, taken from the same
        ladder that produced its ``verdict`` string."""
        return [kind for _channel, kind, _line in type(self).classify(self.outcomes)]

    @model_validator(mode="after")
    def _rows_are_a_complete_grid_measured_at_this_identity(self) -> Self:
        for cell in self.outcomes:
            if cell.runs != self.identity.repeats:
                raise ValueError(
                    f"{cell.arm}/{cell.channel}: measured at {cell.runs} run(s), identity says "
                    f"{self.identity.repeats}"
                )
        # The cells must cover the grid EXACTLY — one row per (arm, channel), no dupes, no strays.
        # BOTH halves are load-bearing and each alone has already shipped a bug: N copies of one
        # cell has the right ARITY but wrong coverage (a channel no `claude -p` call ever ran,
        # credited as full coverage); the correct rows PLUS a duplicate cover the grid as a SET but
        # a verdict rule keys by (arm, channel), so the last duplicate overwrites the real
        # measurement — a genuine SEPARATES rewritten into a fabricated KILL.
        cells = [(cell.arm, cell.channel) for cell in self.outcomes]
        expected = type(self).expected_cells()
        if len(cells) != len(expected) or set(cells) != expected:
            raise ValueError(f"cells {sorted(cells)} are not the grid {sorted(expected)}")
        return self

    @model_validator(mode="after")
    def _verdict_is_the_one_its_own_rows_imply(self) -> Self:
        """The verdict is DERIVED, so a persisted one may only be the one its rows produce.

        Without this the field is the single unchecked value in the record: a hand-edited
        ``"verdict": "SEPARATES: ..."`` over KILL rows passes every other check (identity intact,
        rows intact) and the summary a human reads is built from ``verdict`` strings. It was
        harmless only by accident, because a caller happened to recompute the verdict from the
        loaded rows and overwrite it. That is a redundancy standing in for a missing invariant,
        which is the shape this module exists to refuse: a value outside the checks is not defended
        by the checks around it."""
        implied = type(self).implied_verdict(self.outcomes)
        if self.verdict != implied:
            raise ValueError(f"verdict {self.verdict!r} is not the one its rows imply: {implied!r}")
        return self

    @classmethod
    def of(cls, work_id: str, identity: IdentityT, outcomes: Sequence[CellT]) -> Self:
        """Build the record a run publishes, verdict included — through the same validators a loaded
        file passes, so an invalid record cannot reach disk in the first place."""
        return cls(
            work_id=work_id,
            identity=identity,
            outcomes=list(outcomes),
            verdict=cls.implied_verdict(outcomes),
        )


ResultT = TypeVar("ResultT", bound=BaseCachedResult[Any, Any])


def load_cached(
    result_path: Path, identity: BaseRunIdentity, result_cls: type[ResultT]
) -> ResultT | None:
    """The persisted RECORD of a task whose identity matches this run, or ``None`` meaning MISS.

    Returned whole, and reused as-is: every field it carries — rows, flags, and verdict alike — is
    checked by the schema, so there is nothing left for a caller to recompute and no reason to
    rewrite the file it came from.

    Every rejection is a miss, never a crash and never a partial acceptance. These files are written
    by a sweep that can be killed mid-run and re-read by a PAID resume, so the two failure modes to
    design against are an exception escaping and killing the whole sweep on one bad file, and a
    degenerate or FOREIGN file being scored as a complete task."""
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, RecursionError):
        # A PARSE BOUNDARY over an untrusted file: catch by blast radius, not by enumerating what
        # json.loads is known to raise. ValueError covers JSONDecodeError, UnicodeDecodeError and
        # the >4300-digit int-literal limit; RecursionError covers deeply nested JSON; OSError
        # covers unreadable/directory/permission.
        return None
    try:
        loaded = result_cls.model_validate(raw)
    except ValidationError:
        return None  # wrong shape, drifted type, degenerate row, or an incomplete/forged grid
    if loaded.identity != identity:
        return None  # another run's measurement (dry-run vs paid, model, repeats, world...)
    return loaded


def assert_usable_work_ids(tasks: Sequence[CacheableTask], summary_name: str) -> None:
    """A work_id must be unique and a safe, unclaimed filename — it keys both the identity map and
    the ``<work_id>.json`` result path.

    A DUPLICATE silently aliases two different tasks onto one cache file: the second overwrites the
    first and, on resume, that single record is served for both. An UNSAFE id is corpus data used to
    build a filesystem path before being checked as one — it either claims the summary's name (the
    driver writes the summary into the same directory AFTERWARDS, overwriting that task's result,
    which then misses forever) or carries a separator/traversal that writes outside ``--out``.
    Corpus ids are sequence-derived and a regenerated or hand-assembled corpus can produce either,
    so refuse rather than measure."""
    duplicates = sorted(id_ for id_, n in Counter(t.work_id for t in tasks).items() if n > 1)
    if duplicates:
        raise ValueError(
            f"duplicate work_id(s) in the corpus: {duplicates} — each task must map to exactly "
            "one <work_id>.json, or a resumed run serves one task's measurement for another"
        )
    unsafe = sorted(
        t.work_id
        for t in tasks
        if f"{t.work_id}.json" == summary_name or Path(t.work_id).name != t.work_id or not t.work_id
    )
    if unsafe:
        raise ValueError(
            f"unsafe work_id(s) for a result filename: {unsafe} — a work_id must be a plain "
            f"filename component and must not claim the summary's name ({summary_name})"
        )


@dataclass(frozen=True)
class Evaluation:
    """What evaluating one task produced: the rows to persist, and the ``claude -p`` invocations it
    ACTUALLY made.

    The two travel together because they are one measurement. Returning only the rows is what let a
    grid's identity describe a prompt its arms no longer send: without the invocations coming back
    out of the evaluation there is nothing to check the identity against at the write boundary (see
    ``run_cached_corpus``, which is where that argument is made)."""

    outcomes: Sequence[BaseCellOutcome]
    calls: Sequence[CellCalls]


@dataclass(frozen=True)
class CorpusRun(Generic[ResultT]):
    """What one sweep over the corpus produced, and what it COST to produce it.

    ``executed`` / ``reused`` are the accounting a paid run is read through: they say how many tasks
    this invocation actually spent ``claude -p`` calls on. Each grid shapes its own headline summary
    from ``results``; the split between what was measured and what was replayed is common to all of
    them."""

    results: list[ResultT]
    executed: int
    reused: int


def run_cached_corpus(
    tasks: Sequence[TaskT],
    *,
    out_dir: Path,
    result_cls: type[ResultT],
    identity_of: Callable[[TaskT], BaseRunIdentity],
    evaluate: Callable[[TaskT], Evaluation],
    summary_name: str,
    resume: bool = True,
    before_first_spend: Callable[[], None] | None = None,
) -> CorpusRun[ResultT]:
    """Evaluate every task, persisting one ``<work_id>.json`` each, and reuse a persisted result
    only when its identity matches this run's — so a FREE dry-run's simulated result can never
    satisfy a PAID run over the same ``--out``, and a corrupt or partial file is a miss rather than
    a crash.

    ``identity_of`` and ``evaluate`` are injected rather than named: they are the ONLY two things
    that differ between the grids that share this cache, and injecting them is what keeps the arms,
    the invocations and the verdict rule of one experiment out of the other's.

    ``before_first_spend`` is a driver's paid warm-up — a preflight cycle, a mechanism check — and
    is injected for the same reason: it must fire once if this run will measure anything and NOT AT
    ALL if it will not, and only this loop knows which. A driver that computed the miss set itself
    would keep a MODEL of that decision (the invariant above, one input over) and drift from it the
    moment the two disagreed about a knob such as ``resume``.

    It fires immediately before the first task that reaches ``evaluate``, so
    ``assert_usable_work_ids`` and every cache hit speak FIRST and a fully-served corpus costs zero
    paid calls. Raising from it aborts the run before anything is measured — how a driver halts a
    sweep it has diagnosed as not worth spending on.

    Injected INDEPENDENTLY, though — which is exactly why the write boundary below exists.
    Nothing else binds the invocations a grid HASHES to the ones its arms SEND, and a fingerprint
    left resting on that agreement is one edit from being a hand-written model of the executed
    input: change what a leg surfaces, forget to move the plan, and every persisted cell still
    matches, so a resumed PAID run reports ``reused``, spends nothing, does not crash, and
    publishes pre-change numbers as post-change measurements. A refused measurement is expensive
    — the ``claude -p`` calls are already made and are NOT written — and that is the cheap end of
    this failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    assert_usable_work_ids(tasks, summary_name)

    results: list[ResultT] = []
    executed = 0
    reused = 0
    warmed_up = False
    for task in tasks:
        result_path = out_dir / f"{task.work_id}.json"
        identity = identity_of(task)
        # No is_file() probe first: load_cached is TOTAL over its path — a missing file raises
        # OSError inside it and is a miss like every other rejection.
        cached = load_cached(result_path, identity, result_cls) if resume else None
        if cached is not None:
            # Reused whole. Nothing is recomputed and the file is NOT rewritten: it already passed
            # every validator, so a rewrite could only reproduce it — and a fully cache-served
            # resume then does zero writes, leaving the results' mtimes an honest record of which
            # tasks this run actually measured.
            results.append(cached)
            reused += 1
            continue
        if before_first_spend is not None and not warmed_up:
            # Not `executed == 0`: that is equivalent only while the write boundary below raises on
            # every miss after the first. Soften that raise to a skip and `executed == 0` silently
            # re-fires a PAID warm-up on the next miss.
            warmed_up = True
            before_first_spend()
        evaluation = evaluate(task)
        sent = invocation_digest(evaluation.calls)
        if sent != identity.invocation_fingerprint:
            raise ValueError(
                f"{task.work_id}: the `claude -p` invocations this task actually made hash to "
                f"{sent}, but the identity it was measured under fingerprints "
                f"{identity.invocation_fingerprint} — the grid's plan is no longer what its arms "
                "execute. This measurement is NOT written: publishing it would file a real result "
                "under a fingerprint describing a command line it never sent, and every cell a "
                "later resume serves on that fingerprint would answer for a different run. Fix the "
                "plan (or the arm) so the two are one thing again, then re-measure."
            )
        executed += 1
        result = result_cls.of(task.work_id, identity, evaluation.outcomes)
        # Atomic publish: write a sibling temp file then rename, so a kill mid-write leaves either
        # the old result or the new one, never a half-written JSON the next resume trips on.
        tmp_path = result_path.with_suffix(".json.tmp")
        tmp_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(result_path)
        results.append(result)
    return CorpusRun(results=results, executed=executed, reused=reused)


def corpus_summary(
    tasks: Sequence[CacheableTask],
    run: CorpusRun[ResultT],
    *,
    dry_run: bool,
    repeats: int,
) -> dict[str, Any]:
    """The summary keys EVERY grid emits: what the run covered, what it cost, and the rows behind
    both. Each grid spreads this and adds only its own headline, so the accounting a paid run is
    read through has one owner rather than a copy per grid that can drift apart."""
    return {
        "n_tasks": len(tasks),
        "executed": run.executed,
        "reused": run.reused,
        "dry_run": dry_run,
        "repeats": repeats,
        "per_task": [r.model_dump(mode="json") for r in run.results],
    }
