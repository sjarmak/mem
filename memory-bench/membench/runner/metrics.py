"""Deterministic metric arithmetic for one step trial (§12 core groups).

Pure mechanism (ZFC): counts and ratios over what actually happened, no semantic
judgment. Privacy/interruption groups are left at their stub defaults (plan §A,
DIV-4).
"""

from membench.memory_systems.base import RetrieveResult
from membench.runner.agent import AgentStepResult
from membench.schemas.memory_event import MemoryEvent
from membench.schemas.metrics import (
    EfficiencyMetrics,
    MetricsBundle,
    RetentionMetrics,
    RetrievalMetrics,
    TaskMetrics,
)
from membench.schemas.sequence import SequenceStep


def _ratio(num: int, denom: int) -> float:
    return num / denom if denom else 0.0


def compute_metrics(
    step: SequenceStep,
    agent_result: AgentStepResult,
    retrieve: RetrieveResult | None,
    write_events: list[MemoryEvent],
    *,
    reads_enabled: bool,
) -> MetricsBundle:
    checks = agent_result.check_results
    passed = sum(checks.values())
    reward = _ratio(passed, len(checks))
    task = TaskMetrics(
        reward=reward,
        **{"pass": passed == len(checks) and len(checks) > 0},
        final_goal_success=passed == len(checks) and len(checks) > 0,
        verifier_errors=[cid for cid, ok in checks.items() if not ok],
    )

    mem_calls = (1 if retrieve is not None else 0) + len(write_events)
    efficiency = EfficiencyMetrics(
        input_tokens=agent_result.input_tokens,
        output_tokens=agent_result.output_tokens,
        total_tokens=agent_result.input_tokens + agent_result.output_tokens,
        tool_calls_total=len(agent_result.tool_calls) + mem_calls,
        memory_tool_calls=mem_calls,
        non_memory_tool_calls=len(agent_result.tool_calls),
        turns=agent_result.turns,
    )

    required = list(step.expected_memory_reads)
    retrieved = list(retrieve.payloads) if retrieve is not None else []
    retrieved_relevant = [m for m in retrieved if m in required]
    distractors = retrieve.distractor_ids if retrieve is not None else []
    retrieval = RetrievalMetrics(
        read_attempted=reads_enabled and bool(required),
        # "available" = a required item was actually returned. (A future top-k
        # backend can return distractors while missing every required id, so this
        # must key on the relevant subset, not on "anything came back".)
        relevant_memory_available=bool(retrieved_relevant),
        relevant_memory_retrieved=set(required).issubset(set(retrieved)) and bool(required),
        precision_at_k=_ratio(len(retrieved_relevant), len(retrieved)),
        recall_at_k=_ratio(len(retrieved_relevant), len(required)),
        distractor_retrieval_rate=_ratio(len(distractors), len(retrieved)),
        missed_required_memory_count=len(set(required) - set(retrieved)),
    )

    expected_writes = list(step.expected_memory_writes)
    written = [mid for ev in write_events for mid in ev.written_ids]
    written_expected = [m for m in written if m in expected_writes]
    retention = RetentionMetrics(
        expected_memory_written=bool(expected_writes)
        and set(expected_writes).issubset(set(written)),
        write_hit_rate=_ratio(len(written_expected), len(expected_writes)),
        write_miss_rate=1.0 - _ratio(len(written_expected), len(expected_writes))
        if expected_writes
        else 0.0,
        noise_write_rate=_ratio(len(written) - len(written_expected), len(written)),
    )

    return MetricsBundle(
        task=task,
        efficiency=efficiency,
        retrieval=retrieval,
        retention=retention,
    )
