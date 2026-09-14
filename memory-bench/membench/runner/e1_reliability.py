"""Bd-specific evidence for E1's establish → fresh-session goal pairs.

Required values are authored opaque tokens, matched mechanically. A completed handoff is
an observed conjunction, not a claim that bd causally supplied the goal action. Compound
shell results cannot establish which command returned a value and are left unattributed.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from membench.metrics.scorers import states_value
from membench.runner.bd_actions import UNKNOWN_DESTINATION, write_reason
from membench.runner.leg_plans import PAIR_ROLES
from membench.runner.realagent_probe import REAL_TOOL
from membench.runner.tool_surface import (
    MemoryInvocation,
    command_segments,
    memory_command_is_direct,
    memory_invocations,
    memory_result_is_attributable,
    native_memory_accesses,
    remember_ack_action,
)
from membench.runner.toolreq_corpus import superseded_values
from membench.runner.toolreq_realagent import ToolReqRealAgentTask
from membench.schemas.trace import ToolCall

RELIABILITY_VERSION = 5
# Action and ordering scores (``goal_action_success``, ``bd_recall_before_action``) have been
# sound since v3. v4 added the per-call observations (``bd_calls``) and the stale-recall flag,
# but attributed a tool_result to bd only when the command carried no redirection at all, so
# ``bd recall k 2>&1`` (the spelling of every bd call in the first paid four-leg trial) scored
# as a compound command; v5 reads redirections the way the shell does
# (``tool_surface.strip_redirections``). Observations scored at v4 are therefore not read;
# action scores from v3 on still are. Recall flags from any earlier version may undercount.
ACTION_SCORING_SINCE = 3
CALL_OBSERVATION_SINCE = 5
ROLES = PAIR_ROLES

CallOutcome = Literal[
    "remembered",  # bd stored the write under a fresh key
    "updated",  # bd overwrote an EXISTING key in place
    "recalled",  # bd answered a bare-existing-key ``remember`` as a read
    "refused",  # the tool returned an error
    "unacknowledged",  # a write bd neither acknowledged nor refused (truncated, silenced)
    "returned",  # a read returned payload
    "empty",  # a read returned no payload
    "unattributed",  # part of a compound shell command; the tool_result is nobody's
]


class BdCallObservation(BaseModel):
    """One bd memory invocation as the agent saw it: what it asked, how bd answered, and which
    of the task's versions the exchange stated.

    ``outcome`` is bd's own acknowledgement, the tool's error flag, or the absence of either.
    ``current_values`` / ``superseded_values`` are the task's authored tokens matched in the
    content the call offered (a write) or the payload it received (a read), word-bounded. A
    refused write keeps the values it TRIED to state, because that is the observation a
    rejected stale write leaves behind."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Ordinal among the leg's memory invocations, in stream order.
    position: int = Field(ge=0)
    # The Bash call's position in the stream; several invocations share one when chained.
    tool_use_index: int | None = Field(default=None, ge=0)
    verb: str
    key: str = ""
    outcome: CallOutcome
    current_values: tuple[str, ...] = ()
    superseded_values: tuple[str, ...] = ()


