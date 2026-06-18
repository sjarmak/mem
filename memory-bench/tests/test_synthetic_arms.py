"""Arms-over-synthetic eval — first measurable lift on the mem-val5 substrate.

Proves the substrate discriminates: none scores 0, oracle scores >0, and a
persisting arm (filesystem) lifts above none. The continuity contrast — filesystem
reaches oracle under run_project but NOT when the cross-task project is run
independently — is the run_project payoff.
"""

from __future__ import annotations

from membench.generators import materialize_project, materialize_world
from membench.report.synthetic_arms import (
    eval_arms_over_project,
    eval_arms_over_sequences,
    format_report,
)
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team


def _world(seed: int = 5) -> EnterpriseWorld:
    return EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Lin", role="qa-engineer", team_id="t1"),
        ],
        channels=[Channel(channel_id="c1", name="kernels", kind="chat")],
        seed=seed,
    )


def _project(seed: int = 5) -> Project:
    return Project(
        project_id=f"world-seed{seed}-project",
        world_id=f"world-seed{seed}",
        name="Acme initiative",
        goal="Reconcile the launch config.",
    )


def _by_arm(results) -> dict:
    return {r.arm: r for r in results}


def test_substrate_discriminates_and_filesystem_lifts(tmp_path) -> None:
    seqs = materialize_world(_world(), _project(), n_tasks=2)
    results = _by_arm(
        eval_arms_over_sequences(seqs, ["none", "oracle", "filesystem"], fs_base_dir=tmp_path)
    )
    # none never recalls -> zero; oracle always -> positive.
    assert results["none"].arm_reward == 0.0
    assert results["oracle"].oracle_reward > 0.0
    # a persisting arm lifts above the no-memory baseline.
    assert results["filesystem"].lift > 0.0


def test_continuity_needs_the_shared_store(tmp_path) -> None:
    # A cross-task project: filesystem reaches oracle under run_project (charter
    # carried across tasks) but falls short when each task is run independently.
    seqs = materialize_project(_world(), _project(), n_tasks=3)
    indep = _by_arm(
        eval_arms_over_sequences(seqs, ["filesystem"], fs_base_dir=tmp_path / "indep")
    )["filesystem"]
    proj = _by_arm(
        eval_arms_over_project(seqs, ["filesystem"], fs_base_dir=tmp_path / "proj")
    )["filesystem"]
    # Under the shared store the arm recalls the charter task 0 wrote, so it scores
    # higher than when each task is isolated. (oracle_gap is ~0 in both: filesystem
    # matches oracle by id-retrieval — the honest ScriptedAgent ceiling.)
    assert proj.arm_reward > indep.arm_reward
    assert proj.lift > indep.lift


def test_oracle_and_none_are_arm_independent(tmp_path) -> None:
    # none/oracle are baselines: identical no matter which arm is under test.
    seqs = materialize_world(_world(), _project(), n_tasks=2)
    results = eval_arms_over_sequences(seqs, ["none", "filesystem"], fs_base_dir=tmp_path)
    a, b = results[0], results[1]
    assert a.none_reward == b.none_reward
    assert a.oracle_reward == b.oracle_reward


def test_lexical_arm_trips_confusion_and_staleness_exact_arms_do_not(tmp_path) -> None:
    # The headline of mem-zt1c: a query/top-k arm surfaces seeded distractors and the
    # superseded v1, so Confusion/Staleness go non-zero — while the id-exact arms
    # (oracle/filesystem) request only the current ids and stay at 0.
    seqs = materialize_world(_world(), _project(), n_tasks=2)
    res = _by_arm(
        eval_arms_over_sequences(seqs, ["oracle", "filesystem", "lexical"], fs_base_dir=tmp_path)
    )
    # exact arms: clean on both axes.
    for arm in ("oracle", "filesystem"):
        assert res[arm].arm_confusion == 0.0
        assert res[arm].arm_staleness == 0.0
    # lexical: confused — it cannot tell a distractor / stale v1 from the truth.
    assert res["lexical"].arm_confusion > 0.0
    assert res["lexical"].arm_staleness > 0.0
    # the rate is averaged over real retrieving trials, surfaced as rate_n.
    assert res["lexical"].rate_n > 0
    # honest ceiling: recall stays perfect at the default top-k, so reward still == oracle
    # (this path does NOT claim reward-level differentiation, only Confusion/Staleness).
    assert res["lexical"].oracle_gap == 0.0


def test_lexical_confusion_persists_under_shared_store(tmp_path) -> None:
    seqs = materialize_project(_world(), _project(), n_tasks=3)
    proj = _by_arm(eval_arms_over_project(seqs, ["lexical"], fs_base_dir=tmp_path))["lexical"]
    assert proj.arm_confusion > 0.0
    assert proj.arm_staleness > 0.0


def test_format_report_has_confusion_and_staleness_columns(tmp_path) -> None:
    seqs = materialize_world(_world(), _project(), n_tasks=2)
    results = eval_arms_over_sequences(seqs, ["lexical"], fs_base_dir=tmp_path)
    report = format_report("synthetic arms", results)
    assert "confusion" in report
    assert "staleness" in report
