"""Tests for the C1.3 base-rate go/no-go gate (mem-apg.3c precondition).

The gate protects the score-vs-information headline (architect finding C1/H1): if the
held-out `trace_error` rarely recurs even on the `none` rung (zero memory), the
deterministic avoid-axis has no dynamic range and the curve is a misleading artifact.

Load-bearing properties under test:
- **C1 denominator**: recurrence is measured over PATH-REACHED tasks only
  (`deterministic_term is not None`); tasks the none-rung agent never engaged
  (`det is None`) are reported as `path_reach_rate`, never folded into the
  recurrence denominator (folding them dilutes recurrence and biases toward NO_GO).
- **H1 CI gate**: the decision keys on the Wilson lower bound, not the point
  estimate, and returns INSUFFICIENT_POWER below a minimum applicable-task count
  rather than coin-flipping a GO/NO_GO at tiny n.
- **M2 repeat collapse**: k repeats of one task collapse to ONE observation
  (majority vote) before the across-task statistic — repeats are within-task, not
  independent tasks, so they must not inflate n.
- **M3 axis**: the gate keys on `deterministic_term` only; the judge's
  `rubric_score` is irrelevant to the dynamic-range question and must not leak in.
"""

import pytest

from membench.grading import (
    GateDecision,
    RewardComponents,
    RewardRecord,
    base_rate_gate,
)


def _rec(work_id, *, reached, resolved, repeat=0, rung="none", rubric=None):
    """A RewardRecord whose deterministic_term is:
    reached=True, resolved=True  -> 1.0 (avoided)
    reached=True, resolved=False -> 0.0 (recurred)
    reached=False                -> None (path not reached)."""
    return RewardRecord(
        work_id=work_id,
        rung=rung,
        repeat_idx=repeat,
        components=RewardComponents(
            path_reached=reached, trace_error_resolved=resolved, rubric_score=rubric
        ),
    )


# --- denominator: path-reached only (architect C1) ----------------------------


def test_recurrence_denominator_excludes_path_not_reached():
    # 6 reached+recurred, 2 reached+resolved, 4 never reached. Recurrence must be
    # 6/8 over APPLICABLE tasks, NOT 6/12 over all — and path_reach_rate = 8/12.
    records = (
        [_rec(f"r{i}", reached=True, resolved=False) for i in range(6)]
        + [_rec(f"a{i}", reached=True, resolved=True) for i in range(2)]
        + [_rec(f"n{i}", reached=False, resolved=False) for i in range(4)]
    )
    v = base_rate_gate(records)
    assert v.n_applicable == 8
    assert v.n_recurred == 6
    assert v.recurrence_rate == pytest.approx(6 / 8)
    assert v.path_reach_rate == pytest.approx(8 / 12)
    assert v.n_tasks == 12


# --- decision: GO / NO_GO / INSUFFICIENT_POWER --------------------------------


def test_high_recurrence_clears_lower_bound_go():
    # 12 tasks all recurred -> phat 1.0, Wilson LB well above the 0.20 default.
    records = [_rec(f"r{i}", reached=True, resolved=False) for i in range(12)]
    v = base_rate_gate(records)
    assert v.decision is GateDecision.GO
    assert v.recurrence_lower_bound >= v.threshold


def test_no_recurrence_is_no_go():
    # The floor is already solved without memory -> no dynamic range -> NO_GO.
    records = [_rec(f"a{i}", reached=True, resolved=True) for i in range(12)]
    v = base_rate_gate(records)
    assert v.decision is GateDecision.NO_GO
    assert v.recurrence_rate == 0.0
    # k=0 gives a Wilson LB of exactly 0.0; abs tolerance documents that "≈ 0" is the
    # contract, so swapping the CI method for one with a tiny non-zero LB at k=0 here
    # would still pass.
    assert v.recurrence_lower_bound == pytest.approx(0.0, abs=1e-9)


def test_default_threshold_and_min_applicable_are_pinned():
    # The whole gate boundary is a function of these two constants; pin them so a
    # silent drift (e.g. threshold 0.20 -> 0.30) cannot pass unnoticed.
    records = [_rec(f"r{i}", reached=True, resolved=False) for i in range(12)]
    v = base_rate_gate(records)
    assert v.threshold == pytest.approx(0.20)
    assert v.min_applicable == 5


def test_point_estimate_above_threshold_but_lower_bound_below_is_not_go():
    # 6 applicable tasks, 2 recurred: phat 0.333 (> the 0.20 threshold) but the
    # Wilson LB is ~0.097 (< threshold), so the decision must be NO_GO. This pins
    # H1: decide on the lower bound, not the point estimate.
    records = [_rec(f"r{i}", reached=True, resolved=False) for i in range(2)] + [
        _rec(f"a{i}", reached=True, resolved=True) for i in range(4)
    ]
    v = base_rate_gate(records)
    assert v.recurrence_rate > v.threshold  # point estimate clears it
    assert v.recurrence_lower_bound < v.threshold  # but the lower bound does not
    assert v.decision is GateDecision.NO_GO


def test_insufficient_power_boundary_is_exact():
    # Straddle the min_applicable boundary (5): 4 applicable -> INSUFFICIENT_POWER,
    # 5 applicable -> a real decision (GO here, since all recurred). Pins the
    # off-by-one the suite would otherwise miss.
    four = [_rec(f"r{i}", reached=True, resolved=False) for i in range(4)]
    assert base_rate_gate(four).decision is GateDecision.INSUFFICIENT_POWER

    five = [_rec(f"r{i}", reached=True, resolved=False) for i in range(5)]
    assert base_rate_gate(five).decision is GateDecision.GO


