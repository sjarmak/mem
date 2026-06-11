"""Bundle schema + assembler + admission filter (mem-75t.7.2, plan §4 P1 + §9.3).

Records are Mapping-shaped WorkRecords (the same JSON shape `validity` and
`assess` read); replay results are built directly from the P0 types. Every
rejection path asserts the TYPED reason -- a silent drop is the failure mode the
admission filter exists to prevent.
"""

import pytest
from pydantic import ValidationError

from membench.bundle.assemble import (
    MIN_ADJUSTED_REPLAY_SUCCESS_RATE,
    RejectionReason,
    assemble_bundle,
    loo_excluded_ids,
)
from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.grading.leak_guard import OutcomeLeakError
from membench.schemas.bundle import (
    BundleEnv,
    BundleVerification,
    CuratedOracle,
    TaskBundle,
)

COMMIT_SHA = "deadbeef1234deadbeef1234deadbeef1234dead"
BASE_COMMIT = "cafebabe5678cafebabe5678cafebabe5678cafe"


def make_record(**overrides):
    """A closed, clean-tailed, env-anchored WorkRecord -- the admit-everything
    baseline each test perturbs."""
    record = {
        "work_id": "mem-1.1",
        "rig": "mem",
        "title": "Fix the flaky store writer test",
        "description": "## Spec\nThe writer test flakes under parallel runs.",
        "lifecycle": {
            "created": "2026-06-01T00:00:00Z",
            "started": "2026-06-02T00:00:00Z",
            "closed": "2026-06-03T00:00:00Z",
            "status": "closed",
        },
        "links": {"deps": [], "supersedes": []},
        "outcome": {
            "repo": "sjarmak/mem",
            "commit_sha": COMMIT_SHA,
            "base_commit": BASE_COMMIT,
        },
        "trace": {
            "jsonl_path": "/traces/mem-1.1.jsonl",
            "tool_outcomes": [
                _execution("pytest", "fail", errors=[_trace_error("pytest")]),
                _execution("pytest", "pass"),
                _execution("tsc", "pass"),
            ],
            "errors": [_trace_error("pytest")],
        },
    }
    record.update(overrides)
    return record


def _execution(runner: str, status: str, errors: list | None = None) -> dict:
    return {
        "runner": runner,
        "command": f"{runner} .",
        "status": status,
        "errors": errors or [],
    }


def _trace_error(tool: str) -> dict:
    return {
        "tool": tool,
        "severity": "error",
        "message": "AssertionError: boom",
        "file": "tests/store.test.ts",
        "line": 12,
    }


def make_replay(**overrides) -> ReplayResult:
    fields = {
        "calls": (
            CallReplay(
                index=0,
                tool="Edit",
                path="/orig/work/src/store/writer.ts",
                rebased_path="/tmp/checkout/src/store/writer.ts",
                outcome=ReplayOutcome.APPLIED,
            ),
        ),
        "file_diffs": {"src/store/writer.ts": "diff --git a/src/store/writer.ts ..."},
        "replay_success_rate": 1.0,
    }
    fields.update(overrides)
    return ReplayResult(**fields)


# --- admission: accept path ------------------------------------------------------


def test_clean_closed_record_assembles_a_bundle():
    bundle = assemble_bundle(make_record(), make_replay())
    assert isinstance(bundle, TaskBundle)
    assert bundle.work_id == "mem-1.1"
    assert bundle.rig == "mem"
    assert bundle.issue_title == "Fix the flaky store writer test"
    assert "flakes under parallel runs" in bundle.issue_body
    assert bundle.trace_ref == "/traces/mem-1.1.jsonl"
    assert bundle.output.replay_success_rate == 1.0
    assert "src/store/writer.ts" in bundle.output.diff_by_file()


def test_env_fields_populated_from_record():
    bundle = assemble_bundle(make_record(), make_replay())
    assert isinstance(bundle, TaskBundle)
    assert bundle.env.repo == "sjarmak/mem"
    assert bundle.env.base_commit == BASE_COMMIT
    # rig "mem" maps to node in env_recon's base-image table.
    assert bundle.env.base_image == "node:22-bookworm"


def test_provenance_env_anchor_used_when_outcome_lacks_one():
    record = make_record(
        outcome={"commit_sha": COMMIT_SHA},
        provenance={"repo": "sjarmak/mem", "base_commit": BASE_COMMIT},
    )
    bundle = assemble_bundle(record, make_replay())
    assert isinstance(bundle, TaskBundle)
    assert bundle.env.repo == "sjarmak/mem"
    assert bundle.env.base_commit == BASE_COMMIT


