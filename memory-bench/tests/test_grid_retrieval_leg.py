"""M1 + M2 — scoring-target declaration + the white-box retrieval-correctness leg.

The headline grid scores black-box (final diff / repro). M2 surfaces the *retrieval*
half of the claim by reusing the BUILT, tested ``metrics.scorers.score_retrieval``
at the grid scoring site: precision/recall/MRR/nDCG of an arm's retrieved work_ids
against the gold-relevant set (the bundle's declared source work_ids MINUS
``loo_excluded_work_ids``). It is reported SEPARATELY from ``score_direct`` /
``judge_score`` — never folded into a composite — so the answer-vs-retrieval
divergence (right answer, wrong retrieval) is visible. An empty relevant set scores
``None`` (not measured), never ``0.0``.

M1: a retrieval-bearing result must DECLARE its scoring target (TIAP) — a result
that computed a retrieval leg with no declared target is flagged in summarize, not
scored silently.
"""

from __future__ import annotations

import pytest

from membench.grading.probe_direct import ProbeEfficiency
from membench.grading.retrieval_leg import (
    gold_relevant_ids,
    score_retrieval_leg,
)
from membench.harbor.bundle_grid import GridConditionResult


def _result(**overrides):
    base = {
        "work_id": "w1",
        "condition": "ours",
        "score_direct": 1.0,
        "score_artifact": 1.0,
        "direct_mode": "repro",
        "repro_passed": True,
        "repro_error": None,
        "efficiency": ProbeEfficiency(input_tokens=10, output_tokens=5, turns=1, tool_calls=1),
        "candidate_files": (),
    }
    base.update(overrides)
    return GridConditionResult(**base)


# --------------------------------------------------------------------------- #
# M2 scorer
# --------------------------------------------------------------------------- #
def test_perfect_retrieval_scores_one():
    leg = score_retrieval_leg(["a", "b"], ["a", "b"], target="source")
    assert leg.recall == 1.0
    assert leg.precision == 1.0
    assert leg.retrieval_target == "source"


def test_wrong_id_drops_recall_below_one():
    # Retrieved the wrong id; recall must reflect the miss.
    leg = score_retrieval_leg(["wrong"], ["a", "b"], target="source")
    assert leg.recall is not None and leg.recall < 1.0


def test_empty_relevant_set_is_none_never_zero():
    leg = score_retrieval_leg(["a"], [], target="source")
    assert leg.precision is None
    assert leg.recall is None
    assert leg.mrr is None
    assert leg.ndcg is None


def test_gold_relevant_excludes_loo_ids():
    from membench.bundle.replay import ReplayResult
    from membench.schemas.bundle import BundleEnv, TaskBundle

    bundle = TaskBundle(
        work_id="w1",
        rig="r",
        issue_title="t",
        trace_ref="x.jsonl",
        output=ReplayResult(calls=(), file_diffs=(), replay_success_rate=0.0),
        env=BundleEnv(repo="repo", base_commit="abc123", base_image="img"),
        loo_excluded_work_ids=("w1", "sib-2"),
    )
    # The source set includes the bundle's own id, a sibling, and a legit prior work.
    relevance = {"w1": ["w1", "sib-2", "prior-9"]}
    rel = gold_relevant_ids(bundle, relevance=relevance)
    assert rel == ("prior-9",)  # own + sibling stripped
    # No relevance oracle ⇒ empty (honestly not measured), never a fabricated set.
    assert gold_relevant_ids(bundle) == ()


# --------------------------------------------------------------------------- #
# M2 on GridConditionResult — divergence visible
# --------------------------------------------------------------------------- #
def test_retrieval_leg_rides_metrics_separately_from_repro():
    leg = score_retrieval_leg(["wrong"], ["a", "b"], target="source")
    res = _result().with_retrieval_leg(leg)
    m = res.metrics()
    # The answer passed repro, but the retrieval recall is < 1.0 — the divergence
    # is visible, not laundered into a composite.
    assert m["repro_passed"] == 1.0
    assert m["retrieval_recall"] is not None and m["retrieval_recall"] < 1.0
    assert "retrieval_recall" in m and "retrieval_precision" in m


def test_default_result_has_none_retrieval_fields():
    m = _result().metrics()
    assert m["retrieval_recall"] is None
    assert m["retrieval_precision"] is None
    # None retrieval keys exist (so cached JSONs load) but are omitted from deltas.
    assert _result().retrieval_target is None


def test_metrics_keep_retrieval_separate_from_answer_keys():
    m = _result().metrics()
    # Retrieval metrics are their own keys, never folded into score_direct/judge.
    assert {"retrieval_precision", "retrieval_recall", "retrieval_mrr", "retrieval_ndcg"} <= set(m)
    assert "score_direct" in m and "judge_score" in m


# --------------------------------------------------------------------------- #
# M1 — undeclared target is flagged in summarize, not scored silently
# --------------------------------------------------------------------------- #
def test_summarize_flags_retrieval_bearing_result_with_no_target():
    from membench.harbor.bundle_grid import summarize_grid_3arm, three_arm_row

    # An ours result that computed a retrieval leg but lost its target declaration.
    bad_ours = _result(condition="ours", retrieval_recall=0.5, retrieval_target=None)
    none_clean = _result(condition="none-clean")
    builtin = _result(condition="builtin")
    row = three_arm_row(none_clean, bad_ours, builtin, ours_retrieval_empty=False)
    with pytest.raises(ValueError, match="retrieval_target"):
        summarize_grid_3arm([row], [])


def test_summarize_reports_scoring_target_for_declared_arm():
    from membench.harbor.bundle_grid import summarize_grid_3arm, three_arm_row

    leg = score_retrieval_leg(["a"], ["a", "b"], target="source")
    ours = _result(condition="ours").with_retrieval_leg(leg)
    none_clean = _result(condition="none-clean")
    builtin = _result(condition="builtin")
    row = three_arm_row(none_clean, ours, builtin, ours_retrieval_empty=False)
    summary = summarize_grid_3arm([row], [])
    assert summary["scoring_target"]["ours"] == "source"
