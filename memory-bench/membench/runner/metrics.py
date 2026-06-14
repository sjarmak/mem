"""Deterministic metric assembly for one step trial (§12).

This module owns the trial-level wiring: it pulls the mechanical inputs out of the
agent result + retrieve result + write events and hands them to the pure scorers in
`membench.metrics.scorers`, which do the arithmetic. Privacy/interruption groups and
the judge seams (rubric_score, completion_quality, action-impact, derailment
magnitude) are left at their model defaults — they are populated by the privacy
phase / the LLM judge, not here (ZFC boundary, plan §A DIV-4).
"""

from membench.memory_systems.base import RetrieveResult
from membench.metrics.scorers import (
    RetentionInputs,
    RetrievalInputs,
    SynthesisInputs,
    score_efficiency,
    score_retention,
    score_retrieval,
    score_synthesis,
)
from membench.runner.agent import AgentStepResult
from membench.schemas.memory_event import MemoryEvent
from membench.schemas.metrics import MetricsBundle, TaskMetrics
from membench.schemas.sequence import SequenceStep


def _ratio(num: int, denom: int) -> float:
    return num / denom if denom else 0.0


def _supporting_required_ids(step: SequenceStep) -> list[str]:
    """Memories the step depends on for synthesis = probe targets + check requirements.

    Deduplicated, order-preserving. A step with no probes/required checks reports
    zero supporting memories (synthesis is trivially satisfied)."""
    ids: list[str] = []
    seen: set[str] = set()
    for probe in step.memory_probes:
        if probe.expected_memory_id not in seen:
            seen.add(probe.expected_memory_id)
            ids.append(probe.expected_memory_id)
    for check in step.outcome_checks:
        for mid in check.requires_memory:
            if mid not in seen:
                seen.add(mid)
                ids.append(mid)
    return ids


def compute_metrics(
    step: SequenceStep,
    agent_result: AgentStepResult,
    retrieve: RetrieveResult | None,
    write_events: list[MemoryEvent],
    *,
    reads_enabled: bool,
    stale_ids: list[str] | None = None,
) -> MetricsBundle:
    checks = agent_result.check_results
    passed = sum(checks.values())
    reward = _ratio(passed, len(checks))
    all_passed = passed == len(checks) and len(checks) > 0
    task = TaskMetrics(
        reward=reward,
        **{"pass": all_passed},
        final_goal_success=all_passed,
        verifier_errors=[cid for cid, ok in checks.items() if not ok],
    )

    memory_events: list[MemoryEvent] = []
    if retrieve is not None:
        memory_events.append(retrieve.event)
    memory_events.extend(write_events)

    efficiency = score_efficiency(
        input_tokens=agent_result.input_tokens,
        output_tokens=agent_result.output_tokens,
        non_memory_tool_calls=len(agent_result.tool_calls),
        memory_events=memory_events,
        non_memory_tool_latency_ms=sum(tc.latency_ms for tc in agent_result.tool_calls),
        turns=agent_result.turns,
    )

    # Ordered retrieved ids carry rank; fall back to payload keys if the event
    # did not record an explicit order (both preserve insertion order).
    retrieved_ids = (
        retrieve.event.retrieved_ids or list(retrieve.payloads) if retrieve is not None else []
    )
    retrieval = score_retrieval(
        RetrievalInputs(
            retrieved_ids=list(retrieved_ids),
            required_ids=list(step.expected_memory_reads),
            distractor_ids=retrieve.distractor_ids if retrieve is not None else [],
            stale_ids=list(stale_ids or []),
            read_attempted=reads_enabled,
        )
    )

    written_ids = [mid for ev in write_events for mid in ev.written_ids]
    retention = score_retention(
        RetentionInputs(
            written_ids=written_ids,
            expected_writes=list(step.expected_memory_writes),
        )
    )

    available_ids = list(retrieve.payloads) if retrieve is not None else []
    synthesis = score_synthesis(
        SynthesisInputs(
            supporting_required_ids=_supporting_required_ids(step),
            available_ids=available_ids,
        )
    )

    return MetricsBundle(
        task=task,
        efficiency=efficiency,
        retrieval=retrieval,
        retention=retention,
        synthesis=synthesis,
    )
