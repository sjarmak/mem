"""Relevance-label calibration store + pre-registered frozen judge-vs-human gate (mem-lvp.33).

This is the defensibility anchor for the judged-relevance lane: the quantitative
detector of failure-signature circularity re-entering via the judge. It calibrates
the judge's relevance LABELS (binary relevant?/graded 0-3) against a frozen human
subset and gates on the same pre-registered bar safety_gates uses
(``kappa >= 0.6 AND fpr <= 0.05``), mirroring the ``confabulation_authority``
flag-until-cleared discipline.

NOT ``grading.judge.Calibration`` (MAE / within-tolerance), which is the wrong shape
for categorical relevance labels — a near-miss numeric score is meaningless for a
boolean "relevant?" decision; chance-corrected agreement (kappa) and the per-class
false-positive rate are the operative statistics.

Fully fixture-tested: hand-built (human, judge) label pairs. NO Ollama, NO network,
NO live judge.
"""

from __future__ import annotations

import json

import pytest

from membench.compare.relevance_calibration import (
    PREREGISTERED_FPR_GAP_MAX,
    BinaryLabelPair,
    GradedLabelPair,
    RelevanceCalibration,
    load_frozen_calibration,
    relevance_calibration_authority,
)
from membench.grading.safety_gates import (
    PREREGISTERED_FPR_MAX,
    PREREGISTERED_KAPPA_MIN,
)


# --------------------------------------------------------------------------- #
# Binary: Cohen's kappa, percent-agreement, per-class precision/recall/FPR
# --------------------------------------------------------------------------- #
def _binary(pairs: list[tuple[bool, bool]], **kw: object) -> RelevanceCalibration:
    cal = RelevanceCalibration(mode="binary")
    for human, judge in pairs:
        cal.record_binary(BinaryLabelPair(human=human, judge=judge, **kw))  # type: ignore[arg-type]
    return cal


def test_perfect_binary_agreement_is_kappa_one():
    cal = _binary([(True, True), (False, False), (True, True), (False, False)])
    report = cal.report()
    assert report.n == 4
    assert report.percent_agreement == 1.0
    assert report.kappa == 1.0
    assert report.fpr == 0.0


def test_binary_per_class_precision_recall_fpr():
    # judge says relevant on 3, human agrees on 2 of them (1 false positive);
    # human has 3 relevant total, judge misses 1 (1 false negative).
    #   (h, j): (T,T) (T,T) (T,F) (F,T) (F,F) (F,F)
    pairs = [
        (True, True),
        (True, True),
        (True, False),
        (False, True),
        (False, False),
        (False, False),
    ]
    report = _binary(pairs).report()
    # judge-positive = 3, true-positive = 2 -> precision 2/3
    assert report.precision == pytest.approx(2 / 3)
    # human-positive = 3, judge caught 2 -> recall 2/3
    assert report.recall == pytest.approx(2 / 3)
    # human-negative = 3, judge wrongly flagged 1 -> FPR 1/3
    assert report.fpr == pytest.approx(1 / 3)
    # 4 of 6 match
    assert report.percent_agreement == pytest.approx(4 / 6)


def test_chance_agreement_drives_kappa_below_percent_agreement():
    # 80% raw agreement but heavy class imbalance -> kappa well under 0.8.
    pairs = [(True, True)] * 8 + [(True, False), (False, True)]
    report = _binary(pairs).report()
    assert report.percent_agreement == pytest.approx(0.8)
    assert report.kappa < 0.8


def test_kappa_zero_when_agreement_is_pure_chance():
    # Judge ignores the human entirely: marginals independent -> kappa ~ 0.
    pairs = [(True, True), (True, False), (False, True), (False, False)]
    report = _binary(pairs).report()
    assert report.kappa == pytest.approx(0.0, abs=1e-9)


def test_empty_calibration_set_raises():
    with pytest.raises(ValueError, match="empty"):
        RelevanceCalibration(mode="binary").report()


def test_recording_graded_into_binary_store_raises():
    cal = RelevanceCalibration(mode="binary")
    with pytest.raises(ValueError, match="binary"):
        cal.record_graded(GradedLabelPair(human=2, judge=3))