class BdLegEvidence(BaseModel):
    """Compact evidence persisted beside the stream and in the resumable cell."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Zero identifies historical artifacts that did not persist their scorer version.
    scoring_version: int = Field(default=0, ge=0)
    leg: int = Field(ge=0)
    # The pair's two roles and the trial's two extra ones (``leg_plans``). Action scoring stays
    # gated on the goal role; the others carry attempts, acknowledgements and payloads only.
    role: Literal["establish", "revise", "goal", "stale_writer"]
    status: Literal["ok", "timeout", "error"]
    bd_read_attempts: int = Field(default=0, ge=0)
    bd_write_attempts: int = Field(default=0, ge=0)
    bd_accepted_writes: int = Field(default=0, ge=0)
    bd_unattributed_read_blocks: int = Field(default=0, ge=0)
    bd_unattributed_write_blocks: int = Field(default=0, ge=0)
    bd_capture_complete: bool = False
    bd_recall_complete: bool = False
    bd_recall_before_action: bool | None = False
    # Any attributed read payload states a value the goal action FORBIDS (v4+).
    bd_recall_states_superseded: bool = False
    native_read_attempts: int = Field(default=0, ge=0)
    native_write_attempts: int = Field(default=0, ge=0)
    native_answered_reads: int = Field(default=0, ge=0)
    goal_action_success: bool | None = None
    bd_evidence_unknown: bool = False
    bd_evidence_unknown_reasons: tuple[str, ...] = ()
    # Every bd invocation in stream order (v4+); empty on legacy artifacts.
    bd_calls: tuple[BdCallObservation, ...] = ()


def _read_content(invocation: MemoryInvocation, call: ToolCall) -> list[str]:
    """Extract payload fields from the shipped bd formats, excluding query/key metadata."""
    result = call.result or ""
    segments = command_segments(str(call.arguments.get("command", "")))
    if "--json" in segments[0]:
        try:
            body = json.loads(result)
        except json.JSONDecodeError:
            return []
        if not isinstance(body, dict):
            return []
        if invocation.verb == "memories":
            return (
                [
                    value
                    for key, value in body.items()
                    if key != "schema_version" and isinstance(value, str)
                ]
                if "schema_version" in body
                else []
            )
        value = body.get("value")
        return [value] if body.get("found") is True and isinstance(value, str) else []
    if invocation.verb == "memories":
        # Text search prints a heading (which repeats the query), two-space keys,
        # and four-space content. A miss has no content rows, even though it echoes the query.
        return [line[4:] for line in result.splitlines() if line.startswith("    ")]
    if invocation.is_recall_by_result:
        return ["\n".join(result.splitlines()[1:])]
    return [result]


def _read_payloads(calls: Sequence[ToolCall]) -> tuple[list[str], int]:
    payloads: list[str] = []
    unattributed = 0
    for call in calls:
        invocations = memory_invocations([call])
        reads = [inv for inv in invocations if inv.is_read or inv.is_recall_by_result]
        if not reads or call.result is None or call.is_error:
            continue
        # Only a direct, single bd command owns the whole tool_result. In particular,
        # `bd recall missing; cat MEMORY.md` must not credit the native payload to bd.
        if memory_result_is_attributable(call):
            payloads.extend(_read_content(invocations[0], call))
        else:
            unattributed += 1
    return payloads, unattributed


def _contains_all(payloads: Sequence[str], values: Sequence[str]) -> bool:
    return bool(values) and all(
        any(states_value(payload, value) for payload in payloads) for value in values
    )


def _stated(text: str, values: Sequence[str]) -> tuple[str, ...]:
    return tuple(value for value in values if states_value(text, value))


def _write_outcome(call: ToolCall, invocation: MemoryInvocation) -> CallOutcome:
    if call.is_error:
        return "refused"
    action = remember_ack_action(invocation.result)
    if action == "recalled":
        return "recalled"
    if action in ("remembered", "updated") and invocation.is_accepted_write:
        return "remembered" if action == "remembered" else "updated"
    return "unacknowledged"


def _observe_call(
    call: ToolCall, invocation: MemoryInvocation, *, direct: bool
) -> tuple[CallOutcome, str]:
    """The outcome of one invocation and the text that carries the versions it stated.

    Only a direct, single bd command owns its tool_result; inside a compound command every
    invocation is unattributed, though a write still reports the values it offered."""
    if invocation.is_write:
        outcome = _write_outcome(call, invocation) if direct else "unattributed"
        if outcome == "recalled":
            return outcome, "\n".join(_read_content(invocation, call))
        return outcome, " ".join(invocation.stored_content)
    if not direct:
        return "unattributed", ""
    if invocation.is_read:
        if call.is_error:
            return "refused", ""
        payloads = _read_content(invocation, call)
        return ("returned" if payloads else "empty"), "\n".join(payloads)
    return ("refused" if call.is_error else "returned"), ""


def _observe_calls(
    task: ToolReqRealAgentTask, calls: Sequence[ToolCall]
) -> tuple[BdCallObservation, ...]:
    """Every bd invocation in ``calls`` as the agent saw it, in stream order."""
    current = tuple(task.current_opaque_values)
    superseded = superseded_values(task)
    observations: list[BdCallObservation] = []
    for call in calls:
        direct = memory_command_is_direct(call)
        for invocation in memory_invocations([call]):
            outcome, text = _observe_call(call, invocation, direct=direct)
            observations.append(
                BdCallObservation(
                    position=len(observations),
                    tool_use_index=call.tool_use_index,
                    verb=invocation.verb,
                    key=invocation.key,
                    outcome=outcome,
                    current_values=_stated(text, current),
                    superseded_values=_stated(text, superseded),
                )
            )
    return tuple(observations)


def _goal_writes(
    task: ToolReqRealAgentTask, calls: Sequence[ToolCall], *, cwd: Path | str | None
) -> tuple[tuple[ToolCall, ...], bool]:
    checks = task.goal_step.outcome_checks
    actions = [action for check in checks for action in check.requires_action]
    if (
        not checks
        or any(not check.requires_action for check in checks)
        or any(action.tool != REAL_TOOL or not action.arg_values for action in actions)
    ):
        raise ValueError("bd reliability requires an explicit config.json Write contract")
    required = tuple(value for action in actions for value in action.arg_values)
    forbidden = tuple(value for action in actions for value in action.forbidden_values)
    reasons = [
        (call, write_reason(call, cwd=cwd, required=required, forbidden=forbidden))
        for call in calls
    ]
    return (
        tuple(call for call, reason in reasons if reason == "qualifies"),
        any(reason == UNKNOWN_DESTINATION for _, reason in reasons),
    )


def _recall_before_action(
    task: ToolReqRealAgentTask,
    calls: Sequence[ToolCall],
    *,
    goal_writes: Sequence[ToolCall],
    ambiguous_goal: bool,
) -> bool | None:
    delivered = [(call, _read_payloads([call])[0]) for call in calls]
    before, _ = _receipt_before_action(
        task,
        [(call, payloads) for call, payloads in delivered if payloads],
        goal_writes=goal_writes,
        ambiguous_goal=ambiguous_goal,
    )
    return before


class _ReceiptIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    invocation_id: str = Field(min_length=1)
    tool_use_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    leg_id: str = Field(min_length=1)
    operation_argv: list[str] = Field(min_length=1)
    event: Literal["start", "finish"]


def _finished_receipts(
    receipts: Sequence[Mapping[str, Any]], *, expected_leg_id: str
) -> tuple[list[Mapping[str, Any]], list[str]]:
    indexed: dict[str, list[tuple[_ReceiptIdentity, Mapping[str, Any]]]] = {}
    reasons: list[str] = []
    for row in receipts:
        if row.get("leg_id") not in (None, "", expected_leg_id):
            continue
        try:
            identity = _ReceiptIdentity.model_validate(row)
        except ValueError:
            reasons.append("malformed_receipt")
            continue
        indexed.setdefault(identity.invocation_id, []).append((identity, row))
    finished = []
    for rows in indexed.values():
        if len(rows) != 2 or [identity.event for identity, _ in rows] != ["start", "finish"]:
            reasons.append("incomplete_or_duplicate_receipt")
            continue
        (start, _), (finish, body) = rows
        if start.model_dump(exclude={"event"}) != finish.model_dump(exclude={"event"}):
            reasons.append("receipt_identity_mismatch")
            continue
        if (
            type(body.get("returncode")) is not int
            or not isinstance(body.get("stdout"), str)
            or not isinstance(body.get("stderr"), str)
        ):
            reasons.append("malformed_receipt_outcome")
            continue
        finished.append(body)
    return finished, reasons


def _receipt_call(receipt: Mapping[str, Any]) -> ToolCall:
    return ToolCall(
        name="Bash",
        arguments={"command": shlex.join(["bd", *receipt["operation_argv"]])},
        result=receipt["stdout"],
        is_error=receipt["returncode"] != 0,
    )


def _receipt_before_action(
    task: ToolReqRealAgentTask,
    delivered: Sequence[tuple[ToolCall, Sequence[str]]],
    *,
    goal_writes: Sequence[ToolCall],
    ambiguous_goal: bool,
) -> tuple[bool | None, bool]:
    unknown_order = False
    for call in goal_writes:
        if call.tool_use_index is None:
            unknown_order = True
            continue
        before = [
            payload
            for read, payloads in delivered
            if read.tool_result_index is not None and read.tool_result_index < call.tool_use_index
            for payload in payloads
        ]
        if _contains_all(before, task.current_opaque_values):
            return True, unknown_order
        if any(read.tool_result_index is None for read, _ in delivered):
            unknown_order = True
    return (None if ambiguous_goal or unknown_order else False), unknown_order


def _observe_receipts(
    calls: Sequence[ToolCall], finished: Sequence[Mapping[str, Any]]
) -> tuple[list[MemoryInvocation], list[tuple[ToolCall, Sequence[str]]], list[str]]:
    reasons: list[str] = []
    calls_by_id: dict[str, list[ToolCall]] = {}
    for call in calls:
        if call.tool_use_id:
            calls_by_id.setdefault(call.tool_use_id, []).append(call)
    observed: list[MemoryInvocation] = []
    delivered: list[tuple[ToolCall, Sequence[str]]] = []
    matched_counts: dict[str, int] = {}
    for receipt in finished:
        matching = calls_by_id.get(receipt["tool_use_id"], [])
        if len(matching) != 1 or matching[0].name != "Bash":
            reasons.append("unmatched_receipt")
            continue
        call = matching[0]
        direct = _receipt_call(receipt)
        invocations = memory_invocations([direct])
        matched_counts[receipt["tool_use_id"]] = matched_counts.get(
            receipt["tool_use_id"], 0
        ) + len(invocations)
        observed.extend(invocations)
        reads = [inv for inv in invocations if inv.is_read or inv.is_recall_by_result]
        if not reads or direct.is_error:
            continue
        payloads = _read_content(reads[0], direct)
        if not payloads:
            continue
        if call.result is None or receipt["stdout"].strip() not in call.result:
            reasons.append("bd_output_not_observed_in_tool_result")
            continue
        delivered.append((call, payloads))
        if call.tool_result_index is None:
            reasons.append("missing_result_delivery_order")
    for call in calls:
        expected = len(memory_invocations([call]))
        if expected > matched_counts.get(call.tool_use_id or "", 0):
            reasons.append("missing_execution_receipt")
    return observed, delivered, reasons


def _score_receipts(
    task: ToolReqRealAgentTask,
    calls: Sequence[ToolCall],
    receipts: Sequence[Mapping[str, Any]],
    *,
    expected_leg_id: str,
    score_action: bool,
    goal_writes: Sequence[ToolCall],
    ambiguous_goal: bool,
) -> dict[str, Any]:
    finished, reasons = _finished_receipts(receipts, expected_leg_id=expected_leg_id)
    observed, delivered, observation_reasons = _observe_receipts(calls, finished)
    reasons.extend(observation_reasons)
    accepted = [inv for inv in observed if inv.is_accepted_write]
    before, ordering_unknown = (
        _receipt_before_action(
            task, delivered, goal_writes=goal_writes, ambiguous_goal=ambiguous_goal
        )
        if score_action
        else (False, False)
    )
    if ordering_unknown:
        reasons.append("missing_action_use_order")
    attempts = memory_invocations(calls)
    return {
        "bd_read_attempts": max(
            sum(inv.is_read or inv.is_recall_by_result for inv in attempts),
            sum(inv.is_read or inv.is_recall_by_result for inv in observed),
        ),
        "bd_write_attempts": max(
            sum(inv.is_write for inv in attempts), sum(inv.is_write for inv in observed)
        ),
        "bd_accepted_writes": len(accepted),
        "bd_capture_complete": _contains_all(
            [text for inv in accepted for text in inv.stored_content], task.current_opaque_values
        ),
        "bd_recall_complete": _contains_all(
            [text for _, payloads in delivered for text in payloads], task.current_opaque_values
        ),
        "bd_recall_before_action": before,
        "bd_unattributed_read_blocks": 0,
        "bd_unattributed_write_blocks": 0,
        "bd_evidence_unknown": bool(reasons),
        "bd_evidence_unknown_reasons": tuple(sorted(set(reasons))),
    }


def score_bd_leg(
    task: ToolReqRealAgentTask,
    calls: Sequence[ToolCall],
    *,
    leg: int,
    role: str,
    status: str,
    config_dir: Path | None,
    receipts: Sequence[Mapping[str, Any]] | None = None,
    expected_leg_id: str | None = None,
    cwd: Path | str | None = None,
) -> BdLegEvidence:
    """Separate attempts, acknowledged content, returned values and completed actions."""
    if receipts is not None and not expected_leg_id:
        raise ValueError("Receipt scoring requires an expected leg identity")
    goal_writes, ambiguous_goal = (
        _goal_writes(task, calls, cwd=cwd) if role == "goal" and status == "ok" else ((), False)
    )
    invocations = memory_invocations(calls)
    accepted = [
        inv
        for call in calls
        if memory_result_is_attributable(call)
        for inv in memory_invocations([call])
        if inv.is_accepted_write
    ]
    payloads, unattributed = _read_payloads(calls)
    native = native_memory_accesses(calls, config_dir=config_dir)
    superseded = superseded_values(task)
    return BdLegEvidence.model_validate(
        {
            "scoring_version": RELIABILITY_VERSION,
            "leg": leg,
            "role": role,
            "status": status,
            "bd_calls": _observe_calls(task, calls),
            "bd_recall_states_superseded": any(
                states_value(payload, value) for payload in payloads for value in superseded
            ),
            "bd_read_attempts": sum(inv.is_read or inv.is_recall_by_result for inv in invocations),
            "bd_write_attempts": sum(inv.is_write for inv in invocations),
            "bd_accepted_writes": len(accepted),
            "bd_unattributed_read_blocks": unattributed,
            "bd_unattributed_write_blocks": sum(
                any(inv.is_write for inv in memory_invocations([call]))
                for call in calls
                if not memory_result_is_attributable(call)
                and call.result is not None
                and not call.is_error
            ),
            "bd_capture_complete": _contains_all(
                [text for inv in accepted for text in inv.stored_content],
                task.current_opaque_values,
            ),
            "bd_recall_complete": _contains_all(payloads, task.current_opaque_values),
            "bd_recall_before_action": (
                _recall_before_action(
                    task, calls, goal_writes=goal_writes, ambiguous_goal=ambiguous_goal
                )
                if role == "goal" and status == "ok"
                else False
            ),
            "native_read_attempts": sum(access.is_read for access in native),
            "native_write_attempts": sum(access.is_write for access in native),
            "native_answered_reads": sum(
                access.is_read and access.satisfied is True for access in native
            ),
            "goal_action_success": (
                (True if goal_writes else None if ambiguous_goal else False)
                if role == "goal" and status == "ok"
                else None
            ),
            **(
                _score_receipts(
                    task,
                    calls,
                    receipts,
                    expected_leg_id=expected_leg_id or "",
                    score_action=role == "goal" and status == "ok",
                    goal_writes=goal_writes,
                    ambiguous_goal=ambiguous_goal,
                )
                if receipts is not None
                else {}
            ),
        }
    )


def _role_report(evidence: Sequence[BdLegEvidence], *, scheduled: int) -> dict[str, Any]:
    measured = [leg for leg in evidence if leg.status == "ok"]
    return {
        "scheduled": scheduled,
        "measured": len(measured),
        "bd_evidence_unknown_legs": sum(leg.bd_evidence_unknown for leg in evidence),
        "missing": scheduled - len(evidence),
        "timeout": sum(leg.status == "timeout" for leg in evidence),
        "error": sum(leg.status == "error" for leg in evidence),
        "bd_read_attempt_legs": sum(leg.bd_read_attempts > 0 for leg in measured),
        "bd_write_attempt_legs": sum(leg.bd_write_attempts > 0 for leg in measured),
        "bd_accepted_write_legs": sum(leg.bd_accepted_writes > 0 for leg in measured),
        "bd_capture_complete_legs": sum(leg.bd_capture_complete for leg in measured),
        "bd_recall_complete_legs": sum(leg.bd_recall_complete for leg in measured),
        "bd_unattributed_read_blocks": sum(leg.bd_unattributed_read_blocks for leg in measured),
        "bd_unattributed_write_blocks": sum(leg.bd_unattributed_write_blocks for leg in measured),
        "native_read_attempt_legs": sum(leg.native_read_attempts > 0 for leg in measured),
        "native_write_attempt_legs": sum(leg.native_write_attempts > 0 for leg in measured),
        "native_answered_read_legs": sum(leg.native_answered_reads > 0 for leg in measured),
        "goal_action_success_legs": sum(
            leg.goal_action_success is True and leg.scoring_version >= ACTION_SCORING_SINCE
            for leg in measured
        ),
        "legacy_goal_action_success_legs": sum(
            leg.goal_action_success is True and leg.scoring_version < ACTION_SCORING_SINCE
            for leg in measured
        ),
        "legacy_evidence_legs": sum(leg.scoring_version != RELIABILITY_VERSION for leg in evidence),
    }


def _index_legs(raw: Sequence[Any], *, runs: int) -> dict[int, BdLegEvidence]:
    indexed: dict[int, BdLegEvidence] = {}
    if runs <= 0 or runs % len(ROLES):
        raise ValueError("bd reliability requires a positive number of two-leg pairs")
    for row in raw:
        leg = BdLegEvidence.model_validate(row)
        if leg.leg in indexed:
            raise ValueError(f"duplicate bd evidence for leg {leg.leg}")
        if leg.leg >= runs or leg.role != ROLES[leg.leg % len(ROLES)]:
            raise ValueError(f"leg {leg.leg}: invalid role or position in two-leg pair")
        indexed[leg.leg] = leg
    return indexed


def _bd_component(
    leg: BdLegEvidence | None,
    field: Literal["bd_capture_complete", "bd_recall_complete", "bd_recall_before_action"],
) -> bool | None:
    if leg is None or leg.status != "ok":
        return None
    if field == "bd_recall_before_action" and leg.scoring_version < ACTION_SCORING_SINCE:
        return None
    if getattr(leg, field) is None:
        return None
    if getattr(leg, field):
        return True
    return None if leg.bd_evidence_unknown else False


def _pair_report(indexed: Mapping[int, BdLegEvidence], *, runs: int) -> dict[str, Any]:
    pairs = [(indexed.get(i), indexed.get(i + 1)) for i in range(0, runs, len(ROLES))]
    components = [
        (
            _bd_component(first, "bd_capture_complete"),
            _bd_component(goal, "bd_recall_complete"),
            (
                goal.goal_action_success
                if goal is not None
                and goal.status == "ok"
                and goal.scoring_version >= ACTION_SCORING_SINCE
                else None
            ),
            _bd_component(goal, "bd_recall_before_action"),
        )
        for first, goal in pairs
    ]
    handoffs = [
        False if False in values else True if all(value is True for value in values) else None
        for values in components
    ]
    measured_complete = sum(
        first is not None
        and goal is not None
        and first.status == goal.status == "ok"
        and goal.goal_action_success is not None
        and goal.bd_recall_before_action is not None
        and min(first.scoring_version, goal.scoring_version) >= ACTION_SCORING_SINCE
        and not first.bd_evidence_unknown
        and not goal.bd_evidence_unknown
        for first, goal in pairs
    )
    unknown = sum(outcome is None for outcome in handoffs)
    return {
        "scheduled": len(pairs),
        "complete": len(pairs) - unknown,
        "unknown": unknown,
        "measurement_incomplete": len(pairs) - measured_complete,
        "bd_capture_complete": sum(values[0] is True for values in components),
        "bd_recall_complete": sum(values[1] is True for values in components),
        "goal_action_success": sum(values[2] is True for values in components),
        "bd_handoff_observed": sum(outcome is True for outcome in handoffs),
    }


def _bounds(pairs: Mapping[str, Any]) -> list[float]:
    return [
        pairs["bd_handoff_observed"] / pairs["scheduled"],
        (pairs["bd_handoff_observed"] + pairs["unknown"]) / pairs["scheduled"],
    ]


def _task_report(row: Mapping[str, Any]) -> dict[str, Any]:
    plan = tuple(str(role) for role in row.get("leg_plan", ROLES))
    if plan != ROLES:
        raise ValueError(
            f"bd reliability reports pairs; cell {row.get('rung')}/{row.get('variant')}/"
            f"{row.get('work_id')} ran the plan {plan!r}"
        )
    runs = int(row["metrics"]["runs"])
    indexed = _index_legs(row.get("bd_evidence", ()), runs=runs)
    return {
        "rung": row["rung"],
        "variant": row["variant"],
        "work_id": row["work_id"],
        "native_memory_pinned_off": row["native_memory_pinned_off"],
        "evidence_versions": sorted({leg.scoring_version for leg in indexed.values()}),
        "roles": {
            role: _role_report(
                [leg for leg in indexed.values() if leg.role == role], scheduled=runs // len(ROLES)
            )
            for role in ROLES
        },
        "pairs": _pair_report(indexed, runs=runs),
    }


def _group_reports(tasks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = sorted({(task["rung"], task["variant"]) for task in tasks})
    groups: list[dict[str, Any]] = []
    for rung, variant in keys:
        selected = [task for task in tasks if (task["rung"], task["variant"]) == (rung, variant)]
        pins = {task["native_memory_pinned_off"] for task in selected}
        if len(pins) != 1:
            raise ValueError(f"{rung}/{variant}: mixed native memory settings")
        pairs = {key: sum(task["pairs"][key] for task in selected) for key in selected[0]["pairs"]}
        groups.append(
            {
                "rung": rung,
                "variant": variant,
                "tasks": len(selected),
                "native_memory_pinned_off": selected[0]["native_memory_pinned_off"],
                "roles": {
                    role: {
                        key: sum(task["roles"][role][key] for task in selected)
                        for key in selected[0]["roles"][role]
                    }
                    for role in ROLES
                },
                "pairs": {**pairs, "rate_bounds": _bounds(pairs)},
            }
        )
    return groups


def reliability_report(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate within task/repeat first; legacy cells never acquire invented outcomes."""
    keys = [(row["rung"], row["variant"], row["work_id"]) for row in cells]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate cell in bd reliability evidence")
    evidenced = sum(bool(row.get("bd_evidence")) for row in cells)
    tasks = [_task_report(row) for row in cells] if evidenced else []
    groups = _group_reports(tasks)
    return {
        "version": RELIABILITY_VERSION,
        "evidence_versions": sorted(
            {version for task in tasks for version in task["evidence_versions"]}
        ),
        "endpoint": "bd_handoff_observed",
        "primary_variant": "necessary",
        "status": (
            "measured"
            if tasks and all(task["pairs"]["measurement_incomplete"] == 0 for task in tasks)
            else "partial" if tasks else "unmeasured"
        ),
        "cells_without_evidence": len(cells) - evidenced,
        "same_native_settings_across_rungs": (
            len({group["native_memory_pinned_off"] for group in groups}) <= 1 if groups else None
        ),
        "groups": groups,
        "per_task": [
            {**task, "pairs": {**task["pairs"], "rate_bounds": _bounds(task["pairs"])}}
            for task in tasks
        ],
        "interpretation": (
            "Capture all required values in acknowledged bd writes during establish, receive all "
            "values in authenticated bd output delivered in tool results before completing a goal "
            "config.json Write whose decoded JSON string values carry the required tokens. "
            "This is an observed conjunction, not causal attribution. Bounds assign unknown pairs "
            "failure/success; they are not confidence intervals. Legacy compound shell reads "
            "remain unattributed. Complete counts decidable handoff outcomes; "
            "Unversioned cached action/order scores remain unknown until rescored; "
            "measurement_incomplete "
            "retains receipt/leg gaps even when an outcome is already known. Task rows "
            "support paired, task-clustered analysis; repeat legs are not "
            "independent tasks. Native answered reads only mean the tool returned without an error."
        ),
    }


