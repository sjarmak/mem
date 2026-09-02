"""mem-eg850 — E1: the guidance-strength ladder as the call-rate dial.

Five NESTED guidance rungs, ``R0`` (silent) through ``R4`` (recall + capture), are the
harness-level surrogate for residual steering: each rung's text CONTAINS its predecessor's,
so a rung difference is an ADDED clause and nothing else. That containment is the whole
design — it is asserted on the TABLE (``RUNG_TEXT``), not on rendered prose, so a rung
cannot drift into being a rewrite that happens to read stronger.

What this module decides, and what it deliberately does not:

* **The channel axis is DROPPED here.** ``build_agent_prompt`` emits no block at all when
  ``available_memory`` is empty, so on a bare E1 arm ``RECALLED`` and ``TRUSTED`` produce
  BYTE-IDENTICAL argv — sweeping them would buy two cells of one measurement and a doubled
  bill. ``MemoryChannel`` stays on the oracle-ceiling control (``realagent_probe``), which
  actually surfaces a block. E1 pins ``CHANNEL``.
* **The primary endpoint is the discrimination margin ``d(rung)``**, P(call | necessary) -
  P(call | unnecessary), NOT the raw call rate. A rung that lifts the call rate with ``d``
  flat bought nothing (the arXiv 2605.09252 bluntness result); BOTH are emitted, and the
  gate block says which is which.
* **The guidance block's own token count is a REPORTED adjustment, never a correction.**
  R4's block is longer than R0's BY CONSTRUCTION, so the cost axis is contaminated by the
  treatment. ``guidance_words`` per rung rides in the gate block so a cost comparison can
  subtract it explicitly rather than silently reading treatment length as agent behaviour.
* **The tool-name confound is reported, not fixed.** If R0 already calls at ~100% because
  the model reaches for an allowlisted memory tool, the tool's NAME is the treatment and R0
  is the TOOL-AFFORDANCE FLOOR, which the gate block labels. The tool is NOT renamed: a
  rename moves the argv and invalidates every cached cell.

**Nothing in this module spends money by itself.** ``main`` refuses to spend unless the
operator passes ``--preflight`` or ``--staged`` with a pinned model and an OAuth token. The
preflight is a REAL paid cycle at the TOP rung, deliberately not simulated — the same stance
as ``toolreq_builtin_grid.preflight`` — because a simulated mechanism check proves only that
the simulator cooperates. As of this commit BOTH paid paths are UNRUN.

ZFC: rung text is authored data, the counter is ``tool_surface``'s mechanical argv scan, and
the gates are arithmetic over counts. No semantic judgment anywhere in here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from membench.runner.headless_agent import (
    ENV_OAUTH,
    REFUSE_API_KEY_SET,
    REFUSE_UNPINNED_MODEL,
    HeadlessAgentError,
    HeadlessClaudeAgent,
    MemoryChannel,
    a_paid_run_carries_the_metered_api_key,
    a_paid_run_needs_a_model,
    resolve_model,
    result_event,
    serialize_stream,
)
from membench.runner.sandbox import paid_sandbox
from membench.runner.tool_surface import (
    HOST_DENIED_TOOLS,
    MEMORY_ALLOWED_TOOLS,
    endogenous_memory_tool_calls,
    endogenous_memory_verbs,
    memory_invocations,
    native_memory_accesses,
    provision_memory_tool,
    surface_fingerprint,
)
from membench.runner.toolreq_corpus import load_twin_corpus
from membench.runner.toolreq_realagent import (
    DEFAULT_CORPUS,
    VARIANT_NECESSARY,
    VARIANT_UNNECESSARY,
    ToolReqRealAgentTask,
)
from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep

__all__ = [
    "CHANNEL",
    "GATE_KEY",
    "HALT_NO_CALL",
    "OK_FIRED",
    "RUNG_IDS",
    "RUNG_TEXT",
    "STAGED_REPEATS",
    "STAGED_RUNGS",
    "STAGED_TASKS",
    "SUMMARY_NAME",
    "MonotonicityViolation",
    "PreflightHaltError",
    "RungCell",
    "assert_gates_ride_outside_metrics",
    "call_rate_gates",
    "discrimination_margins",
    "guidance_block",
    "guidance_words",
    "monotonicity_violations",
    "planned_call_count",
    "preflight",
    "preflight_verdict",
    "rung_step",
    "staged_plan",
    "summarize",
]

# The summary E1 emits. Named for the file the acceptance criterion reads with
# `jq '.call_rate_gates' .../summary-e1.json`.
SUMMARY_NAME = "summary-e1.json"

# The one trust framing E1 runs under. NOT a swept axis here — see the module docstring: a bare
# arm surfaces no memory block, so both channels render the same bytes and a channel sweep would
# bill two cells for one measurement.
CHANNEL = MemoryChannel.RECALLED

RUNG_IDS: tuple[str, ...] = ("R0", "R1", "R2", "R3", "R4")

# The ladder, as ADDED CLAUSES. Each rung's text is its predecessor's plus one clause, so the
# nesting `RUNG_TEXT[n] in RUNG_TEXT[n+1]` is structural rather than a property of prose someone
# has to keep true by hand. R0 is EMPTY — the silent rung, whose prompt carries no guidance block
# at all — and it is the TOOL-AFFORDANCE FLOOR, not a zero: the agent still sees an allowlisted
# memory tool, and reaching for it unprompted is the floor this ladder is measured above.
_RUNG_CLAUSES: tuple[str, ...] = (
    "",
    "You have a persistent memory tool available in this session.",
    "Consult it when the task may depend on facts established in earlier sessions.",
    "If something you need is not stated in this task, recall it before you answer.",
    "After you act, record any durable fact you established so a later session can recall it.",
)


def _ladder(clauses: Sequence[str]) -> tuple[str, ...]:
    """Accumulate the clauses into the nested rung table.

    Built by accumulation rather than written out five times, which is what makes containment a
    property of the CONSTRUCTION: no edit to one rung's wording can break the nesting without
    breaking it for every rung above, and `test_ladder_is_nested` still checks the table it
    produced (a construction is not a proof that the table shipped is the one it built)."""
    texts: list[str] = []
    parts: list[str] = []
    for clause in clauses:
        if clause:
            parts.append(clause)
        texts.append(" ".join(parts))
    return tuple(texts)


RUNG_TEXT: tuple[str, ...] = _ladder(_RUNG_CLAUSES)

_GUIDANCE_HEADER = "## Memory guidance"


def guidance_block(rung: str) -> str:
    """The rendered guidance block for ``rung`` — EMPTY for R0.

    R0 emits no header either. A "## Memory guidance\\n(none)" placeholder would make the silent
    rung a instruction about memory, which is the one thing the floor must not be."""
    text = RUNG_TEXT[_rung_index(rung)]
    return f"{_GUIDANCE_HEADER}\n{text}" if text else ""


def guidance_words(rung: str) -> int:
    """Whitespace-word count of the rung's guidance text — the REPORTED cost adjustment.

    A word count, not a tokenizer's count, and named ``words`` so no consumer reads it as one.
    What it is for: R4's block is longer than R0's by construction, so any per-rung token
    comparison has the treatment baked into its cost axis. Reporting the treatment's own length
    beside the cost is what lets a reader subtract it; this module never subtracts it silently."""
    return len(RUNG_TEXT[_rung_index(rung)].split())


def _rung_index(rung: str) -> int:
    try:
        return RUNG_IDS.index(rung)
    except ValueError:
        raise ValueError(f"unknown rung {rung!r}; the ladder is {list(RUNG_IDS)}") from None


def rung_step(task: ToolReqRealAgentTask, rung: str) -> SequenceStep:
    """The goal step as run at ``rung``: the task's own goal request, preceded by the rung's
    guidance block, with the memory tool surface allowlisted.

    The guidance rides in ``user_request`` because that is the only channel that reaches the argv
    for a bare arm (``build_agent_prompt`` emits a memory block only when the harness surfaced
    memory, and E1 surfaces none). So a rung difference IS an argv difference, which is what keeps
    the resume cache from serving one rung's measurement for its neighbour's."""
    block = guidance_block(rung)
    step = task.goal_step
    request = f"{block}\n\n{step.user_request}" if block else step.user_request
    return step.model_copy(
        update={
            "step_id": f"{step.step_id}-{rung}",
            "user_request": request,
            "available_tools": list(MEMORY_ALLOWED_TOOLS),
        }
    )


# --------------------------------------------------------------------------------------
# the measured cell
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RungCell:
    """One ``(rung, variant)`` cell: how often the agent CHOSE to call memory across ``runs``.

    ``calling_runs`` is the numerator of the call rate (repeats with at least one memory call);
    ``memory_calls`` is the raw call total, which can exceed ``runs``. Both are kept: a rung that
    doubles the calls per run without moving the fraction of runs that call at all is a different
    finding from one that recruits new runs."""

    rung: str
    variant: str
    runs: int
    calling_runs: int
    memory_calls: int
    read_calls: int
    write_calls: int
    paid: bool
    verbs: tuple[str, ...] = ()
    # Which task this cell ran. Cells are keyed ``(rung, variant, work_id)``: with eight tasks per
    # variant, a ``(rung, variant)`` key names eight cells, and the first staged fire's gate block
    # read the LAST of them as the rung's rate (a 0.8 that was one task's 4/5).
    work_id: str = ""
    # Legs that hit the spawn timeout. They are ATTEMPTED (they were paid for and ``runs`` counts
    # them) but NOT MEASURED: an unmeasured leg is not a non-calling leg, and scoring it as one
    # biases the call rate down, in the direction that manufactures this series' null.
    timed_out_runs: int = 0

    def __post_init__(self) -> None:
        if self.runs <= 0:
            raise ValueError(
                f"{self.rung}/{self.variant}: a cell with {self.runs} run(s) measured nothing"
            )
        if not 0 <= self.timed_out_runs <= self.runs:
            raise ValueError(
                f"{self.rung}/{self.variant}: timed_out_runs {self.timed_out_runs} "
                f"outside 0..{self.runs}"
            )
        if self.calling_runs > self.measured_runs:
            raise ValueError(
                f"{self.rung}/{self.variant}: calling_runs {self.calling_runs} > measured "
                f"{self.measured_runs}"
            )
        if self.memory_calls < self.calling_runs:
            raise ValueError(
                f"{self.rung}/{self.variant}: {self.memory_calls} memory call(s) cannot cover "
                f"{self.calling_runs} run(s) that each made at least one"
            )

    @property
    def measured_runs(self) -> int:
        """The call-rate DENOMINATOR: legs that returned a stream."""
        return self.runs - self.timed_out_runs

    @property
    def call_rate(self) -> float:
        """Over MEASURED legs. Raises on a cell that measured nothing rather than reporting a rate
        of zero for it; ``pooled_rates`` sums numerators and denominators across cells and never
        needs this on an unmeasured cell."""
        if self.measured_runs == 0:
            raise ValueError(f"{self.rung}/{self.variant}/{self.work_id}: every leg timed out")
        return self.calling_runs / self.measured_runs

    def metrics(self) -> dict[str, Any]:
        """The per-cell metric vector. The GATE BLOCK IS NOT IN HERE, and that is load-bearing:
        a validity verdict flattened into a per-cell metric vector gets averaged with the cells it
        was meant to judge (the ``safety_gates`` / ``mechanism_gate`` precedent). It rides on the
        SUMMARY instead, and ``assert_gates_ride_outside_metrics`` enforces the separation."""
        return {
            "runs": self.runs,
            "timed_out_runs": self.timed_out_runs,
            "measured_runs": self.measured_runs,
            "calling_runs": self.calling_runs,
            "memory_calls": self.memory_calls,
            "read_calls": self.read_calls,
            "write_calls": self.write_calls,
            "call_rate": self.call_rate if self.measured_runs else None,
            "paid": self.paid,
        }

    def row(self) -> dict[str, Any]:
        return {
            "rung": self.rung,
            "variant": self.variant,
            "work_id": self.work_id,
            "guidance_words": guidance_words(self.rung),
            "verbs": list(self.verbs),
            "metrics": self.metrics(),
        }

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rung, self.variant, self.work_id)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> RungCell:
        """The inverse of ``row`` — what a resume reads back from a partial artifact."""
        m = row["metrics"]
        return cls(
            rung=str(row["rung"]),
            variant=str(row["variant"]),
            runs=int(m["runs"]),
            calling_runs=int(m["calling_runs"]),
            memory_calls=int(m["memory_calls"]),
            read_calls=int(m["read_calls"]),
            write_calls=int(m["write_calls"]),
            paid=bool(m["paid"]),
            verbs=tuple(str(v) for v in row.get("verbs", ())),
            work_id=str(row.get("work_id", "")),
            timed_out_runs=int(m.get("timed_out_runs", 0)),
        )


# --------------------------------------------------------------------------------------
# the gates
# --------------------------------------------------------------------------------------

GATE_KEY = "call_rate_gates"


@dataclass(frozen=True)
class MonotonicityViolation:
    """One adjacent-rung pair whose call rate went DOWN as the guidance got stronger.

    Carries the pair by NAME. A boolean "monotone: false" cannot be acted on — the point of the
    ladder is which added clause failed, and a violation that does not name its pair reports that
    something is wrong somewhere on a five-rung ladder."""

    lower: str
    upper: str
    lower_rate: float
    upper_rate: float

    @property
    def drop(self) -> float:
        return self.lower_rate - self.upper_rate

    def describe(self) -> str:
        return (
            f"{self.lower}->{self.upper}: call rate FELL {self.lower_rate:.3f} -> "
            f"{self.upper_rate:.3f} (drop {self.drop:.3f}) as the guidance got strictly stronger"
        )


def monotonicity_violations(
    rates: Mapping[str, float], *, tolerance: float = 0.0
) -> list[MonotonicityViolation]:
    """Every adjacent pair of MEASURED rungs whose call rate INVERTED.

    Adjacency is over the rungs actually present, in ladder order — the staged fire measures only
    R0 and R4, and those two are adjacent in that run. ``tolerance`` is a dead band a caller may
    widen for sampling noise; it defaults to 0.0 so the detector reports the raw inversion and any
    softening is an explicit, visible choice.

    Ordered comparison, never a sort or a max: the ladder's order is the treatment's order, so a
    detector that asked "is the maximum at the top" would stay green on a curve that rose, fell,
    and rose again — the exact shape a mid-ladder clause that BACKFIRES produces."""
    measured = [rung for rung in RUNG_IDS if rung in rates]
    return [
        MonotonicityViolation(
            lower=lower, upper=upper, lower_rate=rates[lower], upper_rate=rates[upper]
        )
        for lower, upper in pairwise(measured)
        if rates[upper] < rates[lower] - tolerance
    ]


def pooled_rates(cells: Sequence[RungCell], variant: str) -> dict[str, float]:
    """P(call | variant) per rung, POOLED over every task cell of that ``(rung, variant)``: the sum
    of calling legs over the sum of measured legs.

    Pooled, not last-wins and not a mean of per-cell rates. The first staged fire's gate block
    built ``{rung: cell.call_rate}`` over eight task cells per rung and reported the eighth
    cell's 4/5 as "R0 = 0.8" while the pooled rate was 16/40. A mean of cell rates would weight a
    cell with one measured leg the same as one with five. Rungs whose measured denominator is zero
    are omitted, never reported as 0.0."""
    calling: dict[str, int] = {}
    measured: dict[str, int] = {}
    for cell in cells:
        if cell.variant != variant:
            continue
        calling[cell.rung] = calling.get(cell.rung, 0) + cell.calling_runs
        measured[cell.rung] = measured.get(cell.rung, 0) + cell.measured_runs
    return {rung: calling[rung] / measured[rung] for rung in measured if measured[rung]}


def discrimination_margins(cells: Sequence[RungCell]) -> dict[str, float]:
    """``d(rung) = P(call | necessary) - P(call | unnecessary)`` per rung — E1's PRIMARY endpoint.

    Only rungs with BOTH halves measured get a margin: a margin computed against a missing half
    would be a call rate wearing the endpoint's name, and E1's whole point is that those two
    numbers can move independently."""
    necessary = pooled_rates(cells, VARIANT_NECESSARY)
    unnecessary = pooled_rates(cells, VARIANT_UNNECESSARY)
    return {rung: necessary[rung] - unnecessary[rung] for rung in necessary if rung in unnecessary}


def _rates_on(cells: Sequence[RungCell], variant: str) -> dict[str, float]:
    return pooled_rates(cells, variant)


def call_rate_gates(cells: Sequence[RungCell], *, tolerance: float = 0.0) -> dict[str, Any]:
    """The gate block E1's summary carries — ALWAYS non-empty, including on a run that measured
    nothing, because "no gate block" and "the gates passed" must not look alike to a reader."""
    necessary = _rates_on(cells, VARIANT_NECESSARY)
    violations = monotonicity_violations(necessary, tolerance=tolerance)
    margins = discrimination_margins(cells)
    floor = necessary.get(RUNG_IDS[0])
    return {
        "endpoint": "discrimination_margin",
        "monotonicity": {
            "rungs_measured": sorted(necessary, key=_rung_index),
            "call_rate_by_rung": necessary,
            "tolerance": tolerance,
            "violations": [asdict(v) | {"drop": v.drop} for v in violations],
            "violation_pairs": [f"{v.lower}->{v.upper}" for v in violations],
            "monotone": not violations,
            "reason": (
                "call rate is non-decreasing across every adjacent measured rung"
                if not violations
                else "; ".join(v.describe() for v in violations)
            ),
        },
        "discrimination": {
            # The PRIMARY endpoint, reported beside the raw rate precisely so a rate lift with a
            # flat margin cannot be read as the ladder working.
            "margin_by_rung": margins,
            "note": (
                "d(rung) = P(call | necessary) - P(call | unnecessary). A rung that lifts the raw "
                "call rate while d stays flat bought nothing."
            ),
        },
        "guidance_token_adjustment": {
            "guidance_words_by_rung": {rung: guidance_words(rung) for rung in RUNG_IDS},
            "note": (
                "REPORTED, never subtracted here: the guidance block is longer at every higher "
                "rung by construction, so a per-rung token cost carries the treatment's own "
                "length. Subtract these words explicitly before comparing cost across rungs."
            ),
        },
        "tool_affordance_floor": {
            "rung": RUNG_IDS[0],
            "call_rate": floor,
            "note": (
                "R0 carries NO guidance text; the agent still sees an allowlisted memory tool. A "
                "high R0 rate means the tool's NAME is the treatment, so R0 is the affordance "
                "FLOOR and not a zero. The tool is deliberately not renamed: a rename moves the "
                "argv and invalidates every cached cell."
            ),
        },
    }


def assert_gates_ride_outside_metrics(summary: Mapping[str, Any]) -> None:
    """Refuse a summary that smuggled the gate block into a per-cell metric vector.

    The acceptance criterion is exactly this shape (``.call_rate_gates`` non-empty AND
    ``.cells[0].metrics.call_rate_gates`` null), and it is a criterion because a validity verdict
    inside ``metrics()`` gets averaged with the cells it judges. Checked at the WRITE boundary so
    a summary that violates it cannot be published, not merely noticed in review."""
    if not summary.get(GATE_KEY):
        raise ValueError(f"summary carries no {GATE_KEY!r} block — a run without gates is unread")
    for row in summary.get("cells", []):
        metrics = row.get("metrics") or {}
        if GATE_KEY in metrics:
            raise ValueError(
                f"cell {row.get('rung')}/{row.get('variant')} carries {GATE_KEY!r} INSIDE its "
                "metrics — the gate block rides on the summary, never in a metric vector where it "
                "would be averaged with the cells it judges"
            )


def summarize(
    cells: Sequence[RungCell], *, model: str, dry_run: bool, repeats: int, tolerance: float = 0.0
) -> dict[str, Any]:
    """The E1 summary: the cells, and the gate block BESIDE them."""
    summary = {
        "experiment": "e1-guidance-ladder",
        "channel": CHANNEL.value,
        "model": resolve_model(model) or "cli-default",
        "surface_fingerprint": surface_fingerprint(),
        "dry_run": dry_run,
        "repeats": repeats,
        "paid": all(cell.paid for cell in cells) if cells else False,
        "rungs": [cell.rung for cell in cells],
        "cells": [cell.row() for cell in cells],
        GATE_KEY: call_rate_gates(cells, tolerance=tolerance),
    }
    assert_gates_ride_outside_metrics(summary)
    return summary


# --------------------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------------------


def _silent_runner(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    """The dry-run stand-in: an agent that makes NO tool call.

    Deliberately not a cooperating agent. A simulator that "recalls when the guidance says to"
    would reproduce E1's entire finding by construction, which is how a wiring check gets read as
    a result. So the free path measures a call rate of 0.0 at every rung and proves only the
    plumbing — the argv, the surface, the counter, the gates — exactly what ``--dry-run`` claims."""
    return subprocess.CompletedProcess(list(argv), 0, serialize_stream([result_event()]), "")


def run_rung_cell(
    task: ToolReqRealAgentTask,
    *,
    rung: str,
    repeats: int,
    model: str,
    dry_run: bool,
    timeout_s: float = 600.0,
    runner: object | None = None,
) -> RungCell:
    """Run one ``(rung, task-variant)`` cell and count the memory calls the agent CHOSE to make.

    Each repeat gets a fresh neutral sandbox and a fresh memory store OUTSIDE it (the store the
    cwd wipe cannot reach, ``tool_surface.provision_memory_tool``). Nothing is seeded into the
    store and no memory is surfaced in the prompt: this measures DISPOSITION, so the arm must not
    hand the agent a reason to call that the rung did not give it.

    ``paid`` is false whenever a runner was substituted or ``dry_run`` is set — the same rule
    ``e1_smoke`` publishes rows under, and for the same reason: an injected runner's call rate is
    the fixture's, not an agent's."""
    calling = 0
    total = 0
    reads = 0
    writes = 0
    timed_out = 0
    verbs: list[str] = []
    step = rung_step(task, rung)
    for i in range(repeats):
        with (
            tempfile.TemporaryDirectory(prefix="membench-memory-") as root,
            paid_sandbox(f"e1-{rung.lower()}-") as sandbox,
        ):
            surface = provision_memory_tool(Path(root), sandbox=sandbox)
            spawn = runner if runner is not None else (_silent_runner if dry_run else None)
            agent = HeadlessClaudeAgent(
                model=model,
                runner=spawn if spawn is not None else subprocess.run,  # type: ignore[arg-type]
                cwd=str(sandbox),
                env=surface.env(),
                memory_channel=CHANNEL,
                disallowed_tools=HOST_DENIED_TOOLS,
                timeout_s=timeout_s,
            )
            ctx = StepContext(
                trial_id=f"e1-{rung}-{task.result_id}-{i}",
                session_id=f"e1-{rung}-{task.result_id}",
                step_id=step.step_id,
            )
            try:
                result = agent.run_step(step, {}, ctx)
            except HeadlessAgentError as exc:
                if not is_spawn_timeout(exc):
                    raise
                # A leg that never returned a stream is UNMEASURED, not silent. The first staged
                # fire died here at cell 13/32 and took the run with it; scoring the leg as a
                # non-calling run instead would bias every rate toward the null. It is counted
                # in ``timed_out_runs`` and left out of the denominator.
                timed_out += 1
                print(
                    f"[timeout] {rung}/{task.variant}/{task.work_id} leg {i}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
        calls = list(result.tool_calls)
        # BOTH affordances count. The bd shim is the one this rig provisions; the native memory
        # file is the one the model reaches for first (mem-gj0pc), and scoring only the former
        # reported an agent that said "let me check memory" and did as a ZERO. The question the
        # rung ladder asks is whether the agent reaches for memory AT ALL, not whether it picks
        # the harness's preferred door.
        native = native_memory_accesses(calls, config_dir=surface.config_dir)
        n = endogenous_memory_tool_calls(calls) + len(native)
        total += n
        calling += 1 if n else 0
        invocations = memory_invocations(calls)
        reads += sum(1 for inv in invocations if inv.is_read)
        reads += sum(1 for access in native if access.is_read)
        writes += sum(1 for inv in invocations if inv.is_write)
        writes += sum(1 for access in native if access.is_write)
        verbs.extend(endogenous_memory_verbs(calls))
        verbs.extend(access.verb for access in native)
    return RungCell(
        rung=rung,
        variant=task.variant,
        runs=repeats,
        calling_runs=calling,
        memory_calls=total,
        read_calls=reads,
        write_calls=writes,
        paid=not dry_run and runner is None,
        verbs=tuple(verbs),
        work_id=task.work_id,
        timed_out_runs=timed_out,
    )


def is_spawn_timeout(exc: HeadlessAgentError) -> bool:
    """Whether a ``HeadlessAgentError`` is the spawn timeout and nothing else.

    Decided on the CAUSE CHAIN (``spawn.run_checked`` raises ``from subprocess.TimeoutExpired``),
    not on the message. Only the timeout is tolerated per leg: a missing CLI, a refused token, or
    a non-zero exit is a broken rig, and a broken rig tolerated leg by leg is a cell full of
    "timeouts" that reads as a measured zero."""
    return isinstance(exc.__cause__, subprocess.TimeoutExpired)


# --------------------------------------------------------------------------------------
# the paid preflight (mechanism-fires at the TOP rung) and the staged spend
# --------------------------------------------------------------------------------------

OK_FIRED = "FIRED"
HALT_NO_CALL = "NO-MEMORY-CALL"
HALT_UNPAID = "UNPAID"

# The rung the preflight runs at. The TOP one, on purpose: if the STRONGEST guidance cannot get a
# single memory call out of the agent, no interior rung can, and every interior cell would buy an
# uninterpretable zero (the mem-lvp.24 null this gate family exists to refuse).
PREFLIGHT_RUNG = RUNG_IDS[-1]

# The staged fire the bead authorizes FIRST: the two ENDS of the ladder only, at T=8 tasks and
# R=5 repeats over both corpus halves — 2 x 8 x 5 x 2 = 160 real calls. If R4 shows zero memory
# calls there, the interior rungs are NOT run and the null IS the result.
STAGED_RUNGS: tuple[str, ...] = (RUNG_IDS[0], RUNG_IDS[-1])
STAGED_TASKS = 8
STAGED_REPEATS = 5


class PreflightHaltError(RuntimeError):
    """The preflight's refusal to authorize the interior sweep, carrying its diagnosis.

    A distinct type for ``toolreq_builtin_grid.PreflightHaltError``'s reason: a preflight halt has
    measured nothing and spent one cycle, so it wants halt counsel, not resume counsel."""

    def __init__(self, kind: str, line: str) -> None:
        super().__init__(line)
        self.kind = kind
        self.line = line


def planned_call_count(
    *, rungs: Sequence[str], n_tasks: int, repeats: int, n_variants: int = 2
) -> int:
    """The real ``claude -p`` calls a fire makes — one leg per repeat, so the product. This is the
    number a human authorizes money against, so it is computed, not quoted."""
    return len(rungs) * n_tasks * repeats * n_variants


def staged_plan(n_tasks: int) -> dict[str, Any]:
    """What the staged fire WOULD spend, priced before anything runs."""
    tasks = min(n_tasks, STAGED_TASKS)
    return {
        "rungs": list(STAGED_RUNGS),
        "n_tasks": tasks,
        "repeats": STAGED_REPEATS,
        "n_variants": 2,
        "calls": planned_call_count(rungs=STAGED_RUNGS, n_tasks=tasks, repeats=STAGED_REPEATS),
        "halt_rule": (
            f"if {RUNG_IDS[-1]} shows ZERO memory calls, the interior rungs are NOT run and the "
            "null is the result"
        ),
    }


def staged_cells(
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    model: str,
    rungs: Sequence[str] = STAGED_RUNGS,
    n_tasks: int = STAGED_TASKS,
    repeats: int = STAGED_REPEATS,
    timeout_s: float = 600.0,
    runner: object | None = None,
    on_cell: Callable[[RungCell], None] | None = None,
    landed: Sequence[RungCell] = (),
) -> list[RungCell]:
    """Execute the staged fire: every ``(rung, variant, task)`` cell, ``repeats`` legs each.

    ``n_tasks`` is applied PER VARIANT, which is what makes the bill the priced one.
    ``staged_plan`` counts ``len(rungs) * n_tasks * repeats * 2``, so capping the flat list would
    spend half of it and report a whole grid. The variant split is done here for that reason.

    ``on_cell`` is called with each completed cell as it lands; ``landed`` is the resume cache
    (mem-78gwf): cells already paid for, keyed ``(rung, variant, work_id)``, are returned in
    place and not re-run. The caller decides whether a prior artifact is admissible against the
    current rig (``resume_cells``); this function only honours the keys it is handed."""
    by_variant: dict[str, list[ToolReqRealAgentTask]] = {}
    for task in tasks:
        by_variant.setdefault(task.variant, []).append(task)
    done = {cell.key: cell for cell in landed}
    cells: list[RungCell] = []
    for rung in rungs:
        for variant in sorted(by_variant):
            for task in by_variant[variant][:n_tasks]:
                prior = done.get((rung, variant, task.work_id))
                if prior is not None:
                    cells.append(prior)
                    continue
                cell = run_rung_cell(
                    task,
                    rung=rung,
                    repeats=repeats,
                    model=model,
                    dry_run=False,
                    timeout_s=timeout_s,
                    runner=runner,
                )
                cells.append(cell)
                if on_cell is not None:
                    on_cell(cell)
    return cells


def preflight_verdict(result: Mapping[str, Any]) -> tuple[str, str]:
    """Classify ONE preflight cycle's result row into its ``(kind, line)`` — the halt logic, as a
    pure function over a row, so it is testable against a FIXTURE without spending anything.

    Same shape and priority argument as ``toolreq_builtin_grid.preflight_kind``: kind and line come
    out of ONE ladder so the kind the gate ACTS on and the line the human READS cannot desync.

    An UNPAID row halts too, however many calls it shows: the mechanism claim is only ever about a
    real ``claude -p``, and a fixture runner's calls are the fixture's
    (``e1_smoke.halt_reason``)."""
    calls = int(result.get("memory_calls") or 0)
    if not calls:
        return HALT_NO_CALL, (
            f"the agent made ZERO memory calls at the TOP rung {result.get('rung')} — the "
            "strongest guidance on the ladder did not move it, so no interior rung can, and every "
            "interior cell would buy an uninterpretable zero"
        )
    if not result.get("paid"):
        return HALT_UNPAID, (
            f"{calls} memory call(s) recorded, but this row is not a paid run (paid=false): the "
            "stream came from a simulator or an injected runner, so the mechanism is UNPROVEN"
        )
    return OK_FIRED, f"the memory mechanism fired at {result.get('rung')}: {calls} call(s)"


def preflight(
    task: ToolReqRealAgentTask, *, model: str, rung: str = PREFLIGHT_RUNG, timeout_s: float = 600.0
) -> dict[str, Any]:
    """ONE REAL ``claude -p`` cycle at the top rung, before any interior rung is paid for.

    Deliberately NOT simulated, mirroring ``toolreq_builtin_grid.preflight``: a mechanism check a
    simulator can satisfy checks the simulator. It takes no ``dry_run`` and no ``runner`` for that
    reason — there is no free path through this function, and a caller that wants one is asking for
    a different (and worthless) measurement."""
    cell = run_rung_cell(
        task, rung=rung, repeats=1, model=model, dry_run=False, timeout_s=timeout_s
    )
    return {
        "rung": rung,
        "work_id": task.work_id,
        "variant": task.variant,
        "paid": cell.paid,
        "memory_calls": cell.memory_calls,
        "verbs": list(cell.verbs),
        "model": resolve_model(model) or "cli-default",
    }


def preflight_gate(result: Mapping[str, Any]) -> dict[str, Any]:
    """Apply ``preflight_verdict`` and RAISE on anything but a fired mechanism."""
    kind, line = preflight_verdict(result)
    if kind != OK_FIRED:
        raise PreflightHaltError(kind, line)
    return {"kind": kind, "line": line, **dict(result)}


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

EXIT_OK = 0
EXIT_HALT = 1
EXIT_REFUSED = 2
EXIT_NO_CORPUS = 4

_PLAN_ONLY = (
    "No fire requested. This printed the PLAN and spent nothing.\n"
    "  paid mechanism check : python -m membench.runner.e1_grid --preflight --rung R4 "
    "--model <id>\n"
    "  staged spend         : python -m membench.runner.e1_grid --staged --model <id>\n"
    "Both need CLAUDE_CODE_OAUTH_TOKEN and a pinned --model, and both spend real money."
)


def _refusal(*, dry_run: bool, model: str) -> str | None:
    """Why a paid path must not run, or ``None``. Checked BEFORE the corpus loads, so a refused
    run costs nothing and reports the reason it was actually refused."""
    if a_paid_run_carries_the_metered_api_key(dry_run=dry_run):
        return REFUSE_API_KEY_SET
    if a_paid_run_needs_a_model(model, dry_run=dry_run):
        return REFUSE_UNPINNED_MODEL
    if not dry_run and not os.environ.get(ENV_OAUTH):
        return (
            f"REFUSING to spend: {ENV_OAUTH} is unset. Source it from an account home and re-run, "
            "or drop the paid flag."
        )
    return None


class ResumeMismatchError(RuntimeError):
    """A partial artifact was produced by a different rig than the one about to resume it."""


def resume_cells(summary: Mapping[str, Any], *, model: str) -> list[RungCell]:
    """The cells a partial ``--out`` artifact contributes to a resumed fire.

    Admissible only when the artifact's model and surface fingerprint match the rig now running:
    a CLI upgrade or a model swap mid-run would otherwise land in the same grid as the cells it
    did not produce, and the grid would read as one measurement. Cells that measured nothing
    (every leg timed out) are NOT carried over; a resume is the chance to measure them."""
    want_model = resolve_model(model) or "cli-default"
    want_surface = surface_fingerprint()
    got_model = summary.get("model")
    got_surface = summary.get("surface_fingerprint")
    if got_model != want_model or got_surface != want_surface:
        raise ResumeMismatchError(
            f"partial artifact was produced under model={got_model!r} "
            f"surface={got_surface!r}; this rig is model={want_model!r} "
            f"surface={want_surface!r}. Not resuming into a different rig's grid."
        )
    cells = [RungCell.from_row(row) for row in summary.get("cells", ())]
    return [cell for cell in cells if cell.measured_runs > 0]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--model", default="")
    ap.add_argument("--rung", default=PREFLIGHT_RUNG, choices=list(RUNG_IDS))
    ap.add_argument(
        "--preflight",
        action="store_true",
        help="ONE real paid cycle at --rung; asserts >=1 memory tool call. Zero is a HALT.",
    )
    ap.add_argument(
        "--staged",
        action="store_true",
        help=(
            f"the staged spend: rungs {list(STAGED_RUNGS)} at "
            f"T={STAGED_TASKS}, R={STAGED_REPEATS}"
        ),
    )
    ap.add_argument(
        "--fire-staged",
        action="store_true",
        help=(
            "EXECUTE the staged spend priced by --staged. Separate from --staged on purpose: "
            "pricing and spending must not be the same keystroke."
        ),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "where to write the summary; cells are written as they land, and an existing file "
            "here is RESUMED (its landed cells are kept, not re-bought) when it matches this rig"
        ),
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    paid = args.preflight or args.fire_staged
    refusal = _refusal(dry_run=not paid, model=args.model)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED

    _, tasks = load_twin_corpus(args.corpus_dir)
    if not tasks:
        print(
            f"no tool-requiring tasks under {args.corpus_dir}: the corpus is missing or empty, so "
            "there is nothing to measure (this is NOT a result)",
            file=sys.stderr,
        )
        return EXIT_NO_CORPUS

    if args.preflight:
        anchor = next((t for t in tasks if t.variant == VARIANT_NECESSARY), tasks[0])
        result = preflight(anchor, model=args.model, rung=args.rung)
        try:
            gated = preflight_gate(result)
        except PreflightHaltError as exc:
            print(json.dumps({"kind": exc.kind, "line": exc.line, **result}, indent=2))
            print(f"HALT: {exc.line}. Do NOT pay for the interior rungs.", file=sys.stderr)
            return EXIT_HALT
        print(json.dumps(gated, indent=2))
        return EXIT_OK

    if args.fire_staged:
        plan = staged_plan(len(tasks))
        landed: list[RungCell] = []
        if args.out is not None and args.out.exists():
            try:
                prior = json.loads(args.out.read_text(encoding="utf-8"))
                landed.extend(resume_cells(prior, model=args.model))
            except (ValueError, KeyError, TypeError) as exc:
                print(f"{args.out}: not a readable partial artifact: {exc}", file=sys.stderr)
                return EXIT_REFUSED
            except ResumeMismatchError as exc:
                print(f"REFUSED: {exc}", file=sys.stderr)
                return EXIT_REFUSED
        print(json.dumps({"firing": plan, "resumed_cells": len(landed)}, indent=2), file=sys.stderr)

        def _record(cell: RungCell) -> None:
            landed.append(cell)
            print(
                f"[{len(landed)}] {cell.rung}/{cell.variant}/{cell.work_id} "
                f"{cell.calling_runs}/{cell.measured_runs} calling "
                f"({cell.timed_out_runs} timed out), {cell.memory_calls} call(s)",
                file=sys.stderr,
                flush=True,
            )
            if args.out is not None:
                # Partial evidence, written as it is paid for: a fire that dies at cell 90 leaves
                # the 89 cells it bought, and the next --fire-staged with the same --out resumes
                # from them (mem-78gwf).
                args.out.write_text(
                    json.dumps(
                        summarize(landed, model=args.model, dry_run=False, repeats=plan["repeats"]),
                        indent=2,
                    ),
                    encoding="utf-8",
                )

        cells = staged_cells(
            tasks,
            model=args.model,
            n_tasks=plan["n_tasks"],
            repeats=plan["repeats"],
            on_cell=_record,
            landed=list(landed),
        )
        summary = summarize(cells, model=args.model, dry_run=False, repeats=plan["repeats"])
        print(json.dumps(summary, indent=2))
        return EXIT_OK

    if args.staged:
        # Reachable, and deliberately not run here: the staged fire is the orchestrator's to
        # trigger after the preflight clears. Wiring it to run off the same flag that prices it
        # would make an authorization and an execution the same keystroke.
        print(json.dumps({"staged_plan": staged_plan(len(tasks))}, indent=2))
        print(
            "STAGED PLAN PRICED, NOT FIRED: run the preflight first; the staged fire is "
            "authorized separately.",
            file=sys.stderr,
        )
        return EXIT_OK

    plan = {
        "n_tasks": len(tasks),
        "rungs": list(RUNG_IDS),
        "guidance_words_by_rung": {rung: guidance_words(rung) for rung in RUNG_IDS},
        "staged_plan": staged_plan(len(tasks)),
        "full_ladder_calls": planned_call_count(
            rungs=RUNG_IDS, n_tasks=min(len(tasks), STAGED_TASKS), repeats=STAGED_REPEATS
        ),
    }
    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print(json.dumps(plan, indent=2))
        print(_PLAN_ONLY, file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
