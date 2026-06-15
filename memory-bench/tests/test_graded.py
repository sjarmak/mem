"""Graded quality metric S3 judge (mem-g6a / mem-r5y): blinding, coarse-scale
per-criterion parsing, weighted score, N-round vote, and the divergence flag.

Offline: the judge is a `StubRubricJudge` or a `ClaudeRubricJudge` with an injected
runner -- no model, no network. A real ``claude -p`` call happens only when an
experimenter wires in a live `ClaudeRubricJudge`.
"""

import json
import subprocess

import pytest

from membench.grading.graded import (
    ALLOWED_CRITERION_SCORES,
    GRADED_DIVERGENCE_THRESHOLD,
    ClaudeRubricJudge,
    RubricParseError,
    StubRubricJudge,
    build_graded_view,
    graded_rubric,
    judge_graded,
    parse_criteria,
    weighted_score,
)

GOLD = {"src/app.ts": "@@\n-const value = 1\n+const value = 2\n"}
CANDIDATE = {"src/app.ts": "@@\n-const value = 1\n+const value = 2\n"}


def _reply(resolves=1.0, completeness=1.0, focus=1.0) -> str:
    return json.dumps(
        {
            "criteria": [
                {"name": "resolves_issue", "score": resolves, "evidence": "src/app.ts line"},
                {"name": "completeness", "score": completeness, "evidence": "covers the change"},
                {"name": "focus", "score": focus, "evidence": "no spurious edits"},
            ]
        }
    )


# --- blinding ---------------------------------------------------------------------


def test_view_is_blinded_to_issue_candidate_gold_only() -> None:
    task, run_output = build_graded_view(
        issue_title="Fix the widget",
        issue_body="it crashes",
        candidate_diff=CANDIDATE,
        gold_diff=GOLD,
    )
    assert "Fix the widget" in task and "it crashes" in task
    assert "Reference (gold) diff" in task and "const value = 2" in task
    assert "const value = 2" in run_output
    # No condition/arm/payload/token leakage in either half.
    for blob in (task, run_output):
        for forbidden in ("none-clean", "ours", "builtin", "oracle", "tokens", "arm"):
            assert forbidden not in blob.lower()


def test_view_renders_empty_candidate_as_no_op() -> None:
    _, run_output = build_graded_view(
        issue_title="t", issue_body="", candidate_diff={}, gold_diff=GOLD
    )
    assert run_output == "(no changes)"


# --- per-criterion parsing (coarse scale + evidence) ------------------------------


def test_parse_criteria_happy_path() -> None:
    verdicts = parse_criteria(_reply(1.0, 0.5, 0.0), graded_rubric())
    assert [v.name for v in verdicts] == ["resolves_issue", "completeness", "focus"]
    assert [v.score for v in verdicts] == [1.0, 0.5, 0.0]


def test_parse_tolerates_surrounding_prose() -> None:
    reply = f"Here is my judgment:\n{_reply()}\nThanks!"
    assert len(parse_criteria(reply, graded_rubric())) == 3


@pytest.mark.parametrize("bad_score", [2.0, -1.0, 1.5])
def test_parse_rejects_out_of_range_scores(bad_score: float) -> None:
    with pytest.raises(RubricParseError, match="out of range"):
        parse_criteria(_reply(resolves=bad_score), graded_rubric())


@pytest.mark.parametrize(
    ("raw", "snapped"),
    [(0.8, 1.0), (0.7, 0.5), (0.9, 1.0), (0.3, 0.5), (0.1, 0.0), (0.25, 0.0)],
)
def test_parse_snaps_in_range_off_grid_scores(raw: float, snapped: float) -> None:
    """An in-range but off-coarse-grid judge score (the `claude -p` seam occasionally
    returns 0.8 despite the 0/0.5/1.0 prompt) snaps to the nearest coarse value rather
    than aborting the run; ties resolve to the lower value."""
    verdicts = parse_criteria(_reply(resolves=raw), graded_rubric())
    assert verdicts[0].score == snapped


def test_parse_rejects_missing_evidence() -> None:
    reply = json.dumps(
        {
            "criteria": [
                {"name": "resolves_issue", "score": 1.0, "evidence": ""},
                {"name": "completeness", "score": 1.0, "evidence": "x"},
                {"name": "focus", "score": 1.0, "evidence": "y"},
            ]
        }
    )
    with pytest.raises(RubricParseError, match="evidence"):
        parse_criteria(reply, graded_rubric())


def test_parse_rejects_omitted_criterion() -> None:
    reply = json.dumps({"criteria": [{"name": "resolves_issue", "score": 1.0, "evidence": "x"}]})
    with pytest.raises(RubricParseError, match="omitted"):
        parse_criteria(reply, graded_rubric())


def test_parse_rejects_unknown_and_duplicate() -> None:
    with pytest.raises(RubricParseError, match="unknown"):
        parse_criteria(
            json.dumps({"criteria": [{"name": "bogus", "score": 1.0, "evidence": "x"}]}),
            graded_rubric(),
        )
    dup = json.dumps(
        {
            "criteria": [
                {"name": "resolves_issue", "score": 1.0, "evidence": "x"},
                {"name": "resolves_issue", "score": 0.0, "evidence": "y"},
            ]
        }
    )
    with pytest.raises(RubricParseError, match="duplicate"):
        parse_criteria(dup, graded_rubric())