# --------------------------------------------------------------------------- #
# Graded: quadratic-weighted kappa
# --------------------------------------------------------------------------- #
def _graded(pairs: list[tuple[int, int]], **kw: object) -> RelevanceCalibration:
    cal = RelevanceCalibration(mode="graded")
    for human, judge in pairs:
        cal.record_graded(GradedLabelPair(human=human, judge=judge, **kw))  # type: ignore[arg-type]
    return cal


def test_perfect_graded_agreement_is_weighted_kappa_one():
    report = _graded([(0, 0), (1, 1), (2, 2), (3, 3)]).report()
    assert report.weighted_kappa == 1.0
    assert report.percent_agreement == 1.0


def test_graded_near_miss_costs_less_than_far_miss():
    # Quadratic weighting: a 1-grade miss penalises far less than a 3-grade miss.
    near = _graded([(2, 2), (2, 1), (1, 1), (0, 0)]).report()
    far = _graded([(2, 2), (3, 0), (1, 1), (0, 0)]).report()
    assert near.weighted_kappa > far.weighted_kappa


def test_graded_out_of_range_label_raises():
    cal = RelevanceCalibration(mode="graded")
    with pytest.raises(ValueError, match="range"):
        cal.record_graded(GradedLabelPair(human=4, judge=1))


def test_graded_report_has_no_binary_only_fields():
    report = _graded([(0, 0), (3, 3)]).report()
    assert report.weighted_kappa is not None
    assert report.kappa is None
    assert report.fpr is None


def test_binary_report_has_no_weighted_kappa():
    report = _binary([(True, True), (False, False)]).report()
    assert report.kappa is not None
    assert report.weighted_kappa is None


# --------------------------------------------------------------------------- #
# Per-arm and per-stratum breakdown + pre-registered FPR-gap bound
# --------------------------------------------------------------------------- #
def test_per_arm_breakdown_isolates_fpr_by_arm():
    cal = RelevanceCalibration(mode="binary")
    # ours arm: 1 false positive out of 2 negatives -> FPR 0.5
    cal.record_binary(BinaryLabelPair(human=False, judge=True, arm="ours"))
    cal.record_binary(BinaryLabelPair(human=False, judge=False, arm="ours"))
    # semantic arm: clean
    cal.record_binary(BinaryLabelPair(human=False, judge=False, arm="semantic"))
    cal.record_binary(BinaryLabelPair(human=False, judge=False, arm="semantic"))
    report = cal.report()
    assert set(report.per_arm) == {"ours", "semantic"}
    assert report.per_arm["ours"].fpr == pytest.approx(0.5)
    assert report.per_arm["semantic"].fpr == pytest.approx(0.0)
    # the per-arm FPR gap is surfaced for the gate
    assert report.fpr_gap == pytest.approx(0.5)


def test_per_stratum_breakdown():
    cal = RelevanceCalibration(mode="binary")
    cal.record_binary(BinaryLabelPair(human=True, judge=True, stratum="transfer"))
    cal.record_binary(BinaryLabelPair(human=False, judge=True, stratum="distractor"))
    cal.record_binary(BinaryLabelPair(human=False, judge=False, stratum="distractor"))
    report = cal.report()
    assert set(report.per_stratum) == {"transfer", "distractor"}
    # the distractor stratum carries the 1 false positive over 2 negatives
    assert report.per_stratum["distractor"].fpr == pytest.approx(0.5)


def test_fpr_gap_zero_when_one_arm_absent():
    # Gap needs both arms; with only one labelled arm there is no gap to bound.
    cal = RelevanceCalibration(mode="binary")
    cal.record_binary(BinaryLabelPair(human=False, judge=True, arm="ours"))
    cal.record_binary(BinaryLabelPair(human=False, judge=False, arm="ours"))
    report = cal.report()
    assert report.fpr_gap is None


