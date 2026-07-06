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
    GRADED_JUDGE_MAX_ATTEMPTS,
    ClaudeRubricJudge,
    RubricParseError,
    StubRubricJudge,
    build_graded_view,
    graded_rubric,
    judge_graded,
    parse_criteria,
    weighted_score,
)
from membench.grading.judge_config import (
    ENV_CLAUDE_CONFIG_DIR,
    FORBIDDEN_CONFIG_ENTRIES,
    STRICT_MCP_CONFIG_FLAG,
    prepare_isolated_judge,
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


# --- config isolation (mem-9ld4: judge must never load a host code-reviewer) ------
# The graded judge shells `claude -p`. Under the host CLAUDE_CONFIG_DIR it
# intermittently loads a code-reviewer agent and answers as a reviewer
# ({"findings": ...}) instead of the rubric ({"criteria": ...}). The invocation
# must instead run under a clean EMPTY config dir + --strict-mcp-config + a neutral
# cwd, so no host/project agent can hijack it. These assert on the invocation
# env/argv/cwd -- no live model call.


def _capturing_runner(reply_text: str, captured: dict):  # type: ignore[no-untyped-def]
    def runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"result": reply_text}), stderr=""
        )

    return runner


def test_claude_judge_invokes_under_isolated_config(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Host env carries a contaminating account config + an OAuth token.
    monkeypatch.setenv(ENV_CLAUDE_CONFIG_DIR, "/home/ds/.claude-homes/account3/.claude")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-token")
    captured: dict = {}
    isolation = prepare_isolated_judge(base=tmp_path)
    judge = ClaudeRubricJudge(runner=_capturing_runner(_reply(), captured), isolation=isolation)

    judge.score("t", "c", graded_rubric())

    argv, env, cwd = captured["argv"], captured["env"], captured["cwd"]
    # MCP disabled (boot-hang + agent-load guard).
    assert STRICT_MCP_CONFIG_FLAG in argv
    # Config surface redirected AWAY from the host account, to the clean dir.
    assert env[ENV_CLAUDE_CONFIG_DIR] == str(isolation.config_dir)
    assert "account3" not in env[ENV_CLAUDE_CONFIG_DIR]
    # Auth preserved -- isolation changes config, not credentials.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "secret-token"
    # Neutral cwd, outside the repo and distinct from the config dir (so `claude`'s
    # upward CLAUDE.md/.claude walk finds nothing).
    assert cwd == isolation.cwd
    assert cwd != isolation.config_dir
    # The clean config dir carries no host/project agent surface.
    for forbidden in FORBIDDEN_CONFIG_ENTRIES:
        assert not (isolation.config_dir / forbidden).exists()


def test_claude_judge_lazy_isolation_materializes_once(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # judge_graded drives `rounds` sequential score() calls on ONE judge instance;
    # the lazy default isolation must materialize on the first call and be reused,
    # not re-created per call (each re-creation would be a fresh on-disk surface and
    # would splinter the pins' attributable config_dir).
    calls = {"n": 0}

    def counting_prepare() -> object:
        calls["n"] += 1
        return prepare_isolated_judge(base=tmp_path / str(calls["n"]))

    monkeypatch.setattr("membench.grading.graded.prepare_isolated_judge", counting_prepare)
    captured: dict = {}
    judge = ClaudeRubricJudge(runner=_capturing_runner(_reply(), captured))

    judge.score("t", "c", graded_rubric())
    first_config_dir = captured["env"][ENV_CLAUDE_CONFIG_DIR]
    judge.score("t", "c", graded_rubric())

    assert calls["n"] == 1
    assert captured["env"][ENV_CLAUDE_CONFIG_DIR] == first_config_dir


def test_claude_judge_default_isolation_marker_is_on(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A judge's isolation marker reports isolated_config=True and a config_dir that
    # points away from any host account. Inject a tmp_path-scoped isolation (per this
    # module's own test convention) rather than letting the judge fall through to the
    # real default base -- that base is the same one a live production judge run
    # would use, so exercising it here would be non-hermetic I/O against shared state.
    isolation = prepare_isolated_judge(base=tmp_path)
    marker = ClaudeRubricJudge(isolation=isolation).isolation_marker
    assert marker["isolated_config"] is True
    assert marker["strict_mcp_config"] is True
    assert "account3" not in str(marker["config_dir"])


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


# --- transient-parse-error retry (mem-eacq: host-config-contaminated judge reply) --
# The `claude -p` judge occasionally returns a wrong-schema reply (e.g. a
# code-review {"findings": [...], "level": ...} object instead of the rubric
# {"criteria": [...]}), which raises RubricParseError. A single such transient
# draw must not abort a whole batch when the design is median-of-`rounds`; the
# draw is re-taken up to GRADED_JUDGE_MAX_ATTEMPTS times. This does NOT change the
# judge's identity -- a parseable reply is scored exactly as before.


def test_judge_graded_retries_transient_rubric_parse_error() -> None:
    # Fail to parse twice, then return a clean 0.5 on every subsequent draw.
    calls = {"n": 0}

    def flaky(task: str, out: str, rubric) -> float:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RubricParseError("judge reply has no 'criteria' list: {'findings': []}")
        return 0.5

    g = judge_graded(
        StubRubricJudge(fn=flaky),
        issue_title="t",
        issue_body="",
        candidate_diff=CANDIDATE,
        gold_diff=GOLD,
        mechanical_reference=None,
        rounds=1,
    )
    assert g.judge_score == 0.5
    assert calls["n"] == 3  # 2 failed draws + 1 that parsed


def test_judge_graded_raises_after_exhausting_parse_retries() -> None:
    calls = {"n": 0}

    def always_bad(task: str, out: str, rubric) -> float:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise RubricParseError("judge reply has no 'criteria' list: {'findings': []}")

    with pytest.raises(RubricParseError):
        judge_graded(
            StubRubricJudge(fn=always_bad),
            issue_title="t",
            issue_body="",
            candidate_diff=CANDIDATE,
            gold_diff=GOLD,
            mechanical_reference=None,
            rounds=1,
        )
    assert calls["n"] == GRADED_JUDGE_MAX_ATTEMPTS  # exhausted, no infinite loop


def test_judge_graded_does_not_retry_out_of_range() -> None:
    # An out-of-range score is a judge bug (plain ValueError from _require_unit),
    # NOT a transient parse failure -- it must fail loud on the first draw.
    calls = {"n": 0}

    def out_of_range(task: str, out: str, rubric) -> float:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return 1.5

    with pytest.raises(ValueError):
        judge_graded(
            StubRubricJudge(fn=out_of_range),
            issue_title="t",
            issue_body="",
            candidate_diff=CANDIDATE,
            gold_diff=GOLD,
            mechanical_reference=None,
            rounds=1,
        )
    assert calls["n"] == 1  # no retry on a contract violation
