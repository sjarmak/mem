"""Dual-verifier scoring (mem-75t.7.5) -- both legs preserved, graceful degradation.

Offline: the test-reproduction leg is a `StubReproRunner`, the comprehension leg runs
on synthetic oracles, and diffs are minimal synthetic unified diffs. The acceptance
path (missing artifact -> artifact 0.0, direct intact) is asserted directly.
"""

import pytest

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.grading.dual_verifier import (
    PASS_THRESHOLD,
    ArtifactScore,
    DualScore,
    ReproOutcome,
    RunResult,
    StubReproRunner,
    compose_automated_score,
    gold_has_tests,
    score_artifact,
    score_direct,
    score_run,
)
from membench.schemas.bundle import BundleEnv, CuratedOracle, TaskBundle


def _diff(path: str, *body: str) -> str:
    """A minimal unified diff for one file; each ``body`` line carries its +/- prefix."""
    head = (
        f"diff --git a/{path} b/{path}\nindex 0..1 100644\n"
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n"
    )
    return head + "\n".join(body) + "\n"


def _bundle(file_diffs: dict[str, str], oracle: CuratedOracle | None = None) -> TaskBundle:
    replay = ReplayResult(
        calls=(
            CallReplay(
                index=0,
                tool="Edit",
                path="/orig/x",
                rebased_path="/co/x",
                outcome=ReplayOutcome.APPLIED,
            ),
        ),
        file_diffs=file_diffs,
        replay_success_rate=1.0,
    )
    return TaskBundle(
        work_id="mem-test",
        rig="gascity_dashboard",
        issue_title="t",
        trace_ref="/tmp/trace.jsonl",
        output=replay,
        oracle_context=oracle,
        env=BundleEnv(repo="x/y", base_commit="c1", base_image="img"),
        loo_excluded_work_ids=("mem-test",),
    )


# --- gold_has_tests ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "src/a.test.ts",
        "src/a.spec.tsx",
        "pkg/foo_test.go",
        "tests/x.py",
        "a/__tests__/b.ts",
        "test_x.py",
    ],
)
def test_gold_has_tests_detects_test_files(path):
    assert gold_has_tests([path]) is True


@pytest.mark.parametrize("path", ["src/a.ts", "frontend/Beads.tsx", "pkg/foo.go"])
def test_gold_has_tests_false_for_source(path):
    assert gold_has_tests([path]) is False


# --- score_artifact (comprehension F1) --------------------------------------------


def _oracle(answer, tiers=()):
    return CuratedOracle(oracle_answer=tuple(answer), oracle_tiers=tuple(tiers))


def test_artifact_perfect_match_f1_one():
    s = score_artifact(["a.ts", "b.ts"], _oracle(["a.ts", "b.ts"]))
    assert s.f1 == 1.0 and s.score == 1.0


def test_artifact_disjoint_zero():
    s = score_artifact(["z.ts"], _oracle(["a.ts"]))
    assert s.f1 == 0.0 and s.score == 0.0


def test_artifact_partial_unweighted_f1():
    # identified {a,x}; oracle {a,b}: precision 1/2, recall 1/2 -> f1 0.5
    s = score_artifact(["a.ts", "x.ts"], _oracle(["a.ts", "b.ts"]))
    assert s.precision == 0.5 and s.recall == 0.5 and s.f1 == 0.5
    assert s.weighted_f1 is None  # no tiers


def test_artifact_weighted_recall_penalises_missing_required():
    # oracle: required r.ts (w2), context c.ts (w0.5). Identify only c.ts.
    oracle = _oracle(["r.ts", "c.ts"], [("r.ts", "required"), ("c.ts", "context")])
    s = score_artifact(["c.ts"], oracle)
    # weighted recall = hit(0.5) / total(2.5) = 0.2; precision = 1.0
    assert s.weighted_recall == pytest.approx(0.2)
    assert s.score == s.weighted_f1
    # plain recall would be 0.5 -> weighting makes missing the required file cost more
    assert s.weighted_f1 < s.f1


def test_artifact_missing_tier_defaults_to_required():
    # 'b.ts' has no tier -> weight 2.0 (required). Identify only 'a.ts' (required).
    oracle = _oracle(["a.ts", "b.ts"], [("a.ts", "required")])
    s = score_artifact(["a.ts"], oracle)
    assert s.weighted_recall == pytest.approx(2.0 / 4.0)  # both weigh 2.0


def test_artifact_empty_oracle_raises():
    with pytest.raises(ValueError):
        score_artifact(["a.ts"], _oracle([]))


# --- score_direct -----------------------------------------------------------------


