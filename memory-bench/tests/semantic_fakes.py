"""Deterministic in-memory ``SemanticMemoryClient`` for arm tests — no network,
no model. Scoring is token overlap so retrieval *order* is exercised without
embeddings; shared by every competitive-arm test (mem-lvp.2-.4)."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from membench.memory_systems.semantic_base import SemanticHit


def _tokens(text: str) -> set[str]:
    return set("".join(c.lower() if c.isalnum() else " " for c in text).split())


@dataclass
class FakeSemanticClient:
    """In-memory store. ``search`` ranks by query/content token overlap descending,
    ties broken by ``memory_id`` for determinism, dropping zero-overlap items, and
    returns at most ``top_k`` hits."""

    store: dict[str, str] = field(default_factory=dict)

    def add(self, memory_id: str, content: str) -> None:
        self.store[memory_id] = content

    def search(self, query: str, top_k: int) -> Sequence[SemanticHit]:
        q = _tokens(query)
        scored = [
            SemanticHit(memory_id=mid, content=content, score=float(overlap))
            for mid, content in self.store.items()
            if (overlap := len(q & _tokens(content))) > 0
        ]
        scored.sort(key=lambda hit: (-hit.score, hit.memory_id))
        return scored[:top_k]

    def reset(self) -> None:
        self.store = {}
