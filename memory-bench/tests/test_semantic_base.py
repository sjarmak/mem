"""Tests for AbstractSemanticArm — the shared competitive-arm base (mem-lvp.1).

Hermetic: the semantic client is a deterministic token-overlap fake, so no network
and no model. These assert the contract translation every competitive arm inherits:
trial-scoped isolation, backend-minted ids, and normalized event emission.
"""

import pytest

from membench.memory_systems.base import MemorySystem, RetrievalRequest
from membench.memory_systems.semantic_base import (
    AbstractSemanticArm,
    SemanticMemoryClient,
)
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryOperation
from tests.semantic_fakes import FakeSemanticClient


class StubArm(AbstractSemanticArm):
    name = "stub"
    backend = MemoryBackend.VECTOR_DB


def _ctx(trial_id: str = "t") -> StepContext:
    return StepContext(trial_id=trial_id, session_id="s", step_id="step", clock=IdClock())


def _arm(top_k: int = 10) -> StubArm:
    return StubArm(FakeSemanticClient(), top_k=top_k)


def test_is_a_memory_system():
    assert isinstance(_arm(), MemorySystem)


def test_fake_client_satisfies_protocol():
    assert isinstance(FakeSemanticClient(), SemanticMemoryClient)


def test_write_returns_backend_minted_id_not_requested_id():
    # The backend mints its own id (mem0/A-MEM/Graphiti behaviour); the event must
    # record the requested id as target and the assigned id as written.
    ev = _arm().write("m1", "missing import foo", _ctx())
    assert ev.normalized_operation == MemoryOperation.WRITE
    assert ev.target_ids == ["m1"]
    assert ev.written_ids == ["t-1"]
    assert ev.written_ids != ev.target_ids
    assert ev.backend == MemoryBackend.VECTOR_DB
    assert ev.success


def test_retrieve_ranks_by_overlap_and_caps_top_k():
    arm = _arm(top_k=2)
    ctx = _ctx()
    arm.write("m1", "missing import foo bar", ctx)  # -> t-1
    arm.write("m2", "totally unrelated content", ctx)  # -> t-2
    arm.write("m3", "missing import baz", ctx)  # -> t-3
    result = arm.retrieve(RetrievalRequest(query_text="missing import"), _ctx())
    # t-1 and t-3 each overlap {missing, import}; t-2 overlaps nothing. top_k caps at 2,
    # tie broken by minted id (t-1 before t-3). Payloads key off the BACKEND ids.
    assert list(result.payloads) == ["t-1", "t-3"]
    assert result.event.retrieved_ids == ["t-1", "t-3"]
    assert result.event.normalized_operation == MemoryOperation.SEARCH
    assert result.event.query == "missing import"
    assert result.total_matched == 2


def test_retrieve_excludes_zero_overlap():
    arm = _arm()
    ctx = _ctx()
    arm.write("m1", "alpha beta", ctx)
    arm.write("m2", "gamma delta", ctx)
    result = arm.retrieve(RetrievalRequest(query_text="alpha"), _ctx())
    assert list(result.payloads) == ["t-1"]
    assert result.payloads["t-1"] == "alpha beta"
    assert result.total_matched == 1


def test_scope_isolation_a_trial_sees_only_its_own_writes():
    arm = _arm()
    arm.write("m1", "alpha beta", _ctx(trial_id="trial-A"))
    # A different trial (scope) must not see trial-A's memory.
    result = arm.retrieve(RetrievalRequest(query_text="alpha"), _ctx(trial_id="trial-B"))
    assert result.payloads == {}
    assert result.total_matched == 0


def test_retrieve_requires_query_text():
    with pytest.raises(ValueError, match="query_text"):
        _arm().retrieve(RetrievalRequest(), _ctx())


def test_reset_clears_only_its_scope():
    arm = _arm()
    arm.write("m1", "alpha beta", _ctx(trial_id="trial-A"))
    arm.write("m2", "alpha gamma", _ctx(trial_id="trial-B"))
    arm.reset("trial-A")
    # trial-A wiped, trial-B intact.
    assert (
        arm.retrieve(RetrievalRequest(query_text="alpha"), _ctx(trial_id="trial-A")).payloads == {}
    )
    kept = arm.retrieve(RetrievalRequest(query_text="alpha"), _ctx(trial_id="trial-B"))
    assert list(kept.payloads) == ["trial-B-2"]


def test_top_k_must_be_positive():
    with pytest.raises(ValueError, match="top_k"):
        StubArm(FakeSemanticClient(), top_k=0)
