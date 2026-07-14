"""mem-rk41.3 / .3.1 — the tool-requiring real-agent grid: the none/oracle/ours arms, their
verdict rule, and the measured inputs only this experiment has.

The pure half of ``scripts/grid_toolreq_realagent.py``, and it lives HERE rather than there for one
reason: ``scripts/`` is not type-checked. CI runs ``mypy --strict membench`` only, and every
resume-cache defect this code has shipped lived in an untyped script. The driver keeps its
argparse/main, its ``ours``-store seeder (which reaches into a sibling script) and its printing;
everything that decides what is EXECUTED, what is SCORED, and what may be REUSED is inside the type
checker.

The RESUME CACHE is not here — it is ``membench.runner.resume_cache``, shared with the builtin grid
(``toolreq_builtin_grid``). Read the cache invariant there; this module supplies only what is
specific to three arms: the arms themselves, the payload the ``ours`` arm retrieves, the prompts
those arms send, and the verdict their rows imply. What this module adds to the identity is the
``ours`` retrieval — the one measured input the shared core cannot know about.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Self

from pydantic import model_validator

from membench.runner.headless_agent import CHANNELS, build_agent_prompt, resolve_model
from membench.runner.realagent_probe import ArmOutcome, run_arm
from membench.runner.resume_cache import (
    BaseCachedResult,
    BaseCellOutcome,
    BaseRunIdentity,
    Cell,
    corpus_summary,
    digest,
    render_verdict,
    run_cached_corpus,
)
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, task_fingerprint
from membench.schemas.sequence import BenchmarkSequence

__all__ = [
    "ARMS",
    "CHANNELS",
    "EXECUTION_PROTOCOL",
    "KILL",
    "LEAK",
    "SEPARATES",
    "SUMMARY_NAME",
    "WEAK",
    "CachedResult",
    "CellOutcome",
    "RunIdentity",
    "SeedFn",
    "arm_memories",
    "classify_channels",
    "evaluate_task",
    "expected_cells",
    "payload_fingerprint",
    "prompt_fingerprint",
    "run_corpus",
    "task_verdict",
]

ARMS = ("none", "oracle", "ours")

# The run summary, written into the SAME directory as the per-task `<work_id>.json` results —
# hence a name the tasks are not allowed to claim (resume_cache.assert_usable_work_ids).
SUMMARY_NAME = "summary-toolreq-realagent.json"

# The executing/scoring CODE this grid's cached cells were measured under
# (BaseRunIdentity.protocol): `run_arm`, `build_agent_prompt`, the stream-json parser,
# `score_goal_action`, DEFAULT_TIMEOUT_S.
# BUMP on any change to those that could move a result.
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
# an informational suffix (see classify_channels).
_GATING_ARMS = ("none", "oracle")

# The verdict kinds. `classify_channels` returns one of these per channel and the summary COUNTS
# them; nothing derives a headline by matching substrings of the rendered line.
LEAK = "LEAK"
SEPARATES = "SEPARATES"
KILL = "KILL"
WEAK = "WEAK"


def classify_channels(outcomes: Sequence[Cell]) -> list[tuple[str, str, str]]:
    """The probe's per-channel verdict rule (valid again under opaque values), as one ladder:
    ``none`` passing means a leak; ``none`` 0 + ``oracle`` ceiling means the arms separate. Arms
    beyond ``_GATING_ARMS`` (today: ``ours``) never gate the call — each rides along as an
    informational suffix, because ``ours`` scoring near ``none`` is the expected substrate finding,
    not a failure of the grid.

    Kind and line leave the same branch (see ``BaseCachedResult.classify``), so the suffix loop
    below can grow an arm whose NAME contains "LEAK" without moving a single headline count."""
    by = {(o.arm, o.channel): o for o in outcomes}
    classified: list[tuple[str, str, str]] = []
    for channel in (c.value for c in CHANNELS):
        none_o = by.get(("none", channel))
        oracle_o = by.get(("oracle", channel))
        if none_o is None or oracle_o is None:
            continue
        runs = oracle_o.runs
        if none_o.passes > 0:
            kind = LEAK
            call = f"LEAK: none {none_o.passes}/{none_o.runs} — value reached the prompt"
        elif oracle_o.passes == runs and runs > 0:
            kind = SEPARATES
            call = f"SEPARATES: none 0/{runs}, oracle {oracle_o.passes}/{runs}"
        elif oracle_o.passes == 0:
            kind = KILL
            call = f"KILL: oracle ceiling 0/{runs} — no separation"
        else:
            kind = WEAK
            call = f"WEAK: none 0/{runs}, oracle {oracle_o.passes}/{runs} — add repeats"
        for arm in ARMS:
            if arm in _GATING_ARMS:
                continue
            extra = by.get((arm, channel))
            if extra is not None:
                call += f" ({arm} {extra.passes}/{extra.runs})"
        classified.append((channel, kind, call))
    return classified


def task_verdict(outcomes: Sequence[Cell]) -> str:
    """The human-readable verdict line, rendered from ``classify_channels``."""
    return render_verdict(classify_channels(outcomes))


def expected_cells() -> set[tuple[str, str]]:
    """The full (arm, channel) grid one task must cover to be scored as complete."""
    return {(arm, channel.value) for arm in ARMS for channel in CHANNELS}


def payload_fingerprint(ours_payload: Mapping[str, str]) -> str:
    """Identifies the ``ours`` arm's actual MEASURED INPUT — the text retrieval surfaced.

    ``task_fingerprint`` is task-LOCAL; this payload is not. It comes from CROSS-TASK retrieval
    over the whole seeded store, so it moves when a sibling sequence is added, when the corpus is
    regenerated, or when ``bin/mem``'s retrieval changes — none of which touch the queried task's
    own fields. Hashing the payload puts all three in the identity without modelling any of them.

    ORDER-PRESERVING, for the same reason ``oracle_memory`` is: ``resolve_payloads`` inserts in
    ``mem retrieve``'s RANK order and the prompt renders that order, so the same SET of lessons
    ranked differently is a different prompt and must miss."""
    return digest(list(ours_payload.items()))


def prompt_fingerprint(task: ToolReqRealAgentTask, ours_payload: Mapping[str, str]) -> str:
    """Hashes the PROMPTS THEMSELVES — the exact text every (arm, channel) cell will send to
    ``claude -p``. See ``BaseRunIdentity.prompt_fingerprint``: it cannot be incomplete about the
    prompt, because it IS the prompt. Building one is string concatenation: FREE, no agent turn."""
    memories = arm_memories(task, ours_payload)
    cells = [
        (arm, channel.value, build_agent_prompt(task.goal_step, memories[arm], channel))
        for channel in CHANNELS
        for arm in ARMS
    ]
    return digest(cells)


class RunIdentity(BaseRunIdentity):
    """What a persisted cell was measured under. The shared knobs and fingerprints are
    ``BaseRunIdentity``'s; these three fields are the measured inputs only THIS grid has.

    ``ours_payload_fingerprint`` is the retrieval the ``ours`` arm actually surfaced — cross-task,
    so it moves when nothing about the queried task did (see ``payload_fingerprint``).

    ``ours_retrieval_empty`` is a first-class identity field, live-computed like the fingerprints,
    never carried forward from the file. It says whether the ``ours`` cell was measured at all or
    relabeled from ``none``, which makes it the denominator the headline is read through. A value
    outside the identity is not defended by the checks that surround it.

    ``arms`` pins the roster the grid was measured over."""

    arms: list[str]
    ours_payload_fingerprint: str
    ours_retrieval_empty: bool


# This grid's cells carry no field beyond the shared four — `ArmOutcome` is exactly
# (arm, channel, passes, runs) — so the base row IS the row, strict typing and `passes <= runs`
# bound included. Named for the readers that persist and assert against it.
CellOutcome = BaseCellOutcome


class CachedResult(BaseCachedResult[RunIdentity, CellOutcome]):
    """One task's persisted result. The complete-grid and verdict-is-derived invariants are the
    shared core's; the one below is this grid's, and it exists because ``ours_retrieval_empty`` is
    an identity field whose truth is also visible in the ROWS."""

    @classmethod
    def expected_cells(cls) -> set[tuple[str, str]]:
        return expected_cells()

    @classmethod
    def classify(cls, outcomes: Sequence[CellOutcome]) -> list[tuple[str, str, str]]:
        return classify_channels(outcomes)

    @model_validator(mode="after")
    def _empty_retrieval_flag_agrees_with_the_rows_filed_next_to_it(self) -> Self:
        """``ours_retrieval_empty`` asserts the ``ours`` cell was never run — it was relabeled from
        ``none`` (evaluate_task) and so is none-equal by construction. A record claiming the flag
        while carrying an ``ours`` row that DIFFERS from its channel's ``none`` row is
        self-contradictory: one of the two is fabricated.

        It stays TOTAL over its input — ``.get``, not ``[]`` — rather than depending on the
        completeness check having run first: a ``KeyError`` is not a ``ValidationError``, so on a
        truncated file it would sail past ``load_cached``'s handler and kill a PAID resume."""
        if not self.identity.ours_retrieval_empty:
            return self
        by_cell = {(cell.arm, cell.channel): cell for cell in self.outcomes}
        for channel in (c.value for c in CHANNELS):
            ours, none = by_cell.get((RETRIEVING_ARM, channel)), by_cell.get(("none", channel))
            if ours is None or none is None:
                continue
            if (ours.passes, ours.runs) != (none.passes, none.runs):
                raise ValueError(
                    f"[{channel}] ours_retrieval_empty claims `ours` was never run, but its "
                    f"row {ours.passes}/{ours.runs} differs from `none` {none.passes}/{none.runs}"
                )
        return self


