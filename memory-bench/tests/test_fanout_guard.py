"""Issue-fanout scope guard (mem-75t.7.7) -- mechanical fanout + model-delegated
scope match. The scope judge is a `StubScopeJudge` (offline); fanout is asserted
against synthetic corpora that reproduce the gate's confound shape (a clean bundle
and a confound both at fanout 2, separated only by the judge).
"""

import pytest

from membench.bundle.assemble import (
    DEFAULT_MIN_FANOUT,
    FanoutDecision,
    RejectionReason,
    ScopeVerdict,
    StubScopeJudge,
    fanout_scope_guard,
    issue_fanout,
)
from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.schemas.bundle import BundleEnv, TaskBundle

ISSUE_KEY = "gc.var.issue"


def _rec(work_id: str, issue_ref: str | None = None) -> dict:
    meta = {ISSUE_KEY: issue_ref} if issue_ref else {}
    return {"work_id": work_id, "metadata": meta}


def _bundle(
    work_id: str, gold_files: list[str], issue_title: str = "t", issue_body: str = ""
) -> TaskBundle:
    replay = ReplayResult(
        calls=(
            CallReplay(
                index=0,
                tool="Edit",
                path="/o/x",
                rebased_path="/c/x",
                outcome=ReplayOutcome.APPLIED,
            ),
        ),
        file_diffs={p: f"diff {p}" for p in gold_files},
        replay_success_rate=1.0,
    )
    return TaskBundle(
        work_id=work_id,
        rig="gascity_dashboard",
        issue_title=issue_title,
        issue_body=issue_body,
        trace_ref="/tmp/t.jsonl",
        output=replay,
        env=BundleEnv(repo="x/y", base_commit="c1", base_image="img"),
        loo_excluded_work_ids=(work_id,),
    )


# --- issue_fanout (mechanical) ----------------------------------------------------


def test_fanout_zero_when_no_issue_ref():
    assert issue_fanout(_rec("w1"), [_rec("w1")]) == 0


def test_fanout_one_for_singleton_issue():
    corpus = [_rec("w1", "epic"), _rec("other", "different-epic")]
    assert issue_fanout(_rec("w1", "epic"), corpus) == 1


def test_fanout_counts_siblings_sharing_issue_ref():
    # 'epic' decomposed into 3 work beads -> fanout 3 (reproduces the e29gw shape).
    corpus = [_rec("w1", "epic"), _rec("w2", "epic"), _rec("w3", "epic"), _rec("z", "other")]
    assert issue_fanout(_rec("w1", "epic"), corpus) == 3


# --- StubScopeJudge ---------------------------------------------------------------


def test_stub_scope_judge_requires_exactly_one_mode():
    with pytest.raises(ValueError):
        StubScopeJudge()
    with pytest.raises(ValueError):
        StubScopeJudge(keep=True, fn=lambda t, b, f: ScopeVerdict(keep=True))


# --- fanout_scope_guard -----------------------------------------------------------


def test_guard_admits_below_threshold_without_review():
    # Singleton issue: no decomposition, judge never consulted.
    corpus = [_rec("w1", "epic")]
    d = fanout_scope_guard(
        _bundle("w1", ["a.ts"]), _rec("w1", "epic"), corpus, judge=StubScopeJudge(keep=False)
    )
    assert d.admitted and d.reviewed is False and d.fanout == 1


def test_guard_admits_high_fanout_when_scope_matches():
    # gye8 shape: fanout 2 but the gold diff matches the issue scope -> keep.
    corpus = [_rec("w1", "epic"), _rec("w2", "epic")]
    d = fanout_scope_guard(
        _bundle("w1", ["a.ts"]),
        _rec("w1", "epic"),
        corpus,
        judge=StubScopeJudge(keep=True),
    )
    assert d.admitted and d.reviewed is True and d.fanout == 2


def test_guard_rejects_high_fanout_scope_mismatch():
    # e29gw shape: many siblings, the issue over-describes the narrow gold diff -> reject.
    corpus = [_rec("w1", "epic")] + [_rec(f"s{i}", "epic") for i in range(30)]
    d = fanout_scope_guard(
        _bundle("w1", ["a.ts"]),
        _rec("w1", "epic"),
        corpus,
        judge=StubScopeJudge(
            fn=lambda t, b, f: ScopeVerdict(keep=False, rationale="issue spans 31 beads")
        ),
    )
    assert not d.admitted
    assert d.rejection.reason == RejectionReason.ISSUE_FANOUT_SCOPE_MISMATCH
    assert d.fanout == 31 and "issue spans 31 beads" in d.rejection.detail


def test_guard_rejects_conservatively_on_judge_error():
    corpus = [_rec("w1", "epic"), _rec("w2", "epic")]
    d = fanout_scope_guard(
        _bundle("w1", ["a.ts"]),
        _rec("w1", "epic"),
        corpus,
        judge=StubScopeJudge(fn=lambda t, b, f: ScopeVerdict(keep=False, error="claude timeout")),
    )
    assert not d.admitted
    assert d.rejection.reason == RejectionReason.ISSUE_FANOUT_SCOPE_MISMATCH
    assert "claude timeout" in d.rejection.detail


def test_guard_admits_high_fanout_unreviewed_without_judge():
    # No judge supplied: never fabricate a scope verdict -> admit, flagged unreviewed.
    corpus = [_rec("w1", "epic"), _rec("w2", "epic")]
    d = fanout_scope_guard(_bundle("w1", ["a.ts"]), _rec("w1", "epic"), corpus, judge=None)
    assert d.admitted and d.reviewed is False and d.fanout == 2


def test_guard_passes_gold_files_and_issue_text_to_judge():
    seen = {}

    def capture(title, body, files):
        seen["title"], seen["body"], seen["files"] = title, body, files
        return ScopeVerdict(keep=True)

    corpus = [_rec("w1", "epic"), _rec("w2", "epic")]
    fanout_scope_guard(
        _bundle("w1", ["b.ts", "a.ts"], issue_title="Fix X", issue_body="details"),
        _rec("w1", "epic"),
        corpus,
        judge=StubScopeJudge(fn=capture),
    )
    assert seen["title"] == "Fix X" and seen["body"] == "details"
    assert seen["files"] == ("a.ts", "b.ts")  # sorted gold-diff paths


def test_default_min_fanout_is_two():
    assert DEFAULT_MIN_FANOUT == 2
    assert isinstance(FanoutDecision(None, 1, False, "x").admitted, bool)
