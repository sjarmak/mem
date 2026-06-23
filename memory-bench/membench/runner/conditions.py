"""Run one benchmark sequence under the three conditions (§4, §15 MVP).

Each step starts with fresh agent context; the memory store is the only continuity
channel across steps (except oracle, which is injected ground truth). Reads happen
before the agent acts; writes are persisted after — so a step can only read what
earlier steps wrote.
"""

from dataclasses import dataclass, field
from pathlib import Path

from membench.memory_systems import build_memory_system
from membench.memory_systems.base import MemorySystem, RetrievalRequest, RetrieveResult
from membench.memory_systems.consolidation import (
    Classifiable,
    ConsolidationCapable,
    ConsolidationResult,
)
from membench.memory_systems.oracle_system import OracleMemory
from membench.runner.agent import Agent, ScriptedAgent
from membench.runner.metrics import compute_metrics
from membench.runtime import IdClock, StepContext
from membench.schemas.conditions import Condition
from membench.schemas.config import ExperimentConfig
from membench.schemas.memory_event import MemoryEvent
from membench.schemas.metrics import MetricsBundle
from membench.schemas.sequence import BenchmarkSequence, SequenceStep
from membench.schemas.trace import Trace


@dataclass
class StepTrial:
    """One (step x condition) trial — mirrors §14 harbor_trial_output + telemetry."""

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
    # The offline consolidation pass per condition (S1). Keyed by Condition.value;
    # populated only for a ConsolidationCapable arm under MEMORY_ENABLED, empty
    # otherwise — so a non-consolidating run carries an honest absence, not a stub.
    consolidations: dict[str, ConsolidationResult] = field(default_factory=dict)

    def by_condition(self) -> dict[Condition, list[StepTrial]]:
        out: dict[Condition, list[StepTrial]] = {}
        for t in self.trials:
            out.setdefault(t.condition, []).append(t)
        return out


def _oracle_pool(seq: BenchmarkSequence) -> dict[str, str]:
    """Ground-truth memory = every memory any step establishes.

    A memory_id written by two steps with DIFFERENT content is ambiguous: the
    pool is loaded once per condition, so last-write-wins would silently hand
    early steps the later content (a within-sequence future leak). Until the
    oracle is supersession-aware, that is a sequence-design error and raises.
    Identical re-writes are harmless and allowed."""
    pool: dict[str, str] = {}
    writers: dict[str, str] = {}
    for step in seq.steps:
        for memory_id, content in step.expected_memory_writes.items():
            existing = pool.get(memory_id)
            if existing is not None and existing != content:
                raise ValueError(
                    f"oracle pool conflict: memory_id {memory_id!r} is written with "
                    f"different content by steps {writers[memory_id]!r} and "
                    f"{step.step_id!r}; use distinct ids (or identical content)"
                )
            pool[memory_id] = content
            writers.setdefault(memory_id, step.step_id)
    return pool


def _assert_superseded_written(seq: BenchmarkSequence) -> None:
    """Every id a step marks ``superseded`` must be a real write by an EARLIER step.

    Staleness is scored by whether retrieval surfaces a superseded (v1) id, which can only
    happen if that id was already in the store when the step ran — the runner does NOT
    separately seed stale ids (unlike distractors), it relies on the establishing step's
    write. Prior-ordering is the real contract: an id written only by a later (or the same)
    step is not in the store at retrieval time, so the stale signal would silently read 0.
    Fail fast on either gap (mirrors ``_oracle_pool``'s raise) — this is exactly the
    invariant the enterprise materialiser guarantees (v1 written before v2)."""
    first_write: dict[str, int] = {}
    for idx, step in enumerate(seq.steps):
        for mid in step.expected_memory_writes:
            first_write.setdefault(mid, idx)
    for idx, step in enumerate(seq.steps):
        for mid in step.superseded_memory_ids:
            written_at = first_write.get(mid)
            if written_at is None or written_at >= idx:
                where = (
                    "is never written by any step"
                    if written_at is None
                    else (f"is first written at step index {written_at} (not BEFORE)")
                )
                raise ValueError(
                    f"superseded id {mid!r} (step {step.step_id!r}, index {idx}) {where} "
                    f"in sequence {seq.sequence_id!r}; supersession must mark a real prior "
                    "write so the staleness signal is retrievable"
                )


def _system_for(
    condition: Condition,
    experiment: ExperimentConfig,
    fs_base_dir: Path | None,
    override: MemorySystem | None = None,
) -> tuple[MemorySystem, str]:
    if condition is Condition.NO_MEMORY:
        return build_memory_system("none"), "none"
    if condition is Condition.ORACLE_MEMORY:
        return build_memory_system("oracle"), "oracle"
    # memory_enabled → the system under test. A caller-supplied instance wins (the
    # injection seam for arms the factory can't build with config alone, e.g. a
    # ConsolidatingMemory with an injected summarizer); else build from config.
    if override is not None:
        return override, getattr(override, "name", experiment.memory.memory_config_id)
    kwargs = {}
    if experiment.memory.system == "filesystem" and fs_base_dir is not None:
        kwargs["base_dir"] = fs_base_dir
    return (
        build_memory_system(experiment.memory.system, **kwargs),
        experiment.memory.memory_config_id,
    )


