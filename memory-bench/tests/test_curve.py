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

from statistics import fmean, stdev

import pytest

from membench.grading import (
    InsufficientLadderError,
    PairedDeltaCI,
    RewardComponents,
    RewardRecord,
    ScoreInformationCurve,
    build_curve,
    min_useful_combo,
    paired_delta_ci,
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


def test_mean_ci_uses_t_quantile_not_normal_z():
    # n=9 tasks -> df=8 -> the 95% half-width is t_{8,.975}=2.3060 sems, NOT the
    # anti-conservative z=1.96 the old normal approximation emitted (mem-lp24).
    rubrics = [0.40 + 0.05 * i for i in range(9)]  # rewards 0.20..0.40
    records = [
        _rec(f"w{i}", "ours", det="reach", resolved=False, rubric=r) for i, r in enumerate(rubrics)
    ]
    rung = build_curve(records, rungs=("ours",)).rung("ours")
    values = [r / 2.0 for r in rubrics]
    sem = stdev(values) / 3.0  # sqrt(n) = 3
    assert rung.mean_reward == pytest.approx(fmean(values))
    half = rung.upper_bound - rung.mean_reward
    assert half == pytest.approx(2.3060041350333704 * sem, rel=1e-6)
    assert half > 1.9599639845400545 * sem  # strictly wider than the old z interval
    assert rung.lower_bound == pytest.approx(rung.mean_reward - half)


# --- paired deltas: bootstrap CI + ITT/matched populations (mem-lp24) ---------


def test_paired_delta_ci_shared_helper_known_input():
    base = {"a": 0.0, "b": 0.0, "c": 0.0}
    treat = {"a": 0.1, "b": 0.3, "c": 0.5}
    ci = paired_delta_ci(base, treat, population="matched", seed=3)
    assert isinstance(ci, PairedDeltaCI)
    assert ci.delta == pytest.approx(0.3)  # median of [0.1, 0.3, 0.5]
    assert ci.n_pairs == 3
    assert ci.n_imputed_zero == 0
    # A percentile bootstrap of the median can never leave the observed range.
    assert 0.1 - 1e-12 <= ci.ci_low <= ci.delta <= ci.ci_high <= 0.5 + 1e-12


def test_paired_delta_ci_empty_population_is_a_caller_error():
    with pytest.raises(ValueError):
        paired_delta_ci({"a": 1.0}, {"b": 1.0}, population="matched")


def test_paired_delta_ci_is_deterministic_for_a_seed():
    base = {f"w{i}": 0.0 for i in range(6)}
    treat = {f"w{i}": 0.2 * i for i in range(6)}
    a = paired_delta_ci(base, treat, population="matched", seed=7)
    b = paired_delta_ci(base, treat, population="matched", seed=7)
    assert a == b
    assert a.ci_low <= a.delta <= a.ci_high


def test_floor_lift_ci_itt_vs_matched_population_accounting():
    # `none` observed on w1..w4; `ours` only on w1/w2 (retrieval fired nothing on
    # w3/w4). Matched = the 2 retrieval-fired pairs; ITT = all 4 admitted tasks
    # with the empty-retrieval tasks contributing delta 0 (the primary).
    records = [_rec(f"w{i}", "none", det="reach", resolved=False) for i in range(1, 5)] + [
        _rec(f"w{i}", "ours", det="reach", resolved=True) for i in range(1, 3)
    ]
    curve = build_curve(records, rungs=("none", "ours"))
    matched = curve.floor_lift_ci(population="matched", seed=1)
    itt = curve.floor_lift_ci(population="itt", seed=1)
    assert matched is not None and itt is not None
    assert matched.population == "matched"
    assert matched.n_pairs == 2
    assert matched.n_imputed_zero == 0
    assert matched.delta == pytest.approx(1.0)
    assert itt.population == "itt"
    assert itt.n_pairs == 4
    assert itt.n_imputed_zero == 2
    assert itt.delta == pytest.approx(0.5)  # median of [1, 1, 0, 0]
    # The imputed zeros pull ITT toward 0 — it can never overstate the matched read.
    assert itt.delta < matched.delta


def test_ceiling_gap_ci_direction_and_default_population_is_itt():
    records = [
        _rec("w1", "ours", det="reach", resolved=False),  # 0.0
        _rec("w1", "oracle", det="reach", resolved=True),  # 1.0
    ]
    curve = build_curve(records, rungs=("ours", "oracle"))
    gap = curve.ceiling_gap_ci()
    assert gap is not None
    assert gap.population == "itt"  # the pre-registered primary is the default
    assert gap.delta == pytest.approx(1.0)  # oracle - ours
    # n=1 -> no resample spread; bounds collapse to the point.
    assert gap.ci_low == pytest.approx(gap.delta)
    assert gap.ci_high == pytest.approx(gap.delta)


def test_paired_delta_readouts_are_none_when_a_rung_is_absent():
    records = [_rec("w1", "none", det="reach", resolved=False)]
    curve = build_curve(records, rungs=("none",))
    assert curve.floor_lift_ci() is None
    assert curve.ceiling_gap_ci() is None


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


# --- combination readouts require the `ours` base rung, not just any combination
# rung (mem-do8r.2 / Codex F1). A 4-rung ladder that carries a combination rung but
# is missing the `ours` baseline has no reference point for "does layering add
# reward?" — the readout would be a category error, so the gate must refuse. These
# pins are mutation-effective: the ladder reaches 4 rungs AND carries a combination
# rung, so it passes the pilot's count-only + axis gate; only the added `ours`-present
# conjunct tells them apart.


@pytest.mark.parametrize("combo_rung", ["builtin", "ours+builtin"])
def test_combination_readouts_refuse_when_ours_baseline_absent(combo_rung):
    records = []
    for i in range(4):
        records.append(_rec(f"w{i}", "none", det="reach", resolved=False))
        records.append(_rec(f"w{i}", "vector-rag", det="reach", resolved=True))
        records.append(_rec(f"w{i}", combo_rung, det="reach", resolved=True))
        records.append(_rec(f"w{i}", "oracle", det="reach", resolved=True))
    curve = build_curve(records, rungs=("none", "vector-rag", combo_rung, "oracle"))
    assert "ours" not in {r.rung for r in curve.rungs}
    with pytest.raises(InsufficientLadderError):
        saturation_point(curve)
    with pytest.raises(InsufficientLadderError):
        min_useful_combo(curve)


def test_combination_readouts_compute_over_ours_plus_builtin_ladder():
    # Positive pin: the true combination ladder (none < ours < ours+builtin < oracle)
    # carries the `ours` baseline AND a combination rung, so the readouts compute.
    # The existing must-not-raise pins only cover `builtin`; this covers `ours+builtin`.
    records = []
    for i in range(4):
        records.append(_rec(f"w{i}", "none", det="reach", resolved=False))  # 0.0
        records.append(_rec(f"w{i}", "ours", det="reach", resolved=True, rubric=0.0))  # 0.5
        records.append(_rec(f"w{i}", "ours+builtin", det="reach", resolved=True))  # 1.0
        records.append(_rec(f"w{i}", "oracle", det="reach", resolved=True))  # 1.0
    curve = build_curve(records, rungs=("none", "ours", "ours+builtin", "oracle"))
    assert saturation_point(curve).rung == "ours+builtin"  # must NOT raise
    assert min_useful_combo(curve).rung == "ours+builtin"


# --- floor_lift keeps the `none` (zero-memory) baseline even when the interior
# `vector-rag` recall rung is present (mem-do8r.2 decision). Re-baselining floor_lift
# to vector-rag would silently redefine the pre-registered D17 headline metric
# (ours - none); the vector-rag → ours interior delta stays reachable via the generic
# paired_delta escape hatch instead.


def test_floor_lift_baselines_on_none_not_vector_rag():
    records = [
        _rec("w1", "none", det="miss", resolved=False),  # 0.0
        _rec("w1", "vector-rag", det="reach", resolved=True, rubric=0.0),  # 0.5 (interior)
        _rec("w1", "ours", det="reach", resolved=True),  # 1.0
    ]
    curve = build_curve(records)
    # ours - none = 1.0, NOT ours - vector-rag = 0.5.
    assert curve.floor_lift == pytest.approx(1.0)


def test_vector_rag_to_ours_interior_delta_reachable_via_paired_delta():
    # The recall-axis interior read (vector-rag → ours) is not a named property but
    # is reachable through the generic paired_delta, so the escape hatch the docstring
    # advertises is locked by a test, not just prose.
    records = [
        _rec("w1", "vector-rag", det="reach", resolved=True, rubric=0.0),  # 0.5
        _rec("w1", "ours", det="reach", resolved=True),  # 1.0
        _rec("w2", "vector-rag", det="reach", resolved=True, rubric=0.0),  # 0.5
        _rec("w2", "ours", det="reach", resolved=True),  # 1.0
    ]
    curve = build_curve(records)
    delta = curve.paired_delta("vector-rag", "ours")
    assert delta is not None
    assert delta.delta == pytest.approx(0.5)  # ours 1.0 - vector-rag 0.5


# --- guards -------------------------------------------------------------------


def test_empty_records_is_a_caller_error():
    with pytest.raises(ValueError):
        build_curve([])


# --- explicit rungs= must be a duplicate-free canonical subsequence (mem-rvu6).
# Both readouts (`saturation_point` / `min_useful_combo`) are order-sensitive and
# gate on the raw slot count, so a duplicated or reordered explicit ladder is a
# latent robustness bug (unreachable from the auto-detect production caller, which
# passes no rungs=). build_curve rejects it loudly rather than silently building a
# fake ladder.


def test_build_curve_rejects_duplicate_rung_names():
    # Finding 1: a duplicate explicit rung inflates the slot count past the distinct
    # membership, so `_require_full_ladder` (len-based) would pass on a fake 4-rung
    # ladder built from only 3 unique rungs.
    records = [
        _rec("w1", "none", det="reach", resolved=False),
        _rec("w1", "ours", det="reach", resolved=True),
        _rec("w1", "builtin", det="reach", resolved=True),
    ]
    with pytest.raises(ValueError):
        build_curve(records, rungs=("none", "ours", "builtin", "builtin"))


def test_build_curve_rejects_non_canonical_rung_order():
    # Finding 2: `saturation_point` ("no later rung exceeds") and `min_useful_combo`
    # ("earliest-on-ladder") are order-sensitive, so an explicit ladder listed out of
    # canonical order would make both readouts return whichever rung was listed first
    # (here `oracle`) — a pure ordering artifact rather than a real saturation read.
    records = []
    for i in range(4):
        records.append(_rec(f"w{i}", "none", det="reach", resolved=False))
        records.append(_rec(f"w{i}", "ours", det="reach", resolved=True))
        records.append(_rec(f"w{i}", "builtin", det="reach", resolved=True))
        records.append(_rec(f"w{i}", "oracle", det="reach", resolved=True))
    with pytest.raises(ValueError):
        build_curve(records, rungs=("oracle", "none", "ours", "builtin"))
