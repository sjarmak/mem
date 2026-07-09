"""Tests for the real-trace structural feature loader (realism axis 1, mem-28n5,
mem-ovi.1).

Pure counting / segmentation over constructed §8 ``Trace`` fixtures — no network,
no model, no live ``.mem`` store. The loader's job is to reduce a real session the
SAME way the synthetic side is reduced, plus declare which features are
recoverable.

Ported near-verbatim from the unmerged mem-28n5 reference (commit c8e4c04). Two
integration tests from that reference are NOT ported here: they exercised an
optional ``features=`` subset argument on ``distance.structural_realism`` that
was never merged (the landed per-feature comparison, ``perfeature_reference.py``,
excludes ``dependency_depth`` a different way — see that module's own tests)."""

from membench.realism.features import FEATURE_NAMES, features_from_sequence
from membench.realism.perfeature_reference import MATCHABLE_FEATURES, MEMORY_OP_FEATURES
from membench.realism.real_loader import (
    RECOVERABLE_FEATURES,
    features_from_trace,
    load_real_features,
    memory_dependency_depth,
    trace_to_steps,
)
from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation
from membench.schemas.sequence import BenchmarkSequence, SequenceStep
from membench.schemas.trace import ToolCall, Trace, TraceMessage


def _trace(*, messages=(), tool_calls=(), memory_events=()):
    return Trace(
        trial_id="t",
        experiment_id="e",
        dataset_id="d",
        task_id="seq/step",
        step_id="step",
        agent_config_id="a",
        memory_config_id="m",
        start_time="2026-06-20T00:00:00Z",
        end_time="2026-06-20T00:01:00Z",
        messages=list(messages),
        tool_calls=list(tool_calls),
        memory_events=list(memory_events),
    )


def _msg(role, content):
    return TraceMessage(role=role, content=content)


def _call(name):
    return ToolCall(name=name)


def _mem(op, *, written=(), retrieved=(), target=(), ts="2026-06-20T00:00:00Z"):
    return MemoryEvent(
        event_id="ev",
        trial_id="t",
        session_id="s",
        step_id="step",
        timestamp=ts,
        concrete_tool="tool",
        normalized_operation=op,
        backend=MemoryBackend.FILESYSTEM,
        written_ids=list(written),
        retrieved_ids=list(retrieved),
        target_ids=list(target),
    )


# --- segmentation -----------------------------------------------------------


def test_steps_segment_by_user_turn():
    trace = _trace(
        messages=[
            _msg("user", "do the first thing"),
            _msg("assistant", "ok"),
            _msg("user", "now the second"),
            _msg("assistant", "done"),
        ]
    )
    steps = trace_to_steps(trace)
    assert len(steps) == 2  # two user turns -> two steps
    assert features_from_trace(trace).n_steps == 2


def test_text_length_counts_user_requests_only():
    trace = _trace(
        messages=[
            _msg("user", "abc"),
            _msg("assistant", "this assistant text must not count"),
            _msg("user", "de"),
        ]
    )
    # Only the two user requests contribute, matching the synthetic side.
    assert features_from_trace(trace).task_text_length == len("abc") + len("de")


def test_no_user_messages_falls_back_to_all_messages():
    trace = _trace(messages=[_msg("assistant", "hi"), _msg("tool", "out")])
    assert features_from_trace(trace).n_steps == 2


def _one_step_per_message(trace):
    return [m.content for m in trace.messages]


def test_custom_segmenter_overrides_default_user_turn_split():
    # Fork B (session -> step segmentation) is injectable, mirroring Fork A
    # (mem_corpus.load_work_records's filters) and Fork C (message_filter): a
    # caller can resolve it without editing this module.
    trace = _trace(messages=[_msg("user", "one"), _msg("assistant", "two"), _msg("user", "three")])
    steps = trace_to_steps(trace, segmenter=_one_step_per_message)
    assert len(steps) == 3
    assert features_from_trace(trace, segmenter=_one_step_per_message).n_steps == 3
    assert [f.n_steps for f in load_real_features([trace], segmenter=_one_step_per_message)] == [3]
    # The default (unspecified) segmenter is unaffected — still user-turn split.
    assert features_from_trace(trace).n_steps == 2


def test_custom_segmenter_preserves_tool_and_memory_consolidation_on_first_step():
    trace = _trace(
        messages=[_msg("user", "a"), _msg("user", "b")],
        tool_calls=[_call("bash")],
        memory_events=[_mem(MemoryOperation.WRITE, written=["k"])],
    )
    steps = trace_to_steps(trace, segmenter=_one_step_per_message)
    assert steps[0].tools == ("bash",)
    assert steps[0].memory_writes == ("k",)
    assert steps[1].tools == ()


def test_tool_or_memory_only_session_is_one_step():
    trace = _trace(tool_calls=[_call("bash"), _call("grep")])
    feat = features_from_trace(trace)
    assert feat.n_steps == 1
    assert feat.n_tool_calls == 2


# --- tool features ----------------------------------------------------------


