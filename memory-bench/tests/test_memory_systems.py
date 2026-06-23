import pytest

from membench.memory_systems import (
    FilesystemMemory,
    NoneMemory,
    OracleMemory,
    RetrievalRequest,
    build_memory_system,
)
from membench.runtime import IdClock, StepContext
from tests.semantic_fakes import FakeSemanticClient


def _ctx(trial="t"):
    return StepContext(trial_id=trial, session_id="sess", step_id="st", clock=IdClock())


def _req(query="q", ids=("m1",)):
    return RetrievalRequest(query_text=query, requested_ids=list(ids))


def test_none_retrieve_empty_and_no_write():
    sys = NoneMemory()
    sys.reset("t")
    result = sys.retrieve(_req(), _ctx())
    assert result.payloads == {}
    with pytest.raises(NotImplementedError):
        sys.write("m1", "x", _ctx())


def test_oracle_returns_exact_requested():
    sys = OracleMemory()
    sys.reset("t")
    sys.load({"m1": "content-1", "m2": "content-2"})
    result = sys.retrieve(_req(), _ctx())
    assert result.payloads == {"m1": "content-1"}
    assert result.event.retrieved_ids == ["m1"]


def test_filesystem_persists_across_steps_within_trial(tmp_path):
    sys = FilesystemMemory(base_dir=tmp_path)
    sys.reset("trial-A")
    write_ev = sys.write("conv-x", "loopback only", _ctx("trial-A"))
    assert write_ev.written_ids == ["conv-x"]
    # A later step in the same trial can read it back.
    result = sys.retrieve(_req("conv", ["conv-x"]), _ctx("trial-A"))
    assert result.payloads == {"conv-x": "loopback only"}


def test_filesystem_reset_clears_prior_trial(tmp_path):
    sys = FilesystemMemory(base_dir=tmp_path)
    sys.reset("trial-A")
    sys.write("conv-x", "v", _ctx("trial-A"))
    sys.reset("trial-A")  # new run of same trial id
    result = sys.retrieve(_req("conv", ["conv-x"]), _ctx("trial-A"))
    assert result.payloads == {}


def test_build_unknown_system_raises():
    with pytest.raises(ValueError):
        build_memory_system("totally-unknown")


def test_build_builtin_constructs_as_no_store_arm():
    # mem-mor1 D-F: `builtin` (the agent's native memory baseline-to-beat) is now a
    # wired arm on the free/OAuth path, not a deferred Harbor stub. mem's store stays
    # uninvolved — it surfaces nothing and does not support writes.
    arm = build_memory_system("builtin")
    assert arm.name == "builtin"
    assert arm.supports_write is False
    arm.reset("t")
    result = arm.retrieve(_req(), _ctx())
    assert result.payloads == {}
    # The retrieve event is labelled `builtin`, distinguishing it from the `none`
    # control even though both surface no mem payload.
    assert result.event.concrete_tool == "builtin"
    with pytest.raises(NotImplementedError):
        arm.write("m1", "x", _ctx())


def test_build_ours_constructs():
    arm = build_memory_system("ours", runner=lambda q: {"items": []}, store_path="x")
    assert arm.name == "ours"
    assert arm.uses_scope is True


def test_build_mem0_constructs_with_injected_client():
    # The competitive arms take an injectable client so construction needs no SDK.
    arm = build_memory_system("mem0", client=FakeSemanticClient())
    assert arm.name == "mem0"
    assert arm.uses_scope is False


def test_build_amem_constructs_with_injected_client():
    arm = build_memory_system("a-mem", client=FakeSemanticClient())
    assert arm.name == "a-mem"
    assert arm.uses_scope is False