def test_unknown_rig_falls_back_to_default_base_image():
    record = make_record(rig="someotherrig")
    bundle = assemble_bundle(record, make_replay())
    assert isinstance(bundle, TaskBundle)
    assert bundle.env.base_image == "ubuntu:24.04"


def test_oracle_context_starts_absent_and_verification_unscored():
    bundle = assemble_bundle(make_record(), make_replay())
    assert isinstance(bundle, TaskBundle)
    assert bundle.oracle_context is None
    assert bundle.verification.score_direct is None
    assert bundle.verification.score_artifact is None


def test_resolved_then_passing_runner_is_a_clean_tail():
    """An early failure FIXED by a later pass of the same runner is resolved --
    only the runner's FINAL execution decides the tail."""
    record = make_record()
    assert isinstance(assemble_bundle(record, make_replay()), TaskBundle)


# --- admission: reject paths -----------------------------------------------------


def test_open_bead_rejected():
    record = make_record(
        lifecycle={
            "created": "2026-06-01T00:00:00Z",
            "started": "2026-06-02T00:00:00Z",
            "status": "in_progress",
        }
    )
    rejection = assemble_bundle(record, make_replay())
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.NOT_CLOSED
    assert rejection.work_id == "mem-1.1"


def test_closed_status_without_closed_timestamp_rejected():
    record = make_record(
        lifecycle={
            "created": "2026-06-01T00:00:00Z",
            "started": "2026-06-02T00:00:00Z",
            "status": "closed",
        }
    )
    rejection = assemble_bundle(record, make_replay())
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.NOT_CLOSED


def test_dirty_trace_tail_rejected():
    record = make_record()
    record["trace"]["tool_outcomes"].append(
        _execution("pytest", "fail", errors=[_trace_error("pytest")])
    )
    rejection = assemble_bundle(record, make_replay())
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.DIRTY_TRACE_TAIL
    assert "pytest" in rejection.detail


def test_failing_final_execution_without_extracted_errors_is_still_dirty():
    record = make_record()
    record["trace"]["tool_outcomes"].append(_execution("go", "fail"))
    rejection = assemble_bundle(record, make_replay())
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.DIRTY_TRACE_TAIL


def test_unparsed_trace_with_errors_is_dirty():
    """trace.errors with no per-execution evidence cannot show resolution --
    the conservative direction is reject, never admit-by-ignorance."""
    record = make_record()
    record["trace"] = {
        "jsonl_path": "/traces/mem-1.1.jsonl",
        "errors": [_trace_error("pytest")],
    }
    rejection = assemble_bundle(record, make_replay())
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.DIRTY_TRACE_TAIL


def test_record_without_trace_rejected():
    record = make_record()
    del record["trace"]
    rejection = assemble_bundle(record, make_replay())
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.NO_TRACE


def test_record_without_env_anchor_rejected():
    record = make_record(outcome={"commit_sha": COMMIT_SHA})
    rejection = assemble_bundle(record, make_replay())
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.MISSING_ENV


def test_empty_gold_diff_rejected():
    replay = make_replay(calls=(), file_diffs={}, replay_success_rate=0.0)
    rejection = assemble_bundle(make_record(), replay)
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.EMPTY_OUTPUT


# --- validation-derived admission gates (mem-75t.7.2 revisions 4-6) ---------------
# Shapes from .gc/docs/mem-75t.7.1-replay-validation.md.


def _call(index: int, outcome: ReplayOutcome, path: str | None = None) -> CallReplay:
    resolved = path or f"/orig/work/src/f{index}.ts"
    outside = outcome is ReplayOutcome.OUTSIDE_WORK_DIR
    return CallReplay(
        index=index,
        tool="Edit",
        path=resolved,
        rebased_path=None if outside else f"/tmp/checkout/src/f{index}.ts",
        outcome=outcome,
    )


def _calls(applied: int, missing: int = 0, outside: int = 0) -> tuple[CallReplay, ...]:
    outcomes = (
        [ReplayOutcome.APPLIED] * applied
        + [ReplayOutcome.OLD_STRING_MISSING] * missing
        + [ReplayOutcome.OUTSIDE_WORK_DIR] * outside
    )
    return tuple(_call(i, outcome) for i, outcome in enumerate(outcomes))


def test_adjusted_rate_at_threshold_admits():
    # Boundary: adjusted rate exactly MIN_ADJUSTED_REPLAY_SUCCESS_RATE (9/10) admits.
    replay = make_replay(calls=_calls(applied=9, missing=1), replay_success_rate=0.9)
    assert replay.adjusted_replay_success_rate == MIN_ADJUSTED_REPLAY_SUCCESS_RATE
    assert isinstance(assemble_bundle(make_record(), replay), TaskBundle)