# --------------------------------------------------------------------------- #
# Four-leg trial verdicts (establish, revise, goal, stale_writer)
# --------------------------------------------------------------------------- #

TRIAL_VERDICTS: tuple[str, ...] = (
    "capture_superseded",
    "revision",
    "revision_captured_current",
    "retrieval_current_only",
    "retrieval_states_superseded",
    "goal_action_success",
    "stale_write",
    "after_rejection",
)
# The verdicts that name a category rather than answer yes/no.
TRIAL_CATEGORICAL: tuple[str, ...] = ("revision", "stale_write", "after_rejection")

_ACCEPTED: frozenset[str] = frozenset({"remembered", "updated"})


def _observed_leg(
    by_role: Mapping[str, BdLegEvidence], role: str, *, since: int
) -> BdLegEvidence | None:
    """The leg for ``role`` when it ran to completion under a scorer that recorded what the
    verdict reads; ``None`` (unknown) otherwise."""
    leg = by_role.get(role)
    if leg is None:
        return None
    if leg.role != role:
        raise ValueError(f"trial leg filed under role {role!r} was scored as {leg.role!r}")
    if leg.status != "ok" or leg.scoring_version < since:
        return None
    return leg


def _writes(leg: BdLegEvidence) -> list[BdCallObservation]:
    """The leg's write ATTEMPTS: a bare-existing-key ``remember`` that bd answered as a read is
    not one."""
    return [call for call in leg.bd_calls if call.verb == "remember" and call.outcome != "recalled"]


