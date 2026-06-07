import pytest

from membench.memory_systems import (
    FilesystemMemory,
    NoneMemory,
    OracleMemory,
    RetrievalRequest,
    build_memory_system,
)
from membench.runtime import IdClock, StepContext


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


def test_build_deferred_system_raises_with_pointer():
    # `builtin` is the paid Harbor audit owned by mem-whi; the factory must reject
    # it with a precise pointer, not pretend it is wired.
    with pytest.raises(ValueError, match="mem-whi"):
        build_memory_system("builtin")
    with pytest.raises(ValueError, match="mem-lvp"):
        build_memory_system("mem0")


def test_build_ours_constructs():
    arm = build_memory_system("ours", runner=lambda q: {"items": []}, store_path="x")
    assert arm.name == "ours"
    assert arm.uses_scope is True