def test_below_threshold_rejected_as_low_replay_fidelity():
    # 8/10 adjusted: a partial replay is a gold diff with MISSING hunks -- a
    # corrupted oracle, worse than no bundle.
    replay = make_replay(calls=_calls(applied=8, missing=2), replay_success_rate=0.8)
    rejection = assemble_bundle(make_record(), replay)
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.LOW_REPLAY_FIDELITY
    assert "0.80" in rejection.detail
    assert "0.9" in rejection.detail


def test_outside_work_dir_calls_do_not_count_against_admission():
    # Raw rate 9/15 = 0.6 but adjusted 9/10 = 0.9: the 5 auto-memory writes are
    # out-of-repo by construction and excluded from the admission denominator.
    replay = make_replay(calls=_calls(applied=9, missing=1, outside=5), replay_success_rate=0.6)
    assert isinstance(assemble_bundle(make_record(), replay), TaskBundle)


def test_first_edit_absent_rejected_as_base_predates_tree():
    # zg4da shape: every gate downstream would also fire (empty diff, 0.0 rate) but
    # the DIAGNOSTIC reason -- base_commit predates the session tree -- must win.
    calls = (
        _call(0, ReplayOutcome.FILE_ABSENT, path="/orig/work/backend/routes/allowlist.ts"),
        _call(1, ReplayOutcome.OLD_STRING_MISSING),
    )
    replay = make_replay(calls=calls, file_diffs={}, replay_success_rate=0.0)
    rejection = assemble_bundle(make_record(), replay)
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.BASE_PREDATES_TREE
    assert "allowlist.ts" in rejection.detail


def test_shared_trace_rejected_with_sharing_work_ids():
    # Mega-session shape: one transcript mapped to several work_records (the mem
    # trio); replaying the full stream against one bead's base mixes edit streams.
    # Per-bead segmentation is deferred -- rejection only.
    record = make_record()
    shared_trace = {"jsonl_path": "/traces/mem-1.1.jsonl"}
    corpus = [
        record,  # the record itself never counts as a sharer
        _corpus_record("mem-9.2", trace=dict(shared_trace)),
        _corpus_record("mem-9.1", trace=dict(shared_trace)),
        _corpus_record("mem-9.3", trace={"jsonl_path": "/traces/other.jsonl"}),
    ]
    rejection = assemble_bundle(record, make_replay(), corpus=corpus)
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.SHARED_TRACE
    assert "mem-9.1, mem-9.2" in rejection.detail
    assert "mem-9.3" not in rejection.detail


def test_own_record_in_corpus_is_not_a_shared_trace():
    record = make_record()
    bundle = assemble_bundle(record, make_replay(), corpus=[record])
    assert isinstance(bundle, TaskBundle)


# --- issue-leg resolution (gc.var.issue workflow records) --------------------------


def _workflow_record(**overrides):
    """A mol-focus-review-shaped workflow record: the stored title is the formula
    name; the real task statement lives on the bead named by metadata['gc.var.issue']."""
    record = make_record(
        title="mol-focus-review",
        description="",
        metadata={
            "gc.var.issue": "mem-issue-7",
            "gc.input_convoy_id": "mem-convoy-3",
            "gc.var.convoy_id": "mem-convoy-3",
        },
    )
    return {**record, **overrides}


def _issue_bead(**overrides):
    record = _corpus_record(
        "mem-issue-7",
        title="Fix the parallel-run flake in the store writer",
        description="## Spec\nWriter test flakes when two runs share a tmpdir.",
    )
    record.update(overrides)
    return record


def test_issue_leg_resolved_from_referenced_bead():
    record = _workflow_record()
    bundle = assemble_bundle(record, make_replay(), corpus=[record, _issue_bead()])
    assert isinstance(bundle, TaskBundle)
    # The issue leg is the REFERENCED bead's text, not the workflow formula name.
    assert bundle.issue_title == "Fix the parallel-run flake in the store writer"
    assert "share a tmpdir" in bundle.issue_body
    # The original work_id stays the bundle anchor; the referenced bead is provenance.
    assert bundle.work_id == "mem-1.1"
    assert bundle.issue_work_id == "mem-issue-7"


def test_record_without_issue_ref_keeps_own_text_and_no_issue_provenance():
    bundle = assemble_bundle(make_record(), make_replay())
    assert isinstance(bundle, TaskBundle)
    assert bundle.issue_title == "Fix the flaky store writer test"
    assert bundle.issue_work_id is None


