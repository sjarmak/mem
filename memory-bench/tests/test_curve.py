"""Tests for the score-vs-information curve (mem-apg.3c headline artifact).

The curve aggregates per-rung `combined_reward` across the held-out set. With the
combinatorial rungs (`builtin`, `ours+builtin`) deferred to mem-whi, the live ladder
has only 3 points (none < ours < oracle). Architect H2: a 3-point ladder cannot
LOCATE a saturation point or a minimum-useful COMBINATION (no interior resolution,
no combination axis), so:

- the curve reports what 3 points DO support: the **ours-vs-oracle ceiling gap**
  (does our memory reach the oracle ceiling?) and the **none-floor lift** (how far
  above zero-memory does our memory move the reward?);
- `saturation_point` / `min_useful_combo` REFUSE to emit a verdict below 4 rungs —
  raising rather than fabricating a vacuous "saturation at ours".

Repeats collapse within task (M2) before the across-task mean + CI; the per-rung
value is the mean of the combined reward, NOT a pooled task*repeat count.
"""

import pytest

from membench.grading import (
    InsufficientLadderError,
    RewardComponents,
    RewardRecord,
    ScoreInformationCurve,
    build_curve,
    min_useful_combo,
    saturation_point,
)


def _rec(work_id, rung, *, det, resolved=True, rubric=None, repeat=0):
    """A RewardRecord on `rung`. `det` selects the deterministic outcome:
    'reach' -> path reached (resolved per `resolved`); 'miss' -> not reached."""
    assert det in {"reach", "miss"}, f"det must be 'reach' or 'miss', got {det!r}"
    reached = det == "reach"
    return RewardRecord(
        work_id=work_id,
        rung=rung,
        repeat_idx=repeat,
        components=RewardComponents(
            path_reached=reached,
            trace_error_resolved=(resolved if reached else False),
            rubric_score=rubric,
        ),
    )


# --- per-rung aggregation -----------------------------------------------------


def test_curve_reports_one_point_per_present_rung_in_ladder_order():
    records = [
        _rec("w1", "none", det="reach", resolved=False),  # combined 0.0
        _rec("w1", "ours", det="reach", resolved=True),  # combined 1.0
        _rec("w1", "oracle", det="reach", resolved=True),  # combined 1.0
    ]
    curve = build_curve(records)
    assert isinstance(curve, ScoreInformationCurve)
    assert [r.rung for r in curve.rungs] == ["none", "ours", "oracle"]
    assert curve.rung("none").mean_reward == pytest.approx(0.0)
    assert curve.rung("ours").mean_reward == pytest.approx(1.0)


def test_auto_detected_rungs_match_explicit_default_ladder():
    # The auto-detect path (no rungs=) must produce the same rung set/order as the
    # explicit default ladder for the same records.
    records = [
        _rec("w1", "none", det="reach", resolved=False),
        _rec("w1", "ours", det="reach", resolved=True),
        _rec("w1", "oracle", det="reach", resolved=True),
    ]
    auto = [r.rung for r in build_curve(records).rungs]
    explicit = [r.rung for r in build_curve(records, rungs=("none", "ours", "oracle")).rungs]
    assert auto == explicit


def test_curve_uses_combined_reward_not_raw_deterministic():
    # det 1.0 + rubric 0.0 -> combined_reward 0.5 at the default det_weight.
    records = [_rec(f"w{i}", "ours", det="reach", resolved=True, rubric=0.0) for i in range(4)]
    curve = build_curve(records, rungs=("ours",))
    assert curve.rung("ours").mean_reward == pytest.approx(0.5)


def test_repeats_collapse_within_task_before_across_task_mean():
    # One task, 2 repeats (rewards 1.0 and 0.0) -> task mean 0.5, so the rung mean
    # is 0.5 over ONE task, NOT a pooled mean over two independent observations.
    records = [
        _rec("w1", "ours", det="reach", resolved=True, repeat=0),  # 1.0
        _rec("w1", "ours", det="reach", resolved=False, repeat=1),  # 0.0
    ]
    curve = build_curve(records, rungs=("ours",))
    rung = curve.rung("ours")
    assert rung.n_tasks == 1
    assert rung.mean_reward == pytest.approx(0.5)


def test_ceiling_gap_and_floor_lift():
    records = (
        [_rec(f"w{i}", "none", det="reach", resolved=False) for i in range(5)]  # 0.0
        + [_rec(f"w{i}", "ours", det="reach", resolved=True) for i in range(5)]  # 1.0
        + [_rec(f"w{i}", "oracle", det="reach", resolved=True) for i in range(5)]  # 1.0
    )
    curve = build_curve(records)
    assert curve.floor_lift == pytest.approx(1.0)  # ours - none
    assert curve.ceiling_gap == pytest.approx(0.0)  # oracle - ours (ours hit ceiling)


