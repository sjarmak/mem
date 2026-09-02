"""E3a — closure world, ceiling/floor lattice, and the read-everything failure."""

from __future__ import annotations

import pytest

from membench.generators.e3_closure_world import (
    assert_unguessable,
    generate_closure_world,
)
from membench.memory_systems.base import RetrievalRequest, RetrieveResult
from membench.memory_systems.filesystem_system import FilesystemMemory
from membench.metrics.scorers import RetentionInputs, score_retention
from membench.report.e3_closure import (
    ANSWER_KEY_CUE,
    ANSWER_KEY_CUED_FIELDS,
    LOO_SUBSTITUTES,
    MIN_SEEDS,
    ClosureCell,
    _main,
    run_closure_cells,
    score_closure_run,
    summarize_closure,
)
from membench.runner.agent import AgentStepResult, ScriptedAgent
from membench.runner.conditions import run_sequence
from membench.runtime import StepContext
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.sequence import SequenceStep

SEEDS = list(range(MIN_SEEDS))


class SurfaceEverythingMemory(FilesystemMemory):
    """An arm with NO precision: every retrieve returns the whole store.

    That is the read-everything strategy the fixture must defeat — it surfaces the
    superseded v1 alongside the current v2, so the recalled text (and therefore the
    tool argument) carries both literals."""

    name = "surface-everything"

    def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
        everything = self._read_all()
        result = super().retrieve(
            RetrievalRequest(query_text=request.query_text, requested_ids=sorted(everything)),
            ctx,
        )
        return result


def _experiment(arm: str) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id=f"e3-test-{arm}",
        agent=AgentConfig(agent_config_id="scripted-ref"),
        memory=MemoryConfig(memory_config_id=arm, system=arm),
        dataset_id="e3-closure-world",
        conditions=[Condition.MEMORY_ENABLED],
    )


def test_world_is_deterministic_per_seed() -> None:
    a = generate_closure_world(7)
    b = generate_closure_world(7)
    assert a.sequence.model_dump() == b.sequence.model_dump()
    assert a.v2_literal == b.v2_literal
    assert generate_closure_world(8).v2_literal != a.v2_literal


def test_v2_literal_is_absent_from_every_memory_free_surface() -> None:
    for seed in SEEDS:
        world = generate_closure_world(seed)
        assert_unguessable(world)
        for step in world.sequence.steps:
            assert world.v2_literal not in step.user_request
            assert world.v2_literal not in " ".join(step.available_tools)
            assert world.v2_literal not in str(step.environment_state)


def test_assert_unguessable_raises_on_a_leaked_world() -> None:
    world = generate_closure_world(3)
    leaked = world.sequence.model_copy(deep=True)
    leaked.steps[-1].user_request += f" (pin {world.v2_literal})"
    with pytest.raises(ValueError, match="leak"):
        assert_unguessable(
            type(world)(
                seed=world.seed,
                service=world.service,
                v1_id=world.v1_id,
                v2_id=world.v2_id,
                v1_literal=world.v1_literal,
                v2_literal=world.v2_literal,
                apply_step_id=world.apply_step_id,
                sequence=leaked,
            )
        )


def test_scripted_ceiling_is_one_on_id_exact_arms() -> None:
    for arm in ("oracle", "filesystem"):
        cells = run_closure_cells(SEEDS, arm=arm, agent_name="scripted")
        summary = summarize_closure(cells, arm=arm, agent_name="scripted", n_seeds=len(SEEDS))
        assert summary["closure_rate"] == 1.0, f"broken fixture on arm {arm}"
        assert summary["validity"]["halt"] is False  # type: ignore[index]


def test_never_writes_floor_is_zero() -> None:
    cells = run_closure_cells(SEEDS, arm="filesystem", agent_name="never-writes")
    summary = summarize_closure(
        cells, arm="filesystem", agent_name="never-writes", n_seeds=len(SEEDS)
    )
    assert summary["closure_rate"] == 0.0
    assert summary["retention"]["write_hit_rate"] == 0.0  # type: ignore[index]


def test_no_memory_smoke_scores_zero() -> None:
    cells = run_closure_cells(SEEDS, arm="none", agent_name="scripted")
    assert all(not c.closure for c in cells)