def _cells(outcomes: Sequence[ArmOutcome]) -> list[CellOutcome]:
    """The measured rows as persisted ones — the parse boundary crossed in the WRITE direction, so
    a row this grid could not have loaded is a row it cannot publish either."""
    return [CellOutcome(**asdict(outcome)) for outcome in outcomes]


def _identity(
    task: ToolReqRealAgentTask,
    ours_payload: Mapping[str, str],
    *,
    repeats: int,
    resolved_model: str,
    dry_run: bool,
) -> RunIdentity:
    """The cache identity for one task (see ``RunIdentity`` / ``BaseRunIdentity``).

    The three payload-derived fields are bound from ONE payload object, not three lookups of it:
    they must describe the same retrieval or they describe nothing, and a fourth such field added
    later must inherit that by construction rather than by everyone remembering the same default."""
    return RunIdentity(
        repeats=repeats,
        dry_run=dry_run,
        model=resolved_model,
        arms=list(ARMS),
        protocol=EXECUTION_PROTOCOL,
        task_fingerprint=task_fingerprint(task),
        prompt_fingerprint=prompt_fingerprint(task, ours_payload),
        ours_payload_fingerprint=payload_fingerprint(ours_payload),
        ours_retrieval_empty=not ours_payload,
    )


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
    """Evaluate every task through the shared resume cache and shape this grid's summary.

    ``seed_fn`` runs BEFORE the cache is consulted and over the WHOLE corpus, every invocation, even
    when every task is cache-served: it is free, and the payload it resolves rides in the identity,
    so it has to be recomputed to know whether a cached cell is still current.

    ``model`` is RESOLVED once here, never taken raw — through ``headless_agent.resolve_model``, the
    same rule the agent itself runs under. ``RunIdentity`` refuses an unresolved one outright
    (``BaseRunIdentity``), so this is the ONE place the rule is applied and the schema is the
    backstop, not a second copy of it."""
    ours_payloads = seed_fn(sequences, tasks, store_path, mem_bin)
    resolved_model = resolve_model(model)

    run = run_cached_corpus(
        tasks,
        out_dir=out_dir,
        result_cls=CachedResult,
        identity_of=lambda task: _identity(
            task,
            ours_payloads.get(task.work_id, {}),
            repeats=repeats,
            resolved_model=resolved_model,
            dry_run=dry_run,
        ),
        evaluate=lambda task: _cells(
            evaluate_task(
                task,
                repeats=repeats,
                model=model,
                dry_run=dry_run,
                ours_payload=ours_payloads.get(task.work_id, {}),
            )
        ),
        summary_name=SUMMARY_NAME,
        resume=resume,
    )
    results = run.results
    kinds = {r.work_id: r.kinds for r in results}
    return {
        **corpus_summary(tasks, run, dry_run=dry_run, repeats=repeats),
        # Counted against len(CHANNELS), never `all(...)` over whatever cells we happen to hold:
        # `all([])` is vacuously True, so an empty or short grid would credit "separates on BOTH
        # channels" off a measurement that covered neither.
        "separates_all_channels": sum(
            1 for r in results if kinds[r.work_id].count(SEPARATES) == len(CHANNELS)
        ),
        "leaked": [r.work_id for r in results if LEAK in kinds[r.work_id]],
        # Attribution, not trivia: for these tasks `ours` was never actually run (empty retrieval,
        # scored none-equivalent), so a flat ours-vs-none result over them means "retrieval
        # surfaced nothing", NOT "memory did not help".
        "ours_empty_retrieval": [r.work_id for r in results if r.identity.ours_retrieval_empty],
    }