def test_tool_features_use_invoked_calls():
    trace = _trace(
        messages=[_msg("user", "q")],
        tool_calls=[_call("read"), _call("read"), _call("grep")],
    )
    feat = features_from_trace(trace)
    assert feat.n_tool_calls == 3
    assert feat.tool_diversity == 2  # read, grep


# --- memory features --------------------------------------------------------


def test_memory_counts_from_recorded_id_fields():
    trace = _trace(
        memory_events=[
            _mem(MemoryOperation.WRITE, written=["m1", "m2"]),
            _mem(MemoryOperation.UPDATE, written=["m3"]),
            _mem(MemoryOperation.READ, retrieved=["m1"]),
            _mem(MemoryOperation.SEARCH, retrieved=["m2", "m9"]),
            # DELETE acts on target_ids only — no written/retrieved ids to count.
            _mem(MemoryOperation.DELETE, target=["x"]),
        ]
    )
    feat = features_from_trace(trace)
    assert feat.n_memory_writes == 3  # WRITE(m1,m2) + UPDATE(m3)
    assert feat.n_memory_reads == 3  # READ(m1) + SEARCH(m2,m9)


def test_dependency_depth_is_na_zero_on_flat_session():
    # A real write->read chain exists in the events, but the flat session cannot
    # attribute it to ordered steps, so the structural vector reports depth 0 (N/A).
    trace = _trace(
        memory_events=[
            _mem(MemoryOperation.WRITE, written=["m1"], ts="2026-06-20T00:00:01Z"),
            _mem(MemoryOperation.READ, retrieved=["m1"], ts="2026-06-20T00:00:02Z"),
        ]
    )
    assert features_from_trace(trace).dependency_depth == 0


def test_recoverable_features_exclude_dependency_depth():
    assert "dependency_depth" not in RECOVERABLE_FEATURES
    assert set(RECOVERABLE_FEATURES) == set(FEATURE_NAMES) - {"dependency_depth"}


def test_recoverable_features_built_from_perfeature_reference_groups():
    # RECOVERABLE_FEATURES is not an independently-maintained "FEATURE_NAMES minus
    # one" derivation — it composes the SAME signed-off groups perfeature_reference
    # already reports separately, so the two modules can never silently disagree
    # about which features exclude dependency_depth.
    assert RECOVERABLE_FEATURES == MATCHABLE_FEATURES + MEMORY_OP_FEATURES


# --- dependency-depth diagnostic -------------------------------------------


def test_memory_dependency_depth_recovers_real_chain():
    # m1 written, then read+rewritten as m2, then m2 read -> depth 2 over events.
    trace = _trace(
        memory_events=[
            _mem(MemoryOperation.WRITE, written=["m1"], ts="2026-06-20T00:00:01Z"),
            _mem(
                MemoryOperation.UPDATE,
                written=["m2"],
                retrieved=["m1"],
                ts="2026-06-20T00:00:02Z",
            ),
            _mem(MemoryOperation.READ, retrieved=["m2"], ts="2026-06-20T00:00:03Z"),
        ]
    )
    assert memory_dependency_depth(trace) == 2


def test_memory_dependency_depth_orders_by_timestamp():
    # Events supplied out of order; ordering by timestamp still finds the chain.
    trace = _trace(
        memory_events=[
            _mem(MemoryOperation.READ, retrieved=["m1"], ts="2026-06-20T00:00:09Z"),
            _mem(MemoryOperation.WRITE, written=["m1"], ts="2026-06-20T00:00:01Z"),
        ]
    )
    assert memory_dependency_depth(trace) == 1


def test_memory_dependency_depth_zero_without_chain():
    trace = _trace(
        memory_events=[_mem(MemoryOperation.WRITE, written=["m1"])],
    )
    assert memory_dependency_depth(trace) == 0


# --- corpus loading ----------------------------------------------------------


def test_load_real_features_maps_each_trace():
    traces = [
        _trace(messages=[_msg("user", "one")], tool_calls=[_call("bash")]),
        _trace(messages=[_msg("user", "two"), _msg("user", "three")]),
    ]
    feats = load_real_features(traces)
    assert [f.n_steps for f in feats] == [1, 2]


def _synthetic_corpus():
    return [
        features_from_sequence(
            BenchmarkSequence(
                sequence_id="s",
                title="t",
                steps=[
                    SequenceStep(
                        step_id="a",
                        user_request="create the db then wire it",
                        available_tools=["bash", "psql"],
                        expected_memory_writes={"db_url": "x"},
                    ),
                    SequenceStep(
                        step_id="b",
                        user_request="run the app",
                        available_tools=["edit"],
                        expected_memory_reads=["db_url"],
                    ),
                ],
            )
        )
    ]


def test_real_and_synthetic_features_are_the_same_shape():
    # Both sides reduce through the same TaskFeatures dataclass, over the same
    # FEATURE_NAMES — the precondition for a meaningful cross-corpus comparison.
    real = load_real_features(
        [_trace(messages=[_msg("user", "do a thing")], tool_calls=[_call("bash")])]
    )
    synthetic = _synthetic_corpus()
    assert set(FEATURE_NAMES) == set(real[0].__dataclass_fields__)
    assert real[0].__dataclass_fields__.keys() == synthetic[0].__dataclass_fields__.keys()
