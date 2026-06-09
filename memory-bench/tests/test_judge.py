"""Tests for the OSS LLM-judge rubric scorer (mem-apg.3b).

The SEMANTIC half of the D17 per-rung reward: it produces the `rubric_score` term
that `combined_reward` (mem-apg.3a) composes with the deterministic trace_error
term. The deterministic axis answers "did the known failure recur"; the judge
answers "was the work actually done" (architect C2) — so a run that avoided the
known failure by doing NOTHING must earn a LOW completion score here.

The whole pipeline runs with a deterministic, injectable StubJudge: NO model and
NO network in tests. The concrete OssLlmJudge is exercised only for its config /
paid-host fence and its prompt assembly — never by actually calling a model.

The load-bearing fences under test:
  - the returned score is validated into [0, 1] and fails loud otherwise;
  - the judge's view carries NO held-out resolution, so the answer cannot leak;
  - the OSS judge refuses a paid host (D4/D16);
  - calibration is mechanical (store labels, compute agreement) — no semantic logic.
"""

import pytest

from membench.grading.judge import (
    Calibration,
    OssLlmJudge,
    Rubric,
    RubricCriterion,
    StubJudge,
    completion_rubric,
    score_completion,
)


def _rubric():
    return completion_rubric()


# --- Rubric construction ------------------------------------------------------


def test_completion_rubric_has_criteria_with_weights():
    r = completion_rubric()
    assert len(r.criteria) >= 2
    assert all(c.weight > 0 for c in r.criteria)


def test_rubric_rejects_all_zero_weights():
    with pytest.raises(ValueError, match="weight"):
        Rubric(criteria=(RubricCriterion(name="x", description="d", weight=0.0),))


def test_rubric_criterion_rejects_negative_weight():
    with pytest.raises(ValueError, match="weight"):
        RubricCriterion(name="x", description="d", weight=-1.0)


def test_rubric_is_frozen():
    r = completion_rubric()
    with pytest.raises(Exception):  # noqa: B017 - dataclass FrozenInstanceError
        r.criteria = ()  # type: ignore[misc]


def test_rubric_renders_each_criterion_into_prompt_block():
    r = completion_rubric()
    block = r.as_prompt_block()
    for c in r.criteria:
        assert c.name in block
        assert c.description in block


# --- StubJudge: the no-model path the whole pipeline runs on ------------------


def test_stub_judge_returns_fixed_score():
    judge = StubJudge(fixed=0.25)
    assert judge.score("goal", "the run did half the work", _rubric()) == 0.25


def test_stub_judge_supports_callable_scoring():
    # A no-op run (empty output) should be cheap to model as "low completion".
    judge = StubJudge(fn=lambda task, output, rubric: 0.0 if not output else 0.9)
    assert judge.score("goal", "", _rubric()) == 0.0
    assert judge.score("goal", "did the work", _rubric()) == 0.9


def test_stub_judge_requires_exactly_one_mode():
    with pytest.raises(ValueError, match=r"fixed.*fn|fn.*fixed"):
        StubJudge()
    with pytest.raises(ValueError, match=r"fixed.*fn|fn.*fixed"):
        StubJudge(fixed=0.5, fn=lambda t, o, r: 0.5)


# --- score_completion: the [0,1] fail-loud validation wrapper -----------------


def test_score_completion_passes_through_valid_score():
    judge = StubJudge(fixed=0.5)
    assert score_completion(judge, "goal", "output", _rubric()) == 0.5


def test_score_completion_accepts_endpoints():
    assert score_completion(StubJudge(fixed=0.0), "g", "o", _rubric()) == 0.0
    assert score_completion(StubJudge(fixed=1.0), "g", "o", _rubric()) == 1.0


@pytest.mark.parametrize("bad", [-0.01, 1.5, 2.0])
def test_score_completion_rejects_out_of_range(bad):
    judge = StubJudge(fn=lambda t, o, r: bad)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        score_completion(judge, "goal", "output", _rubric())


def test_score_completion_rejects_nan():
    judge = StubJudge(fn=lambda t, o, r: float("nan"))
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        score_completion(judge, "goal", "output", _rubric())


# --- No-answer-leak fence -----------------------------------------------------


def test_judge_signature_has_no_resolution_parameter():
    # The judge sees only (task, run_output, rubric). There is structurally no
    # slot to pass the held-out resolution, so it cannot leak into the judge view.
    import inspect

    params = list(inspect.signature(StubJudge.score).parameters)
    assert params == ["self", "task", "run_output", "rubric"]
    assert "resolution" not in params
    assert "answer" not in params


# --- OssLlmJudge: config + paid-host fence (never calls a model) ---------------


def test_oss_judge_default_targets_local_endpoint():
    judge = OssLlmJudge()
    assert "127.0.0.1" in judge.base_url or "localhost" in judge.base_url
    assert judge.model  # a documented default model id


def test_oss_judge_reads_env_overrides(monkeypatch):
    monkeypatch.setenv("MEMBENCH_JUDGE_BASE_URL", "http://10.0.0.5:8000/v1")
    monkeypatch.setenv("MEMBENCH_JUDGE_MODEL", "my-local-model")
    judge = OssLlmJudge()
    assert judge.base_url == "http://10.0.0.5:8000/v1"
    assert judge.model == "my-local-model"


