"""Bundle schema + assembler + admission filter (mem-75t.7.2, plan §4 P1 + §9.3).

Records are Mapping-shaped WorkRecords (the same JSON shape `validity` and
`assess` read); replay results are built directly from the P0 types. Every
rejection path asserts the TYPED reason -- a silent drop is the failure mode the
admission filter exists to prevent.
"""

import pytest
from pydantic import ValidationError

from membench.bundle.assemble import (
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
    assert "src/store/writer.ts" in bundle.output.file_diffs


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
