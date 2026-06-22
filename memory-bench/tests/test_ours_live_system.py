"""Hermetic tests for `OursLiveMemory` — the live forward-capture analog of `ours`.

Injected runners (retrieve + emit) so no built CLI / real store is needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from membench.forward_capture import ForwardCaptureFieldError
from membench.grading.leak_guard import OutcomeLeakError
from membench.memory_systems import build_memory_system, wired_memory_systems
from membench.memory_systems.ours_live_system import (
    FORWARD_CAPTURE_SOURCE,
    OursLiveMemory,
)
from membench.memory_systems.ours_system import OursMemory
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryOperation
from membench.validity import QueryWork

SENTINEL = "SENTINELLEAK0000"


def _ctx() -> StepContext:
    return StepContext(trial_id="t1", session_id="sess-1", step_id="s1", clock=IdClock())


def test_registered_in_factory_and_wired_list() -> None:
    assert "ours-live" in wired_memory_systems()
    arm = build_memory_system("ours-live")
    assert isinstance(arm, OursLiveMemory)
    assert arm.name == "ours-live"


def test_supports_write_true_but_replay_only_ours_unchanged() -> None:
    assert OursLiveMemory.supports_write is True
    # The replay-only arm is left write-free.
    assert OursMemory.supports_write is False


def test_write_emits_forward_capture_event_via_cli() -> None:
    captured: dict[str, list[str]] = {}

    def emit(argv: list[str]) -> dict[str, Any]:
        captured["argv"] = argv
        return {"recorded": 1, "event_id": "forward-capture:sess-1:..:write:ref"}

    arm = OursLiveMemory(store_path="/tmp/store.db", emit_runner=emit)
    event = arm.write(".mem/lessons/lvp.md", "some content", _ctx())

    argv = captured["argv"]
    assert argv[:2] == ["memory-event", "record"]
    # source is the forward-capture literal.
    assert argv[argv.index("--source") + 1] == FORWARD_CAPTURE_SOURCE
    assert argv[argv.index("--op") + 1] == "write"
    assert argv[argv.index("--ref") + 1] == ".mem/lessons/lvp.md"
    assert argv[argv.index("--session") + 1] == "sess-1"
    # Content is never sent — only a reference.
    assert "some content" not in argv

    assert event.normalized_operation is MemoryOperation.WRITE
    assert event.source == FORWARD_CAPTURE_SOURCE
    assert event.written_ids == [".mem/lessons/lvp.md"]


def test_seed_is_noop_no_emit() -> None:
    calls: list[list[str]] = []

    def emit(argv: list[str]) -> dict[str, Any]:
        calls.append(argv)
        return {"recorded": 1}

    arm = OursLiveMemory(store_path="/tmp/store.db", emit_runner=emit)
    arm.seed({"distractor-1": "noise"}, _ctx())
    assert calls == []  # seeding must never capture


def test_retrieve_delegates_to_replay_only_path() -> None:
    def retrieve_runner(query: Any) -> dict[str, Any]:
        return {
            "items": [{"work_id": "mem-prior", "citation": {}, "lessons": []}],
            "total_matched": 1,
        }

    arm = OursLiveMemory(store_path="/tmp/store.db", runner=retrieve_runner)
    from membench.memory_systems.base import RetrievalRequest

    request = RetrievalRequest(
        query_work=QueryWork(work_id="mem-zzz", rig="scix", started="2026-06-02T00:00:00Z"),
        scope="cross_rig",
    )
    result = arm.retrieve(request, _ctx())
    assert "mem-prior" in result.payloads


def test_write_without_emit_runner_or_mem_bin_raises() -> None:
    arm = OursLiveMemory(store_path="/tmp/store.db")
    with pytest.raises(ValueError, match=r"emit_runner.*mem_bin"):
        arm.write("ref", "content", _ctx())


def _recording_emit(calls: list[list[str]]):
    def emit(argv: list[str]) -> dict[str, Any]:
        calls.append(argv)
        return {"recorded": 1, "event_id": "x"}

    return emit


def test_live_write_firewall_raises_on_ref_outcome_leak() -> None:
    """mem-ymxp #3: the LIVE write path value-scans against the arm's known outcome
    labels; a sentinel embedded in the memory_ref RAISES BEFORE any emit — the
    firewall is not decorative on the path that actually runs live."""
    calls: list[list[str]] = []
    arm = OursLiveMemory(
        store_path="/tmp/store.db",
        emit_runner=_recording_emit(calls),
        protected_labels=(SENTINEL,),
    )
    with pytest.raises(OutcomeLeakError):
        arm.write(f"work:mem-x@{SENTINEL}", "content", _ctx())
    assert calls == []  # never emitted


def test_live_write_firewall_raises_on_nested_payload_leak() -> None:
    """mem-ymxp #3 + #1 end-to-end: a nested-in-payload outcome identifier RAISES on
    the LIVE write path, not just the offline projector."""
    calls: list[list[str]] = []
    arm = OursLiveMemory(store_path="/tmp/store.db", emit_runner=_recording_emit(calls))
    with pytest.raises(ForwardCaptureFieldError):
        arm.write(
            "work:mem-x",
            "content",
            _ctx(),
            payload={"diff": {"commit_sha": SENTINEL}},
        )
    assert calls == []  # never emitted


def test_live_write_clean_capture_emits_ref_only() -> None:
    """A clean capture passes the firewall and emits ref-only; a clean payload is
    firewalled then DROPPED (label-side), never forwarded to the store."""
    calls: list[list[str]] = []
    arm = OursLiveMemory(
        store_path="/tmp/store.db",
        emit_runner=_recording_emit(calls),
        protected_labels=(SENTINEL,),
    )
    arm.write("work:mem-prior", "clean content", _ctx(), payload={"bytes": 10})
    assert len(calls) == 1
    argv = calls[0]
    assert argv[argv.index("--ref") + 1] == "work:mem-prior"
    # payload is label-side: never forwarded to the store.
    assert "--payload" not in argv
    assert "bytes" not in " ".join(argv)
