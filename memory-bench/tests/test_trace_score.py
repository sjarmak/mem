"""Tests for the deterministic trace_error scorer (mem-apg.3a).

The deterministic half of the D17 per-rung reward: did the agent's fresh run
avoid/resolve the held-out task's known `trace_error`? The scorer consumes
*structured* errors (the `trace_errors` store shape — tool/file/line/error_class/
signature, all TS-computed) so it never re-derives the canonical failure signature.
Its only new computation is the RELAXED signature that counters path-divergence
(architect C1): the original signature encodes the *original* agent's path, so a
fresh agent that shifts a line number must not score a trivial "resolved".

The load-bearing property under test (architect C1/C2): a run that did nothing —
touched no file, resolved nothing — must NOT score a perfect reward. `path_reached`
gates the deterministic term so a no-op falls through to the (absent/low) judge term
instead of trivially claiming the avoid.
"""

import pytest

from membench.grading import (
    RewardComponents,
    RewardRecord,
    RunTrace,
    TraceErrorRef,
    combined_reward,
    deterministic_term,
    exact_recurrence,
    relaxed_signature,
    score_run,
)


def _err(
    tool="tsc",
    file="src/a.ts",
    line=12,
    error_class="TS2345",
    signature=None,
):
    """A structured trace error in the `trace_errors` store shape. `signature`
    defaults to the canonical `tool:file:line:error_class` the TS side computes."""
    sig = signature if signature is not None else f"{tool}:{file}:{line}:{error_class}"
    return TraceErrorRef(tool=tool, file=file, line=line, error_class=error_class, signature=sig)


# --- relaxed_signature: drops line, basenames file ----------------------------


def test_relaxed_signature_drops_line_and_basenames_file():
    e = _err(tool="tsc", file="src/sub/a.ts", line=12, error_class="TS2345")
    assert relaxed_signature(e) == "tsc:a.ts:TS2345"


def test_relaxed_signature_is_line_invariant():
    # The whole point of the relaxation (architect C1.1): the same failure class
    # in the same file at a shifted line is the SAME relaxed signature.
    a = _err(line=12)
    b = _err(line=40)
    assert relaxed_signature(a) == relaxed_signature(b)


def test_relaxed_signature_distinguishes_error_class():
    assert relaxed_signature(_err(error_class="TS2345")) != relaxed_signature(
        _err(error_class="TS1005")
    )


def test_relaxed_signature_distinguishes_tool_and_file():
    # The full triple (tool, basename, error_class) is load-bearing: holding
    # error_class fixed, a different tool or a different file is a different
    # relaxed signature.
    base = _err(tool="tsc", file="src/a.ts", error_class="TS2345")
    assert relaxed_signature(base) != relaxed_signature(
        _err(tool="eslint", file="src/a.ts", error_class="TS2345")
    )
    assert relaxed_signature(base) != relaxed_signature(
        _err(tool="tsc", file="src/b.ts", error_class="TS2345")
    )


def test_from_mapping_projects_full_store_row_and_ignores_extras():
    # `from_mapping` accepts a raw `trace_errors` row (work_id/col/severity/message
    # all present) and projects to the 5 fields the scorer uses — extras ignored,
    # never required-then-dropped silently.
    e = TraceErrorRef.from_mapping(
        {
            "work_id": "w1",
            "tool": "eslint",
            "file": "src/b.ts",
            "line": 3,
            "col": 5,
            "error_class": "no-unused-vars",
            "signature": "eslint:src/b.ts:3:no-unused-vars",
            "severity": "error",
            "message": "x is unused (no-unused-vars)",
        }
    )
    assert e == _err(
        tool="eslint",
        file="src/b.ts",
        line=3,
        error_class="no-unused-vars",
        signature="eslint:src/b.ts:3:no-unused-vars",
    )


# --- score_run: path_reached + trace_error_resolved ---------------------------


def test_resolved_when_failure_class_absent_from_fresh_run():
    held = [_err(file="src/a.ts", error_class="TS2345")]
    # Fresh run touched the file but produced a DIFFERENT failure class.
    run = RunTrace(
        errors=(_err(file="src/a.ts", error_class="TS1005"),),
        files_touched=frozenset({"src/a.ts"}),
    )
    c = score_run(held, run)
    assert c.path_reached is True
    assert c.trace_error_resolved is True


