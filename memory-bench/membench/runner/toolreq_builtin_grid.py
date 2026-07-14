"""mem-rk41.3.2 / mem-mpxie — the tool-requiring BUILTIN grid: the native-memory arm's cells, its
verdict rule, and the measured inputs only this experiment has.

The sibling of ``toolreq_grid`` (none/oracle/ours), over the same frozen corpus and through the same
resume cache (``membench.runner.resume_cache`` — read the cache invariant there). It runs ONE arm,
``builtin``, under both memory-trust channels: two real ``claude -p`` calls per repeat (establish,
then a bare goal call) sharing one sandbox cwd + one ``CLAUDE_CONFIG_DIR``, so Claude Code's own
native memory is the sole continuity channel (``membench.runner.toolreq_builtin``).

It lives HERE, not in ``scripts/``, for the reason the 3-arm core does: ``scripts/`` is not
type-checked (CI runs ``mypy --strict membench``), and every resume-cache defect this codebase has
shipped lived in an untyped script. The driver keeps argparse, the refuse-to-spend gate, the paid
preflight and its printing; everything that decides what is EXECUTED, what is SCORED, and what may
be REUSED is in this module, inside the type checker and on top of the shared cache.

What this grid adds beyond the shared core:

* **A cell carries its DIAGNOSTICS, not just its score** (``BuiltinCell``). ``engaged`` and
  ``leaked`` are what make a builtin ``passes`` interpretable at all: a pass WITHOUT engagement is a
  leak (the sandbox let a Write scavenge a stale file), not a builtin win, and zero engagement is
  the mechanism never firing. Their cross-field bounds are schema, not caller discipline.
* **A prompt fingerprint over BOTH legs.** The arm sends two different prompts per cell; hashing
  only the goal leg would call two runs identical while the establish leg — the one that has to
  persist the fact — differed.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Self

from pydantic import Field, model_validator

from membench.runner.headless_agent import CHANNELS, resolve_model
from membench.runner.realagent_probe import ArmOutcome
from membench.runner.resume_cache import (
    BaseCachedResult,
    BaseCellOutcome,
    BaseRunIdentity,
    digest,
    run_cached_corpus,
)
from membench.runner.toolreq_builtin import ARM, BuiltinDiagnostics, cell_prompts, run_builtin_arm
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, task_fingerprint

# The run summary, written into the SAME directory as the per-task `<work_id>.json` results —
# hence a name the tasks are not allowed to claim (resume_cache.assert_usable_work_ids).
SUMMARY_NAME = "summary-toolreq-builtin.json"

# The executing/scoring CODE this grid's cached cells were measured under
# (BaseRunIdentity.protocol): `run_builtin_arm` (the establish/goal pair, the cwd firewall, the
# config-dir seed), `_memory_engaged`, `build_agent_prompt`, the stream-json parser,
# `score_goal_action`, DEFAULT_TIMEOUT_S.
# BUMP on any change to those that could move a result.
EXECUTION_PROTOCOL = 1

CALLS_PER_REPEAT = 2  # establish + goal — double none/oracle's 1-call cost


class BuiltinCell(BaseCellOutcome):
    """One persisted ``(builtin, channel)`` row: the score AND the engagement diagnostics that make
    the score interpretable.

    The three cross-field bounds below are STRUCTURAL. A row is a claim about what happened across
    ``runs`` repeats, and these are the claims that cannot all be true at once:

    * ``engaged <= runs`` — the fact cannot have reached native memory in more repeats than ran.
    * ``leaked <= passes`` — a leak IS a pass (one that happened without engagement), so it cannot
      outnumber the passes it is drawn from.
    * ``leaked <= runs - engaged`` — for the same reason, from the other side: a leaked repeat is by
      definition one that did NOT engage, so the leaks cannot outnumber the non-engaged repeats.

    Together they make the pass accounting checkable rather than merely reported: a record claiming
    a clean builtin win it never measured is unconstructible, and one edited to claim it is a MISS.
    """

    engaged: int = Field(ge=0)
    leaked: int = Field(ge=0)
    establish_tool_calls: int = Field(ge=0)
    establish_tool_names: list[str]

    @model_validator(mode="after")
    def _engagement_accounting_is_possible(self) -> Self:
        if self.engaged > self.runs:
            raise ValueError(f"{self.channel}: engaged {self.engaged} > runs {self.runs}")
        if self.leaked > self.passes:
            raise ValueError(
                f"{self.channel}: leaked {self.leaked} > passes {self.passes} — a leak is a PASS "
                "without engagement, so it cannot outnumber the passes"
            )
        if self.leaked > self.runs - self.engaged:
            raise ValueError(
                f"{self.channel}: leaked {self.leaked} > non-engaged repeats "
                f"{self.runs - self.engaged} — a leak is a pass WITHOUT engagement"
            )
        return self


def _cell(outcome: ArmOutcome, diagnostics: BuiltinDiagnostics) -> BuiltinCell:
    """One measured cell as a persisted row — the parse boundary crossed in the WRITE direction, so
    a row this grid could not have loaded is a row it cannot publish either.

    The arm reports ``runs`` on BOTH halves it returns; they are the same measurement, so a
    disagreement means one of the two is fabricated and the record must not be written."""
    if outcome.runs != diagnostics.runs:
        raise ValueError(
            f"{outcome.channel}: outcome measured {outcome.runs} run(s) but its diagnostics claim "
            f"{diagnostics.runs}"
        )
    return BuiltinCell(
        arm=outcome.arm,
        channel=outcome.channel,
        passes=outcome.passes,
        runs=outcome.runs,
        engaged=diagnostics.engaged,
        leaked=diagnostics.leaked,
        establish_tool_calls=diagnostics.establish_tool_calls,
        establish_tool_names=list(diagnostics.establish_tool_names),
    )


def evaluate_task(
    task: ToolReqRealAgentTask, *, repeats: int, model: str, dry_run: bool
) -> list[BuiltinCell]:
    """Run every channel cell for one task."""
    return [
        _cell(
            *run_builtin_arm(task, repeats=repeats, model=model, dry_run=dry_run, channel=channel)
        )
        for channel in CHANNELS
    ]


# The verdict kinds, most severe first. `cell_kind` returns one of these and the summary counts
# them; `NOT-ENGAGED` is the kind the 3-arm grid has no room for, and it is the point of the arm.
LEAK = "LEAK"
SEPARATES = "SEPARATES"
NOT_ENGAGED = "NOT-ENGAGED"
WEAK = "WEAK"


def cell_kind(cell: BuiltinCell) -> tuple[str, str]:
    """Classify one cell into its (kind, display-line). Returning both from one branch ladder is
    what keeps the verdict (display) and the summary (counts) from desyncing — they read the same
    tuple rather than two chains that must agree.

    Priority: a LEAK (pass without engagement) outranks everything else — it means the shared
    sandbox let a Write scavenge a stale file, not that builtin memory worked. Otherwise: full
    engagement + full pass separates; zero engagement means the mechanism never fired (the
    mem-hb9o precedent); partial is WEAK."""
    runs = cell.runs  # >= 1 by schema: a 0-run cell cannot be constructed or loaded
    if cell.leaked:
        return LEAK, f"LEAK: {cell.leaked}/{runs} passed WITHOUT engaging native memory"
    if cell.passes == runs and cell.engaged == runs:
        return SEPARATES, f"SEPARATES: {cell.passes}/{runs} (engaged {cell.engaged}/{runs})"
    if cell.engaged == 0:
        return NOT_ENGAGED, f"NOT-ENGAGED: the fact never reached native memory (0/{runs})"
    return WEAK, f"WEAK: {cell.passes}/{runs} passed, engaged {cell.engaged}/{runs}"


def task_verdict(cells: Sequence[BuiltinCell]) -> str:
    """Human-readable per-channel verdict line, built from ``cell_kind``."""
    return " | ".join(f"[{cell.channel}] {cell_kind(cell)[1]}" for cell in cells)


def expected_cells() -> set[tuple[str, str]]:
    """The full (arm, channel) grid one task must cover to be scored as complete — one arm here, so
    the grid is exactly the channels."""
    return {(ARM, channel.value) for channel in CHANNELS}


def prompt_fingerprint(task: ToolReqRealAgentTask) -> str:
    """Hashes the PROMPTS THEMSELVES — every prompt every cell will send to ``claude -p``.

    BOTH legs, per channel. The establish leg is the one under test (it must persist the fact) and
    the goal leg is the one scored; hashing only the second would call two runs identical while the
    first differed. The pair comes from ``toolreq_builtin.cell_prompts``, beside the ``run_step``
    calls it mirrors, so this cannot fingerprint a prompt the arm no longer sends."""
    return digest([(channel.value, *cell_prompts(task, channel)) for channel in CHANNELS])


class BuiltinRunIdentity(BaseRunIdentity):
    """What a persisted builtin cell was measured under. The knobs, the resolved model, the protocol
    and both fingerprints are ``BaseRunIdentity``'s; this grid adds no measured input of its own.

    Unlike the ``ours`` arm, ``builtin`` retrieves nothing from an external store — its memory is
    the agent's own, established in-band by the first leg — so there is no cross-task payload to
    hash.
    Everything that varies what it executes is in the task and in the prompts, and both are already
    fingerprinted."""


class BuiltinCachedResult(BaseCachedResult[BuiltinRunIdentity, BuiltinCell]):
    """One task's persisted builtin result. Every invariant is the shared core's; this class only
    tells it what the grid is and what verdict the rows imply."""

    @classmethod
    def expected_cells(cls) -> set[tuple[str, str]]:
        return expected_cells()

    @classmethod
    def implied_verdict(cls, outcomes: Sequence[BuiltinCell]) -> str:
        return task_verdict(outcomes)


def _identity(
    task: ToolReqRealAgentTask, *, repeats: int, resolved_model: str, dry_run: bool
) -> BuiltinRunIdentity:
    """The cache identity for one task (see ``BuiltinRunIdentity`` / ``BaseRunIdentity``)."""
    return BuiltinRunIdentity(
        repeats=repeats,
        dry_run=dry_run,
        model=resolved_model,
        protocol=EXECUTION_PROTOCOL,
        task_fingerprint=task_fingerprint(task),
        prompt_fingerprint=prompt_fingerprint(task),
    )


def run_corpus(
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    out_dir: Path,
    repeats: int,
    model: str,
    dry_run: bool,
    resume: bool = True,
) -> dict[str, Any]:
    """Evaluate every task through the shared resume cache and shape this grid's summary.

    ``model`` is RESOLVED once here, never taken raw — through ``headless_agent.resolve_model``, the
    same rule the agent itself runs under. ``BuiltinRunIdentity`` refuses an unresolved one outright
    (``BaseRunIdentity``), so this is the ONE place the rule is applied and the schema is the
    backstop, not a second copy of it."""
    resolved_model = resolve_model(model)
    run = run_cached_corpus(
        tasks,
        out_dir=out_dir,
        result_cls=BuiltinCachedResult,
        identity_of=lambda task: _identity(
            task, repeats=repeats, resolved_model=resolved_model, dry_run=dry_run
        ),
        evaluate=lambda task: evaluate_task(task, repeats=repeats, model=model, dry_run=dry_run),
        summary_name=SUMMARY_NAME,
        resume=resume,
    )
    results = run.results
    kinds = {r.work_id: [cell_kind(cell)[0] for cell in r.outcomes] for r in results}
    return {
        "n_tasks": len(tasks),
        "executed": run.executed,
        "reused": run.reused,
        "dry_run": dry_run,
        "repeats": repeats,
        "per_task": [r.model_dump(mode="json") for r in results],
        # Counted against len(CHANNELS), never `all(...)` over whatever cells we happen to hold:
        # `all([])` is vacuously True, so an empty or short grid would credit "separates on BOTH
        # channels" off a measurement that covered neither. The schema already refuses a short grid;
        # this is the second lock on the same headline, stated positively.
        "separates_all_channels": sum(
            1 for r in results if kinds[r.work_id].count(SEPARATES) == len(CHANNELS)
        ),
        "leaked": [r.work_id for r in results if LEAK in kinds[r.work_id]],
        "not_engaged": [r.work_id for r in results if NOT_ENGAGED in kinds[r.work_id]],
    }
