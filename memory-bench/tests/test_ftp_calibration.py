"""mem-bxhh.5 — the synthetic↔real-ftp calibration (Gate 0) and its shape grounding.

Covers the two acceptance criteria: (1) ≥2 synthetic tasks reproduce real fail-to-pass
shapes (grounded against the frozen corpus), and (2) the synthetic↔real rank correlation
is measured and reported — including the honest "absence" the recorded anchor produces.
"""

from __future__ import annotations

import pytest

from membench.generators import (
    SHAPE_BLUEPRINTS,
    assert_shapes_grounded,
    generate_shape_sequences,
    memory_dependent_shapes,
)
from membench.generators.ftp_shapes import FTP_SHAPES
from membench.report.ftp_calibration import (
    RealAnchor,
    calibrate,
    canonical_arm,
    format_calibration_report,
    mem1fl8_anchor,
    spearman_rho,
)

# --- AC#1: shapes are grounded in the real corpus -------------------------------------


def test_shapes_are_grounded_in_the_behavioral_corpus() -> None:
    # Every example test a shape names is a real behavioral ftp in the frozen corpus.
    assert_shapes_grounded()


def test_taxonomy_has_at_least_two_memory_dependent_shapes() -> None:
    mem_shapes = memory_dependent_shapes()
    assert len(mem_shapes) >= 2
    assert {s.shape_id for s in mem_shapes} >= {"aggregation-projection", "exclusion-filter"}


def test_shape_blueprints_reproduce_at_least_two_real_shapes() -> None:
    shape_ids = {bp.shape_id for bp in SHAPE_BLUEPRINTS}
    catalogued = {s.shape_id for s in FTP_SHAPES if s.memory_dependent}
    assert len(shape_ids) >= 2
    # Each blueprint maps to a real, memory-dependent shape (not an invented one).
    assert shape_ids <= catalogued


def test_shape_sequences_are_memory_dependent_by_construction() -> None:
    # The grounding must not cost the structural memory-dependence: the goal still
    # requires every fact an earlier step established.
    for seq in generate_shape_sequences():
        writes = {mid for step in seq.steps[:-1] for mid in step.expected_memory_writes}
        goal = seq.steps[-1]
        assert writes, f"{seq.sequence_id} establishes no facts"
        assert goal.expected_memory_writes == {}
        assert set(goal.outcome_checks[0].requires_memory) == writes


def test_grounding_fails_closed_on_a_fabricated_test() -> None:
    from membench.generators.ftp_shapes import FtpShape

    bogus = (
        FtpShape(
            shape_id="made-up",
            summary="not real",
            memory_dependent=True,
            example_tests=(("codeprobe", "tests.test_nope::test_does_not_exist"),),
        ),
    )
    with pytest.raises(ValueError, match="absent from the behavioral corpus"):
        assert_shapes_grounded(bogus)


# --- spearman util --------------------------------------------------------------------


def test_spearman_perfect_and_anticorrelation() -> None:
    assert spearman_rho([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert spearman_rho([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_rejects_degenerate_input() -> None:
    with pytest.raises(ValueError):
        spearman_rho([1.0], [1.0])
    with pytest.raises(ValueError, match="constant"):
        spearman_rho([1, 1, 1], [1, 2, 3])


def test_canonical_arm_folds_the_baseline_alias() -> None:
    assert canonical_arm("none-clean") == "none"
    assert canonical_arm("none") == "none"
    assert canonical_arm("ours") == "ours"


# --- RealAnchor validation ------------------------------------------------------------


def test_real_anchor_validates_its_inputs() -> None:
    with pytest.raises(ValueError):
        RealAnchor(pass_counts={"none": 0}, n=0, baseline_arm="none", source="x")
    with pytest.raises(ValueError, match="baseline"):
        RealAnchor(pass_counts={"none": 0}, n=8, baseline_arm="missing", source="x")
    with pytest.raises(ValueError, match="out of range"):
        RealAnchor(pass_counts={"none": 9}, n=8, baseline_arm="none", source="x")


def test_anchor_lifts_are_relative_to_the_baseline() -> None:
    anchor = mem1fl8_anchor()
    lifts = anchor.lifts()
    assert lifts["none-clean"] == 0.0
    assert lifts["ours"] == 0.0
    assert lifts["builtin"] == pytest.approx(0.125)


# --- AC#2: calibration measures the correlation or its absence ------------------------


def test_recorded_anchor_is_flat_so_gate0_is_no_go() -> None:
    # The honest null: the real anchor does not rank configs non-flatly at N=8.
    syn = {"none": 0.0, "oracle": 0.25, "filesystem": 0.25, "lexical": 0.25}
    verdict = calibrate(syn, mem1fl8_anchor())
    assert verdict.go is False
    assert verdict.anchor_flat is True
    assert verdict.rho is None
    assert "flat anchor" in verdict.gate0
    assert verdict.shared_discriminating_arms == ()


def test_non_flat_anchor_without_shared_arms_is_uncomputable() -> None:
    # An anchor that DOES discriminate, but on an arm the synthetic suite cannot run,
    # leaves nothing to correlate — the exact ours/builtin situation, generalised.
    anchor = RealAnchor(
        pass_counts={"none": 0, "builtin": 50}, n=50, baseline_arm="none", source="synthetic"
    )
    verdict = calibrate({"none": 0.0, "filesystem": 0.5}, anchor)
    assert verdict.go is False
    assert verdict.anchor_flat is False
    assert verdict.rho is None
    assert "uncomputable" in verdict.gate0


def test_non_flat_anchor_with_aligned_shared_arms_goes() -> None:
    anchor = RealAnchor(
        pass_counts={"none": 0, "a": 50, "b": 30}, n=50, baseline_arm="none", source="synthetic"
    )
    verdict = calibrate({"none": 0.0, "a": 0.9, "b": 0.5}, anchor, rho_threshold=0.6)
    assert verdict.go is True
    assert verdict.rho == pytest.approx(1.0)
    assert set(verdict.shared_discriminating_arms) == {"a", "b"}


def test_non_flat_anchor_with_reversed_ranking_fails_threshold() -> None:
    anchor = RealAnchor(
        pass_counts={"none": 0, "a": 50, "b": 30}, n=50, baseline_arm="none", source="synthetic"
    )
    verdict = calibrate({"none": 0.0, "a": 0.5, "b": 0.9}, anchor)
    assert verdict.go is False
    assert verdict.anchor_flat is False
    assert verdict.rho == pytest.approx(-1.0)
    assert "rho" in verdict.gate0


def test_format_report_surfaces_the_verdict_and_both_lifts() -> None:
    syn = {"none": 0.0, "filesystem": 0.25}
    report = format_calibration_report(calibrate(syn, mem1fl8_anchor()))
    assert "Gate 0" in report
    assert "NO-GO" in report
    assert "builtin" in report  # an anchor-only arm still appears
    assert "filesystem" in report  # a synthetic-only arm still appears
