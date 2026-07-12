import pytest

from membench.dataset import load_sequence
from membench.memory_systems.ours_live_system import OursLiveMemory
from membench.report.comparison import build_comparison
from membench.report.synthetic_arms import _confusion_staleness
from membench.runner.conditions import (
    ENV_MEM_BIN,
    ENV_MEM_STORE,
    ENV_MEMORY_SYSTEM,
    _system_for,
    run_sequence,
)
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from tests.paths import FIXTURE


def _experiment(context_budget_tokens=None):
    return ExperimentConfig(
        experiment_id="test-exp",
        agent=AgentConfig(agent_config_id="scripted-ref", runtime="scripted"),
        memory=MemoryConfig(
            memory_config_id="filesystem",
            system="filesystem",
            context_budget_tokens=context_budget_tokens,
        ),
        dataset_id="gascity-backend-conventions",
    )


def test_env_override_selects_memory_enabled_arm(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "none")
    system, config_id = _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)
    assert system.name == "none"
    assert config_id == "none"


def test_env_override_unset_uses_config_system(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_MEMORY_SYSTEM, raising=False)
    system, config_id = _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)
    assert system.name == "filesystem"
    assert config_id == "filesystem"


def test_env_override_unknown_raises(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "not-a-real-arm")
    with pytest.raises(ValueError, match="not a wired memory system"):
        _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)


def test_env_override_ignored_for_fixed_controls(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "ours")
    none_system, _ = _system_for(Condition.NO_MEMORY, _experiment(), tmp_path)
    oracle_system, _ = _system_for(Condition.ORACLE_MEMORY, _experiment(), tmp_path)
    assert none_system.name == "none"
    assert oracle_system.name == "oracle"


def test_env_ours_live_wires_working_launch_path(monkeypatch, tmp_path):
    """mem-ymxp #4: MEMBENCH_MEMORY_SYSTEM=ours-live yields a WORKING live write-capture
    arm — mem_bin + store resolved from env — not one that raises on first write."""
    store = tmp_path / "store.db"
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "ours-live")
    monkeypatch.setenv(ENV_MEM_STORE, str(store))
    monkeypatch.setenv(ENV_MEM_BIN, str(tmp_path / "mem"))
    system, config_id = _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)
    assert system.name == "ours-live"
    assert config_id == "ours-live"
    assert isinstance(system, OursLiveMemory)
    # The construction contract is satisfied: resolving the emit runner does NOT raise
    # (the launch-path gap in mem-mtqi was exactly an arm with no mem_bin/emit_runner).
    system._resolve_emit_runner()


def test_env_ours_live_missing_store_raises(monkeypatch, tmp_path):
    """A live arm selected by env with no store path is a loud LAUNCH error, not an arm
    that silently raises later on first use."""
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "ours-live")
    monkeypatch.delenv(ENV_MEM_STORE, raising=False)
    monkeypatch.setenv(ENV_MEM_BIN, str(tmp_path / "mem"))
    with pytest.raises(ValueError, match=ENV_MEM_STORE):
        _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)


def test_env_ours_live_missing_mem_bin_raises(monkeypatch, tmp_path):
    """The symmetric guard: a live arm with no mem_bin is also a loud LAUNCH error."""
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "ours-live")
    monkeypatch.setenv(ENV_MEM_STORE, str(tmp_path / "store.db"))
    monkeypatch.delenv(ENV_MEM_BIN, raising=False)
    with pytest.raises(ValueError, match=ENV_MEM_BIN):
        _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)


def test_env_ours_live_blank_store_raises(monkeypatch, tmp_path):
    """A whitespace-only env value is treated as absent — a blank path must not slip
    through to defer as an opaque subprocess error later."""
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "ours-live")
    monkeypatch.setenv(ENV_MEM_STORE, "   ")
    monkeypatch.setenv(ENV_MEM_BIN, str(tmp_path / "mem"))
    with pytest.raises(ValueError, match=ENV_MEM_STORE):
        _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)


