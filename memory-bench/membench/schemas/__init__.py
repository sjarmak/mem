"""Pydantic schemas mirroring the eval-harness spec.

Each module maps to a spec section:
  - conditions      → §4 evaluation conditions
  - config          → §3 system model (experiment / agent / memory)
  - sequence        → §9.2 benchmark sequence + step
  - memory_event    → §6.2 normalized memory operations + memory_event
  - candidate_memory→ §8 extractor output (schema only; extractor is a later phase)
  - trace           → §8 trace capture
  - metrics         → §12 metric groups (task/efficiency/retrieval/retention now;
                      privacy/interruption stubbed per plan §A DIV-4)
"""

from membench.schemas.candidate_memory import CandidateMemory, MemoryType, RetentionPolicy
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation
from membench.schemas.metrics import (
    EfficiencyMetrics,
    InterruptionMetrics,
    MetricsBundle,
    PrivacyMetrics,
    RetentionMetrics,
    RetrievalMetrics,
    TaskMetrics,
)
from membench.schemas.sequence import BenchmarkSequence, SequenceStep
from membench.schemas.trace import ToolCall, Trace, TraceMessage

__all__ = [
    "AgentConfig",
    "BenchmarkSequence",
    "CandidateMemory",
    "Condition",
    "EfficiencyMetrics",
    "ExperimentConfig",
    "InterruptionMetrics",
    "MemoryBackend",
    "MemoryConfig",
    "MemoryEvent",
    "MemoryOperation",
    "MemoryType",
    "MetricsBundle",
    "PrivacyMetrics",
    "RetentionMetrics",
    "RetentionPolicy",
    "RetrievalMetrics",
    "SequenceStep",
    "TaskMetrics",
    "ToolCall",
    "Trace",
    "TraceMessage",
]
