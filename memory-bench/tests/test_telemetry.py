from membench.dataset import load_sequence
from membench.runner.conditions import run_sequence
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.telemetry import trace_to_atif, trace_to_spans
from tests.paths import FIXTURE


def _trace_for(condition, tmp_path):
    seq = load_sequence(FIXTURE)
    exp = ExperimentConfig(
        experiment_id="tel-exp",
        agent=AgentConfig(agent_config_id="scripted-ref"),
        memory=MemoryConfig(memory_config_id="filesystem", system="filesystem"),
        dataset_id="d",
    )
    run = run_sequence(seq, exp, fs_base_dir=tmp_path)
    return next(t.trace for t in run.by_condition()[condition] if t.step_id == "s3-add-endpoint")


def test_otel_spans_have_genai_root_and_memory_children(tmp_path):
    trace = _trace_for(Condition.MEMORY_ENABLED, tmp_path)
    spans = trace_to_spans(trace)
    names = [s["name"] for s in spans]
    assert "memory_eval.step" in names
    assert any(n.startswith("membench.memory.") for n in names)

    root = next(s for s in spans if s["name"] == "memory_eval.step")
    assert root["attributes"]["gen_ai.system"] == "anthropic"
    assert root["attributes"]["membench.step_id"] == "s3-add-endpoint"
    # Children carry the parent's span id.
    children = [s for s in spans if s["parent_span_id"] is not None]
    assert all(c["parent_span_id"] == root["span_id"] for c in children)


def test_atif_is_derived_and_lists_memory_actions(tmp_path):
    trace = _trace_for(Condition.MEMORY_ENABLED, tmp_path)
    atif = trace_to_atif(trace)
    assert atif["format"] == "atif-derived"
    assert atif["derived_from"] == "otel"
    assert any(a["action_type"] == "memory_operation" for a in atif["actions"])
