from membench.dataset import load_sequence
from membench.schemas import (
    Condition,
    ExperimentConfig,
    MemoryEvent,
    MemoryOperation,
    MetricsBundle,
)
from membench.schemas.config import AgentConfig, MemoryConfig
from tests.paths import FIXTURE


def test_conditions_enum_values():
    assert {c.value for c in Condition} == {
        "no_memory",
        "oracle_memory",
        "memory_enabled",
    }


def test_memory_event_roundtrip():
    ev = MemoryEvent(
        event_id="ev-1",
        trial_id="t",
        session_id="s",
        step_id="st",
        timestamp="1970-01-01T00:00:01Z",
        concrete_tool="Write(...)",
        normalized_operation=MemoryOperation.WRITE,
        backend="filesystem",
        written_ids=["m1"],
    )
    assert MemoryEvent.model_validate_json(ev.model_dump_json()).written_ids == ["m1"]


def test_memory_event_source_defaults_to_harness():
    """`source` is optional here (TS makes it required) so existing in-harness
    construction sites need no change; the default marks the in-process harness."""
    ev = MemoryEvent(
        event_id="ev-1",
        trial_id="t",
        session_id="s",
        step_id="st",
        timestamp="1970-01-01T00:00:01Z",
        concrete_tool="Write(...)",
        normalized_operation=MemoryOperation.WRITE,
        backend="filesystem",
    )
    assert ev.source == "harness"


def test_memory_event_source_forward_capture_literal():
    ev = MemoryEvent(
        event_id="ev-2",
        trial_id="t",
        session_id="s",
        step_id="st",
        timestamp="1970-01-01T00:00:01Z",
        concrete_tool="Read(...)",
        normalized_operation=MemoryOperation.READ,
        backend="kg",
        source="forward-capture",
    )
    assert MemoryEvent.model_validate_json(ev.model_dump_json()).source == "forward-capture"


def test_metrics_pass_alias():
    bundle = MetricsBundle()
    dumped = bundle.model_dump(by_alias=True)
    assert "pass" in dumped["task"]
    assert dumped["task"]["pass"] is False


def test_fixture_loads_and_has_cross_session_dependency():
    seq = load_sequence(FIXTURE)
    assert len(seq.steps) == 3
    last = seq.steps[-1]
    # The final step must read memory established by earlier steps (synthesis).
    assert set(last.expected_memory_reads) == {"conv-bind-loopback", "conv-shared-types"}


def test_experiment_config_default_conditions():
    exp = ExperimentConfig(
        experiment_id="e",
        agent=AgentConfig(agent_config_id="a"),
        memory=MemoryConfig(memory_config_id="m", system="filesystem"),
        dataset_id="d",
    )
    assert exp.conditions == [
        Condition.NO_MEMORY,
        Condition.ORACLE_MEMORY,
        Condition.MEMORY_ENABLED,
    ]
