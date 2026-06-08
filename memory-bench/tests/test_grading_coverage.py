"""Tests for the outcome-source coverage probe (mem-apg.1).

The probe answers: for each held-out WorkRecord, which outcome source can grade
it? Two sources in this bead:
  - ablation     — always feasible (no ground-truth label needed; the agent is its
                   own control across an information ladder).
  - merged_diff  — feasible only when the merged-PR/CI oracle is *constructible*:
                   pr_state==merged AND commit_sha present AND the rig maps to a
                   repo. The base-commit walk + diff fetch need a clone, so they
                   stay `unresolved` until build time (architect findings H2/M4).

The coverage table is a byproduct of running `can_build` across sources (finding
M5) — not a hand-rolled classifier. An unmapped rig on a merged bead is a CONFIG
GAP that is surfaced loudly, never silently reclassified to ablation (finding M6).
"""

import pytest

from membench.config.rigs import UnmappedRigError, repo_for_rig
from membench.grading import (
    AblationSource,
    MergedDiffSource,
    SourceCoverage,
    coverage_table,
    recommend_source,
    summarize,
)

RIG_MAP = {"mem": "github.com/acme/mem", "gascity": "github.com/acme/gascity"}


def _record(work_id, rig="mem", **outcome):
    rec = {"work_id": work_id, "rig": rig}
    if outcome:
        rec["outcome"] = outcome
    return rec


# --- AblationSource: always feasible, label-free design -----------------------


def test_ablation_can_build_is_always_feasible():
    src = AblationSource()
    feas = src.can_build(_record("w1"))  # no outcome at all
    assert feas.feasible is True
    assert feas.source == "ablation"


def test_ablation_design_is_label_free():
    src = AblationSource()
    rec = _record("w1", pr="gh-9", pr_state="merged", commit_sha="abc123def456")
    design = src.design(rec)
    assert design.work_id == "w1"
    assert len(design.rungs) >= 2  # an information ladder, not a single point
    # No outcome-label value may appear in the (agent-facing) ablation design.
    blob = " ".join(design.rungs)
    assert "abc123def456" not in blob and "gh-9" not in blob


# --- MergedDiffSource: constructibility conjunction --------------------------


def test_merged_diff_feasible_when_merged_with_sha_and_mapped_rig():
    src = MergedDiffSource(rig_map=RIG_MAP)
    feas = src.can_build(_record("w1", rig="mem", pr_state="merged", commit_sha="abc123def456"))
    assert feas.feasible is True
    # base walk + diff fetch need a clone — reported as unresolved, not claimed.
    assert "base_commit_walk" in feas.unresolved
    assert "merge_diff_fetch" in feas.unresolved


def test_merged_diff_infeasible_when_not_merged():
    src = MergedDiffSource(rig_map=RIG_MAP)
    feas = src.can_build(_record("w1", pr_state="closed", commit_sha="abc123def456"))
    assert feas.feasible is False
    assert feas.unresolved == ()


def test_merged_diff_infeasible_when_merged_without_commit_sha():
    src = MergedDiffSource(rig_map=RIG_MAP)
    feas = src.can_build(_record("w1", pr_state="merged"))
    assert feas.feasible is False


def test_merged_diff_raises_on_unmapped_rig_for_merged_bead():
    # Fail loud (finding M6): a merged bead on a rig we cannot map to a repo is a
    # config gap, not a legitimate "infeasible".
    src = MergedDiffSource(rig_map=RIG_MAP)
    with pytest.raises(UnmappedRigError):
        src.can_build(_record("w1", rig="unknownrig", pr_state="merged", commit_sha="abc123def456"))


# --- repo_for_rig fail-loud primitive ---------------------------------------


def test_repo_for_rig_returns_mapped_repo():
    assert repo_for_rig("mem", RIG_MAP) == "github.com/acme/mem"


def test_repo_for_rig_raises_on_unmapped():
    with pytest.raises(UnmappedRigError):
        repo_for_rig("nope", RIG_MAP)


# --- coverage_table: byproduct of can_build across sources -------------------


def _table():
    records = [
        _record("merged-ok", rig="mem", pr_state="merged", commit_sha="abc123def456"),
        _record("not-merged", rig="mem", pr_state="closed", commit_sha="abc123def456"),
        _record("merged-unmapped", rig="weird", pr_state="merged", commit_sha="abc123def456"),
        _record("open-bead", rig="gascity"),
    ]
    sources = [AblationSource(), MergedDiffSource(rig_map=RIG_MAP)]
    return coverage_table(records, sources)


def test_coverage_table_has_a_row_per_record():
    table = _table()
    assert {row.work_id for row in table} == {
        "merged-ok",
        "not-merged",
        "merged-unmapped",
        "open-bead",
    }


def test_coverage_table_ablation_feasible_everywhere():
    table = _table()
    assert all(row.feasibilities["ablation"].feasible for row in table)


def test_coverage_table_surfaces_unmapped_rig_without_crashing():
    # The unmapped-but-merged bead must not abort the survey, and must not be
    # silently folded into ablation-only — it is reported as a config gap.
    table = _table()
    row = next(r for r in table if r.work_id == "merged-unmapped")
    md = row.feasibilities["merged_diff"]
    assert md.feasible is False
    assert "rig_repo_mapping" in md.unresolved


def test_summarize_counts_and_flags_unmapped_rigs():
    summary = summarize(_table())
    assert summary.per_source["ablation"].feasible == 4
    assert summary.per_source["merged_diff"].feasible == 1  # only merged-ok
    assert "weird" in summary.unmapped_rigs


# --- recommend_source: explicit precedence (finding M7) ----------------------


def test_recommend_prefers_merged_diff_when_constructible():
    table = _table()
    row = next(r for r in table if r.work_id == "merged-ok")
    assert recommend_source(row) == "merged_diff"


def test_recommend_falls_to_ablation_when_merged_diff_infeasible():
    table = _table()
    row = next(r for r in table if r.work_id == "not-merged")
    assert recommend_source(row) == "ablation"


def test_recommend_raises_when_no_source_is_feasible():
    # A malformed row (ablation never probed) must fail loudly, not return a
    # phantom "ablation" that isn't in the table.
    row = SourceCoverage(work_id="w", rig="mem", feasibilities={})
    with pytest.raises(ValueError):
        recommend_source(row)


def test_coverage_rows_are_read_only():
    table = _table()
    with pytest.raises(TypeError):
        table[0].feasibilities["injected"] = None  # type: ignore[index]
