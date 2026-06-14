"""Deterministic in-memory ``SemanticMemoryClient`` for arm tests — no network,
no model. Scoring is token overlap so retrieval *order* is exercised without
embeddings; shared by every competitive-arm test (mem-lvp.2-.4a).

It faithfully models the two traits that bit the real backends: it mints its OWN
id on ``store`` (ignoring the caller's ``memory_id``, like mem0/A-MEM/Graphiti),
and it isolates by ``scope`` so a trial only ever sees its own writes."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from membench.memory_systems.semantic_base import SemanticHit


def _tokens(text: str) -> set[str]:
    return set("".join(c.lower() if c.isalnum() else " " for c in text).split())


@dataclass
class FakeSemanticClient:
    """In-memory, scope-isolated store. ``store`` mints a deterministic per-scope id
    (``<scope>-<n>``) and returns it. ``query`` ranks a scope's items by query/content
    token overlap descending, ties broken by minted id, dropping zero-overlap items,
    and returns at most ``top_k``. ``clear`` drops one scope only."""

    # scope -> {minted_id: content}
    scopes: dict[str, dict[str, str]] = field(default_factory=dict)
    _counter: int = 0

    def store(self, *, scope: str, content: str, memory_id: str) -> str:
        self._counter += 1
        minted = f"{scope}-{self._counter}"
        self.scopes.setdefault(scope, {})[minted] = content
        return minted

    def query(self, *, scope: str, query_text: str, top_k: int) -> Sequence[SemanticHit]:
        q = _tokens(query_text)
        items = self.scopes.get(scope, {})
        scored = [
            SemanticHit(memory_id=mid, content=content, score=float(overlap))
            for mid, content in items.items()
            if (overlap := len(q & _tokens(content))) > 0
        ]
        scored.sort(key=lambda hit: (-(hit.score or 0.0), hit.memory_id))
        return scored[:top_k]

    def clear(self, *, scope: str) -> None:
        self.scopes.pop(scope, None)