# --------------------------------------------------------------------------- #
# Pre-registered gate (binary): kappa >= 0.6 AND fpr <= 0.05 AND gap bound
# --------------------------------------------------------------------------- #
def _clearing_pairs() -> list[tuple[bool, bool]]:
    # 18 agreeing + 2 disagreeing, balanced classes, no false positives ->
    # kappa >= 0.6, fpr 0.0.
    pairs = [(True, True)] * 9 + [(False, False)] * 9
    pairs += [(True, False), (True, False)]  # only false negatives, never positives
    return pairs


def test_clearing_run_passes_the_gate():
    verdict = _binary(_clearing_pairs()).report().gate()
    assert verdict.kappa >= PREREGISTERED_KAPPA_MIN
    assert verdict.fpr <= PREREGISTERED_FPR_MAX
    assert verdict.passed is True
    assert verdict.win_eligible is True


def test_high_fpr_fails_the_gate():
    pairs = [(True, True)] * 9 + [(False, False)] * 5
    pairs += [(False, True)] * 4  # 4 false positives over 9 negatives -> fpr high
    verdict = _binary(pairs).report().gate()
    assert verdict.fpr > PREREGISTERED_FPR_MAX
    assert verdict.passed is False
    assert verdict.win_eligible is False
    assert "fpr" in verdict.reason.lower()


def test_low_kappa_fails_the_gate_even_with_low_fpr():
    # Many agreed negatives + several false negatives, one true positive: the judge
    # never false-positives (FPR 0) but its positive-class recall is poor, dragging
    # chance-corrected kappa below the bar.
    pairs = [(False, False)] * 20 + [(True, True), (True, False), (True, False)]
    verdict = _binary(pairs).report().gate()
    assert verdict.fpr <= PREREGISTERED_FPR_MAX
    assert verdict.kappa < PREREGISTERED_KAPPA_MIN
    assert verdict.passed is False


def test_fpr_gap_breach_fails_the_gate():
    cal = RelevanceCalibration(mode="binary")
    # Strong overall kappa, low overall fpr, but ours is much worse than semantic.
    for _ in range(10):
        cal.record_binary(BinaryLabelPair(human=True, judge=True, arm="ours"))
        cal.record_binary(BinaryLabelPair(human=True, judge=True, arm="semantic"))
    # ours: many negatives, several false positives; semantic: clean negatives
    for _ in range(20):
        cal.record_binary(BinaryLabelPair(human=False, judge=False, arm="semantic"))
        cal.record_binary(BinaryLabelPair(human=False, judge=False, arm="ours"))
    for _ in range(3):
        cal.record_binary(BinaryLabelPair(human=False, judge=True, arm="ours"))
    verdict = cal.report().gate()
    assert verdict.fpr_gap is not None
    assert verdict.fpr_gap > PREREGISTERED_FPR_GAP_MAX
    assert verdict.passed is False
    assert "gap" in verdict.reason.lower()


def test_graded_gate_uses_weighted_kappa_bar():
    report = _graded([(g % 4, g % 4) for g in range(20)]).report()
    verdict = report.gate()
    assert verdict.weighted_kappa is not None
    assert verdict.weighted_kappa >= PREREGISTERED_KAPPA_MIN
    assert verdict.passed is True


def test_graded_verdict_has_no_linear_kappa_or_fpr():
    # Graded mode has no FPR and no linear kappa — both surfaced as None, never a
    # meaningful-looking zero. The weighted kappa carries the only κ signal.
    verdict = _graded([(g % 4, g % 4) for g in range(20)]).report().gate()
    assert verdict.kappa is None
    assert verdict.fpr is None
    assert verdict.weighted_kappa is not None


def test_graded_per_arm_metrics_carry_weighted_kappa_not_linear_kappa():
    cal = RelevanceCalibration(mode="graded")
    for g in range(8):
        cal.record_graded(GradedLabelPair(human=g % 4, judge=g % 4, arm="ours"))
    metrics = cal.report().per_arm["ours"]
    assert metrics.kappa is None
    assert metrics.weighted_kappa is not None


