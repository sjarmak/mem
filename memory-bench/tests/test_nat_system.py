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
        # Explicit None check rather than ``or 0.0`` so a real 0.0 distance (exact
        # match) is not conflated with "no score"; both sort nearest-first.
        hits.sort(key=lambda h: h.similarity_score if h.similarity_score is not None else 0.0)
        return hits[:top_k]

    async def remove_items(self, **kwargs: Any) -> None:
        self._scopes.pop(kwargs["user_id"], None)


# Bridges created by the helpers below each own a persistent event loop; track them
# so the autouse fixture closes every one at test teardown (close() drains async-gens
# and the executor — see test_async_bridge.py). Without this they leak loop+self-pipe
# sockets until GC, which trips ``-W error::ResourceWarning``.
_OPEN_BRIDGES: list[AsyncClientBridge] = []


@pytest.fixture(autouse=True)
def _close_bridges() -> Any:
    yield
    while _OPEN_BRIDGES:
        _OPEN_BRIDGES.pop().close()


def _nat_client(editor: FakeMemoryEditor) -> _NatClient:
    # Bind ``_FakeItem`` as the item factory so ``store`` builds the fake editor's
    # item type without importing the nat SDK. The bridge is registered for teardown.
    bridge = AsyncClientBridge()
    _OPEN_BRIDGES.append(bridge)
    return _NatClient(editor, bridge, item_factory=_FakeItem)


def _client() -> _NatClient:
    return _nat_client(FakeMemoryEditor())


# --- _similarity normalization ------------------------------------------------


def test_similarity_is_monotone_decreasing_in_distance() -> None:
    assert _similarity(0.0) == 1.0
    assert _similarity(1.0) == pytest.approx(0.5)
    assert _similarity(0.0) > _similarity(1.0) > _similarity(3.0) > 0.0


# --- _NatClient adapter -------------------------------------------------------


def test_client_satisfies_protocol() -> None:
    assert isinstance(_client(), SemanticMemoryClient)


def test_store_returns_minted_id_round_tripped_via_metadata() -> None:
    editor = FakeMemoryEditor()
    client = _nat_client(editor)
    minted = client.store(scope="trial-A", content="missing import foo", memory_id="m1")
    # MemoryItem has no id field, so the adapter mints its own and round-trips it via
    # metadata; the returned id is what retrieval keys off.
    stored = editor._scopes["trial-A"][0]
    assert stored.metadata["memory_id"] == minted
    assert stored.user_id == "trial-A"
    assert stored.memory == "missing import foo"


def test_query_maps_content_id_and_normalizes_distance_to_similarity() -> None:
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


def test_query_respects_scope_isolation() -> None:
    client = _client()
    client.store(scope="trial-A", content="alpha beta", memory_id="m1")
    assert list(client.query(scope="trial-B", query_text="alpha", top_k=10)) == []


def test_query_respects_top_k() -> None:
    client = _client()
    for i in range(5):
        client.store(scope="t", content=f"alpha note {i}", memory_id=f"m{i}")
    assert len(list(client.query(scope="t", query_text="alpha", top_k=2))) == 2


def test_query_with_no_similarity_score_yields_none_score() -> None:
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


def test_query_missing_memory_id_in_metadata_raises() -> None:
    # The adapter round-trips the id through metadata; an item missing it means the
    # editor dropped it, so the mapping is broken — fail loud rather than guess.
    class NoIdEditor(FakeMemoryEditor):
        async def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[_FakeItem]:
            return [_FakeItem(user_id=kwargs["user_id"], memory="alpha", similarity_score=0.0)]

    client = _nat_client(NoIdEditor())
    client.store(scope="t", content="alpha", memory_id="m1")
    with pytest.raises(KeyError, match="memory_id"):
        list(client.query(scope="t", query_text="alpha", top_k=5))


def test_clear_removes_only_its_scope() -> None:
    client = _client()
    client.store(scope="A", content="alpha", memory_id="m1")
    client.store(scope="B", content="alpha", memory_id="m2")
    client.clear(scope="A")
    assert list(client.query(scope="A", query_text="alpha", top_k=5)) == []
    kept = list(client.query(scope="B", query_text="alpha", top_k=5))
    assert [h.content for h in kept] == ["alpha"]


# --- clear-all guard (mem-lvp.16) ---------------------------------------------


class _SpyEditor(FakeMemoryEditor):
    """Records every ``remove_items`` call so a test can assert the guard short-circuits
    BEFORE the editor is touched — the real RedisEditor with no ``user_id`` would wipe
    the whole store, so clear with a blank scope must never reach it."""

    def __init__(self) -> None:
        super().__init__()
        self.remove_calls: list[dict[str, Any]] = []

    async def remove_items(self, **kwargs: Any) -> None:
        self.remove_calls.append(kwargs)
        await super().remove_items(**kwargs)