def test_env_ours_replay_also_wired_from_env(monkeypatch, tmp_path):
    """The same wiring serves replay-only `ours`: env-selected `ours` resolves its store
    + mem_bin so retrieval works, instead of building an arm with no store."""
    store = tmp_path / "store.db"
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "ours")
    monkeypatch.setenv(ENV_MEM_STORE, str(store))
    monkeypatch.setenv(ENV_MEM_BIN, str(tmp_path / "mem"))
    system, config_id = _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)
    assert system.name == "ours"
    assert config_id == "ours"


def test_env_builtin_selects_no_store_arm(monkeypatch, tmp_path):
    """mem-mor1 D-F: `builtin` is selectable at launch like none/ours-live, but it is a
    control (the agent's native memory), so it needs NO store / mem_bin — it must build
    cleanly without the live-arm env vars."""
    monkeypatch.setenv(ENV_MEMORY_SYSTEM, "builtin")
    monkeypatch.delenv(ENV_MEM_STORE, raising=False)
    monkeypatch.delenv(ENV_MEM_BIN, raising=False)
    system, config_id = _system_for(Condition.MEMORY_ENABLED, _experiment(), tmp_path)
    assert system.name == "builtin"
    assert config_id == "builtin"
    assert system.supports_write is False


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


def test_context_gate_skips_retrieve_when_request_fits_budget(tmp_path):
    """mem-1m0s: a budget large enough that the step's own request (12 words for
    s3-add-endpoint) does NOT overflow it skips the retrieve entirely and marks
    context_gated — memory would add cost here, not capability."""
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(context_budget_tokens=50), fs_base_dir=tmp_path)
    by_cond = run.by_condition()
    s3 = next(t for t in by_cond[Condition.MEMORY_ENABLED] if t.step_id == "s3-add-endpoint")
    assert s3.metrics.retrieval.context_gated is True
    assert s3.metrics.efficiency.memory_tool_calls == 0
    assert s3.trace.memory_events == []
    assert s3.reward < 1.0  # the gated-off conventions are no longer recalled


@pytest.mark.parametrize("context_budget_tokens", [1, None])
def test_context_gate_allows_retrieve_when_not_overflowing(tmp_path, context_budget_tokens):
    """A budget the step's own request already exceeds (1), and no budget at all
    (None, the always-on default), both leave the gate open: retrieval behaves
    exactly as if the gate did not exist."""
    seq = load_sequence(FIXTURE)
    run = run_sequence(
        seq, _experiment(context_budget_tokens=context_budget_tokens), fs_base_dir=tmp_path
    )
    by_cond = run.by_condition()
    s3 = next(t for t in by_cond[Condition.MEMORY_ENABLED] if t.step_id == "s3-add-endpoint")
    assert s3.metrics.retrieval.context_gated is False
    assert s3.metrics.efficiency.memory_tool_calls == 1
    assert s3.reward == 1.0


def test_context_gate_excludes_gated_trial_from_confusion_staleness(tmp_path):
    """mem-1m0s rework: a context_gated trial must not leak a fabricated 0.0 rate into
    _confusion_staleness's samples. Before the fix, read_attempted was ANDed only with
    bool(required_ids) — not with `not context_gated` — so a gated trial (retrieve
    skipped, retrieved_ids=[]) still reported read_attempted=True and diluted the
    arm's true distractor/stale average with a rate that was never actually measured."""
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(context_budget_tokens=50), fs_base_dir=tmp_path)
    by_cond = run.by_condition()
    s3 = next(t for t in by_cond[Condition.MEMORY_ENABLED] if t.step_id == "s3-add-endpoint")
    assert s3.metrics.retrieval.context_gated is True
    assert s3.metrics.retrieval.read_attempted is False

    confusion, staleness = _confusion_staleness(run.trials)
    assert confusion == []
    assert staleness == []


def test_context_gate_does_not_affect_oracle_condition(tmp_path):
    """The gate only applies to MEMORY_ENABLED — oracle stays the fixed always-on
    ceiling control regardless of the configured budget."""
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(context_budget_tokens=50), fs_base_dir=tmp_path)
    by_cond = run.by_condition()
    s3_oracle = next(t for t in by_cond[Condition.ORACLE_MEMORY] if t.step_id == "s3-add-endpoint")
    assert s3_oracle.metrics.retrieval.context_gated is False
    assert s3_oracle.metrics.efficiency.memory_tool_calls == 1
    assert s3_oracle.reward == 1.0


