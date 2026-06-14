"""Tests for AbstractSemanticArm — the shared competitive-arm base (mem-lvp.1).

Hermetic: the semantic client is a deterministic token-overlap fake, so no network
and no model. These assert the contract translation every competitive arm inherits.
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


def _ctx() -> StepContext:
    return StepContext(trial_id="t", session_id="s", step_id="step", clock=IdClock())


def _arm(top_k: int = 10) -> StubArm:
    return StubArm(FakeSemanticClient(), top_k=top_k)


def test_is_a_memory_system():
    assert isinstance(_arm(), MemorySystem)


def test_fake_client_satisfies_protocol():
    assert isinstance(FakeSemanticClient(), SemanticMemoryClient)


def test_write_adds_and_emits_write_event():
    ev = _arm().write("m1", "missing import foo", _ctx())
    assert ev.normalized_operation == MemoryOperation.WRITE
    assert ev.written_ids == ["m1"]
    assert ev.target_ids == ["m1"]
    assert ev.backend == MemoryBackend.VECTOR_DB
    assert ev.success


def test_retrieve_ranks_by_overlap_and_caps_top_k():
    arm = _arm(top_k=2)
    ctx = _ctx()
    arm.write("m1", "missing import foo bar", ctx)
    arm.write("m2", "totally unrelated content", ctx)
    arm.write("m3", "missing import baz", ctx)
    result = arm.retrieve(RetrievalRequest(query_text="missing import"), _ctx())
    # m1 and m3 each overlap {missing, import}; m2 overlaps nothing. top_k caps at 2.
    assert list(result.payloads) == ["m1", "m3"]
    assert result.event.retrieved_ids == ["m1", "m3"]
    assert result.event.normalized_operation == MemoryOperation.SEARCH
    assert result.event.query == "missing import"
    assert result.total_matched == 2


def test_retrieve_excludes_zero_overlap():
    arm = _arm()
    ctx = _ctx()
    arm.write("m1", "alpha beta", ctx)
    arm.write("m2", "gamma delta", ctx)
    result = arm.retrieve(RetrievalRequest(query_text="alpha"), _ctx())
    assert list(result.payloads) == ["m1"]
    assert result.payloads["m1"] == "alpha beta"
    assert result.total_matched == 1


def test_retrieve_requires_query_text():
    with pytest.raises(ValueError, match="query_text"):
        _arm().retrieve(RetrievalRequest(), _ctx())


def test_reset_clears_state():
    arm = _arm()
    arm.write("m1", "alpha beta", _ctx())
    arm.reset("trial-1")
    result = arm.retrieve(RetrievalRequest(query_text="alpha"), _ctx())
    assert result.payloads == {}
    assert result.total_matched == 0


def test_top_k_must_be_positive():
    with pytest.raises(ValueError, match="top_k"):
        StubArm(FakeSemanticClient(), top_k=0)