def test_unresolved_issue_ref_rejected():
    # Without the referenced bead the issue leg would be the formula name --
    # meaningless as an agent-facing task statement. Typed rejection, never a
    # silent fall-back to the workflow title.
    record = _workflow_record()
    rejection = assemble_bundle(record, make_replay(), corpus=[record])
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.ISSUE_REF_UNRESOLVED
    assert "mem-issue-7" in rejection.detail


def test_referenced_issue_text_passes_the_same_leak_guard():
    record = _workflow_record()
    leaky_issue = _issue_bead(title=f"Land the fix from {COMMIT_SHA}")
    with pytest.raises(OutcomeLeakError):
        assemble_bundle(record, make_replay(), corpus=[record, leaky_issue])


def test_referenced_issue_own_outcome_labels_also_guarded():
    issue_sha = "feedface9876feedface9876feedface9876feed"
    record = _workflow_record()
    leaky_issue = _issue_bead(
        title=f"Cherry-pick {issue_sha} cleanly",
        outcome={"commit_sha": issue_sha},
    )
    with pytest.raises(OutcomeLeakError):
        assemble_bundle(record, make_replay(), corpus=[record, leaky_issue])


def test_issue_bead_in_loo_excluded_work_ids():
    record = _workflow_record()
    bundle = assemble_bundle(record, make_replay(), corpus=[record, _issue_bead()])
    assert isinstance(bundle, TaskBundle)
    assert "mem-issue-7" in bundle.loo_excluded_work_ids


def test_issue_bead_sharing_the_transcript_is_not_a_shared_trace():
    # tkhkg shape: the workflow bead and its OWN issue bead point at the same
    # transcript -- one unit of work recorded on two beads, not a mega-session.
    record = _workflow_record()
    issue = _issue_bead(trace={"jsonl_path": "/traces/mem-1.1.jsonl"})
    bundle = assemble_bundle(record, make_replay(), corpus=[record, issue])
    assert isinstance(bundle, TaskBundle)
    assert bundle.issue_work_id == "mem-issue-7"


def test_foreign_record_sharing_the_transcript_still_rejects():
    record = _workflow_record()
    corpus = [
        record,
        _issue_bead(),
        _corpus_record("mem-9.9", trace={"jsonl_path": "/traces/mem-1.1.jsonl"}),
    ]
    rejection = assemble_bundle(record, make_replay(), corpus=corpus)
    assert not isinstance(rejection, TaskBundle)
    assert rejection.reason is RejectionReason.SHARED_TRACE
    assert "mem-9.9" in rejection.detail


# --- leak guard ------------------------------------------------------------------


def test_leak_guard_fires_on_planted_outcome_label_in_issue_text():
    record = make_record(title=f"Land the fix from {COMMIT_SHA}")
    with pytest.raises(OutcomeLeakError):
        assemble_bundle(record, make_replay())


def test_leak_guard_fires_on_planted_label_in_body():
    record = make_record(description=f"See base {BASE_COMMIT} for context.")
    with pytest.raises(OutcomeLeakError):
        assemble_bundle(record, make_replay())


# --- LOO exclusion invariant -----------------------------------------------------


def _corpus_record(work_id: str, **overrides):
    record = {
        "work_id": work_id,
        "rig": "mem",
        "title": work_id,
        "lifecycle": {
            "created": "2026-05-01T00:00:00Z",
            "closed": "2026-05-02T00:00:00Z",
            "status": "closed",
        },
        "links": {"deps": [], "supersedes": []},
    }
    record.update(overrides)
    return record


def test_loo_excluded_ids_cover_self_and_siblings():
    query = make_record(
        external_ref="branch-x",
        links={"deps": [], "convoy_id": "convoy-7", "supersedes": ["mem-0.9"]},
    )
    corpus = [
        query,
        _corpus_record("mem-2.2", links={"deps": [], "convoy_id": "convoy-7", "supersedes": []}),
        _corpus_record("mem-3.3", external_ref="branch-x"),
        _corpus_record("mem-0.9"),  # supersedes-chain member
        _corpus_record("mem-4.4"),  # unrelated -- must NOT be excluded
    ]
    excluded = loo_excluded_ids(query, corpus)
    assert excluded == ("mem-0.9", "mem-1.1", "mem-2.2", "mem-3.3")


def test_loo_includes_own_supersedes_links_when_record_absent_from_corpus():
    # Regression: the record's own links.supersedes edges must seed the closure
    # even when the corpus omits the record (the default corpus=()).
    query = make_record(links={"deps": [], "supersedes": ["mem-0.9"]})
    assert loo_excluded_ids(query, ()) == ("mem-0.9", "mem-1.1")
    # And transitively through corpus edges hanging off the record's own link.
    corpus = [_corpus_record("mem-0.8", links={"deps": [], "supersedes": ["mem-0.9"]})]
    assert loo_excluded_ids(query, corpus) == ("mem-0.8", "mem-0.9", "mem-1.1")


