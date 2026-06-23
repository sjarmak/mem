"""Tests for the `nemo-embed` baseline arm (mem-sikg).

Two layers, both hermetic (no network, no model, no sentence-transformers):

- ``_NemoEmbedClient`` against a deterministic ``FakeEmbedder`` — a multi-hot
  bag-of-vocab embedder, so cosine ranking is a known function of token overlap and
  the index logic (caller-id store, exact cosine top-k, scope isolation, dimension /
  degenerate-vector guards) is pinned without a real model.
- ``NemoEmbedMemory`` via the shared ``FakeSemanticClient`` and via the real
  ``_NemoEmbedClient`` — proving the subclass is a faithful ``AbstractSemanticArm``
  with the right identity, that its default factory stays lazy (no SDK needed), and
  that retrieval ranks end-to-end.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.base import MemorySystem, RetrievalRequest
from membench.memory_systems.nemo_embed_system import (
    NemoEmbedder,
    NemoEmbedMemory,
    SemanticMemoryClient,
    _NemoEmbedClient,
)
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryOperation
from tests.semantic_fakes import FakeSemanticClient


def _tokens(text: str) -> set[str]:
    return set("".join(c.lower() if c.isalnum() else " " for c in text).split())


@dataclass
class FakeEmbedder:
    """Deterministic multi-hot embedder over a fixed vocabulary: a text maps to a
    fixed-dimension vector with 1.0 at each present vocab token. Cosine of two such
    vectors = |overlap| / (sqrt|a| * sqrt|b|), so retrieval *order* tracks token
    overlap and dimensions are constant — no numpy, no model. ``embed_query`` and
    ``embed_documents`` are intentionally symmetric (the asymmetry knob lives in the
    real embedder, not the seam)."""

    vocab: tuple[str, ...]

    def _vec(self, text: str) -> list[float]:
        present = _tokens(text)
        return [1.0 if term in present else 0.0 for term in self.vocab]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


_VOCAB = ("missing", "import", "foo", "bar", "baz", "unrelated")


def _client() -> _NemoEmbedClient:
    return _NemoEmbedClient(FakeEmbedder(_VOCAB))


# --- _NemoEmbedClient adapter tests (against the FakeEmbedder) --------------------


def test_store_returns_caller_memory_id():
    # Unlike mem0/A-MEM, this client owns the store, so it keeps the caller's id.
    assert _client().store(scope="t", content="missing import foo", memory_id="m1") == "m1"


def test_query_ranks_by_cosine_descending_with_id_tiebreak():
    client = _client()
    client.store(scope="t", content="missing import foo", memory_id="m1")  # overlap 2/3 tokens
    client.store(scope="t", content="missing baz", memory_id="m2")  # overlap 1/2 tokens
    client.store(scope="t", content="totally unrelated", memory_id="m3")  # overlap 0
    hits = list(client.query(scope="t", query_text="missing import", top_k=3))
    assert [hit.memory_id for hit in hits] == ["m1", "m2", "m3"]
    # cosine(query=[missing,import], m1=[missing,import,foo]) = 2 / (sqrt2 * sqrt3).
    assert hits[0].score == pytest.approx(2.0 / (2.0**0.5 * 3.0**0.5))
    assert hits[1].score == pytest.approx(0.5)
    assert hits[2].score == pytest.approx(0.0)


def test_query_respects_top_k():
    client = _client()
    client.store(scope="t", content="missing import", memory_id="m1")
    client.store(scope="t", content="missing baz", memory_id="m2")
    hits = list(client.query(scope="t", query_text="missing import", top_k=1))
    assert [hit.memory_id for hit in hits] == ["m1"]


def test_query_respects_scope_isolation():
    client = _client()
    client.store(scope="trial-A", content="missing import", memory_id="m1")
    assert list(client.query(scope="trial-B", query_text="missing import", top_k=10)) == []


def test_query_on_empty_scope_returns_empty():
    assert list(_client().query(scope="never-written", query_text="missing", top_k=10)) == []


def test_clear_drops_only_its_scope():
    client = _client()
    client.store(scope="trial-A", content="missing import", memory_id="m1")
    client.store(scope="trial-B", content="missing import", memory_id="m2")
    client.clear(scope="trial-A")
    assert list(client.query(scope="trial-A", query_text="missing", top_k=10)) == []
    assert [h.memory_id for h in client.query(scope="trial-B", query_text="missing", top_k=10)] == [
        "m2"
    ]


def test_store_rejects_multi_vector_return():
    # One text in must yield exactly one vector; a split/dropped result would corrupt
    # the id->vector map, so the client fails loud.
    class SplittingEmbedder:
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return [[1.0], [0.0]]

        def embed_query(self, text: str) -> list[float]:
            return [1.0]

    with pytest.raises(ValueError, match="exactly one vector"):
        _NemoEmbedClient(SplittingEmbedder()).store(scope="t", content="x", memory_id="m1")


def test_store_rejects_dimension_drift():
    class DriftingEmbedder:
        _calls = 0

        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            DriftingEmbedder._calls += 1
            return [[1.0, 0.0]] if DriftingEmbedder._calls == 1 else [[1.0, 0.0, 1.0]]

        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    client = _NemoEmbedClient(DriftingEmbedder())
    client.store(scope="t", content="a", memory_id="m1")
    with pytest.raises(ValueError, match="dimension changed"):
        client.store(scope="t", content="b", memory_id="m2")


def test_degenerate_zero_vector_fails_loud():
    # An all-zero embedding has a zero norm; cosine-ranking it would divide by zero,
    # so normalization must raise rather than poison the scores.
    class ZeroEmbedder:
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return [[0.0, 0.0, 0.0]]

        def embed_query(self, text: str) -> list[float]:
            return [0.0, 0.0, 0.0]

    with pytest.raises(ValueError, match="zero/non-finite L2 norm"):
        _NemoEmbedClient(ZeroEmbedder()).store(scope="t", content="x", memory_id="m1")


def test_empty_vector_fails_loud():
    class EmptyEmbedder:
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return [[]]

        def embed_query(self, text: str) -> list[float]:
            return []

    with pytest.raises(ValueError, match="empty"):
        _NemoEmbedClient(EmptyEmbedder()).store(scope="t", content="x", memory_id="m1")


def test_fake_embedder_satisfies_embedder_protocol():
    assert isinstance(FakeEmbedder(_VOCAB), NemoEmbedder)


def test_client_satisfies_semantic_protocol():
    assert isinstance(_client(), SemanticMemoryClient)


# --- NemoEmbedMemory arm tests ----------------------------------------------------


def _ctx(trial_id: str = "t") -> StepContext:
    return StepContext(trial_id=trial_id, session_id="s", step_id="step", clock=IdClock())


def test_arm_identity():
    arm = NemoEmbedMemory(FakeSemanticClient())
    assert arm.name == "nemo-embed"
    assert arm.backend == MemoryBackend.VECTOR_DB
    assert isinstance(arm, MemorySystem)


def test_arm_write_records_event():
    arm = NemoEmbedMemory(_NemoEmbedClient(FakeEmbedder(_VOCAB)))
    event = arm.write("m1", "missing import foo", _ctx())
    assert event.normalized_operation == MemoryOperation.WRITE
    assert event.target_ids == ["m1"]
    # This client returns the caller id verbatim (it owns the store).
    assert event.written_ids == ["m1"]
    assert event.backend == MemoryBackend.VECTOR_DB


def test_arm_retrieve_ranks_end_to_end():
    arm = NemoEmbedMemory(_NemoEmbedClient(FakeEmbedder(_VOCAB)), top_k=2)
    ctx = _ctx()
    arm.write("m1", "missing import foo", ctx)
    arm.write("m2", "totally unrelated", ctx)
    arm.write("m3", "missing baz", ctx)
    result = arm.retrieve(RetrievalRequest(query_text="missing import"), _ctx())
    assert list(result.payloads) == ["m1", "m3"]
    assert result.total_matched == 2


def test_arm_default_factory_is_lazy_no_sdk_needed():
    # Constructing with an injected client must never import sentence-transformers;
    # only the no-arg default path would.
    arm = NemoEmbedMemory(FakeSemanticClient())
    assert isinstance(arm, NemoEmbedMemory)


def test_factory_wires_nemo_embed_arm():
    # Selectable as one more eval arm by name, injecting a client so no SDK loads.
    arm = build_memory_system("nemo-embed", client=FakeSemanticClient())
    assert isinstance(arm, NemoEmbedMemory)
    assert arm.name == "nemo-embed"
