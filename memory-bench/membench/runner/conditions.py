"""Run one benchmark sequence under the three conditions (§4, §15 MVP).

Each step starts with fresh agent context; the memory store is the only continuity
channel across steps (except oracle, which is injected ground truth). Reads happen
before the agent acts; writes are persisted after — so a step can only read what
earlier steps wrote.
"""

from dataclasses import dataclass, field
from pathlib import Path

from membench.memory_systems import build_memory_system
from membench.memory_systems.base import MemorySystem, RetrieveResult
from membench.memory_systems.oracle_system import OracleMemory
from membench.runner.agent import Agent, ScriptedAgent
from membench.runner.metrics import compute_metrics
from membench.runtime import IdClock, StepContext
from membench.schemas.conditions import Condition
from membench.schemas.config import ExperimentConfig
from membench.schemas.memory_event import MemoryEvent
from membench.schemas.metrics import MetricsBundle
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.trace import Trace


@dataclass
class StepTrial:
    """One (step × condition) trial — mirrors §14 harbor_trial_output + telemetry."""

    trial_id: str
    sequence_id: str
    step_id: str
    condition: Condition
    agent_config_id: str
    memory_config_id: str
    reward: float
    trace: Trace
    metrics: MetricsBundle


@dataclass
class SequenceRun:
    sequence_id: str
    experiment_id: str
    trials: list[StepTrial] = field(default_factory=list)

    def by_condition(self) -> dict[Condition, list[StepTrial]]:
        out: dict[Condition, list[StepTrial]] = {}
        for t in self.trials:
            out.setdefault(t.condition, []).append(t)
        return out


def _oracle_pool(seq: BenchmarkSequence) -> dict[str, str]:
    """Ground-truth memory = every memory any step establishes."""
    pool: dict[str, str] = {}
    for step in seq.steps:
        pool.update(step.expected_memory_writes)
    return pool


def _system_for(
    condition: Condition, experiment: ExperimentConfig, fs_base_dir: Path | None
) -> tuple[MemorySystem, str]:
    if condition is Condition.NO_MEMORY:
        return build_memory_system("none"), "none"
    if condition is Condition.ORACLE_MEMORY:
        return build_memory_system("oracle"), "oracle"
    # memory_enabled → the configured system under test (skeleton: filesystem).
    kwargs = {}
    if experiment.memory.system == "filesystem" and fs_base_dir is not None:
        kwargs["base_dir"] = fs_base_dir
    return (
        build_memory_system(experiment.memory.system, **kwargs),
        experiment.memory.memory_config_id,
    )


def run_sequence(
    seq: BenchmarkSequence,
    experiment: ExperimentConfig,
    agent: Agent | None = None,
    *,
    conditions: list[Condition] | None = None,
    fs_base_dir: str | Path | None = None,
) -> SequenceRun:
    agent = agent or ScriptedAgent()
    conditions = conditions or experiment.conditions
    base = Path(fs_base_dir) if fs_base_dir is not None else None
    run = SequenceRun(sequence_id=seq.sequence_id, experiment_id=experiment.experiment_id)

    for condition in conditions:
        system, memory_config_id = _system_for(condition, experiment, base)
        condition_root = f"{seq.sequence_id}-{condition.value}"
        system.reset(condition_root)
        if isinstance(system, OracleMemory):
            system.load(_oracle_pool(seq))

        for step in seq.steps:
            trial_id = f"{condition_root}-{step.step_id}"
            clock = IdClock()
            ctx = StepContext(
                trial_id=trial_id, session_id=condition_root, step_id=step.step_id, clock=clock
            )
            reads_enabled = condition is not Condition.NO_MEMORY

            retrieve: RetrieveResult | None = None
            memory_events: list[MemoryEvent] = []
            # Only issue a retrieve when the step actually depends on prior memory;
            # a retrieve with no requested ids would emit a phantom event and inflate
            # memory_tool_calls for establishing steps.
            if reads_enabled and step.expected_memory_reads:
                retrieve = system.retrieve(step.user_request, step.expected_memory_reads, ctx)
                memory_events.append(retrieve.event)
            available_memory = retrieve.payloads if retrieve is not None else {}

            agent_result = agent.run_step(step, available_memory, ctx)

            write_events: list[MemoryEvent] = []
            if condition is Condition.MEMORY_ENABLED and system.supports_write:
                for mid, content in agent_result.writes_performed.items():
                    write_events.append(system.write(mid, content, ctx))
            memory_events.extend(write_events)

            metrics = compute_metrics(
                step, agent_result, retrieve, write_events, reads_enabled=reads_enabled
            )
            files_read = list(available_memory) if condition is Condition.MEMORY_ENABLED else []
            files_written = [mid for ev in write_events for mid in ev.written_ids]
            trace = Trace(
                trial_id=trial_id,
                experiment_id=experiment.experiment_id,
                dataset_id=experiment.dataset_id,
                task_id=f"{seq.sequence_id}/{step.step_id}",
                step_id=step.step_id,
                agent_config_id=agent.agent_config_id,
                memory_config_id=memory_config_id,
                start_time=clock.timestamp(),
                end_time=clock.timestamp(),
                messages=agent_result.messages,
                tool_calls=agent_result.tool_calls,
                memory_events=memory_events,
                files_read=files_read,
                files_written=files_written,
                errors=agent_result.errors,
                final_answer=agent_result.final_answer,
                verifier_result={"reward": metrics.task.reward},
            )
            run.trials.append(
                StepTrial(
                    trial_id=trial_id,
                    sequence_id=seq.sequence_id,
                    step_id=step.step_id,
                    condition=condition,
                    agent_config_id=agent.agent_config_id,
                    memory_config_id=memory_config_id,
                    reward=metrics.task.reward,
                    trace=trace,
                    metrics=metrics,
                )
            )
    return run
