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

What this grid adds beyond the shared core: a cell that carries its DIAGNOSTICS, not just its score
(``BuiltinCell``). ``engaged`` and ``leaked`` are what make a builtin ``passes`` interpretable at
all: a pass WITHOUT engagement is a leak (the sandbox let a Write scavenge a stale file), not a
builtin win, and zero engagement is the mechanism never firing. Their cross-field bounds are schema,
not caller discipline.
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
    Evaluation,
    corpus_summary,
    invocation_digest,
    render_verdict,
    run_cached_corpus,
)
from membench.runner.toolreq_builtin import ARM, BuiltinDiagnostics, cell_calls, run_builtin_arm
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, task_fingerprint

# The run summary, written into the SAME directory as the per-task `<work_id>.json` results —
# hence a name the tasks are not allowed to claim (resume_cache.assert_usable_work_ids).
SUMMARY_NAME = "summary-toolreq-builtin.json"

# The executing/scoring CODE this grid's cached cells were measured under
# (BaseRunIdentity.protocol) — what MOVES A RESULT while every fingerprint stays identical:
# `run_builtin_arm`'s cwd firewall (`_wipe_cwd_contents`) and config-dir seed (`_seed_config_dir`),
# `_memory_engaged` + NATIVE_MEMORY_GLOB, the stream-json parser, `score_goal_action`,
# DEFAULT_TIMEOUT_S, and `simulated_builtin_runner` (which decides the ENTIRE dry-run measurement —
# free runs only, since `dry_run` is itself in the identity).
# NOT here, because `invocation_fingerprint` now carries them: the prompts, --allowedTools, --model,
# --strict-mcp-config, and the legs' count and order.
# BUMP on any change to the former that could move a result.
EXECUTION_PROTOCOL = 2

CALLS_PER_REPEAT = 2  # establish + goal — double none/oracle's 1-call cost


class BuiltinCell(BaseCellOutcome):
    """One persisted ``(builtin, channel)`` row: the score AND the engagement diagnostics that make
    the score interpretable.

    ``leaked`` is defined by the arm as the number of repeats that PASSED while NOT engaging
    (``run_builtin_arm``), and the four cross-field bounds below fall straight out of that
    definition. A row is a claim about what happened across ``runs`` repeats; these are the claims
    that cannot all be true at once:

    * ``engaged <= runs`` — the fact cannot have reached native memory in more repeats than ran.
    * ``leaked <= passes`` — a leak IS a pass, so it cannot outnumber the passes it is drawn from.
    * ``leaked <= runs - engaged`` — the same, from the other side: a leaked repeat is by definition
      one that did NOT engage, so the leaks cannot outnumber the non-engaged repeats.
    * ``leaked >= passes - engaged`` — the LOWER bound, and the one whose absence is a hole rather
      than a slack: by inclusion-exclusion, at least ``passes - engaged`` of the passing repeats
      cannot have been engaged ones. Without it a row can claim ``passes=2, engaged=0, leaked=0``,
      which is arithmetically impossible — both passes were leaks — and ``cell_kind`` then reports
      NOT-ENGAGED for a cell that actually LEAKED. That is a one-field edit to a ``<work_id>.json``
      that erases the most severe verdict the arm can produce (a pass the sandbox handed over, not
      a builtin win) and quietly moves the task from the summary's ``leaked`` list to its
      ``not_engaged`` list. The upper bounds do not catch it: they only ever say leaked is too BIG.

    Together they make the pass accounting checkable rather than merely reported. They do NOT make
    ``leaked`` derivable — with ``passes=1, engaged=1, runs=2`` the passing repeat may or may not be
    the engaged one — so the field carries real information and is bounded, not computed.
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
        if self.leaked < self.passes - self.engaged:
            raise ValueError(
                f"{self.channel}: leaked {self.leaked} < passes {self.passes} - engaged "
                f"{self.engaged} — at least {self.passes - self.engaged} of the passing repeat(s) "
                "cannot have engaged native memory, and a pass without engagement IS a leak"
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
) -> Evaluation:
    """Run every channel cell for one task, and hand back the invocations they made alongside the
    rows they scored — the cache checks the second against this run's identity before it will
    publish the first (``resume_cache.run_cached_corpus``)."""
    runs = [
        run_builtin_arm(task, repeats=repeats, model=model, dry_run=dry_run, channel=channel)
        for channel in CHANNELS
    ]
    return Evaluation(
        outcomes=[_cell(outcome, diagnostics) for outcome, diagnostics, _calls in runs],
        calls=[calls for _outcome, _diagnostics, calls in runs],
    )


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
    """The human-readable verdict line, rendered from ``cell_kind``."""
    return render_verdict([(cell.channel, *cell_kind(cell)) for cell in cells])


def invocation_fingerprint(task: ToolReqRealAgentTask, *, model: str) -> str:
    """Hashes the COMMAND LINES THEMSELVES — every ``claude -p`` argv every cell will spawn.

    Rendered from ``toolreq_builtin.cell_legs`` through ``toolreq_builtin.cell_agent``: the same
    legs ``run_builtin_arm`` executes, through the same agent it executes them with. So this cannot
    fingerprint an invocation the arm does not make — it is not a copy of the arm's behaviour kept
    beside it, it is the arm's own plan. The write boundary then checks the RECORDED invocations
    against it (``resume_cache.run_cached_corpus``), which is what catches a leg the plan never
    declared."""
    return invocation_digest(cell_calls(task, channel, model=model) for channel in CHANNELS)


class BuiltinCachedResult(BaseCachedResult[BaseRunIdentity, BuiltinCell]):
    """One task's persisted builtin result. Every invariant is the shared core's; this class only
    tells it what the grid is and how its rows classify.

    The identity is ``BaseRunIdentity`` unextended: unlike the ``ours`` arm, ``builtin`` retrieves
    nothing from an external store — its memory is the agent's own, established in-band by the first
    leg — so there is no cross-task payload to hash. Everything that varies what it executes is in
    the task and in the command lines, and both are already fingerprinted."""

    @classmethod
    def expected_cells(cls) -> set[tuple[str, str]]:
        # One arm here, so the grid is exactly the channels.
        return {(ARM, channel.value) for channel in CHANNELS}

    @classmethod
    def classify(cls, outcomes: Sequence[BuiltinCell]) -> list[tuple[str, str, str]]:
        return [(cell.channel, *cell_kind(cell)) for cell in outcomes]


def _identity(
    task: ToolReqRealAgentTask, *, repeats: int, resolved_model: str, dry_run: bool
) -> BaseRunIdentity:
    """The cache identity for one task (see ``BaseRunIdentity``)."""
    return BaseRunIdentity(
        repeats=repeats,
        dry_run=dry_run,
        model=resolved_model,
        protocol=EXECUTION_PROTOCOL,
        task_fingerprint=task_fingerprint(task),
        invocation_fingerprint=invocation_fingerprint(task, model=resolved_model),
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
        evaluate=lambda task: evaluate_task(
            task, repeats=repeats, model=resolved_model, dry_run=dry_run
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
        # channels" off a measurement that covered neither. The schema already refuses a short grid;
        # this is the second lock on the same headline, stated positively.
        "separates_all_channels": sum(
            1 for r in results if kinds[r.work_id].count(SEPARATES) == len(CHANNELS)
        ),
        "leaked": [r.work_id for r in results if LEAK in kinds[r.work_id]],
        "not_engaged": [r.work_id for r in results if NOT_ENGAGED in kinds[r.work_id]],
    }
