"""Tests for the NAT (NeMo Agent Toolkit) arm (mem-lvp.3).

Hermetic: ``_NatClient`` runs against a ``FakeMemoryEditor`` stand-in for
``nat.memory.interfaces.MemoryEditor`` — an ASYNC editor that mints a synthetic
monotone L2 distance and round-trips the caller's ``memory_id`` through
``MemoryItem.metadata`` (the real editor has no native id field). The adapter
drives it through a real ``AsyncClientBridge``, so the async-to-sync seam is
exercised end to end. ``NatMemory`` runs against the shared ``FakeSemanticClient``.
No network, no Redis, no Ollama, no nvidia-nat SDK installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.async_bridge import AsyncClientBridge
from membench.memory_systems.base import MemorySystem, RetrievalRequest
from membench.memory_systems.nat_system import (
    NatMemory,
    _NatClient,
    _similarity,
)
from membench.memory_systems.semantic_base import (
    AbstractSemanticArm,
    SemanticMemoryClient,
)
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryOperation
from tests.semantic_fakes import FakeSemanticClient


@dataclass
class _FakeItem:
    """Stand-in for ``nat.memory.models.MemoryItem``: the field slice the adapter
    touches. ``memory`` is the text, ``metadata`` round-trips the caller's id (the
    real model has no id field), and ``similarity_score`` is an L2 distance (lower =
    closer), matching RedisEditor."""

    user_id: str
    memory: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    similarity_score: float | None = None


class FakeMemoryEditor:
    """Stand-in for ``nat.memory.interfaces.MemoryEditor`` — ASYNC by design so the
    adapter's ``AsyncClientBridge`` path is exercised. Isolates by ``user_id``,
    round-trips ``metadata`` (so ``memory_id`` survives), and on ``search`` synthesizes
    a monotone L2 distance (0.0 for a substring match, else a rank-increasing value)
    so retrieval order is deterministic without an embedder."""

    def __init__(self) -> None:
        # user_id -> list of stored _FakeItem
        self._scopes: dict[str, list[_FakeItem]] = {}

    async def add_items(self, items: list[_FakeItem]) -> None:
        for item in items:
            self._scopes.setdefault(item.user_id, []).append(item)

    async def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[_FakeItem]:
        user_id = kwargs["user_id"]
        items = self._scopes.get(user_id, [])
        # Distance 0.0 for a substring hit, else 1.0 (loose match), nearest-first;
        # zero-distance ties broken by insertion order.
        hits = [
            _FakeItem(
                user_id=item.user_id,
                memory=item.memory,
                metadata=item.metadata,
                similarity_score=0.0 if query in (item.memory or "") else 1.0,
            )
            for item in items
        ]
        hits.sort(key=lambda h: h.similarity_score or 0.0)
        return hits[:top_k]

    async def remove_items(self, **kwargs: Any) -> None:
        self._scopes.pop(kwargs["user_id"], None)


def _nat_client(editor: FakeMemoryEditor) -> _NatClient:
    # Bind ``_FakeItem`` as the item factory so ``store`` builds the fake editor's
    # item type without importing the nat SDK.
    return _NatClient(editor, AsyncClientBridge(), item_factory=_FakeItem)


def _client() -> _NatClient:
    return _nat_client(FakeMemoryEditor())


# --- _similarity normalization ------------------------------------------------


def test_similarity_is_monotone_decreasing_in_distance():
    assert _similarity(0.0) == 1.0
    assert _similarity(1.0) == pytest.approx(0.5)
    assert _similarity(0.0) > _similarity(1.0) > _similarity(3.0) > 0.0


# --- _NatClient adapter -------------------------------------------------------


def test_client_satisfies_protocol():
    assert isinstance(_client(), SemanticMemoryClient)


def test_store_returns_minted_id_round_tripped_via_metadata():
    editor = FakeMemoryEditor()
    client = _nat_client(editor)
    minted = client.store(scope="trial-A", content="missing import foo", memory_id="m1")
    # MemoryItem has no id field, so the adapter mints its own and round-trips it via
    # metadata; the returned id is what retrieval keys off.
    stored = editor._scopes["trial-A"][0]
    assert stored.metadata["memory_id"] == minted
    assert stored.user_id == "trial-A"
    assert stored.memory == "missing import foo"


def test_query_maps_content_id_and_normalizes_distance_to_similarity():
    client = _client()
    a = client.store(scope="t", content="alpha beta", memory_id="m1")
    b = client.store(scope="t", content="gamma delta", memory_id="m2")
    hits = list(client.query(scope="t", query_text="alpha", top_k=10))
    # "alpha" is a substring of "alpha beta" (distance 0 -> sim 1.0) but not of
    # "gamma delta" (distance 1 -> sim 0.5); nearest-first; ids round-trip.
    assert [h.memory_id for h in hits] == [a, b]
    assert hits[0].content == "alpha beta"
    assert hits[0].score == 1.0
    assert hits[1].score == pytest.approx(0.5)


def test_query_respects_scope_isolation():
    client = _client()
    client.store(scope="trial-A", content="alpha beta", memory_id="m1")
    assert list(client.query(scope="trial-B", query_text="alpha", top_k=10)) == []


def test_query_respects_top_k():
    client = _client()
    for i in range(5):
        client.store(scope="t", content=f"alpha note {i}", memory_id=f"m{i}")
    assert len(list(client.query(scope="t", query_text="alpha", top_k=2))) == 2


def test_query_with_no_similarity_score_yields_none_score():
    # similarity_score is optional on MemoryItem; when the backend omits it, the hit
    # carries score=None and the base trusts the editor's list order (Graphiti path).
    class NoScoreEditor(FakeMemoryEditor):
        async def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[_FakeItem]:
            return [
                _FakeItem(user_id=kwargs["user_id"], memory="alpha", metadata={"memory_id": "x"})
            ]

    client = _nat_client(NoScoreEditor())
    client.store(scope="t", content="alpha", memory_id="m1")
    hits = list(client.query(scope="t", query_text="alpha", top_k=5))
    assert hits[0].score is None


def test_query_missing_memory_id_in_metadata_raises():
    # The adapter round-trips the id through metadata; an item missing it means the
    # editor dropped it, so the mapping is broken — fail loud rather than guess.
    class NoIdEditor(FakeMemoryEditor):
        async def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[_FakeItem]:
            return [_FakeItem(user_id=kwargs["user_id"], memory="alpha", similarity_score=0.0)]

    client = _nat_client(NoIdEditor())
    client.store(scope="t", content="alpha", memory_id="m1")
    with pytest.raises(KeyError, match="memory_id"):
        list(client.query(scope="t", query_text="alpha", top_k=5))


def test_clear_removes_only_its_scope():
    client = _client()
    client.store(scope="A", content="alpha", memory_id="m1")
    client.store(scope="B", content="alpha", memory_id="m2")
    client.clear(scope="A")
    assert list(client.query(scope="A", query_text="alpha", top_k=5)) == []
    kept = list(client.query(scope="B", query_text="alpha", top_k=5))
    assert [h.content for h in kept] == ["alpha"]


# --- NatMemory arm (via the shared FakeSemanticClient) ------------------------


def _ctx(trial_id: str = "t") -> StepContext:
    return StepContext(trial_id=trial_id, session_id="s", step_id="step", clock=IdClock())


def _arm(top_k: int = 10) -> NatMemory:
    return NatMemory(FakeSemanticClient(), top_k=top_k)


def test_arm_identity():
    arm = _arm()
    assert arm.name == "nat"
    assert arm.backend == MemoryBackend.VECTOR_DB
    assert isinstance(arm, AbstractSemanticArm)
    assert isinstance(arm, MemorySystem)


def test_arm_write_records_requested_and_backend_ids():
    ev = _arm().write("m1", "missing import foo", _ctx())
    assert ev.normalized_operation == MemoryOperation.WRITE
    assert ev.target_ids == ["m1"]
    assert ev.written_ids == ["t-1"]
    assert ev.success


def test_arm_retrieve_keys_payloads_off_backend_ids():
    arm = _arm()
    ctx = _ctx()
    arm.write("m1", "missing import foo", ctx)
    arm.write("m2", "unrelated content", ctx)
    res = arm.retrieve(RetrievalRequest(query_text="missing import"), _ctx())
    assert list(res.payloads) == ["t-1"]
    assert res.payloads["t-1"] == "missing import foo"


# --- factory wiring -----------------------------------------------------------


def test_factory_builds_arm_with_injected_client():
    arm = build_memory_system("nat", client=FakeSemanticClient())
    assert isinstance(arm, NatMemory)
    assert arm.name == "nat"