def test_v1_surfaced_fails_requires_action() -> None:
    """An arm that surfaces BOTH versions drives both literals into the tool
    argument, tripping the action's forbidden_values — so read-everything is not a
    winning strategy on this fixture."""
    world = generate_closure_world(11)
    run = run_sequence(
        world.sequence,
        _experiment("surface-everything"),
        ScriptedAgent(),
        conditions=[Condition.MEMORY_ENABLED],
        memory_system=SurfaceEverythingMemory(),
    )
    cell = score_closure_run(world, run)
    assert cell.closure is False
    assert cell.apply_reward == 0.0
    # It is not a MISS — the required v2 was retrieved; the stale v1 rode along.
    assert cell.relevant_memory_retrieved is True
    assert cell.missed_required_memory_count == 0
    assert cell.stale_memory_retrieval_rate > 0.0
    assert cell.distractor_retrieval_rate > 0.0


def test_forbidden_write_rate_is_a_directed_channel() -> None:
    clean = score_retention(
        RetentionInputs(written_ids=["v2"], expected_writes=["v2"], forbidden_write_ids=["v1"])
    )
    assert clean.forbidden_write_rate == 0.0
    dirty = score_retention(
        RetentionInputs(
            written_ids=["v2", "v1"],
            expected_writes=["v2", "v1"],
            forbidden_write_ids=["v1"],
        )
    )
    assert dirty.forbidden_write_rate == 0.5
    # over_retention_rate's arithmetic is untouched: both ids were expected.
    assert dirty.over_retention_rate == 0.0


def test_summary_names_the_loo_substitutes_and_absences() -> None:
    summary = summarize_closure([], arm="filesystem", agent_name="scripted", n_seeds=0)
    validity = summary["validity"]
    assert isinstance(validity, dict)
    assert validity["loo_substitutes"] == list(LOO_SUBSTITUTES)
    assert len(LOO_SUBSTITUTES) == 3
    absences = summary["unmeasurable_endpoints"]
    assert isinstance(absences, dict)
    assert set(absences) == {
        "supersession_correct",
        "stale_memory_removed",
        "correct_scope_rate",
    }
    # No store-level temporal LOO is claimed by name anywhere in the summary.
    assert "closedBefore" not in str(summary)


def test_cli_refuses_below_the_seed_floor() -> None:
    with pytest.raises(SystemExit) as exc:
        _main(["--seeds", "5"])
    assert exc.value.code == 2


def test_score_closure_run_refuses_a_multi_condition_run() -> None:
    """Trials are keyed by step_id, so scoring two conditions at once would be
    last-write-wins and make closure depend on condition ORDER."""
    world = generate_closure_world(3)
    experiment = ExperimentConfig(
        experiment_id="e3-test-multi",
        agent=AgentConfig(agent_config_id="scripted-ref"),
        memory=MemoryConfig(memory_config_id="filesystem", system="filesystem"),
        dataset_id="e3-closure-world",
        conditions=[Condition.MEMORY_ENABLED, Condition.NO_MEMORY],
    )
    run = run_sequence(
        world.sequence,
        experiment,
        ScriptedAgent(),
        conditions=[Condition.MEMORY_ENABLED, Condition.NO_MEMORY],
    )
    assert len({t.condition for t in run.trials}) == 2
    with pytest.raises(ValueError, match="SINGLE-condition"):
        score_closure_run(world, run)


def test_summary_discloses_the_answer_key_cue_and_unexercised_channels() -> None:
    cells = run_closure_cells(SEEDS, arm="filesystem", agent_name="scripted")
    summary = summarize_closure(cells, arm="filesystem", agent_name="scripted", n_seeds=len(SEEDS))
    retrieval = summary["retrieval"]
    assert isinstance(retrieval, dict)
    # The two guaranteed fields are named as cued, right where they are reported.
    assert retrieval["relevant_memory_retrieved_rate"] == 1.0
    assert retrieval["distractor_retrieval_rate"] == 0.0
    # The generator requests ONLY the v2 id, so the SAME cue pins the stale/missed
    # fields too — all four are disclosed as cued, and none of them discriminates
    # among arms that honor requested_ids.
    assert retrieval["stale_memory_retrieval_rate"] == 0.0
    assert retrieval["missed_required_memory_count"] == 0.0
    assert retrieval["answer_key_cued"] == [
        "relevant_memory_retrieved_rate",
        "distractor_retrieval_rate",
        "stale_memory_retrieval_rate",
        "missed_required_memory_count",
    ]
    assert "requested_ids" in str(retrieval["answer_key_cue"])

    retention = summary["retention"]
    assert isinstance(retention, dict)
    assert retention["forbidden_write_rate"] == 0.0
    assert retention["forbidden_write_rate_exercised"] is False
    assert "NOT EXERCISED" in str(retention["forbidden_write_rate_note"])

    validity = summary["validity"]
    assert isinstance(validity, dict)
    assert validity["condition"] == Condition.MEMORY_ENABLED.value
    assert validity["write_read_closure_path"] is True
    assert validity["condition_caveat"] == ""
    assert "ID-PRESENCE" in str(validity["floor_is_id_presence"])


