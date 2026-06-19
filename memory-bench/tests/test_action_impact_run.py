"""§4.4 action-impact run harness: pairing, per-arm aggregation, raw 5-axis vector.

Hermetic — the only judge is `StubComparativeJudge` returning a canned action-impact
verdict; no model, network, or container. The harness consumes already-extracted
`AttemptStep` trajectories, so the real-run substrate is never exercised here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from membench.bbon.comparative_judge import StubComparativeJudge
from membench.bbon.models import AttemptStep, deterministic_id
from membench.metrics.action_impact_run import (
    ArmStepTrajectory,
    aggregate_metrics,
    pair_trajectories,
    run_action_impact,
    score_arm_run,
)
from membench.schemas.metrics import ActionImpactMetrics


def _step(index: int, kind: str, **inp: Any) -> AttemptStep:
    body = {"i": index, "kind": kind, "input": inp}
    return AttemptStep(
        id=deterministic_id(body),
        attempt_id=deterministic_id({"a": kind, "i": index}),
        step_index=index,
        kind=kind,
        input=inp,
    )


def _verdict_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "memory_changed_tool_choice": False,
        "memory_changed_plan": False,
        "memory_changed_output": False,
        "memory_prevented_known_failure": False,
        "memory_improved_verification": False,
        "rationale": "stub",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _traj(arm: str, seq: str, step: str, steps: list[AttemptStep], **kw: Any) -> ArmStepTrajectory:
    return ArmStepTrajectory(arm=arm, sequence_id=seq, step_id=step, steps=tuple(steps), **kw)


# --------------------------------------------------------------------------- #
# pairing
# --------------------------------------------------------------------------- #
def test_pair_trajectories_maps_on_off_and_context() -> None:
    control = _traj("none", "s1", "k1", [_step(0, "Read")], status="completed")
    treated = _traj(
        "ours", "s1", "k1", [_step(0, "Grep")], status="completed",
        work_id="mem-x", known_failure="flaky import",
    )
    inp = pair_trajectories(control, treated)
    assert inp.on_steps == treated.steps  # on == treated (memory-enabled)
    assert inp.off_steps == control.steps  # off == none control
    assert inp.on_status == "completed"
    assert inp.work_id == "mem-x"
    assert inp.known_failure == "flaky import"


def test_pair_trajectories_rejects_step_mismatch() -> None:
    control = _traj("none", "s1", "k1", [_step(0, "Read")])
    treated = _traj("ours", "s1", "k2", [_step(0, "Read")])
    with pytest.raises(ValueError, match="different steps"):
        pair_trajectories(control, treated)


# --------------------------------------------------------------------------- #
# aggregation — raw 5-axis, None-aware
# --------------------------------------------------------------------------- #
def test_aggregate_counts_true_over_decided() -> None:
    metrics = [
        ActionImpactMetrics(memory_changed_tool_choice=True),
        ActionImpactMetrics(memory_changed_tool_choice=False),
        ActionImpactMetrics(memory_changed_tool_choice=True),
    ]
    agg = aggregate_metrics("ours", metrics)
    tool_choice = agg.tallies[0]
    assert tool_choice.axis == "memory_changed_tool_choice"
    assert tool_choice.true_count == 2
    assert tool_choice.decided_count == 3
    assert tool_choice.rate == pytest.approx(2 / 3)
    assert agg.n_pairs == 3


def test_aggregate_none_axis_is_undecided_not_zero() -> None:
    # Every pair leaves the outcome axes None (no judge decided them).
    metrics = [ActionImpactMetrics(memory_prevented_known_failure=None) for _ in range(3)]
    agg = aggregate_metrics("ours", metrics)
    prevented = next(t for t in agg.tallies if t.axis == "memory_prevented_known_failure")
    assert prevented.decided_count == 0
    assert prevented.rate is None  # never imputed to 0.0


def test_aggregate_axes_vector_canonical_order() -> None:
    agg = aggregate_metrics("ours", [ActionImpactMetrics()])
    assert tuple(t.axis for t in agg.tallies) == (
        "memory_changed_tool_choice",
        "memory_changed_plan",
        "memory_changed_output",
        "memory_prevented_known_failure",
        "memory_improved_verification",
    )
    assert agg.axes() == (None, None, None, None, None)


# --------------------------------------------------------------------------- #
# score_arm_run — pairing + judge + aggregation end to end
# --------------------------------------------------------------------------- #
def test_score_arm_run_judge_decides_axes() -> None:
    # A real behavioral difference (different tool) forces the judge call; the stub
    # verdict flags tool_choice changed.
    control = [_traj("none", "s1", "k1", [_step(0, "Read")], status="completed")]
    treated = [_traj("ours", "s1", "k1", [_step(0, "Grep")], status="completed")]
    judge = StubComparativeJudge(fn=lambda _p: _verdict_json(memory_changed_tool_choice=True))
    agg = score_arm_run(control, treated, treated_name="ours", judge=judge)
    tool_choice = agg.tallies[0]
    assert tool_choice.true_count == 1
    assert tool_choice.decided_count == 1
    assert agg.arm == "ours"


def test_score_arm_run_missing_control_raises() -> None:
    control = [_traj("none", "s1", "k1", [_step(0, "Read")])]
    treated = [_traj("ours", "s1", "k2", [_step(0, "Read")])]  # no none twin for k2
    with pytest.raises(ValueError, match="no 'none' control"):
        score_arm_run(control, treated, treated_name="ours", judge=None)


def test_score_arm_run_duplicate_control_raises() -> None:
    control = [
        _traj("none", "s1", "k1", [_step(0, "Read")]),
        _traj("none", "s1", "k1", [_step(0, "Grep")]),
    ]
    treated = [_traj("ours", "s1", "k1", [_step(0, "Read")])]
    with pytest.raises(ValueError, match="duplicate control"):
        score_arm_run(control, treated, treated_name="ours", judge=None)


def test_score_arm_run_no_judge_leaves_semantic_axes_none() -> None:
    # Identical streams + equal status: mechanical pre-filter sets the behavioral axes
    # False; with no judge the outcome axes stay None.
    steps = [_step(0, "Read"), _step(1, "Edit")]
    control = [_traj("none", "s1", "k1", steps, status="completed")]
    treated = [_traj("ours", "s1", "k1", list(steps), status="completed")]
    agg = score_arm_run(control, treated, treated_name="ours", judge=None)
    by_axis = {t.axis: t for t in agg.tallies}
    assert by_axis["memory_changed_tool_choice"].decided_count == 1  # mechanical False
    assert by_axis["memory_changed_tool_choice"].true_count == 0
    assert by_axis["memory_prevented_known_failure"].rate is None  # judge seam untouched


# --------------------------------------------------------------------------- #
# run_action_impact — multi-arm driver
# --------------------------------------------------------------------------- #
def test_run_action_impact_scores_each_treated_arm() -> None:
    control = [_traj("none", "s1", "k1", [_step(0, "Read")], status="completed")]
    ours = [_traj("ours", "s1", "k1", [_step(0, "Grep")], status="completed")]
    builtin = [_traj("builtin", "s1", "k1", [_step(0, "Read")], status="completed")]
    judge = StubComparativeJudge(fn=lambda _p: _verdict_json(memory_changed_plan=True))
    out = run_action_impact({"none": control, "ours": ours, "builtin": builtin}, judge=judge)
    assert set(out) == {"ours", "builtin"}  # control itself is not a treated arm
    # ours diverged (Grep vs Read) -> judge consulted -> plan flagged.
    plan = next(t for t in out["ours"].tallies if t.axis == "memory_changed_plan")
    assert plan.true_count == 1
    # builtin identical to control + equal status -> zero-impact pre-filter, no judge.
    builtin_plan = next(t for t in out["builtin"].tallies if t.axis == "memory_changed_plan")
    assert builtin_plan.true_count == 0
    assert builtin_plan.decided_count == 1


def test_run_action_impact_requires_control() -> None:
    with pytest.raises(ValueError, match="must include the 'none' control"):
        run_action_impact({"ours": []}, judge=None)


def test_arm_action_impact_to_dict_shape() -> None:
    agg = aggregate_metrics("ours", [ActionImpactMetrics(memory_changed_output=True)])
    d = agg.to_dict()
    assert d["arm"] == "ours"
    assert d["n_pairs"] == 1
    assert d["axes"]["memory_changed_output"]["rate"] == pytest.approx(1.0)
    assert d["axes"]["memory_changed_tool_choice"]["rate"] is None