def test_too_few_applicable_tasks_is_insufficient_power():
    records = [_rec(f"r{i}", reached=True, resolved=False) for i in range(3)]
    v = base_rate_gate(records)
    assert v.decision is GateDecision.INSUFFICIENT_POWER
    assert v.n_applicable == 3
    assert v.n_tasks == 3


def test_path_not_reached_can_starve_applicable_count_to_insufficient():
    # Plenty of tasks, but almost none reach the path -> too few applicable to decide.
    records = [_rec(f"n{i}", reached=False, resolved=False) for i in range(20)] + [
        _rec(f"r{i}", reached=True, resolved=False) for i in range(2)
    ]
    v = base_rate_gate(records)
    assert v.n_applicable == 2
    assert v.decision is GateDecision.INSUFFICIENT_POWER


def test_all_tasks_unreached_is_insufficient_power():
    # The n_applicable == 0 corner: recurrence_rate / path_reach_rate must stay 0.0
    # (no ZeroDivisionError) and the verdict is INSUFFICIENT_POWER, not NO_GO.
    records = [_rec(f"n{i}", reached=False, resolved=False) for i in range(10)]
    v = base_rate_gate(records)
    assert v.n_applicable == 0
    assert v.recurrence_rate == 0.0
    assert v.path_reach_rate == pytest.approx(0.0)
    assert v.decision is GateDecision.INSUFFICIENT_POWER


# --- repeat collapse within task (architect M2) -------------------------------


def test_repeats_collapse_to_one_task_observation():
    # One task, 3 repeats (2 recurred, 1 resolved) -> ONE applicable task that
    # recurred by majority. n_applicable counts TASKS (1), never repeats (3).
    records = [
        _rec("w1", reached=True, resolved=False, repeat=0),
        _rec("w1", reached=True, resolved=False, repeat=1),
        _rec("w1", reached=True, resolved=True, repeat=2),
    ]
    v = base_rate_gate(records)
    assert v.n_applicable == 1
    assert v.n_recurred == 1


def test_task_applicable_by_majority_path_reach():
    # 3 repeats, 2 reached the path, 1 did not -> task is applicable (majority),
    # and recurred (both reached repeats recurred).
    records = [
        _rec("w1", reached=True, resolved=False, repeat=0),
        _rec("w1", reached=True, resolved=False, repeat=1),
        _rec("w1", reached=False, resolved=False, repeat=2),
    ]
    v = base_rate_gate(records)
    assert v.n_applicable == 1
    assert v.n_recurred == 1


def test_task_not_applicable_when_majority_never_reached():
    records = [
        _rec("w1", reached=False, resolved=False, repeat=0),
        _rec("w1", reached=False, resolved=False, repeat=1),
        _rec("w1", reached=True, resolved=False, repeat=2),
    ]
    v = base_rate_gate(records)
    assert v.n_applicable == 0
    # A not-applicable task contributes to neither the numerator nor the
    # denominator (C1): its lone reached+recurred repeat must NOT count as recurred.
    assert v.n_recurred == 0
    assert v.n_tasks == 1


def test_path_reach_tie_breaks_toward_applicable():
    # Even repeat count, exactly split on path_reached. Tiebreak = applicable
    # (lean to include the data point). The single reached repeat recurred.
    records = [
        _rec("w1", reached=True, resolved=False, repeat=0),
        _rec("w1", reached=False, resolved=False, repeat=1),
    ]
    v = base_rate_gate(records)
    assert v.n_applicable == 1
    assert v.n_recurred == 1


def test_recurrence_tie_breaks_toward_recurred():
    # Task is applicable (both repeats reached); recurrence is tied (1 recurred,
    # 1 resolved). Tiebreak = recurred (conservative: assume the failure can recur).
    records = [
        _rec("w1", reached=True, resolved=False, repeat=0),
        _rec("w1", reached=True, resolved=True, repeat=1),
    ]
    v = base_rate_gate(records)
    assert v.n_applicable == 1
    assert v.n_recurred == 1


# --- M3: gate keys on the deterministic term, never the judge -----------------


def test_judge_rubric_does_not_change_recurrence():
    # A recurred task (det 0.0) with a high rubric_score is STILL a recurrence —
    # the judge term must not rescue it (M3: the gate is the deterministic axis).
    records = [_rec(f"r{i}", reached=True, resolved=False, rubric=0.95) for i in range(12)]
    v = base_rate_gate(records)
    assert v.n_recurred == 12
    assert v.decision is GateDecision.GO


# --- rung scoping + guards ----------------------------------------------------


def test_only_the_requested_rung_is_scored():
    records = [_rec(f"r{i}", reached=True, resolved=False, rung="none") for i in range(12)] + [
        _rec(f"o{i}", reached=True, resolved=True, rung="oracle") for i in range(12)
    ]
    v = base_rate_gate(records, rung="none")
    assert v.n_tasks == 12
    assert v.decision is GateDecision.GO


def test_default_rung_is_none_on_mixed_input():
    # Without an explicit rung=, the gate scopes to the none rung (the zero-memory
    # floor is the only rung whose recurrence answers the dynamic-range question).
    records = [_rec(f"r{i}", reached=True, resolved=False, rung="none") for i in range(12)] + [
        _rec(f"o{i}", reached=True, resolved=True, rung="oracle") for i in range(12)
    ]
    v = base_rate_gate(records)
    assert v.n_tasks == 12  # only the 12 none-rung tasks
    assert v.decision is GateDecision.GO


def test_empty_records_is_a_caller_error():
    with pytest.raises(ValueError):
        base_rate_gate([])


def test_no_records_for_rung_is_a_caller_error():
    records = [_rec("o1", reached=True, resolved=False, rung="oracle")]
    with pytest.raises(ValueError):
        base_rate_gate(records, rung="none")
