"""Tests for the `builtin` arm — the agent's native memory baseline-to-beat (mem-mor1 D-F).

`builtin` is a no-store arm: mem surfaces nothing and captures nothing. Its distinction
from the `none` control is realized at agent launch (native memory ON vs no memory at
all); the in-store half tested here carries an honest empty store interaction and a
distinct `builtin` telemetry label so the forward-capture pool attributes results to the
right condition.
"""

from __future__ import annotations

import pytest

from membench.memory_systems import (
    BuiltinMemory,
    NoneMemory,
    build_memory_system,
    wired_memory_systems,
)
from membench.memory_systems.base import RetrievalRequest
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryOperation


def _ctx() -> StepContext:
    return StepContext(trial_id="t1", session_id="sess-1", step_id="s1", clock=IdClock())


def _req() -> RetrievalRequest:
    return RetrievalRequest(query_text="q", requested_ids=["m1"])


def test_registered_in_factory_and_wired_list() -> None:
    assert "builtin" in wired_memory_systems()
    arm = build_memory_system("builtin")
    assert isinstance(arm, BuiltinMemory)
    assert arm.name == "builtin"


def test_retrieve_surfaces_nothing_labelled_builtin() -> None:
    arm = BuiltinMemory()
    arm.reset("t")
    result = arm.retrieve(_req(), _ctx())
    # mem's store is uninvolved — the agent's native memory recalls off-store.
    assert result.payloads == {}
    # ...but the event is labelled `builtin`, NOT `none`: the pool must be able to
    # distinguish the native-memory baseline from the no-memory control.
    assert result.event.concrete_tool == "builtin"
    assert result.event.normalized_operation is MemoryOperation.SEARCH


def test_does_not_support_writes() -> None:
    # No mem-store capture: the agent's native memory captures off-store, opaque to mem.
    assert BuiltinMemory.supports_write is False
    with pytest.raises(NotImplementedError, match="native memory"):
        BuiltinMemory().write("m1", "x", _ctx())


def test_seed_is_noop_no_store_state() -> None:
    # A no-store arm holds no extra state; seeding short-circuits via supports_write
    # (the base MemorySystem.seed contract) and never raises through write().
    arm = BuiltinMemory()
    arm.reset("t")
    arm.seed({"distractor-1": "noise"}, _ctx())  # must not raise
    assert arm.retrieve(_req(), _ctx()).payloads == {}


def test_distinct_telemetry_label_from_none_control() -> None:
    # Both arms surface no mem payload, but they encode different conditions and must
    # be labelled distinctly so the pool does not conflate the baseline with the control.
    builtin_event = BuiltinMemory().retrieve(_req(), _ctx()).event
    none_event = NoneMemory().retrieve(_req(), _ctx()).event
    assert builtin_event.concrete_tool == "builtin"
    assert none_event.concrete_tool == "none"
    assert builtin_event.concrete_tool != none_event.concrete_tool