@pytest.mark.parametrize(
    "paid",
    [
        "https://api.openai.com/v1",
        "https://api.anthropic.com",
        "http://api.openai.com/v1",
        # hostname-based fence: the bare registrable domain and subdomains other than
        # `api.` must also be rejected (a substring blocklist on "api.openai.com"
        # missed these).
        "https://openai.com/v1",
        "https://gateway.openai.com/v1",
        "https://anthropic.com/v1",
    ],
)
def test_oss_judge_rejects_paid_host(paid):
    with pytest.raises(ValueError, match=r"paid|self-hosted|OSS"):
        OssLlmJudge(base_url=paid)


def test_oss_judge_is_frozen_so_fence_cannot_be_mutated_off():
    # The paid-host fence is a construction invariant; a frozen dataclass stops a
    # caller reassigning base_url to a paid host after the check has passed.
    judge = OssLlmJudge()
    with pytest.raises(Exception):  # noqa: B017 - dataclass FrozenInstanceError
        judge.base_url = "https://api.openai.com/v1"


def test_oss_judge_parses_scientific_notation_score():
    # A model emitting `1e-3` must parse as 0.001, NOT 1.0 — a bare mantissa match
    # would silently inflate a near-zero score and corrupt the curve.
    judge = OssLlmJudge()
    assert judge.parse_score('{"score": 1e-3}') == pytest.approx(0.001)
    assert judge.parse_score('{"score": 2.5e-1}') == pytest.approx(0.25)


def test_oss_judge_builds_rubric_grounded_prompt():
    judge = OssLlmJudge()
    prompt = judge.build_prompt("fix the type error", "the run edited a.ts", completion_rubric())
    assert "fix the type error" in prompt
    assert "the run edited a.ts" in prompt
    # The rubric criteria must appear so the model scores against them, not vibes.
    for c in completion_rubric().criteria:
        assert c.name in prompt


def test_oss_judge_parses_score_from_model_json():
    judge = OssLlmJudge()
    assert judge.parse_score('{"score": 0.7}') == 0.7
    assert judge.parse_score('here you go: {"score": 0.0} done') == 0.0


def test_oss_judge_parse_rejects_out_of_range():
    judge = OssLlmJudge()
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        judge.parse_score('{"score": 1.4}')


def test_oss_judge_parse_rejects_missing_score():
    judge = OssLlmJudge()
    with pytest.raises(ValueError, match="score"):
        judge.parse_score("no json here")


# --- Calibration: mechanical agreement, no semantic logic ---------------------


def test_calibration_records_and_reports_agreement():
    cal = Calibration(tolerance=0.1)
    cal.record(label=0.8, judge_score=0.75)
    cal.record(label=0.2, judge_score=0.25)
    report = cal.report()
    assert report.n == 2
    assert report.mean_abs_error == pytest.approx(0.05)
    assert report.within_tolerance_rate == pytest.approx(1.0)


def test_calibration_flags_disagreement_outside_tolerance():
    cal = Calibration(tolerance=0.1)
    cal.record(label=0.9, judge_score=0.1)  # off by 0.8
    cal.record(label=0.5, judge_score=0.5)  # exact
    report = cal.report()
    assert report.n == 2
    assert report.mean_abs_error == pytest.approx(0.4)
    assert report.within_tolerance_rate == pytest.approx(0.5)


def test_calibration_can_drive_a_stub_judge_over_a_set():
    # The spot-check loop: run a judge over a labeled set, record agreement.
    judge = StubJudge(fn=lambda t, o, r: 1.0 if "done" in o else 0.0)
    samples = [("g", "work done", 1.0), ("g", "nothing", 0.0)]
    cal = Calibration(tolerance=0.05)
    for task, output, label in samples:
        cal.record(label=label, judge_score=score_completion(judge, task, output, _rubric()))
    report = cal.report()
    assert report.within_tolerance_rate == pytest.approx(1.0)


def test_calibration_rejects_out_of_range_inputs():
    cal = Calibration(tolerance=0.1)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        cal.record(label=1.2, judge_score=0.5)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        cal.record(label=0.5, judge_score=-0.1)


def test_calibration_empty_report_is_a_caller_error():
    cal = Calibration(tolerance=0.1)
    with pytest.raises(ValueError, match=r"empty|no .*sample|at least one"):
        cal.report()


def test_calibration_tolerance_must_be_in_unit_range():
    with pytest.raises(ValueError, match="tolerance"):
        Calibration(tolerance=1.5)


# --- The rubric_score feeds combined_reward (mem-apg.3a) end to end ------------


def test_judge_term_feeds_combined_reward():
    from membench.grading.trace_score import RewardComponents, combined_reward

    judge = StubJudge(fixed=0.4)
    rubric_score = score_completion(judge, "goal", "partial work", _rubric())
    # A different-path solve: deterministic axis N/A (path not reached), judge credits.
    comp = RewardComponents(
        path_reached=False, trace_error_resolved=False, rubric_score=rubric_score
    )
    assert combined_reward(comp) == pytest.approx(0.4)
