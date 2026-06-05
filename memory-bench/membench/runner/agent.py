"""The agent interface + a deterministic reference agent for the skeleton.

The real agent-under-test is Claude Code / Opus / Sonnet / Haiku, run on our Claude
account (OAuth subscription) through Harbor (plan §A, DIV-1) — that path is the
`environment/` + `task.toml` emitted by the Harbor adapter and is exercised by
`harbor run`, not in-process.

`ScriptedAgent` is the in-process reference agent that makes the skeleton runnable
and its pipeline testable WITHOUT Docker or any paid API: its outcome is a
deterministic function of which required memory the harness surfaced to it. It is
test/reference infrastructure, not a stand-in for production agent logic.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep
from membench.schemas.trace import ToolCall, TraceMessage


@dataclass
class AgentStepResult:
    final_answer: str
    check_results: dict[str, bool]  # check_id → passed
    writes_performed: dict[str, str]  # memory_id → content the agent chose to persist
    messages: list[TraceMessage] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 1


@runtime_checkable
class Agent(Protocol):
    agent_config_id: str

    def run_step(
        self,
        step: SequenceStep,
        available_memory: dict[str, str],
        ctx: StepContext,
    ) -> AgentStepResult: ...


class ScriptedAgent:
    """Deterministic reference agent.

    A step's outcome check passes iff every memory id the check requires is present
    in `available_memory` (what the harness surfaced this step). Checks that require
    no memory pass statelessly. The agent persists the step's
    `expected_memory_writes` — its (skeleton) write decision is write-all; a learned
    write policy replaces this later.
    """

    def __init__(self, agent_config_id: str = "scripted-ref") -> None:
        self.agent_config_id = agent_config_id

    def run_step(
        self,
        step: SequenceStep,
        available_memory: dict[str, str],
        ctx: StepContext,
    ) -> AgentStepResult:
        have = set(available_memory)
        check_results: dict[str, bool] = {}
        for check in step.outcome_checks:
            check_results[check.check_id] = set(check.requires_memory).issubset(have)

        messages = [
            TraceMessage(role="user", content=step.user_request),
            TraceMessage(
                role="assistant",
                content=f"step {step.step_id}: used {len(have)} memories",
            ),
        ]
        tool_calls = [
            ToolCall(name=t, arguments={}, latency_ms=ctx.clock.latency_ms())
            for t in step.available_tools
        ]
        passed = sum(check_results.values())
        final_answer = f"{passed}/{len(check_results)} checks satisfied"
        # Deterministic token accounting from payload sizes (no model call).
        input_tokens = len(step.user_request.split()) + sum(
            len(v.split()) for v in available_memory.values()
        )
        output_tokens = len(final_answer.split())
        return AgentStepResult(
            final_answer=final_answer,
            check_results=check_results,
            writes_performed=dict(step.expected_memory_writes),
            messages=messages,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