def _placement(call: BdCallObservation) -> str:
    return "updated_in_place" if call.outcome == "updated" else "wrote_beside"


def _revision(leg: BdLegEvidence | None) -> str | None:
    """How the REVISE leg recorded the current version, off its first accepted write that states
    a current value."""
    if leg is None:
        return None
    writes = _writes(leg)
    accepted = [call for call in writes if call.outcome in _ACCEPTED]
    for call in accepted:
        if call.current_values:
            return _placement(call)
    if accepted:
        return "wrote_without_current"
    if any(call.outcome == "unattributed" for call in writes):
        return None
    return "no_write"


def _stale_write(leg: BdLegEvidence | None) -> str | None:
    """What became of the STALE WRITER's first write attempt."""
    if leg is None:
        return None
    writes = _writes(leg)
    if not writes:
        return "no_write"
    first = writes[0]
    if first.outcome == "unattributed":
        return None
    if first.outcome == "refused":
        return "rejected"
    if first.outcome == "unacknowledged":
        return "unacknowledged"
    return _placement(first) if first.superseded_values else "wrote_without_superseded"


def _after_rejection(leg: BdLegEvidence | None) -> str | None:
    """After a REJECTED first write: did the agent read before writing again (``reread``), write
    again without reading (``retried``), or make no further memory call (``stopped``)?"""
    if leg is None or _stale_write(leg) != "rejected":
        return None
    rejected = _writes(leg)[0]
    for call in leg.bd_calls:
        if call.position <= rejected.position:
            continue
        if call.verb in ("recall", "memories") or call.outcome == "recalled":
            return "reread"
        if call.verb == "remember":
            return "retried"
    return "stopped"