def test_recurred_when_same_class_at_shifted_line():
    # Proves the relaxation works: a fresh recurrence of the SAME class in the
    # SAME file at a DIFFERENT line must count as NOT resolved.
    held = [_err(file="src/a.ts", line=12, error_class="TS2345")]
    run = RunTrace(
        errors=(_err(file="src/a.ts", line=88, error_class="TS2345"),),
        files_touched=frozenset({"src/a.ts"}),
    )
    c = score_run(held, run)
    assert c.path_reached is True
    assert c.trace_error_resolved is False


def test_path_reached_false_forces_resolved_false():
    # You cannot "resolve" a failure you never reached. When path_reached is False,
    # score_run must NOT emit the contradictory (path_reached=False, resolved=True)
    # state, even though the fresh error set is trivially disjoint — the gate is
    # path_reached, and resolved is pinned False so the component is unambiguous.
    held = [_err(file="src/a.ts")]
    run = RunTrace(errors=(), files_touched=frozenset({"src/other.ts"}))
    c = score_run(held, run)
    assert c.path_reached is False
    assert c.trace_error_resolved is False


def test_path_reached_uses_basename_so_cwd_differences_dont_matter():
    # The fresh Harbor run executes in a different cwd than the original trace,
    # so the same logical file prints under a different prefix. Basename match.
    held = [_err(file="src/a.ts")]
    run = RunTrace(errors=(), files_touched=frozenset({"/work/repo/src/a.ts"}))
    c = score_run(held, run)
    assert c.path_reached is True
    assert c.trace_error_resolved is True


def test_path_match_is_case_sensitive():
    # Harbor runs on Linux (case-sensitive fs); a basename that differs only in
    # case is a different file, not a match.
    held = [_err(file="src/A.ts")]
    run = RunTrace(errors=(), files_touched=frozenset({"src/a.ts"}))
    assert score_run(held, run).path_reached is False


def test_path_match_rejects_same_basename_in_different_dir():
    # Path-suffix (not bare basename) matching: a same-named file in a different
    # directory must NOT satisfy path_reached, or a run could "reach" any held file
    # by touching an unrelated index.ts.
    held = [_err(file="src/utils/index.ts", error_class="TS2345")]
    run = RunTrace(errors=(), files_touched=frozenset({"src/components/index.ts"}))
    assert score_run(held, run).path_reached is False


def test_multi_file_partial_reach_resolves_on_engaged_file():
    # Whole-set resolution + any-file path_reached: the run engaged one held file
    # (a.ts) and no known class recurs anywhere, so the deterministic axis reports
    # resolved. Completeness for the UNtouched held file (b.ts) is the judge's job,
    # not this axis (architect C2). This test pins that deliberate behavior.
    held = [
        _err(file="src/a.ts", error_class="TS2345"),
        _err(file="src/b.ts", error_class="TS1005"),
    ]
    run = RunTrace(errors=(), files_touched=frozenset({"src/a.ts"}))
    c = score_run(held, run)
    assert c.path_reached is True
    assert c.trace_error_resolved is True


# --- deterministic_term: the no-op guard --------------------------------------


def test_deterministic_term_none_when_not_engaged():
    # Not path_reached => the deterministic term is NOT APPLICABLE (None), never a
    # free 1.0. This is the architect-C2 no-op guard at the term level.
    c = RewardComponents(path_reached=False, trace_error_resolved=True)
    assert deterministic_term(c) is None


def test_deterministic_term_one_when_engaged_and_resolved():
    c = RewardComponents(path_reached=True, trace_error_resolved=True)
    assert deterministic_term(c) == 1.0


def test_deterministic_term_zero_when_engaged_but_recurred():
    c = RewardComponents(path_reached=True, trace_error_resolved=False)
    assert deterministic_term(c) == 0.0


# --- combined_reward: the four cases ------------------------------------------


def test_combined_both_terms_weighted():
    c = RewardComponents(path_reached=True, trace_error_resolved=True, rubric_score=0.4)
    # det=1.0, rubric=0.4, default det_weight 0.5 -> 0.7
    assert combined_reward(c) == pytest.approx(0.7)


def test_combined_det_only_resolves_to_det_term():
    # No rubric => reward IS the deterministic term, both for a resolved (1.0)...
    resolved = RewardComponents(path_reached=True, trace_error_resolved=True)
    assert combined_reward(resolved) == pytest.approx(1.0)
    # ...and a recurred (0.0) run.
    recurred = RewardComponents(path_reached=True, trace_error_resolved=False)
    assert combined_reward(recurred) == 0.0