@pytest.mark.parametrize("blank", ["", "   "])
def test_clear_with_blank_scope_raises_and_never_calls_remove_items(blank: str) -> None:
    # A blank scope would map to a missing/empty user_id; the real RedisEditor would
    # then clear-all. The adapter must fail loud and never reach the backend.
    editor = _SpyEditor()
    client = _nat_client(editor)
    client.store(scope="A", content="alpha", memory_id="m1")
    with pytest.raises(ValueError, match="scope"):
        client.clear(scope=blank)
    assert editor.remove_calls == []
    # The store is untouched — nothing was wiped.
    assert [h.content for h in client.query(scope="A", query_text="alpha", top_k=5)] == ["alpha"]


# --- distance validation (mem-lvp.16) -----------------------------------------


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_query_raises_on_non_finite_or_negative_distance(bad: float) -> None:
    # A misbehaving backend returning a negative/NaN/inf L2 distance must fail loud:
    # raw 1/(1+d) is a ZeroDivisionError at d=-1 and silently NaN/out-of-range otherwise.
    class BadDistanceEditor(FakeMemoryEditor):
        async def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[_FakeItem]:
            return [
                _FakeItem(
                    user_id=kwargs["user_id"],
                    memory="alpha",
                    metadata={"memory_id": "x"},
                    similarity_score=bad,
                )
            ]

    client = _nat_client(BadDistanceEditor())
    client.store(scope="t", content="alpha", memory_id="m1")
    with pytest.raises(ValueError, match="distance"):
        list(client.query(scope="t", query_text="alpha", top_k=5))


# --- close() lifecycle (mem-lvp.15) -------------------------------------------


def test_nat_client_close_tears_down_bridge_loop() -> None:
    client = _client()
    loop = client._bridge.loop
    client.close()
    # The persistent loop (and its self-pipe sockets / executor) is released; without
    # this the bridge leaks them for the process lifetime.
    assert loop.is_closed()


def test_nat_client_close_is_idempotent() -> None:
    client = _client()
    client.close()
    client.close()  # second close must not raise (bridge.close no-ops once closed)


def test_arm_close_delegates_to_the_closable_client() -> None:
    # NatMemory inherits AbstractSemanticArm.close(), which closes the client only
    # when it holds a resource — the real _NatClient wraps a bridge, so the arm's
    # close tears that loop down.
    bridge = AsyncClientBridge()
    _OPEN_BRIDGES.append(bridge)
    arm = NatMemory(_NatClient(FakeMemoryEditor(), bridge, item_factory=_FakeItem))
    arm.close()
    assert bridge.loop.is_closed()


def test_arm_close_noops_on_a_non_closable_client() -> None:
    # The shared in-memory fake holds no resource and exposes no close(); the arm's
    # close() must be a safe no-op, not an AttributeError.
    NatMemory(FakeSemanticClient()).close()


# --- NatMemory arm (via the shared FakeSemanticClient) ------------------------


def _ctx(trial_id: str = "t") -> StepContext:
    return StepContext(trial_id=trial_id, session_id="s", step_id="step", clock=IdClock())


def _arm(top_k: int = 10) -> NatMemory:
    return NatMemory(FakeSemanticClient(), top_k=top_k)


def test_arm_identity() -> None:
    arm = _arm()
    assert arm.name == "nat"
    assert arm.backend == MemoryBackend.VECTOR_DB
    assert isinstance(arm, AbstractSemanticArm)
    assert isinstance(arm, MemorySystem)


def test_arm_write_records_requested_and_backend_ids() -> None:
    ev = _arm().write("m1", "missing import foo", _ctx())
    assert ev.normalized_operation == MemoryOperation.WRITE
    assert ev.target_ids == ["m1"]
    assert ev.written_ids == ["t-1"]
    assert ev.success


def test_arm_retrieve_keys_payloads_off_backend_ids() -> None:
    arm = _arm()
    ctx = _ctx()
    arm.write("m1", "missing import foo", ctx)
    arm.write("m2", "unrelated content", ctx)
    res = arm.retrieve(RetrievalRequest(query_text="missing import"), _ctx())
    assert list(res.payloads) == ["t-1"]
    assert res.payloads["t-1"] == "missing import foo"


# --- factory wiring -----------------------------------------------------------


def test_factory_builds_arm_with_injected_client() -> None:
    arm = build_memory_system("nat", client=FakeSemanticClient())
    assert isinstance(arm, NatMemory)
    assert arm.name == "nat"