def test_parse_rejects_no_json() -> None:
    with pytest.raises(RubricParseError, match="no JSON"):
        parse_criteria("the model refused", graded_rubric())


# --- weighted score (arithmetic over coarse scores) -------------------------------


def test_weighted_score_uses_rubric_weights() -> None:
    rubric = graded_rubric()
    # All 1.0 -> 1.0; weights sum to 1.0.
    assert weighted_score(parse_criteria(_reply(1.0, 1.0, 1.0), rubric), rubric) == 1.0
    # resolves(0.5*1.0=... ) -> 0.5*0.5 + 0.3*0 + 0.2*0 = 0.25
    assert weighted_score(parse_criteria(_reply(0.5, 0.0, 0.0), rubric), rubric) == pytest.approx(
        0.25
    )


def test_allowed_scale_is_three_point() -> None:
    assert ALLOWED_CRITERION_SCORES == (0.0, 0.5, 1.0)


# --- ClaudeRubricJudge (injected runner, no real claude) --------------------------


def _fake_claude(reply_text: str):
    def runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        assert argv[0] == "claude" and "--output-format" in argv and "json" in argv
        assert "--model" in argv
        wrapper = json.dumps({"result": reply_text})
        return subprocess.CompletedProcess(argv, 0, stdout=wrapper, stderr="")

    return runner


def test_claude_judge_parses_and_weights() -> None:
    judge = ClaudeRubricJudge(model="claude-sonnet-4-6", runner=_fake_claude(_reply(1.0, 0.5, 0.0)))
    score = judge.score(
        *build_graded_view(
            issue_title="t", issue_body="", candidate_diff=CANDIDATE, gold_diff=GOLD
        ),
        graded_rubric(),
    )
    # 0.5*1.0 + 0.3*0.5 + 0.2*0.0 = 0.65
    assert score == pytest.approx(0.65)
    assert judge.model == "claude-sonnet-4-6"


def test_claude_judge_nonzero_exit_raises() -> None:
    def runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="quota exceeded")

    judge = ClaudeRubricJudge(runner=runner)
    with pytest.raises(RuntimeError, match="claude -p failed"):
        judge.score("t", "c", graded_rubric())


def test_claude_judge_defaults_to_locked_sonnet() -> None:
    assert ClaudeRubricJudge().model == "claude-sonnet-4-6"


# --- judge_graded orchestration (vote + divergence) -------------------------------


def test_judge_graded_median_vote_and_high_confidence() -> None:
    # Three identical rounds -> median == that score, spread 0 -> confidence 1.0.
    judge = StubRubricJudge(fixed=0.5)
    g = judge_graded(
        judge,
        issue_title="t",
        issue_body="",
        candidate_diff=CANDIDATE,
        gold_diff=GOLD,
        mechanical_reference=0.5,
    )
    assert g.judge_score == 0.5
    assert g.judge_confidence == 1.0
    assert g.rounds == (0.5, 0.5, 0.5)
    assert g.divergence == 0.0 and not g.divergence_flagged
    assert g.model == "stub" and g.prompt_version == "v1"


def test_judge_graded_flags_divergence_from_mechanical() -> None:
    judge = StubRubricJudge(fixed=0.9)
    g = judge_graded(
        judge,
        issue_title="t",
        issue_body="",
        candidate_diff=CANDIDATE,
        gold_diff=GOLD,
        mechanical_reference=0.1,  # mechanical says far, judge says close
    )
    assert g.divergence == pytest.approx(0.8)
    assert g.divergence > GRADED_DIVERGENCE_THRESHOLD and g.divergence_flagged


def test_judge_graded_no_mechanical_reference_no_flag() -> None:
    g = judge_graded(
        StubRubricJudge(fixed=1.0),
        issue_title="t",
        issue_body="",
        candidate_diff=CANDIDATE,
        gold_diff=GOLD,
        mechanical_reference=None,
    )
    assert g.divergence is None and not g.divergence_flagged


def test_judge_graded_varying_rounds_uses_median_and_spread() -> None:
    # A judge whose score depends on a per-call mutable counter -> 0.0, 0.5, 1.0.
    seq = iter([0.0, 0.5, 1.0])
    judge = StubRubricJudge(fn=lambda task, out, rubric: next(seq))
    g = judge_graded(
        judge,
        issue_title="t",
        issue_body="",
        candidate_diff=CANDIDATE,
        gold_diff=GOLD,
        mechanical_reference=0.5,
        rounds=3,
    )
    assert g.rounds == (0.0, 0.5, 1.0)
    assert g.judge_score == 0.5  # median
    assert g.judge_confidence == pytest.approx(0.0)  # spread 1.0 -> confidence 0.0


def test_judge_graded_rejects_zero_rounds() -> None:
    with pytest.raises(ValueError, match="rounds"):
        judge_graded(
            StubRubricJudge(fixed=1.0),
            issue_title="t",
            issue_body="",
            candidate_diff=CANDIDATE,
            gold_diff=GOLD,
            mechanical_reference=None,
            rounds=0,
        )


def test_judge_graded_propagates_out_of_range_judge_score() -> None:
    # score_completion validates the judge's [0,1] contract -- a buggy judge fails loud.
    with pytest.raises(ValueError):
        judge_graded(
            StubRubricJudge(fixed=1.5),
            issue_title="t",
            issue_body="",
            candidate_diff=CANDIDATE,
            gold_diff=GOLD,
            mechanical_reference=None,
        )