def test_bundle_carries_the_loo_exclusion_ids():
    query = make_record(links={"deps": [], "convoy_id": "convoy-7", "supersedes": []})
    sibling = _corpus_record(
        "mem-2.2", links={"deps": [], "convoy_id": "convoy-7", "supersedes": []}
    )
    bundle = assemble_bundle(query, make_replay(), corpus=[query, sibling])
    assert isinstance(bundle, TaskBundle)
    assert bundle.loo_excluded_work_ids == ("mem-1.1", "mem-2.2")


def test_own_work_id_always_excluded_even_with_empty_corpus():
    bundle = assemble_bundle(make_record(), make_replay())
    assert isinstance(bundle, TaskBundle)
    assert bundle.loo_excluded_work_ids == ("mem-1.1",)


def test_loo_excluded_keys_on_gc_metadata_link_fields():
    # These records carry convoy/issue links in gc.* METADATA, not links.convoy_id
    # (the store shape: links.convoy_id is null on every workflow record). Sibling
    # detection must group on the same values mechanically.
    query = _workflow_record()
    corpus = [
        query,
        # The convoy bead itself (its work_id IS the metadata convoy value).
        _corpus_record("mem-convoy-3", title="input convoy for mem-issue-7"),
        # The issue bead (its work_id IS the gc.var.issue value).
        _issue_bead(),
        # Another session on the SAME issue (the e29gw/usu9f shape).
        _corpus_record("mem-8.8", metadata={"gc.var.issue": "mem-issue-7"}),
        # Another record in the SAME convoy.
        _corpus_record("mem-7.7", metadata={"gc.input_convoy_id": "mem-convoy-3"}),
        # Unrelated -- must NOT be excluded.
        _corpus_record("mem-4.4", metadata={"gc.var.issue": "mem-other-issue"}),
    ]
    excluded = loo_excluded_ids(query, corpus)
    assert "mem-convoy-3" in excluded
    assert "mem-issue-7" in excluded
    assert "mem-8.8" in excluded
    assert "mem-7.7" in excluded
    assert "mem-4.4" not in excluded


def test_loo_excluded_includes_gc_link_values_even_with_empty_corpus():
    # The referenced bead ids are work refs by the gc contract: they belong in
    # the exclusion set even when the assembly corpus happens to omit them.
    excluded = loo_excluded_ids(_workflow_record(), ())
    assert set(excluded) == {"mem-1.1", "mem-convoy-3", "mem-issue-7"}


# --- schema: round-trip + immutability --------------------------------------------


def test_bundle_round_trips_through_json():
    bundle = assemble_bundle(make_record(), make_replay())
    assert isinstance(bundle, TaskBundle)
    restored = TaskBundle.model_validate_json(bundle.model_dump_json())
    assert restored == bundle


def test_bundle_with_oracle_context_round_trips():
    base = assemble_bundle(make_record(), make_replay())
    assert isinstance(base, TaskBundle)
    enriched = base.model_copy(
        update={
            "oracle_context": CuratedOracle(
                oracle_answer=("src/store/writer.ts",),
                oracle_tiers=(("src/store/writer.ts", "required"),),
                oracle_backends_consensus=("grep", "ast"),
            )
        }
    )
    restored = TaskBundle.model_validate_json(enriched.model_dump_json())
    assert restored == enriched
    assert restored.oracle_context is not None
    assert restored.oracle_context.oracle_answer == ("src/store/writer.ts",)


def test_scoring_policy_vocabulary_is_constrained():
    # Plan §9.5 vocabulary (+ the current "direct" probe leg); a typo fails loudly.
    for policy in ("direct", "min", "mean", "weighted"):
        assert BundleVerification(scoring_policy=policy).scoring_policy == policy
    with pytest.raises(ValidationError):
        BundleVerification(scoring_policy="wieghted")


def test_bundle_models_are_frozen():
    bundle = assemble_bundle(make_record(), make_replay())
    assert isinstance(bundle, TaskBundle)
    with pytest.raises(ValidationError):
        bundle.issue_title = "mutated"
    env = BundleEnv(repo="a/b", base_commit="c1", base_image="img")
    with pytest.raises(ValidationError):
        env.repo = "x/y"
    verification = BundleVerification()
    with pytest.raises(ValidationError):
        verification.score_direct = 1.0