def test_combined_rubric_only_for_diff_path_solve():
    # A genuine different-path solve: never touched the original file (det N/A) but
    # the judge says the task is complete. Reward must follow the judge, not 0.
    c = RewardComponents(path_reached=False, trace_error_resolved=False, rubric_score=0.9)
    assert combined_reward(c) == pytest.approx(0.9)


def test_combined_zero_when_neither_term_available():
    c = RewardComponents(path_reached=False, trace_error_resolved=False)
    assert combined_reward(c) == 0.0


def test_combined_respects_det_weight():
    c = RewardComponents(path_reached=True, trace_error_resolved=True, rubric_score=0.0)
    assert combined_reward(c, det_weight=0.75) == pytest.approx(0.75)


# --- the headline property: a no-op cannot score a perfect reward -------------


def test_noop_run_cannot_score_high():
    # The failure mode the whole gate exists to prevent (architect C1/C2): an agent
    # that does nothing — no files touched, no errors, judge unimpressed — must not
    # win the avoid by default.
    held = [_err(file="src/a.ts", error_class="TS2345")]
    noop = RunTrace(errors=(), files_touched=frozenset())
    c = score_run(held, noop, rubric_score=0.0)
    assert c.path_reached is False
    assert combined_reward(c) == 0.0


def test_same_path_fix_scores_full():
    held = [_err(file="src/a.ts", error_class="TS2345")]
    fixed = RunTrace(errors=(), files_touched=frozenset({"src/a.ts"}))
    c = score_run(held, fixed, rubric_score=1.0)
    assert combined_reward(c) == pytest.approx(1.0)


# --- exact_recurrence: full-signature reporting -------------------------------


def test_exact_recurrence_matches_full_signature_only():
    held = [_err(file="src/a.ts", line=12, error_class="TS2345")]
    run = RunTrace(
        errors=(
            _err(file="src/a.ts", line=12, error_class="TS2345"),  # exact
            _err(file="src/a.ts", line=88, error_class="TS2345"),  # same class, diff line
        ),
        files_touched=frozenset({"src/a.ts"}),
    )
    assert exact_recurrence(held, run) == ("tsc:src/a.ts:12:TS2345",)


def test_exact_recurrence_empty_when_no_exact_match():
    held = [_err(line=12)]
    run = RunTrace(errors=(_err(line=88),), files_touched=frozenset({"src/a.ts"}))
    assert exact_recurrence(held, run) == ()


def test_exact_recurrence_reports_each_held_signature_that_recurs():
    held = [
        _err(file="src/a.ts", line=12, error_class="TS2345"),
        _err(file="src/b.ts", line=4, error_class="TS1005"),
    ]
    run = RunTrace(
        errors=(
            _err(file="src/a.ts", line=12, error_class="TS2345"),
            _err(file="src/b.ts", line=4, error_class="TS1005"),
        ),
        files_touched=frozenset({"src/a.ts", "src/b.ts"}),
    )
    assert exact_recurrence(held, run) == (
        "tsc:src/a.ts:12:TS2345",
        "tsc:src/b.ts:4:TS1005",
    )


# --- multi-error held-out sets ------------------------------------------------


def test_resolved_requires_all_held_classes_absent():
    held = [
        _err(file="src/a.ts", error_class="TS2345"),
        _err(file="src/b.ts", error_class="TS1005"),
    ]
    # One of the two held classes recurs => not resolved.
    run = RunTrace(
        errors=(_err(file="src/b.ts", line=99, error_class="TS1005"),),
        files_touched=frozenset({"src/a.ts", "src/b.ts"}),
    )
    assert score_run(held, run).trace_error_resolved is False


def test_score_run_requires_nonempty_held_set():
    # The held-out set is "beads with >=1 trace_error" by construction; an empty
    # held set is a caller error, not a vacuous "resolved".
    with pytest.raises(ValueError, match="at least one held-out error"):
        score_run([], RunTrace(errors=(), files_touched=frozenset()))


# --- RewardComponents / RewardRecord validation -------------------------------


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_rubric_score_must_be_in_unit_interval(bad):
    with pytest.raises(ValueError, match="rubric_score"):
        RewardComponents(path_reached=True, trace_error_resolved=True, rubric_score=bad)


def test_reward_record_carries_key_and_reward():
    c = RewardComponents(path_reached=True, trace_error_resolved=True, rubric_score=1.0)
    rec = RewardRecord(work_id="w1", rung="ours", repeat_idx=2, components=c)
    assert (rec.work_id, rec.rung, rec.repeat_idx) == ("w1", "ours", 2)
    assert rec.reward == pytest.approx(1.0)
