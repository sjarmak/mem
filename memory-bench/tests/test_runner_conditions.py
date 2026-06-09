from membench.dataset import load_sequence
from membench.report.comparison import build_comparison
from membench.runner.conditions import run_sequence
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from tests.paths import FIXTURE


def _experiment():
    return ExperimentConfig(
        experiment_id="test-exp",
        agent=AgentConfig(agent_config_id="scripted-ref", runtime="scripted"),
        memory=MemoryConfig(memory_config_id="filesystem", system="filesystem"),
        dataset_id="gascity-backend-conventions",
    )


def test_runs_all_three_conditions_for_every_step(tmp_path):
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(), fs_base_dir=tmp_path)
    # 3 steps x 3 conditions = 9 trials.
    assert len(run.trials) == 9
    by_cond = run.by_condition()
    assert set(by_cond) == set(Condition)
    for trials in by_cond.values():
        assert len(trials) == 3


def test_no_memory_underperforms_oracle_on_memory_dependent_step(tmp_path):
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(), fs_base_dir=tmp_path)
    by_cond = run.by_condition()

    def step_reward(cond, step_id):
        return next(t.reward for t in by_cond[cond] if t.step_id == step_id)

    # The memory-dependent step: oracle fully passes, no_memory only the stateless check.
    assert step_reward(Condition.ORACLE_MEMORY, "s3-add-endpoint") == 1.0
    assert step_reward(Condition.NO_MEMORY, "s3-add-endpoint") < 0.5
    # memory_enabled recovers prior-session writes → matches oracle.
    assert step_reward(Condition.MEMORY_ENABLED, "s3-add-endpoint") == 1.0


def test_memory_enabled_retrieval_and_retention_metrics(tmp_path):
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(), fs_base_dir=tmp_path)
    by_cond = run.by_condition()
    s3 = next(t for t in by_cond[Condition.MEMORY_ENABLED] if t.step_id == "s3-add-endpoint")
    assert s3.metrics.retrieval.recall_at_k == 1.0
    assert s3.metrics.retrieval.relevant_memory_retrieved is True
    assert s3.metrics.retrieval.missed_required_memory_count == 0

    s1 = next(t for t in by_cond[Condition.MEMORY_ENABLED] if t.step_id == "s1-establish-binding")
    assert s1.metrics.retention.expected_memory_written is True
    assert s1.metrics.retention.write_hit_rate == 1.0


def test_comparison_interpretation_is_discriminating(tmp_path):
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(), fs_base_dir=tmp_path)
    report = build_comparison(run)
    o = report.summaries["oracle_memory"].mean_reward
    n = report.summaries["no_memory"].mean_reward
    assert o > n  # task discriminates memory benefit (DIV-3 gate passes)
    assert "performing well" in report.interpretation
    assert "| condition |" in report.to_markdown()


def test_no_memory_condition_does_not_invoke_memory(tmp_path):
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(), fs_base_dir=tmp_path)
    by_cond = run.by_condition()
    for t in by_cond[Condition.NO_MEMORY]:
        assert t.metrics.efficiency.memory_tool_calls == 0
        assert t.trace.memory_events == []


def test_establishing_steps_emit_no_phantom_retrieve(tmp_path):
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(), fs_base_dir=tmp_path)
    by_cond = run.by_condition()
    # s1 has no expected_memory_reads → oracle must not issue a (phantom) retrieve.
    oracle_s1 = next(
        t for t in by_cond[Condition.ORACLE_MEMORY] if t.step_id == "s1-establish-binding"
    )
    assert oracle_s1.metrics.efficiency.memory_tool_calls == 0
    assert oracle_s1.trace.memory_events == []
    # memory_enabled s1 still writes (1 memory op), but issues no retrieve.
    mem_s1 = next(
        t for t in by_cond[Condition.MEMORY_ENABLED] if t.step_id == "s1-establish-binding"
    )
    assert mem_s1.metrics.efficiency.memory_tool_calls == 1
    assert all(e.normalized_operation.value == "write" for e in mem_s1.trace.memory_events)
