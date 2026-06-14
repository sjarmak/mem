"""Tests for the A-MEM arm (mem-lvp.9).

Hermetic: ``_AMemClient`` runs against a ``FakeAMem`` stand-in for
``agentic_memory.AgenticMemorySystem`` (mints its own id, returns ChromaDB-style
distance hits), and ``AMemMemory`` runs against the shared ``FakeSemanticClient``.
No network, no model, no SDK installed.
"""

from __future__ import annotations

from typing import Any

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.amem_system import (
    AMemMemory,
    _AMemClient,
    _similarity,
    build_amem_kwargs,
)
from membench.memory_systems.base import MemorySystem, RetrievalRequest
from membench.memory_systems.local_stack import LocalModelStack
from membench.memory_systems.semantic_base import (
    AbstractSemanticArm,
    SemanticMemoryClient,
)
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryOperation
from tests.semantic_fakes import FakeSemanticClient


class FakeAMem:
    """Stand-in for ``AgenticMemorySystem``: mints its own uuid on ``add_note``
    (ignoring any caller id) and returns ChromaDB-style hits carrying an L2
    ``distance`` (lower = closer). One instance owns one collection's notes."""

    def __init__(self, collection_name: str) -> None:
        self.collection_name = collection_name
        self._notes: dict[str, str] = {}
        self._n = 0

    def add_note(self, content: str, **_kwargs: Any) -> str:
        self._n += 1
        minted = f"{self.collection_name}-uuid-{self._n}"
        self._notes[minted] = content
        return minted

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        # Toy distance: 0.0 for an exact substring match, else 1.0, nearest-first.
        hits = [
            {
                "id": nid,
                "content": content,
                "distance": 0.0 if query in content else 1.0,
            }
            for nid, content in self._notes.items()
        ]
        hits.sort(key=lambda h: (h["distance"], h["id"]))
        return hits[:k]


def _client() -> _AMemClient:
    return _AMemClient(lambda scope: FakeAMem(f"membench_{scope}"))


# --- _similarity normalization ------------------------------------------------


def test_similarity_is_monotone_decreasing_in_distance():
    assert _similarity(0.0) == 1.0
    assert _similarity(1.0) == pytest.approx(0.5)
    assert _similarity(0.0) > _similarity(1.0) > _similarity(3.0) > 0.0


# --- _AMemClient adapter ------------------------------------------------------


def test_client_satisfies_protocol():
    assert isinstance(_client(), SemanticMemoryClient)


def test_store_returns_backend_minted_id_not_requested():
    client = _client()
    minted = client.store(scope="t", content="missing import foo", memory_id="m1")
    assert minted == "membench_t-uuid-1"
    assert minted != "m1"


def test_query_maps_ids_content_and_normalizes_distance_to_similarity():
    client = _client()
    client.store(scope="t", content="alpha beta", memory_id="m1")
    client.store(scope="t", content="gamma delta", memory_id="m2")
    hits = list(client.query(scope="t", query_text="alpha", top_k=10))
    # alpha is a substring of "alpha beta" (distance 0 -> sim 1.0) but not of
    # "gamma delta" (distance 1 -> sim 0.5); nearest-first.
    assert [h.memory_id for h in hits] == ["membench_t-uuid-1", "membench_t-uuid-2"]
    assert hits[0].content == "alpha beta"
    assert hits[0].score == 1.0
    assert hits[1].score == pytest.approx(0.5)


def test_query_on_unknown_scope_returns_empty():
    assert list(_client().query(scope="never-written", query_text="x", top_k=5)) == []


def test_query_respects_top_k():
    client = _client()
    for i in range(5):
        client.store(scope="t", content=f"alpha note {i}", memory_id=f"m{i}")
    assert len(list(client.query(scope="t", query_text="alpha", top_k=2))) == 2


def test_clear_wipes_only_its_scope():
    client = _client()
    client.store(scope="A", content="alpha", memory_id="m1")
    client.store(scope="B", content="alpha", memory_id="m2")
    client.clear(scope="A")
    assert list(client.query(scope="A", query_text="alpha", top_k=5)) == []
    kept = list(client.query(scope="B", query_text="alpha", top_k=5))
    assert [h.memory_id for h in kept] == ["membench_B-uuid-1"]


def test_clear_then_store_rebuilds_a_fresh_collection():
    client = _client()
    client.store(scope="t", content="alpha", memory_id="m1")
    client.clear(scope="t")
    # Fresh per-trial collection: the minted-id counter restarts from 1.
    minted = client.store(scope="t", content="beta", memory_id="m2")
    assert minted == "membench_t-uuid-1"


def test_missing_distance_field_raises():
    class NoDistance(FakeAMem):
        def search(self, query: str, k: int) -> list[dict[str, Any]]:
            return [{"id": "x", "content": "alpha"}]

    client = _AMemClient(lambda scope: NoDistance(f"membench_{scope}"))
    client.store(scope="t", content="alpha", memory_id="m1")
    with pytest.raises(KeyError, match="distance"):
        list(client.query(scope="t", query_text="alpha", top_k=5))


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_query_raises_on_non_finite_or_negative_distance(bad: float):
    # ChromaDB L2 distance is finite and >= 0; a negative/NaN/inf value means the
    # backend misbehaved, so fail loud rather than emit a NaN/out-of-range score
    # (raw 1/(1+d) is a ZeroDivisionError at d=-1). Shared with the NAT arm (mem-lvp.16).
    class BadDistance(FakeAMem):
        def search(self, query: str, k: int) -> list[dict[str, Any]]:
            return [{"id": "x", "content": "alpha", "distance": bad}]

    client = _AMemClient(lambda scope: BadDistance(f"membench_{scope}"))
    client.store(scope="t", content="alpha", memory_id="m1")
    with pytest.raises(ValueError, match="distance"):
        list(client.query(scope="t", query_text="alpha", top_k=5))


# --- AMemMemory arm (via the shared FakeSemanticClient) -----------------------


def _ctx(trial_id: str = "t") -> StepContext:
    return StepContext(trial_id=trial_id, session_id="s", step_id="step", clock=IdClock())


def _arm(top_k: int = 10) -> AMemMemory:
    return AMemMemory(FakeSemanticClient(), top_k=top_k)


def test_arm_identity():
    arm = _arm()
    assert arm.name == "a-mem"
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
    arm = build_memory_system("a-mem", client=FakeSemanticClient())
    assert isinstance(arm, AMemMemory)
    assert arm.name == "a-mem"


def test_amem_kwargs_pin_local_llm_never_openai():
    # The pre-mem-lvp.5 factory passed no llm_backend, so A-MEM defaulted to a PAID
    # OpenAI call at ingest. The shared stack must pin a local Ollama backend.
    kwargs = build_amem_kwargs("trial-A")
    assert kwargs["llm_backend"] == "ollama"
    assert kwargs["llm_backend"] != "openai"
    assert kwargs["collection_name"] == "membench_trial-A"


def test_amem_kwargs_source_models_from_shared_stack():
    stack = LocalModelStack(chat_model="my-chat", sentence_transformer_model="my-st")
    kwargs = build_amem_kwargs("t", stack=stack)
    assert kwargs["model_name"] == "my-st"
    assert kwargs["llm_model"] == "my-chat"
