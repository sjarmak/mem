"""Tests for the mem0 arm (mem-lvp.2).

Two layers, both hermetic (no network, no model, no mem0 SDK):

- ``_Mem0Client`` against ``FakeMem0`` — a stand-in exposing the native
  ``add``/``search``/``delete_all`` and the ``{"results": [...]}`` shapes mem0
  returns. This pins the adapter logic: infer=False, user_id=scope mapping,
  minted-id return, score pass-through.
- ``Mem0Memory`` via the shared ``FakeSemanticClient`` — proving the subclass is a
  faithful ``AbstractSemanticArm`` with the right identity and no extra logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from membench.memory_systems.base import MemorySystem, RetrievalRequest
from membench.memory_systems.mem0_system import (
    LOCAL_CONFIG,
    Mem0Memory,
    SemanticMemoryClient,
    _Mem0Client,
    build_mem0_config,
    default_mem0_store_path,
)
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryOperation
from tests.semantic_fakes import FakeSemanticClient


@dataclass
class FakeMem0:
    """Native mem0.Memory stand-in. Mints its own id per add (ignoring the caller's
    metadata memory_id, like the real SDK), isolates by user_id, and returns the
    ``{"results": [...]}`` envelope mem0 uses. ``search`` ranks by token overlap so
    order is exercised; scores are higher-better, matching mem0."""

    # user_id -> list of {"id", "memory", "metadata"}
    users: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _counter: int = 0

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set("".join(c.lower() if c.isalnum() else " " for c in text).split())

    def add(
        self,
        content: str,
        *,
        user_id: str,
        infer: bool,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        assert infer is False, "the arm must call add with infer=False"
        self._counter += 1
        minted = f"mem0-{self._counter}"
        record = {"id": minted, "memory": content, "metadata": metadata}
        self.users.setdefault(user_id, []).append(record)
        return {"results": [{"id": minted, "memory": content, "event": "ADD"}]}

    def search(
        self,
        query: str,
        *,
        top_k: int,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        user_id = filters["user_id"]
        q = self._tokens(query)
        scored = [
            {"id": r["id"], "memory": r["memory"], "score": float(overlap)}
            for r in self.users.get(user_id, [])
            if (overlap := len(q & self._tokens(r["memory"]))) > 0
        ]
        scored.sort(key=lambda r: (-r["score"], r["id"]))
        return {"results": scored[:top_k]}

    def delete_all(self, *, user_id: str) -> None:
        self.users.pop(user_id, None)


# --- _Mem0Client adapter tests (against the native FakeMem0) ---------------------


def test_store_returns_minted_id_with_infer_off():
    native = FakeMem0()
    client = _Mem0Client(native)
    assert client.store(scope="t", content="missing import foo", memory_id="m1") == "mem0-1"


def test_store_maps_scope_to_user_id_and_keeps_metadata_memory_id():
    native = FakeMem0()
    _Mem0Client(native).store(scope="trial-A", content="alpha", memory_id="m1")
    record = native.users["trial-A"][0]
    assert record["metadata"] == {"memory_id": "m1"}


def test_store_rejects_multi_memory_result():
    # infer=False guarantees one result; if mem0 ever returns several, the id mapping
    # would be ambiguous, so the adapter must fail loudly rather than guess.
    class SplittingMem0(FakeMem0):
        def add(self, content: str, **kwargs: Any) -> dict[str, Any]:
            return {"results": [{"id": "a"}, {"id": "b"}]}

    with pytest.raises(ValueError, match="exactly one memory"):
        _Mem0Client(SplittingMem0()).store(scope="t", content="x", memory_id="m1")


def test_query_maps_results_to_hits_with_score_passthrough():
    native = FakeMem0()
    client = _Mem0Client(native)
    client.store(scope="t", content="missing import foo bar", memory_id="m1")  # mem0-1
    client.store(scope="t", content="missing import baz", memory_id="m2")  # mem0-2
    client.store(scope="t", content="totally unrelated", memory_id="m3")  # mem0-3
    hits = list(client.query(scope="t", query_text="missing import", top_k=10))
    # mem0-1 & mem0-2 overlap {missing, import}; mem0-3 overlaps nothing.
    assert [h.memory_id for h in hits] == ["mem0-1", "mem0-2"]
    assert hits[0].content == "missing import foo bar"
    # mem0 score is higher-better; the adapter does not normalize it.
    assert hits[0].score == 2.0


def test_query_respects_scope_isolation():
    native = FakeMem0()
    client = _Mem0Client(native)
    client.store(scope="trial-A", content="alpha beta", memory_id="m1")
    assert list(client.query(scope="trial-B", query_text="alpha", top_k=10)) == []


def test_clear_deletes_only_its_user():
    native = FakeMem0()
    client = _Mem0Client(native)
    client.store(scope="trial-A", content="alpha", memory_id="m1")
    client.store(scope="trial-B", content="alpha", memory_id="m2")
    client.clear(scope="trial-A")
    assert "trial-A" not in native.users
    assert list(client.query(scope="trial-B", query_text="alpha", top_k=10))


def test_client_satisfies_protocol():
    assert isinstance(_Mem0Client(FakeMem0()), SemanticMemoryClient)


# --- Mem0Memory arm tests (against the shared FakeSemanticClient) -----------------


def _ctx(trial_id: str = "t") -> StepContext:
    return StepContext(trial_id=trial_id, session_id="s", step_id="step", clock=IdClock())


def _arm(top_k: int = 10) -> Mem0Memory:
    return Mem0Memory(FakeSemanticClient(), top_k=top_k)


def test_arm_identity():
    arm = _arm()
    assert arm.name == "mem0"
    assert arm.backend == MemoryBackend.VECTOR_DB
    assert isinstance(arm, MemorySystem)


def test_arm_write_records_backend_minted_id():
    ev = _arm().write("m1", "missing import foo", _ctx())
    assert ev.normalized_operation == MemoryOperation.WRITE
    assert ev.target_ids == ["m1"]
    assert ev.written_ids == ["t-1"]
    assert ev.backend == MemoryBackend.VECTOR_DB


def test_arm_retrieve_ranks_and_keys_off_backend_ids():
    arm = _arm(top_k=2)
    ctx = _ctx()
    arm.write("m1", "missing import foo bar", ctx)
    arm.write("m2", "totally unrelated content", ctx)
    arm.write("m3", "missing import baz", ctx)
    result = arm.retrieve(RetrievalRequest(query_text="missing import"), _ctx())
    assert list(result.payloads) == ["t-1", "t-3"]
    assert result.total_matched == 2


def test_arm_default_factory_is_lazy_no_sdk_needed():
    # Constructing with an injected client must never import mem0; only the no-arg
    # default path would, and that is exercised separately where the SDK is present.
    arm = Mem0Memory(FakeSemanticClient())
    assert isinstance(arm, Mem0Memory)


def test_local_config_is_network_free_local_models():
    assert LOCAL_CONFIG["vector_store"]["provider"] == "qdrant"
    assert LOCAL_CONFIG["embedder"]["provider"] == "ollama"
    assert LOCAL_CONFIG["llm"]["provider"] == "ollama"


def test_local_config_bakes_no_shared_store_path():
    # The path must be injected per run, never baked: a static shared path would put
    # every instance/run in one Qdrant collection (mem-lvp.12 contamination risk).
    assert "config" not in LOCAL_CONFIG["vector_store"]


def test_default_store_path_is_unique_per_call():
    # Two arms — or two runs — must land in different collections.
    assert default_mem0_store_path() != default_mem0_store_path()


def test_store_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBENCH_MEM0_STORE_DIR", str(tmp_path))
    path = default_mem0_store_path()
    assert path.startswith(str(tmp_path))


def test_build_mem0_config_injects_path_without_mutating_constant():
    config = build_mem0_config("/some/run/store")
    assert config["vector_store"]["config"] == {"path": "/some/run/store", "on_disk": True}
    # local models preserved; module constant untouched (deepcopy).
    assert config["embedder"]["provider"] == "ollama"
    assert "config" not in LOCAL_CONFIG["vector_store"]