def test_control_condition_arm_is_flagged_as_not_a_closure() -> None:
    """--arm oracle runs under ORACLE_MEMORY, where the agent performs ZERO writes,
    so its 1.0 is the oracle path rather than a write->read closure."""
    summary = summarize_closure([], arm="oracle", agent_name="scripted", n_seeds=0)
    validity = summary["validity"]
    assert isinstance(validity, dict)
    assert validity["condition"] == Condition.ORACLE_MEMORY.value
    assert validity["write_read_closure_path"] is False
    assert "ZERO agent" in str(validity["condition_caveat"])


class WritesStaleAgent(ScriptedAgent):
    """Test-local control that RE-PERSISTS the superseded id on a step that forbids
    it, so the forbidden-write channel actually moves. Deliberately test-local: the
    wired reference agents stay as they are."""

    def __init__(self) -> None:
        super().__init__(agent_config_id="writes-stale")

    def run_step(
        self,
        step: SequenceStep,
        available_memory: dict[str, str],
        ctx: StepContext,
    ) -> AgentStepResult:
        result = super().run_step(step, available_memory, ctx)
        result.writes_performed = {
            **result.writes_performed,
            **dict.fromkeys(step.forbidden_memory_writes, "re-persisted stale pin"),
        }
        return result


def test_the_cue_pins_stale_and_missed_on_every_compliant_arm() -> None:
    """The disclosure's claim, measured: on BOTH id-exact arms the cued fields are
    constant, so no retrieval field here separates compliant arms."""
    for arm in ("filesystem", "oracle"):
        cells = run_closure_cells(SEEDS, arm=arm, agent_name="scripted")
        summary = summarize_closure(cells, arm=arm, agent_name="scripted", n_seeds=len(SEEDS))
        retrieval = summary["retrieval"]
        assert isinstance(retrieval, dict)
        for field in ANSWER_KEY_CUED_FIELDS:
            assert field in retrieval, f"{field} disclosed as cued but not reported"
        assert retrieval["stale_memory_retrieval_rate"] == 0.0, arm
        assert retrieval["missed_required_memory_count"] == 0.0, arm
    # The docstring must not promise discrimination it does not have.
    assert "still discriminate" not in ANSWER_KEY_CUE


def test_forbidden_write_rate_denominator_is_the_forbidden_bearing_steps() -> None:
    """Only the apply step carries forbidden ids; averaging over all three trials
    understates the rate ~3x, and the denominator caveat must be emitted whether or
    not the channel was exercised."""
    world = generate_closure_world(5)
    run = run_sequence(
        world.sequence,
        _experiment("filesystem"),
        WritesStaleAgent(),
        conditions=[Condition.MEMORY_ENABLED],
    )
    cell = score_closure_run(world, run)
    all_trial_mean = sum(t.metrics.retention.forbidden_write_rate for t in run.trials) / len(
        run.trials
    )
    assert cell.forbidden_write_rate > 0.0
    assert cell.forbidden_write_rate == pytest.approx(all_trial_mean * len(run.trials))

    summary = summarize_closure([cell], arm="filesystem", agent_name="scripted", n_seeds=1)
    retention = summary["retention"]
    assert isinstance(retention, dict)
    assert retention["forbidden_write_rate_exercised"] is True
    # The caveat does NOT switch off at the moment the number becomes non-trivial.
    assert "DENOMINATOR" in str(retention["forbidden_write_rate_note"])
    assert "DENOMINATOR" in str(retention["forbidden_write_rate_denominator"])


def test_oracle_ceiling_is_named_a_weaker_delivery_only_check() -> None:
    """--arm oracle satisfies the ceiling gate with ZERO agent writes, so it must not
    read as a certified write->read closure."""
    memory_enabled = summarize_closure([], arm="filesystem", agent_name="scripted", n_seeds=0)
    control = summarize_closure([], arm="oracle", agent_name="scripted", n_seeds=0)
    assert memory_enabled["validity"]["ceiling_kind"] == "write_read_closure"  # type: ignore[index]
    assert memory_enabled["validity"]["ceiling_caveat"] == ""  # type: ignore[index]
    assert control["validity"]["ceiling_kind"] == "delivery_only"  # type: ignore[index]
    caveat = str(control["validity"]["ceiling_caveat"])  # type: ignore[index]
    assert "DELIVERY-ONLY" in caveat
    assert "write_hit_rate" in caveat