def test_ceiling_gap_none_when_a_rung_absent():
    records = [_rec(f"w{i}", "ours", det="reach", resolved=True) for i in range(5)]
    curve = build_curve(records, rungs=("ours",))
    assert curve.ceiling_gap is None
    assert curve.floor_lift is None


def test_floor_lift_computable_while_ceiling_gap_absent():
    # Realistic partial run: none + ours present, oracle not yet run. floor_lift is
    # defined; ceiling_gap is None (no oracle to compare against).
    records = [_rec(f"w{i}", "none", det="reach", resolved=False) for i in range(5)] + [
        _rec(f"w{i}", "ours", det="reach", resolved=True) for i in range(5)
    ]
    curve = build_curve(records, rungs=("none", "ours"))
    assert curve.floor_lift == pytest.approx(1.0)
    assert curve.ceiling_gap is None


def test_confidence_interval_is_nondegenerate_and_brackets_the_mean():
    # Four tasks with combined rewards 0.4, 0.5, 0.5, 0.6 (mean 0.5, modest spread)
    # must yield a STRICTLY bracketing, narrow interval well inside [0, 1] — not the
    # degenerate [0, 1] a stub would return. (combined = 0.5*det + 0.5*rubric.)
    records = [
        _rec("w1", "ours", det="reach", resolved=False, rubric=0.8),  # 0.4
        _rec("w2", "ours", det="reach", resolved=True, rubric=0.0),  # 0.5
        _rec("w3", "ours", det="reach", resolved=False, rubric=1.0),  # 0.5
        _rec("w4", "ours", det="reach", resolved=True, rubric=0.2),  # 0.6
    ]
    rung = build_curve(records, rungs=("ours",)).rung("ours")
    assert rung.n_tasks == 4
    assert rung.mean_reward == pytest.approx(0.5)
    assert rung.lower_bound < rung.mean_reward < rung.upper_bound
    assert rung.upper_bound - rung.lower_bound > 0.0
    # A degenerate [0, 1] interval would span the whole range; this t-CI is far tighter.
    assert rung.lower_bound > 0.0 and rung.upper_bound < 1.0


def test_single_task_rung_has_defined_but_zero_width_interval():
    # n=1 -> no across-task variance to interval; bounds collapse to the mean
    # rather than raising or fabricating a spread.
    rung = build_curve([_rec("w1", "ours", det="reach", resolved=True)], rungs=("ours",)).rung(
        "ours"
    )
    assert rung.n_tasks == 1
    assert rung.lower_bound == pytest.approx(rung.mean_reward)
    assert rung.upper_bound == pytest.approx(rung.mean_reward)


# --- saturation / min-useful-combo refuse below 4 rungs (architect H2) --------


def test_saturation_point_refuses_below_four_rungs():
    records = [
        _rec("w1", "none", det="reach", resolved=False),
        _rec("w1", "ours", det="reach", resolved=True),
        _rec("w1", "oracle", det="reach", resolved=True),
    ]
    curve = build_curve(records)
    with pytest.raises(InsufficientLadderError):
        saturation_point(curve)


def test_min_useful_combo_refuses_below_four_rungs():
    records = [_rec("w1", "ours", det="reach", resolved=True)]
    curve = build_curve(records, rungs=("ours",))
    with pytest.raises(InsufficientLadderError):
        min_useful_combo(curve)


def _four_rung_saturating_records():
    # none 0.0, ours 0.5, builtin 1.0, oracle 1.0 -> reward saturates at builtin
    # (oracle adds nothing), and builtin is also the cheapest rung at the ceiling.
    out = []
    for i in range(4):
        out.append(_rec(f"w{i}", "none", det="reach", resolved=False))  # 0.0
        out.append(_rec(f"w{i}", "ours", det="reach", resolved=True, rubric=0.0))  # 0.5
        out.append(_rec(f"w{i}", "builtin", det="reach", resolved=True))  # 1.0
        out.append(_rec(f"w{i}", "oracle", det="reach", resolved=True))  # 1.0
    return out


def test_saturation_point_at_four_rungs_returns_the_plateau_rung():
    curve = build_curve(
        _four_rung_saturating_records(), rungs=("none", "ours", "builtin", "oracle")
    )
    sat = saturation_point(curve)  # must NOT raise at 4 rungs
    assert sat.rung == "builtin"


def test_min_useful_combo_at_four_rungs_returns_cheapest_rung_at_ceiling():
    curve = build_curve(
        _four_rung_saturating_records(), rungs=("none", "ours", "builtin", "oracle")
    )
    combo = min_useful_combo(curve)  # must NOT raise at 4 rungs
    assert combo.rung == "builtin"


# --- guards -------------------------------------------------------------------


def test_empty_records_is_a_caller_error():
    with pytest.raises(ValueError):
        build_curve([])