# --------------------------------------------------------------------------- #
# Frozen human-subset artifact loader + authority (mirrors confabulation_authority)
# --------------------------------------------------------------------------- #
def test_load_frozen_calibration_round_trips(tmp_path):
    report = _binary(_clearing_pairs()).report()
    path = tmp_path / "frozen.json"
    report.write_frozen(path, prompt_version="rel-v1")
    loaded = load_frozen_calibration(path)
    assert loaded.frozen is True
    assert loaded.prompt_version == "rel-v1"
    assert loaded.kappa == pytest.approx(report.kappa)
    assert loaded.fpr == pytest.approx(report.fpr)
    assert "per_arm" in loaded.breakdown


def test_loader_rejects_unfrozen_artifact(tmp_path):
    path = tmp_path / "draft.json"
    path.write_text(json.dumps({"frozen": False, "kappa": 0.9, "fpr": 0.0}))
    with pytest.raises(ValueError, match="frozen"):
        load_frozen_calibration(path)


def test_loader_rejects_frozen_artifact_missing_prompt_version(tmp_path):
    # A frozen flag without a pinned prompt_version is malformed — raise ValueError
    # (not KeyError) so the authority helper can flag it rather than crash.
    path = tmp_path / "noversion.json"
    path.write_text(json.dumps({"frozen": True, "mode": "binary", "kappa": 0.9, "fpr": 0.0}))
    with pytest.raises(ValueError, match="prompt_version"):
        load_frozen_calibration(path)


def test_loader_rejects_frozen_artifact_missing_mode(tmp_path):
    path = tmp_path / "nomode.json"
    path.write_text(json.dumps({"frozen": True, "prompt_version": "rel-v1", "kappa": 0.9}))
    with pytest.raises(ValueError, match="mode"):
        load_frozen_calibration(path)


def test_authority_flags_malformed_frozen_artifact_without_crashing(tmp_path):
    # A frozen=True but otherwise malformed artifact must FLAG, never propagate.
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"frozen": True, "kappa": 0.9, "fpr": 0.0}))
    assert relevance_calibration_authority(path) == "flag"


def test_loader_rejects_missing_frozen_flag(tmp_path):
    path = tmp_path / "nope.json"
    path.write_text(json.dumps({"kappa": 0.9, "fpr": 0.0}))
    with pytest.raises(ValueError, match="frozen"):
        load_frozen_calibration(path)


def test_authority_flag_when_no_frozen_set():
    assert relevance_calibration_authority(None) == "flag"


def test_authority_flag_for_missing_path(tmp_path):
    assert relevance_calibration_authority(tmp_path / "absent.json") == "flag"


def test_authority_clears_only_when_frozen_set_passes_full_gate(tmp_path):
    path = tmp_path / "frozen.json"
    _binary(_clearing_pairs()).report().write_frozen(path, prompt_version="rel-v1")
    assert relevance_calibration_authority(path) == "cleared"


def test_authority_stays_flag_for_failing_frozen_set(tmp_path):
    path = tmp_path / "frozen.json"
    bad = [(True, True)] * 5 + [(False, True)] * 5  # fpr 1.0
    _binary(bad).report().write_frozen(path, prompt_version="rel-v1")
    assert relevance_calibration_authority(path) == "flag"


def test_authority_stays_flag_for_unfrozen_set(tmp_path):
    path = tmp_path / "draft.json"
    path.write_text(json.dumps({"frozen": False, "kappa": 0.9, "fpr": 0.0}))
    assert relevance_calibration_authority(path) == "flag"


# --------------------------------------------------------------------------- #
# Win-eligibility flag for the compare envelope
# --------------------------------------------------------------------------- #
def test_failed_gate_marks_metrics_diagnostic_only():
    pairs = [(True, True)] * 5 + [(False, True)] * 5  # fpr 1.0
    verdict = _binary(pairs).report().gate()
    assert verdict.win_eligible is False
    assert verdict.diagnostic_only is True


def test_passed_gate_is_not_diagnostic_only():
    verdict = _binary(_clearing_pairs()).report().gate()
    assert verdict.win_eligible is True
    assert verdict.diagnostic_only is False
