"""The need-gate scorer, checked against the pre-registered thresholds.

The constants are restated from ``docs/mem-jev-need-gate-prereg.md``; a drift
in either direction must red this file rather than change a verdict quietly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev_gate.score import (
    MIN_ACCEPTED_ACCURACY,
    MIN_COVERAGE,
    MIN_REPEAT_STABILITY,
    N_BINS,
    PRIMARY_QUESTION,
    Row,
    ScoreError,
    brier,
    coverage_sweep,
    kill_gates,
    per_class_accuracy,
    pick_cutoff,
    read_labels,
    regex_rows,
    reliability_bins,
    repeat_stability,
    score_model,
    to_rows,
    verdict,
    wilson,
)


def row(qid: str, p: float, needs: bool, *, variant: str = "necessary", repeat: int = 1) -> Row:
    return Row(qid, "typesafe-ai/jev", repeat, p, variant, needs)


def result(
    qid: str, p: float, *, model: str = "typesafe-ai/jev", repeat: int = 1
) -> dict[str, object]:
    return {
        "question_id": qid,
        "model": model,
        "repeat": repeat,
        "answers": {PRIMARY_QUESTION: {"type": "boolean", "probability": p}},
    }


LABELS = {"a": ("necessary", True), "b": ("unnecessary", False)}


# --- the pre-registered constants --------------------------------------------------


def test_the_thresholds_are_the_pre_registered_ones() -> None:
    assert MIN_ACCEPTED_ACCURACY == 0.9
    assert MIN_REPEAT_STABILITY == 0.95
    assert MIN_COVERAGE == 0.5
    assert N_BINS == 5


# --- joining rows -------------------------------------------------------------------


def test_an_error_row_refuses_rather_than_scoring_around_it() -> None:
    rows = [result("a", 0.9), {"question_id": "b", "model": "m", "repeat": 1, "error": "boom"}]
    with pytest.raises(ScoreError, match="error row"):
        to_rows(rows, LABELS)


def test_an_unlabelled_case_a_duplicate_and_a_bad_probability_all_refuse() -> None:
    with pytest.raises(ScoreError, match="no label"):
        to_rows([result("zzz", 0.5)], LABELS)
    with pytest.raises(ScoreError, match="duplicate"):
        to_rows([result("a", 0.5), result("a", 0.6)], LABELS)
    with pytest.raises(ScoreError, match="outside"):
        to_rows([result("a", 1.5)], LABELS)
    with pytest.raises(ScoreError, match="no needsMemory"):
        to_rows([{"question_id": "a", "model": "m", "repeat": 1, "answers": {}}], LABELS)


def test_read_labels_refuses_a_duplicate_and_a_non_bool(tmp_path: Path) -> None:
    good = tmp_path / "good.jsonl"
    good.write_text(json.dumps({"question_id": "a", "variant": "necessary", "needs_memory": True}))
    assert read_labels(good) == {"a": ("necessary", True)}
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"question_id": "a", "variant": "necessary", "needs_memory": "yes"}))
    with pytest.raises(ScoreError, match="not a bool"):
        read_labels(bad)


# --- metrics ------------------------------------------------------------------------


def test_a_row_reports_its_call_confidence_and_correctness() -> None:
    assert row("a", 0.91, True).predicted is True
    assert row("a", 0.09, True).confidence == pytest.approx(0.91)
    assert row("a", 0.09, True).correct is False


def test_brier_is_zero_when_certain_and_right_and_one_when_certain_and_wrong() -> None:
    assert brier([row("a", 1.0, True), row("b", 0.0, False)]) == 0.0
    assert brier([row("a", 0.0, True)]) == 1.0
    assert brier([row("a", 0.5, True)]) == pytest.approx(0.25)


def test_the_interval_is_wilson_not_the_normal_approximation() -> None:
    # A clean sweep does not collapse to a zero-width interval, which is the
    # whole reason gate 3 can use the lower bound at this sample size.
    low, high = wilson(20, 20)
    assert low < 1.0 and high == 1.0
    assert wilson(0, 0) == (0.0, 1.0)


def test_reliability_bins_span_the_half_range_and_keep_every_row() -> None:
    rows = [row(f"q{i}", 0.5 + i / 20, True) for i in range(10)]
    bins = reliability_bins(rows)
    assert len(bins) == N_BINS
    assert sum(b.n for b in bins) == len(rows)
    assert bins[0].lower == 0.5 and bins[-1].upper == 1.0


def test_the_sweep_trades_coverage_for_accuracy() -> None:
    rows = [row("a", 0.99, True), row("b", 0.51, False, variant="unnecessary")]
    rows.append(row("c", 0.52, False))  # confident-ish and wrong
    sweep = coverage_sweep(rows)
    assert sweep[0].coverage == 1.0
    assert sweep[-1].confidence == pytest.approx(0.99)
    assert sweep[-1].accuracy == 1.0


def test_the_cutoff_is_the_strictest_point_the_coverage_floor_allows() -> None:
    rows = [row("a", 0.99, True), row("b", 0.98, True), row("c", 0.6, True), row("d", 0.55, True)]
    cutoff = pick_cutoff(coverage_sweep(rows))
    assert cutoff is not None
    assert cutoff.confidence == pytest.approx(0.98)
    assert cutoff.coverage == pytest.approx(0.5)


def test_repeat_stability_counts_cases_whose_call_never_flips() -> None:
    steady = [row("a", 0.9, True, repeat=r) for r in (1, 2, 3)]
    assert repeat_stability(steady) == 1.0
    flipping = [*steady, row("b", 0.9, True, repeat=1), row("b", 0.1, True, repeat=2)]
    assert repeat_stability(flipping) == 0.5


def test_per_class_accuracy_splits_on_the_variant() -> None:
    rows = [row("a", 0.9, True), row("b", 0.9, False, variant="unnecessary")]
    assert per_class_accuracy(rows) == {"necessary": 1.0, "unnecessary": 0.0}


# --- the regex comparator -----------------------------------------------------------


def case(qid: str, question: str) -> dict[str, object]:
    return {"question_id": qid, "state": {"question": question, "sections": []}}


def test_the_regex_predicts_need_exactly_when_no_state_block_is_present() -> None:
    labels = {"a": ("necessary", True), "b": ("unnecessary", False)}
    cases = [case("a", "do the thing"), case("b", "do the thing\n\nCurrent state:\n- x: 1")]
    rows = regex_rows(cases, labels)
    assert [r.predicted for r in rows] == [True, False]
    assert all(r.correct for r in rows)


def test_the_regex_is_wrong_on_the_partial_class_by_construction() -> None:
    # The partial prompt carries the heading but withholds one value, so the
    # heading match calls it self-contained and misses.
    labels = {"p": ("partial", True)}
    rows = regex_rows([case("p", "do it\n\nCurrent state:\n- x: 1")], labels)
    assert rows[0].correct is False


def test_the_regex_refuses_a_case_it_has_no_label_for() -> None:
    with pytest.raises(ScoreError, match="no label"):
        regex_rows([case("zzz", "x")], {})


# --- the kill gates -----------------------------------------------------------------


def jev_score(rows: list[Row]) -> object:
    return score_model("typesafe-ai/jev", rows)


def test_gate_one_kills_a_model_that_is_confidently_wrong() -> None:
    rows = [row(f"q{i}", 0.99, i % 2 == 0) for i in range(10)]
    gates = kill_gates(jev_score(rows), None, None)  # type: ignore[arg-type]
    assert gates[0]["passed"] is False
    assert verdict(gates) == {"passed": False, "killed_by": 1, "reason": gates[0]["name"]}


def test_gate_two_kills_a_model_whose_call_flips_between_repeats() -> None:
    rows = [row("a", 0.99, True, repeat=1), row("a", 0.01, True, repeat=2)]
    rows += [row("b", 0.99, True, repeat=1), row("b", 0.99, True, repeat=2)]
    gates = kill_gates(jev_score(rows), None, None)  # type: ignore[arg-type]
    assert gates[1]["passed"] is False


def test_gate_three_needs_the_lower_bound_to_clear_haiku() -> None:
    perfect = [row(f"q{i}", 0.99, True) for i in range(40)]
    haiku = score_model("claude-cli/haiku", [row(f"q{i}", 0.99, i < 20) for i in range(40)])
    assert kill_gates(jev_score(perfect), haiku, None)[2]["passed"] is True  # type: ignore[arg-type]
    tie = score_model("claude-cli/haiku", [row(f"q{i}", 0.99, True) for i in range(40)])
    assert kill_gates(jev_score(perfect), tie, None)[2]["passed"] is False  # type: ignore[arg-type]


def test_gate_four_compares_only_the_partial_class() -> None:
    jev = [row(f"q{i}", 0.99, True, variant="partial") for i in range(4)]
    regex = score_model(
        "regex/state-heading", [row(f"q{i}", 0.0, True, variant="partial") for i in range(4)]
    )
    gates = kill_gates(jev_score(jev), None, regex)  # type: ignore[arg-type]
    assert gates[3]["passed"] is True
    assert gates[3]["observed"] == 1.0 and gates[3]["comparator"] == 0.0


def test_a_missing_comparator_leaves_the_verdict_undecided_not_passed() -> None:
    rows = [row(f"q{i}", 0.99, True, repeat=r) for i in range(4) for r in (1, 2)]
    gates = kill_gates(jev_score(rows), None, None)  # type: ignore[arg-type]
    assert [g["passed"] for g in gates[:2]] == [True, True]
    assert verdict(gates)["passed"] is None


def test_the_gates_are_reported_in_the_pre_registered_order() -> None:
    rows = [row(f"q{i}", 0.99, True, repeat=r) for i in range(4) for r in (1, 2)]
    gates = kill_gates(jev_score(rows), None, None)  # type: ignore[arg-type]
    assert [g["gate"] for g in gates] == [1, 2, 3, 4]
