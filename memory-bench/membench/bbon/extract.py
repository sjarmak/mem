"""Build `Attempt`s and `AttemptStep`s from the membench trace substrate.

The qualitative layer needs the same inputs the mechanical layer reads — a bead's
canonical record (for the pass/fail outcomes) and its raw trace stream (for the
tool-call sequence). This module turns those into the bBoN `Attempt` (one arm's run,
with a terminal status and its metric vector as `result`) and the ordered
`AttemptStep`s (one per tool call) the narrative diff aligns.

Step extraction reuses `armcompare._iter_tool_use_blocks` (the single tolerant
tool-use walk over a Claude Code stream) rather than re-deriving it — same parsing,
same shell-mediated-call exclusion as everywhere else in membench.

ZFC: pure mechanism — structural parsing and content-addressed id construction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from membench.armcompare import _iter_tool_use_blocks
from membench.bbon.models import Attempt, AttemptStatus, AttemptStep, deterministic_id


def terminal_status(tool_outcomes: Sequence[Mapping[str, Any]]) -> AttemptStatus:
    """The attempt's terminal status from its ``trace.tool_outcomes``: ``unknown``
    when no outcome carries a verdict, ``failed`` when any runner's LAST status is a
    fail, else ``completed`` (every runner ended green). A malformed outcome raises —
    the outcomes are our own store projection, so a shapeless one is an ingest bug,
    not a silent skip (same strictness as `armcompare.iterations_to_green`)."""
    last_status: dict[str, str] = {}
    for i, outcome in enumerate(tool_outcomes):
        if not isinstance(outcome, Mapping):
            raise ValueError(f"tool_outcome[{i}] is not a mapping: {outcome!r}")
        runner, status = outcome.get("runner"), outcome.get("status")
        if not isinstance(runner, str) or not isinstance(status, str):
            raise ValueError(f"tool_outcome[{i}] missing runner/status: {dict(outcome)!r}")
        last_status[runner] = status
    if not last_status:
        return "unknown"
    if any(status == "fail" for status in last_status.values()):
        return "failed"
    return "completed"


def steps_from_stream(stream_text: str, attempt_id: str) -> list[AttemptStep]:
    """One `AttemptStep` per ``tool_use`` block in the trace, in stream order. ``kind``
    is the tool name; ``input`` is its input block (``{}`` when absent or not a
    mapping). Each step id is content-addressed over (attempt_id, index, kind, input)."""
    steps: list[AttemptStep] = []
    for index, block in enumerate(_iter_tool_use_blocks(stream_text)):
        name = block.get("name")
        kind = name if isinstance(name, str) and name else "unknown"
        raw_input = block.get("input")
        input_dict: dict[str, Any] = dict(raw_input) if isinstance(raw_input, Mapping) else {}
        step_id = deterministic_id(
            {"attempt_id": attempt_id, "step_index": index, "kind": kind, "input": input_dict}
        )
        steps.append(
            AttemptStep(
                id=step_id,
                attempt_id=attempt_id,
                step_index=index,
                kind=kind,
                input=input_dict,
            )
        )
    return steps


def build_attempt(
    work_id: str,
    arm: str,
    record: Mapping[str, Any],
    stream_text: str,
    *,
    metrics: Mapping[str, Any] | None = None,
) -> tuple[Attempt, list[AttemptStep]]:
    """One arm's `Attempt` (id content-addressed over work_id+arm; status from the
    record's tool outcomes; ``result`` the mechanical metric vector) plus its ordered
    steps. ``metrics`` is the `armcompare.BeadMetrics` dump the caller already
    computed — passed in so this module stays decoupled from metric extraction."""
    if not work_id:
        raise ValueError("work_id is required")
    if not arm:
        raise ValueError("arm is required")
    trace = record.get("trace")
    outcomes = trace.get("tool_outcomes", []) if isinstance(trace, Mapping) else []
    if not isinstance(outcomes, list):
        raise ValueError(f"{work_id}: trace.tool_outcomes is not a list: {type(outcomes)}")

    attempt_id = deterministic_id({"work_id": work_id, "arm": arm})
    attempt = Attempt(
        id=attempt_id,
        work_id=work_id,
        arm=arm,
        status=terminal_status(outcomes),
        result=dict(metrics) if metrics is not None else {},
    )
    return attempt, steps_from_stream(stream_text, attempt_id)
