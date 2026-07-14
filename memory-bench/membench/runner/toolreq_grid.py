"""mem-rk41.3 / .3.1 — the tool-requiring real-agent grid core: arms, verdict, and the
resumable per-task result cache.

The pure half of ``scripts/grid_toolreq_realagent.py``, and it lives HERE rather than there
for one reason: ``scripts/`` is not type-checked. CI runs ``mypy --strict membench`` only,
and every resume-cache defect this code has shipped lived in the untyped script. The driver
keeps its argparse/main, its ``ours``-store seeder (which reaches into a sibling script) and
its printing; everything that decides what is EXECUTED, what is SCORED, and what may be
REUSED is in this module, inside the type checker.

THE CACHE INVARIANT — stated once here, not re-argued at each check. A resumed paid run may
reuse a persisted cell only when that cell's identity is a total function of what was
measured: the prompts sent, the inputs the scorer grades against, and the run knobs. Every
cache defect this code has shipped had a single shape — the identity hashed a MODEL of the
executed input, the model was one field short, and the task then reported ``reused``, spent
nothing, did NOT crash, and printed a stale or fabricated number as a real measurement. A
green suite is not evidence against that. Each such shape is an executable case in
``tests/test_toolreq_realagent.py``; do not delete an identity field or a validity check
below without a test proving the survivor covers every input the removed one did. Two
collapses have already re-opened a hole that only LOOKED subsumed.

The persisted record is a SCHEMA (``CachedResult``), not a dict a reader re-derives: strict,
``extra="forbid"``, identity NESTED, so a cached file is accepted only on WHOLE-OBJECT
identity equality. The previous subset comparison (``any(loaded.get(k) != v ...)``) accepted
any file carrying an identity field the reader did not know about — a newer writer's record,
served to an older reader as its own.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from membench.bbon.models import deterministic_id
from membench.runner.headless_agent import (
    MemoryChannel,
    build_agent_prompt,
    resolve_model,
)
from membench.runner.realagent_probe import ArmOutcome, run_arm
from membench.runner.toolreq_realagent import ToolReqRealAgentTask
from membench.schemas.sequence import BenchmarkSequence

ARMS = ("none", "oracle", "ours")
CHANNELS = (MemoryChannel.RECALLED, MemoryChannel.TRUSTED)

# The run summary, written into the SAME directory as the per-task `<work_id>.json` results —
# hence a name the tasks are not allowed to claim (see _assert_usable_work_ids).
SUMMARY_NAME = "summary-toolreq-realagent.json"

# Rides in the cache identity to cover what the fingerprints structurally CANNOT: the executing
# and scoring CODE. A change to `run_arm`, `build_agent_prompt`, the stream-json parser,
# `score_goal_action`, or DEFAULT_TIMEOUT_S moves a result without touching any task field, so
# every fingerprint stays identical across it and a resumed sweep would serve pre-change answers
# as if they measured the new protocol.
#
# BUMP THIS when the execution or scoring path changes in a way that could move a result. It is a
# MANUAL gate and that is its weakness: the alternative (hashing those modules' source) would
# invalidate the whole paid grid on any comment edit and re-spend real money. It cannot cover the
# `claude` binary itself (version, PATH, account config) — see the driver docstring.
EXECUTION_PROTOCOL = 1

# Seeds the `ours` store + resolves its payload: (sequences, tasks, store_path, mem_bin)
# -> work_id -> (source work_id -> rendered payload). Injected by the caller, never defaulted:
# the real seeder depends on a script-resident payload resolver and a built `bin/mem`, neither of
# which belongs under `membench/`, and a hermetic test stubs it out (this codebase's
# CliRunner/RetrieveRunner injection convention — see headless_agent.CliRunner).
SeedFn = Callable[
    [Sequence[BenchmarkSequence], Sequence[ToolReqRealAgentTask], Path, str],
    dict[str, dict[str, str]],
]

# The one arm whose memory can legitimately come back EMPTY: `ours` retrieves, and temporal
# LOO admits no priors for the lifecycle-earliest task. See evaluate_task.
RETRIEVING_ARM = "ours"


def _digest(payload: object) -> str:
    """The one hash used by every fingerprint below — same encoding, same width, one place.

    Canonical (``deterministic_id`` -> RFC8785-style: object keys SORTED, list order KEPT), which
    is what a cache identity needs and a plain ``json.dumps`` is not. ``json.dumps`` serializes
    dict keys in INSERTION order, so ``task_fingerprint``'s ``goal_step.model_dump()`` would hash
    the pydantic field-DECLARATION order: reordering two fields in ``SequenceStep`` — a no-op that
    moves nothing executed and nothing scored — would miss every persisted cell and re-spend the
    whole paid grid. Sorting keys is safe precisely because every input whose ORDER IS a measured
    input (``oracle_memory``, the ``ours`` payload, the prompt cells) is passed as a LIST, and
    canonical JSON preserves list order."""
    return deterministic_id(payload)[:16]


def arm_memories(
    task: ToolReqRealAgentTask, ours_payload: Mapping[str, str] | None = None
) -> dict[str, dict[str, str]]:
    """The surfaced memory per arm, resolved once per task: nothing for ``none`` (the leak
    detector), the id-exact opaque ceiling for ``oracle``, the genuine ``mem retrieve``
    payload (rendered citation+lessons text, keyed by source work_id) for ``ours``."""
    memories = {
        "none": {},
        "oracle": dict(task.oracle_memory),
        "ours": dict(ours_payload or {}),
    }
    if set(memories) != set(ARMS):
        raise ValueError(f"arm memories {sorted(memories)} out of sync with ARMS {ARMS}")
    return memories


def evaluate_task(
    task: ToolReqRealAgentTask,
    *,
    repeats: int,
    model: str,
    dry_run: bool,
    ours_payload: Mapping[str, str] | None = None,
) -> list[ArmOutcome]:
    """Run every (arm, channel) cell for one task.

    One cell is never run: ``ours`` when its retrieval came back EMPTY — not an edge case but a
    guarantee for the lifecycle-earliest task, which temporal LOO leaves with no priors. An empty
    payload makes the ``ours`` prompt byte-identical to ``none``, so the cell is none-equivalent
    by construction (delta exactly 0); running it would spend ``repeats`` real ``claude -p`` turns
    per channel to re-measure ``none``, and would leave a flat ``(ours 0/N)`` unattributable — a
    retrieval miss reads exactly like memory-did-not-help. The ``none`` cell is relabeled instead
    (``run_grid_3arm``'s empty-retrieval convention) and ``run_corpus`` records the
    ``ours_retrieval_empty`` flag so the two causes stay distinguishable."""
    memories = arm_memories(task, ours_payload)
    outcomes: list[ArmOutcome] = []
    for channel in CHANNELS:
        cells: dict[str, ArmOutcome] = {}
        for arm in ARMS:
            if arm == RETRIEVING_ARM and not memories[arm]:
                continue  # filled from `none` below — never spent on
            cells[arm] = run_arm(
                arm=arm,
                step=task.goal_step,
                memory=memories[arm],
                channel=channel,
                repeats=repeats,
                model=model,
                dry_run=dry_run,
                current_values=task.current_opaque_values,
            )
        if RETRIEVING_ARM not in cells:
            cells[RETRIEVING_ARM] = replace(cells["none"], arm=RETRIEVING_ARM)
        outcomes.extend(cells[arm] for arm in ARMS)
    return outcomes


# The verdict is decided by these two arms alone; every other ARMS member rides along as
# an informational suffix (see task_verdict).
_GATING_ARMS = ("none", "oracle")


def task_verdict(outcomes: Sequence[ArmOutcome]) -> str:
    """The probe's per-channel verdict rule (valid again under opaque values): ``none``
    passing means a leak; ``none`` 0 + ``oracle`` ceiling means the arms separate. Arms beyond
    ``_GATING_ARMS`` (today: ``ours``) never gate the call — each rides along as an
    informational suffix, because ``ours`` scoring near ``none`` is the expected substrate
    finding, not a failure of the grid."""
    by = {(o.arm, o.channel): o for o in outcomes}
    lines: list[str] = []
    for channel in (c.value for c in CHANNELS):
        none_o = by.get(("none", channel))
        oracle_o = by.get(("oracle", channel))
        if none_o is None or oracle_o is None:
            continue
        runs = oracle_o.runs
        if none_o.passes > 0:
            call = f"LEAK: none {none_o.passes}/{none_o.runs} — value reached the prompt"
        elif oracle_o.passes == runs and runs > 0:
            call = f"SEPARATES: none 0/{runs}, oracle {oracle_o.passes}/{runs}"
        elif oracle_o.passes == 0:
            call = f"KILL: oracle ceiling 0/{runs} — no separation"
        else:
            call = f"WEAK: none 0/{runs}, oracle {oracle_o.passes}/{runs} — add repeats"
        for arm in ARMS:
            if arm in _GATING_ARMS:
                continue
            extra = by.get((arm, channel))
            if extra is not None:
                call += f" ({arm} {extra.passes}/{extra.runs})"
        lines.append(f"[{channel}] {call}")
    return " | ".join(lines)


def expected_cells() -> set[tuple[str, str]]:
    """The full (arm, channel) grid one task must cover to be scored as complete."""
    return {(arm, channel.value) for arm in ARMS for channel in CHANNELS}


def task_fingerprint(task: ToolReqRealAgentTask) -> str:
    """Identifies the WORLD a cached result was measured against — everything that determines what
    is EXECUTED and how it is SCORED, not merely the authored values.

    Work ids are positional (``w-0``, ``w-1``), so a regenerated corpus reuses them and, without
    this, a re-run over the same ``--out`` reports the PREVIOUS world's numbers at executed=0.
    ``goal_step`` is hashed whole (a pydantic ``model_dump`` covers new fields automatically): it
    is the prompt actually sent AND carries the ``outcome_checks`` the run is graded against, so
    an adapter change that leaves the authored values untouched still moves the measurement.

    ``oracle_memory`` is hashed in ITS OWN ORDER, deliberately: ``build_agent_prompt`` renders
    ``available_memory.items()``, so insertion order IS the order of lines in the prompt, and a
    sorted hash would call two different ceiling-arm prompts identical. ``current_opaque_values``
    IS sorted, and that is consistent rather than contradictory — it never reaches a prompt, only
    the scorer's membership test, so its order is not a measured input."""
    return _digest(
        {
            "work_id": task.work_id,
            "oracle_memory": list(task.oracle_memory.items()),
            "current_opaque_values": sorted(task.current_opaque_values),
            "goal_step": task.goal_step.model_dump(mode="json"),
        }
    )


def payload_fingerprint(ours_payload: Mapping[str, str]) -> str:
    """Identifies the ``ours`` arm's actual MEASURED INPUT — the text retrieval surfaced.

    ``task_fingerprint`` is task-LOCAL; this payload is not. It comes from CROSS-TASK retrieval
    over the whole seeded store, so it moves when a sibling sequence is added, when the corpus is
    regenerated, or when ``bin/mem``'s retrieval changes — none of which touch the queried task's
    own fields. Hashing the payload puts all three in the identity without modelling any of them.

    ORDER-PRESERVING, for the same reason ``oracle_memory`` is: ``resolve_payloads`` inserts in
    ``mem retrieve``'s RANK order and the prompt renders that order, so the same SET of lessons
    ranked differently is a different prompt and must miss."""
    return _digest(list(ours_payload.items()))


def prompt_fingerprint(task: ToolReqRealAgentTask, ours_payload: Mapping[str, str]) -> str:
    """Hashes the PROMPTS THEMSELVES — the exact text every (arm, channel) cell will send to
    ``claude -p`` — rather than a model of what goes into them, and so it cannot be incomplete
    *about the prompt*, because it IS the prompt. Memory content, memory order, the trust
    channel's framing, the user request, and every prompt-visible field of ``goal_step`` collapse
    into one digest whether or not anyone remembered to enumerate them. Building a prompt is
    string concatenation: FREE, no agent turn.

    It does NOT subsume the other fingerprints and is added ALONGSIDE them, never in place of
    them: the scorer grades ``outcome_checks`` and ``current_opaque_values``, which the prompt
    never mentions."""
    memories = arm_memories(task, ours_payload)
    cells = [
        (arm, channel.value, build_agent_prompt(task.goal_step, memories[arm], channel))
        for channel in CHANNELS
        for arm in ARMS
    ]
    return _digest(cells)


class RunIdentity(BaseModel):
    """What a persisted cell was measured under: the run's knobs plus every measured input.

    ``strict`` + ``extra="forbid"`` + whole-object equality is the point. The predecessor compared
    a file's identity as a SUBSET (``any(loaded.get(k) != v for k, v in identity.items())``), so a
    record carrying a field the reader did not know about — a NEWER writer's file, read by an older
    binary — matched on the fields they happened to share and was served as ``reused``. Forbidding
    the unknown field turns that into a miss, which is the fail-safe direction: a miss re-measures,
    an accept publishes someone else's number.

    ``ours_retrieval_empty`` is a first-class identity field, live-computed like the fingerprints,
    never carried forward from the file. It says whether the ``ours`` cell was measured at all or
    relabeled from ``none``, which makes it the denominator the headline is read through. A value
    outside the identity is not defended by the checks that surround it.

    ``repeats`` is bounded at 1: ``--repeats 0`` evaluates nothing and persists 0/0 rows that
    ``task_verdict`` reads as a confident "KILL: no separation" for a task that was never run.
    ``main`` rejects it at the flag too; this is the structural backstop, and both are deliberate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    repeats: int = Field(ge=1)
    dry_run: bool
    model: str
    arms: list[str]
    protocol: int
    task_fingerprint: str
    ours_payload_fingerprint: str
    prompt_fingerprint: str
    ours_retrieval_empty: bool


class CellOutcome(BaseModel):
    """One persisted ``(arm, channel)`` row — the schema ``ArmOutcome`` cannot enforce.

    ``ArmOutcome`` is a plain frozen dataclass that type-checks nothing, so a hand-edited
    ``{"passes": "0"}`` constructs happily and only detonates LATER inside ``task_verdict``
    (``passes > 0`` -> TypeError), an unhandled exception escaping mid-resume and killing a paid
    sweep. Strict mode rejects the string, the float, and the bool (an ``int`` subclass that would
    otherwise sail straight through an ``isinstance`` check) structurally, at the parse boundary,
    where a bad row is still just a cache miss.

    ``runs`` is bounded at 1 for the reason ``RunIdentity.repeats`` is; ``passes`` is bounded above
    by ``runs`` because a row claiming more passes than runs is a fabricated ceiling."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    arm: str
    channel: str
    passes: int = Field(ge=0)
    runs: int = Field(ge=1)

    @model_validator(mode="after")
    def _passes_cannot_exceed_runs(self) -> CellOutcome:
        if self.passes > self.runs:
            raise ValueError(f"{self.arm}/{self.channel}: passes {self.passes} > runs {self.runs}")
        return self


class CachedResult(BaseModel):
    """One task's persisted result — the ONLY shape ``<work_id>.json`` may take, on write and on
    read alike, so the writer cannot emit a record the reader would have to reject.

    The identity is NESTED, not spread across the top level: nesting is what makes acceptance a
    whole-object ``==`` against this run's identity instead of a field-by-field walk that can only
    check the fields the reader thought to enumerate.

    The three CROSS-ROW invariants below are schema, not caller discipline — a record that
    violates one cannot be constructed, let alone loaded."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    work_id: str
    identity: RunIdentity
    outcomes: list[CellOutcome]
    verdict: str

    @model_validator(mode="after")
    def _rows_are_a_complete_grid_measured_at_this_identity(self) -> CachedResult:
        for cell in self.outcomes:
            if cell.runs != self.identity.repeats:
                raise ValueError(
                    f"{cell.arm}/{cell.channel}: measured at {cell.runs} run(s), identity says "
                    f"{self.identity.repeats}"
                )
        # The cells must cover the grid EXACTLY — one row per (arm, channel), no dupes, no strays.
        # BOTH halves are load-bearing and each alone has already shipped a bug: six copies of one
        # cell has the right ARITY but wrong coverage (empty verdict, task vanishes from the
        # accounting, still `reused`); the six correct rows PLUS a duplicate cover the grid as a
        # SET but `task_verdict` keys by (arm, channel), so the last duplicate overwrites the real
        # measurement — a genuine SEPARATES rewritten into a fabricated KILL.
        cells = [(cell.arm, cell.channel) for cell in self.outcomes]
        expected = expected_cells()
        if len(cells) != len(expected) or set(cells) != expected:
            raise ValueError(f"cells {sorted(cells)} are not the grid {sorted(expected)}")
        # The flag must AGREE with the rows filed next to it. `ours_retrieval_empty` asserts the
        # `ours` cell was never run — it was relabeled from `none` (evaluate_task) and so is
        # none-equal by construction. A record claiming the flag while carrying an `ours` row that
        # DIFFERS from its channel's `none` row is self-contradictory: one of the two is fabricated.
        if self.identity.ours_retrieval_empty:
            by_cell = {(cell.arm, cell.channel): cell for cell in self.outcomes}
            for channel in (c.value for c in CHANNELS):
                ours, none = by_cell[(RETRIEVING_ARM, channel)], by_cell[("none", channel)]
                if (ours.passes, ours.runs) != (none.passes, none.runs):
                    raise ValueError(
                        f"[{channel}] ours_retrieval_empty claims `ours` was never run, but its "
                        f"row {ours.passes}/{ours.runs} differs from `none` {none.passes}/"
                        f"{none.runs}"
                    )
        return self

    @model_validator(mode="after")
    def _verdict_is_the_one_its_own_rows_imply(self) -> CachedResult:
        """The verdict is DERIVED, so a persisted one may only be the one its rows produce.

        Without this the field is the single unchecked value in the record: a hand-edited
        ``"verdict": "SEPARATES: ..."`` over KILL rows passes every other check (identity intact,
        rows intact) and `leaked` / `separates_all_channels` — the summary a human reads — are
        built from `verdict` strings. It was harmless only by accident, because the caller happened
        to recompute the verdict from the loaded rows and overwrite it. That is a redundancy
        standing in for a missing invariant, which is the shape this module exists to refuse: a
        value outside the checks is not defended by the checks around it."""
        implied = task_verdict(self.arm_outcomes())
        if self.verdict != implied:
            raise ValueError(f"verdict {self.verdict!r} is not the one its rows imply: {implied!r}")
        return self

    @classmethod
    def of(
        cls, work_id: str, identity: RunIdentity, outcomes: Sequence[ArmOutcome]
    ) -> CachedResult:
        """Build the record a run publishes, verdict included — through the same validators a
        loaded file passes, so an invalid record cannot reach disk in the first place."""
        return cls(
            work_id=work_id,
            identity=identity,
            outcomes=[CellOutcome(**asdict(o)) for o in outcomes],
            verdict=task_verdict(outcomes),
        )

    def arm_outcomes(self) -> list[ArmOutcome]:
        """The rows as the in-memory measurement type the rest of the grid speaks."""
        return [ArmOutcome(**cell.model_dump()) for cell in self.outcomes]


def _load_cached(result_path: Path, identity: RunIdentity) -> CachedResult | None:
    """The persisted RECORD of a task whose identity matches this run, or ``None`` meaning MISS.

    Returned whole, and reused as-is: every field it carries — rows, flag, and verdict alike — is
    checked by ``CachedResult``, so there is nothing left for a caller to recompute and no reason
    to rewrite the file it came from.

    Every rejection is a miss, never a crash and never a partial acceptance. These files are
    written by a sweep that can be killed mid-run and re-read by a PAID resume, so the two failure
    modes to design against are an exception escaping and killing the whole sweep on one bad file,
    and a degenerate or FOREIGN file being scored as a complete task."""
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, RecursionError):
        # A PARSE BOUNDARY over an untrusted file: catch by blast radius, not by enumerating what
        # json.loads is known to raise. ValueError covers JSONDecodeError, UnicodeDecodeError and
        # the >4300-digit int-literal limit; RecursionError covers deeply nested JSON; OSError
        # covers unreadable/directory/permission.
        return None
    try:
        loaded = CachedResult.model_validate(raw)
    except ValidationError:
        return None  # wrong shape, drifted type, degenerate row, or an incomplete/forged grid
    if loaded.identity != identity:
        return None  # another run's measurement (dry-run vs paid, model, repeats, arms, world...)
    return loaded


def _assert_usable_work_ids(tasks: Sequence[ToolReqRealAgentTask]) -> None:
    """A work_id must be unique and a safe, unclaimed filename — it keys both the identity map and
    the ``<work_id>.json`` result path.

    A DUPLICATE silently aliases two different tasks onto one cache file: the second overwrites
    the first and, on resume, that single record is served for both. An UNSAFE id is corpus data
    used to build a filesystem path before being checked as one — it either claims the summary's
    name (``main`` writes the summary into the same directory AFTERWARDS, overwriting that task's
    result, which then misses forever) or carries a separator/traversal that writes outside
    ``--out``. Corpus ids are sequence-derived and a regenerated or hand-assembled corpus can
    produce either, so refuse rather than measure."""
    duplicates = sorted(id_ for id_, n in Counter(t.work_id for t in tasks).items() if n > 1)
    if duplicates:
        raise ValueError(
            f"duplicate work_id(s) in the corpus: {duplicates} — each task must map to exactly "
            "one <work_id>.json, or a resumed run serves one task's measurement for another"
        )
    unsafe = sorted(
        t.work_id
        for t in tasks
        if f"{t.work_id}.json" == SUMMARY_NAME or Path(t.work_id).name != t.work_id or not t.work_id
    )
    if unsafe:
        raise ValueError(
            f"unsafe work_id(s) for a result filename: {unsafe} — a work_id must be a plain "
            f"filename component and must not claim the summary's name ({SUMMARY_NAME})"
        )


def _task_identities(
    tasks: Sequence[ToolReqRealAgentTask],
    ours_payloads: Mapping[str, Mapping[str, str]],
    *,
    repeats: int,
    model: str,
    dry_run: bool,
) -> dict[str, RunIdentity]:
    """The cache identity per task: the run's knobs plus every measured input (see RunIdentity).

    ``model`` is RESOLVED here, not taken raw — through ``headless_agent.resolve_model``, the same
    rule the agent itself runs under, never a second copy of it. ``--model`` defaults to "" and
    ``HeadlessClaudeAgent`` then reads ``MEMBENCH_AGENT_MODEL``, so caching the raw "" makes the
    driver's primary independent variable invisible: run the sweep under one model, point the env
    var at another, resume, and every cached task is served as ``reused`` with the FIRST model's
    numbers relabelled as the second's.

    The three payload-derived fields are bound from ONE payload object, not three lookups of it:
    they must describe the same retrieval or they describe nothing, and a fourth such field added
    later must inherit that by construction rather than by everyone remembering the same default."""
    resolved_model = resolve_model(model)
    identities: dict[str, RunIdentity] = {}
    for task in tasks:
        payload = ours_payloads.get(task.work_id, {})
        identities[task.work_id] = RunIdentity(
            repeats=repeats,
            dry_run=dry_run,
            model=resolved_model,
            arms=list(ARMS),
            protocol=EXECUTION_PROTOCOL,
            task_fingerprint=task_fingerprint(task),
            ours_payload_fingerprint=payload_fingerprint(payload),
            prompt_fingerprint=prompt_fingerprint(task, payload),
            ours_retrieval_empty=not payload,
        )
    return identities


def run_corpus(
    tasks: Sequence[ToolReqRealAgentTask],
    sequences: Sequence[BenchmarkSequence],
    *,
    out_dir: Path,
    repeats: int,
    model: str,
    dry_run: bool,
    store_path: Path,
    mem_bin: str,
    seed_fn: SeedFn,
    resume: bool = True,
) -> dict[str, Any]:
    """Evaluate every task, persisting one ``<work_id>.json`` each, and reuse a persisted result
    only when its identity (see ``RunIdentity``) matches this run's — so a FREE dry-run's simulated
    result can never satisfy a PAID run over the same ``--out``, and a corrupt or partial file is a
    miss rather than a crash.

    ``seed_fn`` runs BEFORE the cache is consulted and over the WHOLE corpus, every invocation,
    even when every task is cache-served: it is free, and the payload it resolves rides in the
    identity, so it has to be recomputed to know whether a cached cell is still current."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _assert_usable_work_ids(tasks)
    ours_payloads = seed_fn(sequences, tasks, store_path, mem_bin)
    identity_of = _task_identities(
        tasks, ours_payloads, repeats=repeats, model=model, dry_run=dry_run
    )

    results: list[CachedResult] = []
    executed = 0
    reused = 0
    for task in tasks:
        result_path = out_dir / f"{task.work_id}.json"
        identity = identity_of[task.work_id]
        cached = _load_cached(result_path, identity) if resume and result_path.is_file() else None
        if cached is not None:
            # Reused whole. Nothing is recomputed and the file is NOT rewritten: it already passed
            # every validator, so a rewrite could only reproduce it — and a fully cache-served
            # resume then does zero writes, leaving the results' mtimes an honest record of which
            # tasks this run actually measured.
            results.append(cached)
            reused += 1
            continue
        outcomes = evaluate_task(
            task,
            repeats=repeats,
            model=model,
            dry_run=dry_run,
            ours_payload=ours_payloads.get(task.work_id, {}),
        )
        executed += 1
        result = CachedResult.of(task.work_id, identity, outcomes)
        # Atomic publish: write a sibling temp file then rename, so a kill mid-write leaves either
        # the old result or the new one, never a half-written JSON the next resume trips on.
        tmp_path = result_path.with_suffix(".json.tmp")
        tmp_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(result_path)
        results.append(result)
    return {
        "n_tasks": len(tasks),
        "executed": executed,
        "reused": reused,
        "dry_run": dry_run,
        "repeats": repeats,
        "per_task": [r.model_dump(mode="json") for r in results],
        "separates_all_channels": sum(
            1 for r in results if r.verdict.count("SEPARATES") == len(CHANNELS)
        ),
        "leaked": [r.work_id for r in results if "LEAK" in r.verdict],
        # Attribution, not trivia: for these tasks `ours` was never actually run (empty retrieval,
        # scored none-equivalent), so a flat ours-vs-none result over them means "retrieval
        # surfaced nothing", NOT "memory did not help".
        "ours_empty_retrieval": [r.work_id for r in results if r.identity.ours_retrieval_empty],
    }