def trial_outcomes(by_role: Mapping[str, BdLegEvidence]) -> dict[str, Any]:
    """The four-leg trial's verdicts, read mechanically off the per-leg observations.

    Every verdict is ``None`` (unknown) when the leg it reads is missing, did not finish, or was
    scored before its evidence existed; a missing verdict is never a failure. Refuses a leg
    filed under a role it was not scored as."""
    establish = _observed_leg(by_role, "establish", since=CALL_OBSERVATION_SINCE)
    revise = _observed_leg(by_role, "revise", since=CALL_OBSERVATION_SINCE)
    revise_any = _observed_leg(by_role, "revise", since=0)
    goal = _observed_leg(by_role, "goal", since=CALL_OBSERVATION_SINCE)
    goal_action = _observed_leg(by_role, "goal", since=ACTION_SCORING_SINCE)
    stale_writer = _observed_leg(by_role, "stale_writer", since=CALL_OBSERVATION_SINCE)
    return {
        "capture_superseded": (
            None
            if establish is None
            else any(
                call.outcome in _ACCEPTED and call.superseded_values for call in _writes(establish)
            )
        ),
        "revision": _revision(revise),
        "revision_captured_current": None if revise_any is None else revise_any.bd_capture_complete,
        "retrieval_current_only": (
            None
            if goal is None
            else goal.bd_recall_complete and not goal.bd_recall_states_superseded
        ),
        "retrieval_states_superseded": None if goal is None else goal.bd_recall_states_superseded,
        "goal_action_success": None if goal_action is None else goal_action.goal_action_success,
        "stale_write": _stale_write(stale_writer),
        "after_rejection": _after_rejection(stale_writer),
    }
