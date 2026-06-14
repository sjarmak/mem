"""Tests for the Graphiti arm (mem-lvp.4).

Hermetic: ``_GraphitiClient`` runs against ``FakeGraphiti`` — an ASYNC stand-in for
``graphiti_core.Graphiti`` (mints its own episode + edge uuids, isolates by
``group_id``, exposes no per-edge score), so the adapter's ``AsyncClientBridge`` path is
exercised with no network, no model, no SDK. ``GraphitiMemory`` runs against the shared
``FakeSemanticClient``. The fresh-``group_id``-per-trial reset (mem-lvp.4b) means
``clear`` is a no-op; the bridge-loop teardown (mem-lvp.15) is asserted via the loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.async_bridge import AsyncClientBridge
from membench.memory_systems.base import MemorySystem, RetrievalRequest
from membench.memory_systems.graphiti_system import (
    GraphitiMemory,
    _GraphitiClient,
)
from membench.memory_systems.semantic_base import (
    AbstractSemanticArm,
    SemanticMemoryClient,
)
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryOperation
from tests.semantic_fakes import FakeSemanticClient


@dataclass
class _FakeEpisode:
    uuid: str


@dataclass
class _FakeAddResult:
    """Stand-in for ``AddEpisodeResults``: the adapter reads only ``.episode.uuid``."""

    episode: _FakeEpisode


@dataclass
class _FakeEdge:
    """Stand-in for ``EntityEdge``: the field slice a search hit exposes."""

    uuid: str
    fact: str


class FakeGraphiti:
    """ASYNC stand-in for ``graphiti_core.Graphiti``. Mints its own episode uuid per
    ``add_episode`` (ignoring the caller's ``name``, like the real SDK) and stores one
    edge per episode keyed by ``group_id``; ``search`` returns substring matches as
    ``_FakeEdge`` with their minted edge uuids, no score (Graphiti hybrid-search order)."""

    def __init__(self) -> None:
        # group_id -> list of (edge_uuid, fact)
        self._groups: dict[str, list[_FakeEdge]] = {}
        self._n = 0

    async def add_episode(
        self,
        *,
        name: str,
        episode_body: str,
        source: Any,
        group_id: str,
        reference_time: datetime,
    ) -> _FakeAddResult:
        self._n += 1
        episode_uuid = f"episode-{self._n}"
        # One extracted edge per episode, with its OWN uuid (≠ the episode uuid) — the
        # mint-fan-out trait the adapter must surface (retrieval keys off the edge id).
        self._groups.setdefault(group_id, []).append(
            _FakeEdge(uuid=f"edge-{self._n}", fact=episode_body)
        )
        return _FakeAddResult(episode=_FakeEpisode(uuid=episode_uuid))

    async def search(
        self, query: str, *, group_ids: list[str], num_results: int
    ) -> list[_FakeEdge]:
        hits = [
            edge for gid in group_ids for edge in self._groups.get(gid, []) if query in edge.fact
        ]
        return hits[:num_results]


# Bridges own a persistent loop; track + close them at teardown so they don't leak
# loop/self-pipe sockets under -W error::ResourceWarning (mirrors test_nat_system).
_OPEN_BRIDGES: list[AsyncClientBridge] = []


@pytest.fixture(autouse=True)
def _close_bridges() -> Any:
    yield
    while _OPEN_BRIDGES:
        _OPEN_BRIDGES.pop().close()


def _graphiti_client(backend: FakeGraphiti) -> _GraphitiClient:
    bridge = AsyncClientBridge()
    _OPEN_BRIDGES.append(bridge)
    # episode_source is a plain sentinel — the fake never inspects it (the real client
    # passes EpisodeType.text); a fixed clock keeps reference_time deterministic.
    return _GraphitiClient(
        backend,
        bridge,
        episode_source="text",
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def _client() -> _GraphitiClient:
    return _graphiti_client(FakeGraphiti())


# --- _GraphitiClient adapter --------------------------------------------------


def test_client_satisfies_protocol() -> None:
    assert isinstance(_client(), SemanticMemoryClient)


def test_store_returns_episode_uuid_not_requested_id() -> None:
    client = _client()
    minted = client.store(scope="t", content="missing import foo", memory_id="m1")
    # The backend id is Graphiti's EPISODE uuid, never the caller's memory_id.
    assert minted == "episode-1"
    assert minted != "m1"


def test_query_maps_edge_uuid_and_fact_with_no_score() -> None:
    client = _client()
    client.store(scope="t", content="missing import foo", memory_id="m1")
    client.store(scope="t", content="unrelated content", memory_id="m2")
    hits = list(client.query(scope="t", query_text="missing import", top_k=10))
    # Retrieval keys off the EDGE uuid (≠ the episode uuid), content is the edge fact,
    # and score is None (search exposes none) so the base trusts list order.
    assert [h.memory_id for h in hits] == ["edge-1"]
    assert hits[0].content == "missing import foo"
    assert hits[0].score is None


def test_query_respects_scope_isolation() -> None:
    client = _client()
    client.store(scope="trial-A", content="alpha beta", memory_id="m1")
    assert list(client.query(scope="trial-B", query_text="alpha", top_k=10)) == []


def test_query_respects_num_results() -> None:
    client = _client()
    for i in range(5):
        client.store(scope="t", content=f"alpha note {i}", memory_id=f"m{i}")
    assert len(list(client.query(scope="t", query_text="alpha", top_k=2))) == 2


def test_clear_is_a_noop_isolation_is_the_fresh_group_id() -> None:
    # mem-lvp.4b: clear does NOT purge — isolation comes from the never-reused
    # group_id, so a stored episode survives a clear() of its own scope.
    client = _client()
    client.store(scope="t", content="alpha beta", memory_id="m1")
    client.clear(scope="t")
    assert [h.memory_id for h in client.query(scope="t", query_text="alpha", top_k=5)] == ["edge-1"]


def test_close_tears_down_the_bridge_loop() -> None:
    client = _client()
    loop = client._bridge.loop
    client.close()
    assert loop.is_closed()


# --- GraphitiMemory arm (via the shared FakeSemanticClient) -------------------


def _ctx(trial_id: str = "t") -> StepContext:
    return StepContext(trial_id=trial_id, session_id="s", step_id="step", clock=IdClock())


def _arm(top_k: int = 10) -> GraphitiMemory:
    return GraphitiMemory(FakeSemanticClient(), top_k=top_k)


def test_arm_identity() -> None:
    arm = _arm()
    assert arm.name == "graphiti"
    assert arm.backend == MemoryBackend.KG
    assert isinstance(arm, AbstractSemanticArm)
    assert isinstance(arm, MemorySystem)


def test_arm_write_records_requested_and_backend_ids() -> None:
    ev = _arm().write("m1", "missing import foo", _ctx())
    assert ev.normalized_operation == MemoryOperation.WRITE
    assert ev.target_ids == ["m1"]
    assert ev.written_ids == ["t-1"]
    assert ev.backend == MemoryBackend.KG
    assert ev.success


def test_arm_retrieve_keys_payloads_off_backend_ids() -> None:
    arm = _arm()
    ctx = _ctx()
    arm.write("m1", "missing import foo", ctx)
    arm.write("m2", "unrelated content", ctx)
    res = arm.retrieve(RetrievalRequest(query_text="missing import"), _ctx())
    assert list(res.payloads) == ["t-1"]
    assert res.payloads["t-1"] == "missing import foo"


def test_arm_close_delegates_to_the_real_client() -> None:
    backend = FakeGraphiti()
    client = _graphiti_client(backend)
    arm = GraphitiMemory(client)
    arm.close()
    assert client._bridge.loop.is_closed()


# --- factory wiring -----------------------------------------------------------


def test_factory_builds_graphiti_arm_with_injected_client() -> None:
    arm = build_memory_system("graphiti", client=FakeSemanticClient())
    assert isinstance(arm, GraphitiMemory)
    assert arm.name == "graphiti"


def test_graphiti_no_longer_deferred() -> None:
    # It used to raise as a deferred arm; now it is wired.
    from membench.memory_systems import _DEFERRED

    assert "graphiti" not in _DEFERRED
