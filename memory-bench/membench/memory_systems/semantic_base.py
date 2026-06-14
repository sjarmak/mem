"""AbstractSemanticArm — the shared base for embedding/semantic competitive arms.

The competitive systems (mem0 / NAT / A-MEM / Graphiti, mem-lvp.2-.4) are all the
same translation against this harness: ``write`` -> the backend's ``add``,
``retrieve`` over ``request.query_text`` -> the backend's ``search(query, top_k)``,
``reset`` -> the backend's clear. The concrete vector/LLM client is INJECTED behind
``SemanticMemoryClient``, so the arm wiring, event normalization, and payload
shaping are testable against a deterministic fake with no network and no model —
the scix no-paid-API contract holds in CI, and the real (local-model) client plugs
in behind the same seam when the infra (mem-lvp.5) lands.

A concrete arm only sets ``name`` + ``backend`` and is registered in
``build_memory_system``; it adds no retrieval logic of its own. The harness owns the
leave-one-out boundary and re-checks every arm's output with
``validity.assert_no_leak`` — that stays the runner's job, so this base is a pure
translation layer (mirrors ``OursMemory``). See docs/competitive-arms-integration.md.
"""

from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from membench.memory_systems.base import MemorySystem, RetrievalRequest, RetrieveResult
from membench.runtime import StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation

DEFAULT_TOP_K = 10


@dataclass(frozen=True)
class SemanticHit:
    """One search hit from a semantic client: the stored id, its content, and the
    backend's relevance score (higher = closer; the scale is backend-specific and
    used only for ordering, never compared across backends)."""

    memory_id: str
    content: str
    score: float


@runtime_checkable
class SemanticMemoryClient(Protocol):
    """The minimal surface a competitive memory backend must expose. Each arm adapts
    its native SDK to this shape (mem0 ``add``/``search``, NAT
    ``add_memory``/``get_memory``, ...); tests inject a deterministic fake.

    ``search`` returns at most ``top_k`` hits already ranked best-first; the arm does
    no re-ranking, so determinism is the client's responsibility."""

    def add(self, memory_id: str, content: str) -> None: ...

    def search(self, query: str, top_k: int) -> Sequence[SemanticHit]: ...

    def reset(self) -> None: ...


class AbstractSemanticArm(MemorySystem, ABC):
    """Translates the uniform ``MemorySystem`` contract onto an injected
    ``SemanticMemoryClient``. Subclasses set ``name`` + ``backend`` only."""

    name: str = "semantic"
    backend: MemoryBackend = MemoryBackend.VECTOR_DB
    # query_text/top_k semantic path — not the Decision-7 dual-track scope.
    uses_scope = False

    def __init__(self, client: SemanticMemoryClient, *, top_k: int = DEFAULT_TOP_K) -> None:
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k}")
        self._client = client
        self._top_k = top_k

    def reset(self, trial_id: str) -> None:
        self._client.reset()

    def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
        query = request.query_text
        if query is None:
            raise ValueError(
                f"{self.name!r} is a semantic arm: it needs request.query_text (the "
                "query/failure string). It does not serve the failure-triggered "
                "query_work path that `ours` uses."
            )
        hits = list(self._client.search(query, self._top_k))
        # Insertion order = the client's rank order; dict dedupes by id (vector
        # stores key uniquely, so a collision would be a backend bug, not silent loss).
        payloads = {hit.memory_id: hit.content for hit in hits}
        event = MemoryEvent(
            event_id=ctx.clock.event_id(),
            trial_id=ctx.trial_id,
            session_id=ctx.session_id,
            step_id=ctx.step_id,
            timestamp=ctx.clock.timestamp(),
            concrete_tool=f"{self.name}.search(top_k={self._top_k})",
            normalized_operation=MemoryOperation.SEARCH,
            backend=self.backend,
            query=query,
            retrieved_ids=list(payloads),
            latency_ms=ctx.clock.latency_ms(),
            success=True,
        )
        # total_matched is the injected count: a vector top_k arm has no FTS-style
        # uncapped candidate set, so near_duplicate_top / fts_truncated stay default.
        return RetrieveResult(payloads=payloads, event=event, total_matched=len(hits))

    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent:
        self._client.add(memory_id, content)
        return MemoryEvent(
            event_id=ctx.clock.event_id(),
            trial_id=ctx.trial_id,
            session_id=ctx.session_id,
            step_id=ctx.step_id,
            timestamp=ctx.clock.timestamp(),
            concrete_tool=f"{self.name}.add",
            normalized_operation=MemoryOperation.WRITE,
            backend=self.backend,
            target_ids=[memory_id],
            written_ids=[memory_id],
            latency_ms=ctx.clock.latency_ms(),
            success=True,
        )
