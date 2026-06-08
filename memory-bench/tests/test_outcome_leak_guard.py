"""Tests for the outcome-label leak guard (mem-apg.1, architect finding C1).

D6 (`validity.assert_no_leak`) guards what an ARM may ingest at retrieval time. It
says nothing about the task-construction path, where outcome labels can reach the
agent through `instruction.md` / verifier markers (`harbor/adapter.py`). This guard
is the mechanical mirror of `assert_no_leak` for that second path: given the files
an agent can read and a task's outcome-label values, assert none of the labels
appears in agent-readable text.
"""

import pytest

from membench.grading import (
    OutcomeLeakError,
    assert_no_outcome_leak,
    outcome_labels,
)


def _record(**outcome):
    return {"work_id": "w1", "rig": "mem", "outcome": outcome}


def test_outcome_labels_returns_identifying_values():
    # High-entropy identifiers (commit_sha, pr) are the scan set; the low-entropy
    # enum states (pr_state / ci) are deliberately excluded — a task instruction
    # may legitimately contain the word "pass" or "merged", so substring-scanning
    # them would be false-positive noise. Enum leakage is prevented structurally by
    # the design/task manifest split, not by this substring scan.
    rec = _record(pr="gh-12", pr_state="merged", commit_sha="abc123def456", ci="pass")
    labels = set(outcome_labels(rec))
    assert labels == {"gh-12", "abc123def456"}


def test_outcome_labels_skips_missing_outcome():
    assert outcome_labels({"work_id": "w", "rig": "mem"}) == ()
    assert set(outcome_labels(_record(commit_sha="deadbeefcafe"))) == {"deadbeefcafe"}


def test_no_leak_passes_when_labels_absent():
    # Returns None (no raise) when the content is clean.
    assert assert_no_outcome_leak("fix the parser bug in src/parse.py", ["abc123def456"]) is None


def test_leak_raises_on_commit_sha_in_content():
    with pytest.raises(OutcomeLeakError) as ei:
        assert_no_outcome_leak("the base commit is abc123def456 — start there", ["abc123def456"])
    assert "abc123def456" in str(ei.value)


def test_leak_raises_on_pr_id_in_content():
    with pytest.raises(OutcomeLeakError):
        assert_no_outcome_leak("this resolves gh-12", ["gh-12"])


def test_leak_scans_a_mapping_of_files_and_names_the_offending_file():
    files = {"instruction.md": "fix it", "context.md": "see commit abc123def456"}
    with pytest.raises(OutcomeLeakError) as ei:
        assert_no_outcome_leak(files, ["abc123def456"])
    assert "context.md" in str(ei.value)


def test_leak_match_is_case_insensitive():
    # A SHA reproduced in a different case must still be caught — the guard errs
    # toward over-catching (the safe direction for a validity control).
    with pytest.raises(OutcomeLeakError):
        assert_no_outcome_leak("see ABC123DEF456 in the build log", ["abc123def456"])


def test_duplicate_labels_yield_one_offender():
    with pytest.raises(OutcomeLeakError) as ei:
        assert_no_outcome_leak("commit abc123def456", ["abc123def456", "abc123def456"])
    assert len(ei.value.offenders) == 1


def test_empty_label_set_is_noop():
    assert assert_no_outcome_leak("anything at all", []) is None


def test_blank_labels_never_match():
    # An empty/whitespace label must not match every document.
    assert assert_no_outcome_leak("anything at all", ["", "   "]) is None
