"""The uniform memory-system interface.

The harness drives every system identically: it owns the record set, the scope, and
telemetry; the system only implements `retrieve` and `write` and reports each as a
normalized `MemoryEvent` (§6.2). This is the same uniform-arm contract promoted as
ARCHITECTURE.md Decision 11.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from membench.runtime import StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryEvent


@dataclass
class RetrieveResult:
    """What a retrieve call returns: the recovered payloads + the normalized event."""

    payloads: dict[str, str]  # memory_id → content
    event: MemoryEvent
    distractor_ids: list[str] = field(default_factory=list)


class MemorySystem(ABC):
    """Uniform interface implemented by every reference / competitive system."""

    name: str
    backend: MemoryBackend
    supports_write: bool = True

    @abstractmethod
    def reset(self, trial_id: str) -> None:
        """Clear all state for a fresh trial (per condition run)."""

    @abstractmethod
    def retrieve(
        self, query: str | None, requested_ids: list[str], ctx: StepContext
    ) -> RetrieveResult:
        """Return payloads for `requested_ids` that the store can recover."""

    @abstractmethod
    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent:
        """Persist a memory; returns the normalized write event."""