def test_direct_test_repro_primary_when_gold_has_tests():
    bundle = _bundle({"a.test.ts": _diff("a.test.ts", "+x")})
    d = score_direct(
        bundle,
        {"a.test.ts": _diff("a.test.ts", "+x")},
        test_runner=StubReproRunner(ReproOutcome(passed=True)),
    )
    assert d.mode == "test_repro" and d.score == 1.0


def test_direct_test_repro_fail_scores_zero():
    bundle = _bundle({"a.test.ts": _diff("a.test.ts", "+x")})
    d = score_direct(
        bundle,
        {"a.test.ts": _diff("a.test.ts", "+x")},
        test_runner=StubReproRunner(ReproOutcome(passed=False)),
    )
    assert d.mode == "test_repro" and d.score == 0.0


def test_direct_falls_back_to_diff_sim_when_no_tests():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    d = score_direct(
        _bundle(gold), dict(gold), test_runner=StubReproRunner(ReproOutcome(passed=True))
    )
    # No test files in gold -> the runner is never consulted; diff-sim of identical
    # diffs is 1.0.
    assert d.mode == "diff_sim" and d.score == 1.0


def test_direct_falls_back_when_runner_errors():
    bundle = _bundle({"a.test.ts": _diff("a.test.ts", "+x")})
    d = score_direct(
        bundle,
        {"a.test.ts": _diff("a.test.ts", "+x")},
        test_runner=StubReproRunner(ReproOutcome(passed=False, error="git apply failed")),
    )
    assert d.mode == "diff_sim" and d.diff_sim is not None
    assert d.repro_error == "git apply failed"  # fallback records WHY it downgraded


def test_direct_empty_candidate_is_zero_diff_sim():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    d = score_direct(_bundle(gold), {}, test_runner=StubReproRunner(ReproOutcome(passed=True)))
    assert d.mode == "diff_sim" and d.score == 0.0


def test_direct_carries_test_ratio_from_outcome():
    # S1: a partial per-file outcome surfaces as DirectScore.test_ratio alongside the
    # binary score (which stays 0.0 -- not every file passed).
    bundle = _bundle({"a.test.ts": _diff("a.test.ts", "+x")})
    outcome = ReproOutcome(passed=False, tests_passed=1, tests_total=2)
    d = score_direct(
        bundle, {"a.test.ts": _diff("a.test.ts", "+x")}, test_runner=StubReproRunner(outcome)
    )
    assert d.mode == "test_repro" and d.score == 0.0 and d.test_ratio == 0.5


def test_direct_fallback_has_no_test_ratio():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    d = score_direct(_bundle(gold), dict(gold), test_runner=None)
    assert d.mode == "diff_sim" and d.test_ratio is None


# --- compose_automated_score ------------------------------------------------------


def test_compose_direct_is_default_and_ignores_artifact():
    assert (
        compose_automated_score(0.8, 0.2, policy="direct", weight_direct=0.5, weight_artifact=0.5)
        == 0.8
    )


def test_compose_min_mean_weighted():
    assert (
        compose_automated_score(0.8, 0.2, policy="min", weight_direct=0.5, weight_artifact=0.5)
        == 0.2
    )
    assert compose_automated_score(
        0.8, 0.2, policy="mean", weight_direct=0.5, weight_artifact=0.5
    ) == pytest.approx(0.5)
    assert compose_automated_score(
        1.0, 0.0, policy="weighted", weight_direct=0.7, weight_artifact=0.3
    ) == pytest.approx(0.7)


def test_compose_weighted_bad_weights_raises():
    with pytest.raises(ValueError):
        compose_automated_score(1.0, 1.0, policy="weighted", weight_direct=0.6, weight_artifact=0.6)


def test_compose_none_leg_treated_as_zero_in_composite():
    assert (
        compose_automated_score(0.8, None, policy="min", weight_direct=0.5, weight_artifact=0.5)
        == 0.0
    )


# --- score_run (end-to-end) -------------------------------------------------------


def _scored_bundle(file_diffs, oracle, run, **kw):
    return score_run(_bundle(file_diffs, oracle), run, **kw)


def test_score_run_emits_both_sub_scores():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    oracle = _oracle(["src/a.ts"])
    dual, new_bundle = _scored_bundle(
        gold, oracle, RunResult(candidate_diff=dict(gold), identified_files=("src/a.ts",))
    )
    assert dual.score_direct == 1.0
    assert dual.score_artifact == 1.0
    assert new_bundle.verification.score_direct == 1.0
    assert new_bundle.verification.score_artifact == 1.0


