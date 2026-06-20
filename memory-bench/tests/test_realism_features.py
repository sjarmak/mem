"""Tests for the structural feature extractor (realism axis 1, mechanical).

No network, no model — every case is pure counting / longest-path arithmetic.
"""

from membench.realism.features import (
    FEATURE_NAMES,
    TaskFeatures,
    TraceStep,
    features_from_sequence,
    features_from_trace_steps,
)
from membench.schemas.sequence import BenchmarkSequence, SequenceStep


def _step(step_id, request, *, tools=(), writes=None, reads=()):
    return SequenceStep(
        step_id=step_id,
        user_request=request,
        available_tools=list(tools),
        expected_memory_writes=dict(writes or {}),
        expected_memory_reads=list(reads),
    )


def test_feature_names_match_dataclass_fields():
    feat = TaskFeatures(1, 2, 3, 4, 5, 6, 7)
    for name in FEATURE_NAMES:
        assert feat.value(name) == float(getattr(feat, name))


def test_value_rejects_unknown_feature():
    feat = TaskFeatures(1, 1, 1, 1, 1, 1, 1)
    try:
        feat.value("not_a_feature")
    except ValueError as exc:
        assert "not_a_feature" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected ValueError")


def test_reduce_counts_tools_and_diversity():
    steps = [
        TraceStep(tools=("read", "read", "grep")),
        TraceStep(tools=("read", "bash")),
    ]
    feat = features_from_trace_steps(steps)
    assert feat.n_steps == 2
    assert feat.n_tool_calls == 5  # read,read,grep,read,bash
    assert feat.tool_diversity == 3  # read,grep,bash


def test_reduce_counts_memory_and_text():
    steps = [
        TraceStep(memory_writes=("m1", "m2"), memory_reads=(), text="abc"),
        TraceStep(memory_writes=("m3",), memory_reads=("m1",), text="de"),
    ]
    feat = features_from_trace_steps(steps)
    assert feat.n_memory_writes == 3
    assert feat.n_memory_reads == 1
    assert feat.task_text_length == 5  # "abc" + "de"


def test_dependency_depth_chain():
    # m1 written at step0, read+rewritten-as-m2 at step1, m2 read at step2 -> depth 2.
    steps = [
        TraceStep(memory_writes=("m1",)),
        TraceStep(memory_writes=("m2",), memory_reads=("m1",)),
        TraceStep(memory_reads=("m2",)),
    ]
    assert features_from_trace_steps(steps).dependency_depth == 2


def test_dependency_depth_zero_without_reads():
    steps = [TraceStep(memory_writes=("m1",)), TraceStep(memory_writes=("m2",))]
    assert features_from_trace_steps(steps).dependency_depth == 0


def test_dependency_depth_ignores_forward_and_self_reads():
    # A step reading an id written LATER (or by itself) creates no dependency.
    steps = [
        TraceStep(memory_writes=("m1",), memory_reads=("m1", "m2")),  # m2 is future/self
        TraceStep(memory_writes=("m2",)),
    ]
    assert features_from_trace_steps(steps).dependency_depth == 0


def test_features_from_sequence_maps_authored_fields():
    seq = BenchmarkSequence(
        sequence_id="s1",
        title="Provision the staging DB",
        goal="DB reachable from the app",
        steps=[
            _step("a", "create the db", tools=["bash", "psql"], writes={"db_url": "x"}),
            _step("b", "wire the app", tools=["edit"], reads=["db_url"]),
        ],
    )
    feat = features_from_sequence(seq)
    assert feat.n_steps == 2
    assert feat.n_tool_calls == 3  # bash, psql, edit
    assert feat.tool_diversity == 3
    assert feat.n_memory_writes == 1
    assert feat.n_memory_reads == 1
    assert feat.dependency_depth == 1
    # title/goal are excluded; only the two request strings count.
    assert feat.task_text_length == len("create the db") + len("wire the app")


def test_empty_sequence_is_all_zero():
    seq = BenchmarkSequence(sequence_id="empty", title="t", steps=[])
    feat = features_from_sequence(seq)
    assert feat == TaskFeatures(0, 0, 0, 0, 0, 0, 0)