def _cell(seed: int, *, closure: bool, forbidden_step_writes: int = 0) -> ClosureCell:
    """A hand-built cell, so the summary gate can be driven at values the wired
    agents cannot produce (a broken fixture, a clean-but-real forbidden step)."""
    return ClosureCell(
        seed=seed,
        sequence_id=f"e3-closure-{seed}",
        closure=closure,
        apply_reward=1.0 if closure else 0.0,
        relevant_memory_retrieved=True,
        distractor_retrieval_rate=0.0,
        stale_memory_retrieval_rate=0.0,
        missed_required_memory_count=0,
        write_hit_rate=1.0,
        forbidden_write_rate=0.0,
        forbidden_step_write_count=forbidden_step_writes,
    )


def test_summarize_halts_when_the_ceiling_comes_in_below_one() -> None:
    """The gate the bead turns on: a ceiling run below 1.0 means the FIXTURE is
    broken, and summarize_closure - not just the CLI - must say so."""
    cells = [_cell(i, closure=i != 0) for i in range(MIN_SEEDS)]
    summary = summarize_closure(cells, arm="filesystem", agent_name="scripted", n_seeds=MIN_SEEDS)
    assert summary["closure_rate"] < 1.0
    validity = summary["validity"]
    assert isinstance(validity, dict)
    assert validity["is_ceiling_run"] is True
    assert validity["halt"] is True
    assert validity["ceiling_ok"] is False
    assert "fixture is broken" in str(validity["halt_reason"])

    # ... and the same cells under a NON-ceiling agent do not halt: the gate is the
    # ceiling condition, not merely closure_rate != 1.0.
    floor = summarize_closure(cells, arm="filesystem", agent_name="never-writes", n_seeds=MIN_SEEDS)
    assert floor["validity"]["halt"] is False  # type: ignore[index]


def test_ceiling_halt_makes_the_cli_exit_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """The halt has to reach the exit code, or a broken fixture prints a number and
    passes CI."""
    broken = [_cell(i, closure=i != 0) for i in range(MIN_SEEDS)]
    monkeypatch.setattr(
        "membench.report.e3_closure.run_closure_cells",
        lambda seeds, *, arm, agent_name: broken,
    )
    assert _main(["--seeds", str(MIN_SEEDS), "--arm", "filesystem", "--json"]) == 2


class WritesPermittedIdAgent(ScriptedAgent):
    """Writes an id nobody forbade on the forbidden-bearing step. The forbidden RATE
    stays 0.0 while the channel is genuinely exercised - the case a rate-derived
    `exercised` flag gets wrong."""

    def __init__(self) -> None:
        super().__init__(agent_config_id="writes-permitted")

    def run_step(
        self,
        step: SequenceStep,
        available_memory: dict[str, str],
        ctx: StepContext,
    ) -> AgentStepResult:
        result = super().run_step(step, available_memory, ctx)
        if step.forbidden_memory_writes:
            result.writes_performed = {
                **result.writes_performed,
                "scratch-note": "a write on a forbidden-bearing step, but not a forbidden id",
            }
        return result


def test_exercised_means_a_write_occurred_not_that_the_rate_moved() -> None:
    world = generate_closure_world(7)
    run = run_sequence(
        world.sequence,
        _experiment("filesystem"),
        WritesPermittedIdAgent(),
        conditions=[Condition.MEMORY_ENABLED],
    )
    cell = score_closure_run(world, run)
    # A real write on the forbidden-bearing step, and a clean 0.0 rate for it.
    assert cell.forbidden_step_write_count > 0
    assert cell.forbidden_write_rate == 0.0

    summary = summarize_closure([cell], arm="filesystem", agent_name="scripted", n_seeds=1)
    retention = summary["retention"]
    assert isinstance(retention, dict)
    assert retention["forbidden_write_rate_exercised"] is True
    assert retention["forbidden_step_write_count"] == cell.forbidden_step_write_count
    # A measured clean run must NOT be labelled structurally unexercised.
    assert "NOT EXERCISED" not in str(retention["forbidden_write_rate_note"])


def test_no_writes_on_the_forbidden_step_is_reported_as_unexercised() -> None:
    cells = run_closure_cells(SEEDS, arm="filesystem", agent_name="scripted")
    assert all(c.forbidden_step_write_count == 0 for c in cells)
    summary = summarize_closure(cells, arm="filesystem", agent_name="scripted", n_seeds=len(SEEDS))
    retention = summary["retention"]
    assert isinstance(retention, dict)
    assert retention["forbidden_write_rate_exercised"] is False
    assert "NOT EXERCISED" in str(retention["forbidden_write_rate_note"])