def test_comparison_excludes_context_gated_trials_from_retrieval_means(tmp_path):
    """mem-1m0s rejection issue #2: _summarize's retrieval-metric means (precision/recall/
    mrr/nDCG) must exclude context_gated trials the same way _confusion_staleness already
    does, else the gated s3 trial's fabricated retrieved_ids=[] zero dilutes the arm's true
    average with a rate that was never measured."""
    seq = load_sequence(FIXTURE)
    run = run_sequence(seq, _experiment(context_budget_tokens=50), fs_base_dir=tmp_path)
    report = build_comparison(run)
    mem = report.summaries["memory_enabled"]

    by_cond = run.by_condition()
    mem_trials = by_cond[Condition.MEMORY_ENABLED]
    retrieving = [t for t in mem_trials if not t.metrics.retrieval.context_gated]
    assert len(retrieving) < len(mem_trials)  # the gate did exclude at least s3

    expected_recall = sum(t.metrics.retrieval.recall_at_k for t in retrieving) / len(retrieving)
    expected_precision = sum(t.metrics.retrieval.precision_at_k for t in retrieving) / len(
        retrieving
    )
    expected_mrr = sum(t.metrics.retrieval.mrr for t in retrieving) / len(retrieving)
    expected_ndcg = sum(t.metrics.retrieval.nDCG for t in retrieving) / len(retrieving)
    assert mem.mean_recall_at_k == expected_recall
    assert mem.mean_precision_at_k == expected_precision
    assert mem.mean_mrr == expected_mrr
    assert mem.mean_ndcg == expected_ndcg

    # n_steps still counts every step (an all-gated condition must not shrink the
    # denominator used for reward/pass_rate/tokens).
    assert mem.n_steps == len(mem_trials)


def _two_step_sequence(writes_s1, writes_s2):
    from membench.schemas.sequence import BenchmarkSequence, SequenceStep

    return BenchmarkSequence(
        sequence_id="seq-conflict",
        title="t",
        steps=[
            SequenceStep(step_id="s1", user_request="a", expected_memory_writes=writes_s1),
            SequenceStep(step_id="s2", user_request="b", expected_memory_writes=writes_s2),
        ],
    )


def test_oracle_pool_conflicting_duplicate_memory_id_raises(tmp_path):
    import pytest

    seq = _two_step_sequence({"conv": "use snake_case"}, {"conv": "use camelCase"})
    with pytest.raises(ValueError, match=r"oracle pool conflict.*'conv'.*'s1'.*'s2'"):
        run_sequence(seq, _experiment(), fs_base_dir=tmp_path)


def test_oracle_pool_identical_rewrite_is_allowed(tmp_path):
    seq = _two_step_sequence({"conv": "use snake_case"}, {"conv": "use snake_case"})
    run = run_sequence(seq, _experiment(), fs_base_dir=tmp_path)
    assert len(run.trials) == 6  # 2 steps x 3 conditions, no crash


def test_agent_crash_fails_loud_with_trial_context(tmp_path):
    import pytest

    class CrashingAgent:
        agent_config_id = "crash-ref"

        def run_step(self, step, available_memory, ctx):
            raise KeyError("boom")

    seq = load_sequence(FIXTURE)
    with pytest.raises(RuntimeError, match=r"condition 'no_memory'.*step 's1-"):
        run_sequence(seq, _experiment(), agent=CrashingAgent(), fs_base_dir=tmp_path)


def test_superseded_must_be_a_real_prior_write(tmp_path):
    # _assert_superseded_written: a sequence marking an id superseded that no step ever
    # writes would make the staleness signal silently unretrievable — fail fast instead.
    import pytest

    from membench.schemas.sequence import BenchmarkSequence, SequenceStep

    bad = BenchmarkSequence(
        sequence_id="bad-seq",
        title="dangling supersession",
        steps=[
            SequenceStep(
                step_id="s0",
                user_request="record",
                expected_memory_writes={"v2": "new value"},
                superseded_memory_ids=["v1-never-written"],
            ),
        ],
    )
    with pytest.raises(ValueError, match="never written by any step"):
        run_sequence(bad, _experiment(), fs_base_dir=tmp_path)
