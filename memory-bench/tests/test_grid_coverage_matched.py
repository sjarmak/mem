"""M5 — coverage-matched query set in summarize_grid_3arm.

Deltas must be reported over the bundle set all compared arms could GENUINELY
attempt, alongside the full set, with both populations labeled — otherwise a
coverage hole reads as "covered everything". In the 3-arm grid the genuine-attempt
signal already exists: ``ours_retrieval_empty`` marks the degenerate case where the
ours leg reused the none-clean run (retrieval returned nothing), so it is NOT a
genuine memory attempt and is excluded from the matched deltas (present, labeled, in
the full set).
"""

from __future__ import annotations

from membench.grading.probe_direct import ProbeEfficiency
from membench.harbor.bundle_grid import GridConditionResult, summarize_grid_3arm, three_arm_row


def _r(work_id, condition, tokens):
    return GridConditionResult(
        work_id=work_id,
        condition=condition,
        score_direct=1.0,
        score_artifact=1.0,
        direct_mode="repro",
        repro_passed=True,
        repro_error=None,
        efficiency=ProbeEfficiency(input_tokens=tokens, output_tokens=5, turns=1, tool_calls=1),
        candidate_files=(),
    )


def _row(work_id, *, ours_empty):
    return three_arm_row(
        _r(work_id, "none-clean", 10),
        _r(work_id, "ours", 8),
        _r(work_id, "builtin", 10),
        ours_retrieval_empty=ours_empty,
    )


def test_coverage_matched_excludes_degenerate_attempts():
    rows = [_row("w-genuine", ours_empty=False), _row("w-empty", ours_empty=True)]
    summary = summarize_grid_3arm(rows, [])
    cov = summary["coverage_matched"]
    assert cov["full_set_size"] == 2
    assert set(cov["full_set"]) == {"w-genuine", "w-empty"}
    assert cov["matched_set"] == ["w-genuine"]
    assert cov["matched_set_size"] == 1
    assert cov["excluded"] == ["w-empty"]


def test_coverage_matched_all_genuine_is_full_set():
    rows = [_row("w1", ours_empty=False), _row("w2", ours_empty=False)]
    cov = summarize_grid_3arm(rows, [])["coverage_matched"]
    assert cov["matched_set_size"] == 2
    assert cov["excluded"] == []
    # The matched gap stats are present when at least one bundle matched.
    assert "ours_vs_none_clean" in cov["matched_gaps"]


def test_coverage_matched_all_degenerate_reports_empty_matched_gaps():
    rows = [_row("w1", ours_empty=True)]
    cov = summarize_grid_3arm(rows, [])["coverage_matched"]
    assert cov["matched_set_size"] == 0
    assert cov["matched_gaps"] == {}