def test_score_run_missing_artifact_yields_zero_direct_intact():
    # ACCEPTANCE: no identified-files artifact -> artifact 0.0, direct leg intact.
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    dual, new_bundle = _scored_bundle(
        gold, _oracle(["src/a.ts"]), RunResult(candidate_diff=dict(gold), identified_files=None)
    )
    assert dual.score_artifact == 0.0
    assert dual.score_direct == 1.0  # direct leg unaffected
    assert any(leg == "artifact" for leg, _ in dual.degradations)
    assert new_bundle.verification.score_artifact == 0.0


def test_score_run_missing_oracle_leaves_artifact_unscoreable():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    dual, _ = _scored_bundle(
        gold, None, RunResult(candidate_diff=dict(gold), identified_files=("src/a.ts",))
    )
    assert dual.score_artifact is None  # not 0.0 -- a missing oracle does not blame the run
    assert dual.score_direct == 1.0


def test_score_run_records_efficiency_from_transcript():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    transcript = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use"}],'
        '"usage":{"input_tokens":10,"output_tokens":5}}}'
    )
    dual, _ = _scored_bundle(
        gold,
        _oracle(["src/a.ts"]),
        RunResult(candidate_diff=dict(gold), identified_files=("src/a.ts",), transcript=transcript),
    )
    assert dual.efficiency is not None
    assert dual.efficiency.turns == 1 and dual.efficiency.tool_calls == 1
    assert dual.efficiency.input_tokens == 10 and dual.efficiency.output_tokens == 5


def test_score_run_no_transcript_degrades_efficiency():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    dual, _ = _scored_bundle(gold, _oracle(["src/a.ts"]), RunResult(candidate_diff=dict(gold)))
    assert dual.efficiency is None
    assert any(leg == "efficiency" for leg, _ in dual.degradations)


def test_score_run_policy_override_composes_automated_score():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    oracle = _oracle(
        ["src/a.ts", "src/b.ts"]
    )  # identify only a -> artifact 0.5 (p1, r0.5 -> f1 .667)
    dual, _ = _scored_bundle(
        gold,
        oracle,
        RunResult(candidate_diff=dict(gold), identified_files=("src/a.ts",)),
        scoring_policy="min",
    )
    # min(direct=1.0, artifact≈0.667) == artifact
    assert dual.automated_score == pytest.approx(dual.score_artifact)
    assert dual.scoring_policy == "min"


def test_score_run_default_policy_is_direct():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    dual, _ = _scored_bundle(
        gold, _oracle(["q.ts"]), RunResult(candidate_diff=dict(gold), identified_files=("z.ts",))
    )
    assert dual.scoring_policy == "direct"
    assert dual.automated_score == dual.score_direct  # artifact ignored under 'direct'


def test_score_run_is_immutable():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    bundle = _bundle(gold, _oracle(["src/a.ts"]))
    assert bundle.verification.score_direct is None
    _, new_bundle = score_run(
        bundle, RunResult(candidate_diff=dict(gold), identified_files=("src/a.ts",))
    )
    assert bundle.verification.score_direct is None  # original untouched
    assert new_bundle.verification.score_direct == 1.0


def test_score_run_computes_always_on_diff_sim_on_test_repro_path():
    # S2: diff_sim is computed on EVERY run, including the test-repro happy path where
    # the direct leg itself never falls back to it. Identical candidate==gold -> 1.0.
    gold = {"a.test.ts": _diff("a.test.ts", "+x")}
    dual, _ = _scored_bundle(
        gold,
        _oracle(["a.test.ts"]),
        RunResult(candidate_diff=dict(gold), identified_files=("a.test.ts",)),
        test_runner=StubReproRunner(ReproOutcome(passed=True, tests_passed=1, tests_total=1)),
    )
    assert dual.direct.mode == "test_repro"
    assert dual.diff_sim is not None and dual.diff_sim.combined == 1.0
    assert dual.test_ratio == 1.0


def test_score_run_diff_sim_reuses_fallback_computation():
    # On the diff-sim fallback the always-on signal IS the leg's own computation.
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    dual, _ = _scored_bundle(
        gold, _oracle(["src/a.ts"]), RunResult(candidate_diff=dict(gold), identified_files=None)
    )
    assert dual.direct.mode == "diff_sim"
    assert dual.diff_sim is dual.direct.diff_sim
    assert dual.test_ratio is None  # no tests ran on the fallback


def test_score_run_pass_flags_use_threshold():
    gold = {"src/a.ts": _diff("src/a.ts", "+x")}
    dual, _ = _scored_bundle(
        gold,
        _oracle(["src/a.ts"]),
        RunResult(candidate_diff=dict(gold), identified_files=("src/a.ts",)),
    )
    assert dual.passed_direct is (dual.score_direct >= PASS_THRESHOLD)
    assert dual.passed_artifact is (dual.score_artifact >= PASS_THRESHOLD)
    assert isinstance(dual, DualScore) and isinstance(dual.artifact, ArtifactScore)
