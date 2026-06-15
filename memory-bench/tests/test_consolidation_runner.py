"""S1 lifecycle — ConsolidationCapable dispatch from run_sequence.

The runner calls ``system.consolidate(ctx)`` ONCE, after the per-step write loop,
and only when the arm satisfies the ``ConsolidationCapable`` Protocol (the
ClosableClient isinstance pattern — the MemorySystem ABC is NOT widened). A
non-capable arm is never asked to consolidate. The write() path stays model-free;
the offline consolidate() pass is where any background cost lives.
"""

from __future__ import annotations

from membench.memory_systems.base import MemorySystem, RetrievalRequest, RetrieveResult
from membench.memory_systems.consolidation import (
    ConsolidatedItem,
    ConsolidationCapable,
    ConsolidationResult,
)
from membench.runner.conditions import run_sequence
from membench.runtime import StepContext
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation
from membench.schemas.sequence import BenchmarkSequence, SequenceStep


class _RecordingConsolidator(MemorySystem):
    """Minimal in-memory arm that records when consolidate() was called and what
    had been written by then — enough to prove the dispatch contract."""

    name = "recording-consolidator"
    backend = MemoryBackend.FILESYSTEM
    supports_write = True

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.consolidate_calls = 0
        self.writes_seen_at_consolidate = 0
        self.tombstoned: list[str] = []

    def reset(self, trial_id: str) -> None:
        self._store = {}

    def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
        payloads = {mid: self._store[mid] for mid in request.requested_ids if mid in self._store}
        ev = MemoryEvent(
            event_id=ctx.clock.event_id(),
            trial_id=ctx.trial_id,
            session_id=ctx.session_id,
            step_id=ctx.step_id,
            timestamp=ctx.clock.timestamp(),
            concrete_tool="rec.search",
            normalized_operation=MemoryOperation.SEARCH,
            backend=self.backend,
            retrieved_ids=list(payloads),
        )
        return RetrieveResult(payloads=payloads, event=ev)

    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent:
        self._store[memory_id] = content
        return MemoryEvent(
            event_id=ctx.clock.event_id(),
            trial_id=ctx.trial_id,
            session_id=ctx.session_id,
            step_id=ctx.step_id,
            timestamp=ctx.clock.timestamp(),
            concrete_tool="rec.add",
            normalized_operation=MemoryOperation.WRITE,
            backend=self.backend,
            written_ids=[memory_id],
        )

    def consolidate(self, ctx: StepContext) -> ConsolidationResult:
        self.consolidate_calls += 1
        self.writes_seen_at_consolidate = len(self._store)
        return ConsolidationResult(
            items=(
                ConsolidatedItem(
                    memory_id="schema-1", content="merged", source_trace_ids=("e1", "e2")
                ),
            ),
            tombstoned_ids=("e1", "e2"),
            background_tokens=0,
        )

    def tombstone(self, memory_id: str) -> None:
        self.tombstoned.append(memory_id)


def _experiment():
    return ExperimentConfig(
        experiment_id="exp-consolidate",
        agent=AgentConfig(agent_config_id="scripted-ref", runtime="scripted"),
        memory=MemoryConfig(memory_config_id="recording", system="filesystem"),
        dataset_id="seq-consolidate",
    )


def _two_write_sequence():
    return BenchmarkSequence(
        sequence_id="seq-consolidate",
        title="t",
        steps=[
            SequenceStep(
                step_id="s1", user_request="a", expected_memory_writes={"e1": "alpha one"}
            ),
            SequenceStep(
                step_id="s2", user_request="b", expected_memory_writes={"e2": "alpha two"}
            ),
        ],
    )


def test_protocol_isinstance_recognises_a_capable_arm():
    assert isinstance(_RecordingConsolidator(), ConsolidationCapable)


def test_filesystem_arm_is_not_consolidation_capable():
    from membench.memory_systems.filesystem_system import FilesystemMemory

    assert not isinstance(FilesystemMemory(), ConsolidationCapable)


def test_consolidate_called_once_after_all_writes(tmp_path):
    arm = _RecordingConsolidator()
    run = run_sequence(
        _two_write_sequence(),
        _experiment(),
        conditions=[Condition.MEMORY_ENABLED],
        memory_system=arm,
        fs_base_dir=tmp_path,
    )
    assert arm.consolidate_calls == 1
    # Both writes are visible by the time consolidate runs (after the step loop).
    assert arm.writes_seen_at_consolidate == 2
    assert run.consolidations[Condition.MEMORY_ENABLED.value].items[0].memory_id == "schema-1"


def test_non_capable_arm_records_no_consolidation(tmp_path):
    run = run_sequence(
        _two_write_sequence(),
        _experiment(),
        conditions=[Condition.MEMORY_ENABLED],
        fs_base_dir=tmp_path,
    )
    assert run.consolidations == {}


def test_no_memory_condition_does_not_consolidate(tmp_path):
    arm = _RecordingConsolidator()
    run_sequence(
        _two_write_sequence(),
        _experiment(),
        conditions=[Condition.NO_MEMORY],
        memory_system=arm,
        fs_base_dir=tmp_path,
    )
    assert arm.consolidate_calls == 0