def _execute_step(
    *,
    seq_id: str,
    step: SequenceStep,
    system: MemorySystem,
    condition: Condition,
    scope: str,
    memory_config_id: str,
    experiment: ExperimentConfig,
    agent: Agent,
) -> StepTrial:
    """Run one step against ``system`` under ``condition``, returning its trial.

    ``scope`` is the memory scope + session id — the per-sequence ``condition_root``
    for ``run_sequence`` or the per-project root for ``run_project``. Using one
    scope across sequences is exactly what lets a later task read what an earlier
    task wrote (cross-task continuity). Behaviour is identical to the inline loop
    this was extracted from; ``run_sequence``'s tests pin that."""
    trial_id = f"{scope}-{step.step_id}"
    clock = IdClock()
    ctx = StepContext(trial_id=trial_id, session_id=scope, step_id=step.step_id, clock=clock)
    reads_enabled = condition is not Condition.NO_MEMORY

    # Seed the step's distractor memories (§10 interference) into the store BEFORE the
    # retrieve, so a query/top-k arm surfaces them as competitors (Confusion, mem-zt1c).
    # Only under the write-bearing condition: oracle is load()-injected and short-circuits
    # via supports_write, none never reads. The dedicated clock + discarded events keep
    # seeding off the trial's telemetry (env state the harness owns, not an agent write).
    # The stale v1 needs no seeding — an earlier step already wrote it as a real memory
    # that persists in scope (asserted by _assert_superseded_written).
    if condition is Condition.MEMORY_ENABLED and step.distractor_memories:
        seed_ctx = StepContext(
            trial_id=trial_id, session_id=scope, step_id=step.step_id, clock=IdClock()
        )
        # seed() iterates read-only; no defensive copy needed.
        system.seed(step.distractor_memories, seed_ctx)

    retrieve: RetrieveResult | None = None
    memory_events: list[MemoryEvent] = []
    # Only issue a retrieve when the step actually depends on prior memory; a
    # retrieve with no requested ids would emit a phantom event and inflate
    # memory_tool_calls for establishing steps.
    if reads_enabled and step.expected_memory_reads:
        request = RetrievalRequest(
            query_text=step.user_request,
            requested_ids=step.expected_memory_reads,
        )
        retrieve = system.retrieve(request, ctx)
        memory_events.append(retrieve.event)
    available_memory = retrieve.payloads if retrieve is not None else {}

    # Fail fast WITH context. A crashed step is deliberately not captured as trial
    # data: a partially-run sequence yields incomparable condition gaps, and a
    # silent partial result is worse than a loud abort.
    try:
        agent_result = agent.run_step(step, available_memory, ctx)
    except Exception as exc:
        raise RuntimeError(
            f"agent failed at condition {condition.value!r}, "
            f"step {step.step_id!r} (trial {trial_id})"
        ) from exc

    write_events: list[MemoryEvent] = []
    if condition is Condition.MEMORY_ENABLED and system.supports_write:
        # A consolidation arm takes a retention CLASS per record at write; assigning the
        # step's ``record_class`` here is what lets the offline sweep act on it (§10.C
        # consolidation factor). A step may write several ids (current + a stale v1), so the
        # class is applied to EACH written id. Non-classifying arms and class-free steps are
        # left untouched (backward compat) — the gate is the Classifiable Protocol + a non-None
        # class, never a default that silently labels records the sequence did not label.
        record_class = step.record_class
        for mid, content in agent_result.writes_performed.items():
            write_events.append(system.write(mid, content, ctx))
            if record_class is not None and isinstance(system, Classifiable):
                system.assign_class(mid, record_class)
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
        task_id=f"{seq_id}/{step.step_id}",
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
    return StepTrial(
        trial_id=trial_id,
        sequence_id=seq_id,
        step_id=step.step_id,
        condition=condition,
        agent_config_id=agent.agent_config_id,
        memory_config_id=memory_config_id,
        reward=metrics.task.reward,
        trace=trace,
        metrics=metrics,
    )


def run_sequence(
    seq: BenchmarkSequence,
    experiment: ExperimentConfig,
    agent: Agent | None = None,
    *,
    conditions: list[Condition] | None = None,
    fs_base_dir: str | Path | None = None,
    memory_system: MemorySystem | None = None,
) -> SequenceRun:
    agent = agent or ScriptedAgent()
    conditions = conditions or experiment.conditions
    base = Path(fs_base_dir) if fs_base_dir is not None else None
    _assert_superseded_written(seq)
    run = SequenceRun(sequence_id=seq.sequence_id, experiment_id=experiment.experiment_id)

    for condition in conditions:
        system, memory_config_id = _system_for(condition, experiment, base, memory_system)
        condition_root = f"{seq.sequence_id}-{condition.value}"
        system.reset(condition_root)
        if isinstance(system, OracleMemory):
            system.load(_oracle_pool(seq))

        for step in seq.steps:
            run.trials.append(
                _execute_step(
                    seq_id=seq.sequence_id,
                    step=step,
                    system=system,
                    condition=condition,
                    scope=condition_root,
                    memory_config_id=memory_config_id,
                    experiment=experiment,
                    agent=agent,
                )
            )

        # Offline consolidation runs ONCE per condition, after every step's writes
        # are persisted (so it sees the full episode set), and only for an arm that
        # opts into the ConsolidationCapable Protocol under the write-bearing
        # condition. The ClosableClient isinstance pattern keeps the MemorySystem
        # ABC un-widened; non-capable arms record an honest empty consolidation.
        if condition is Condition.MEMORY_ENABLED and isinstance(system, ConsolidationCapable):
            consolidate_ctx = StepContext(
                trial_id=f"{condition_root}-consolidate",
                session_id=condition_root,
                step_id="consolidate",
                clock=IdClock(),
            )
            run.consolidations[condition.value] = system.consolidate(consolidate_ctx)
    return run
