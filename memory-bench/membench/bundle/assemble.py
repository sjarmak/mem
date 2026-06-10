"""Bundle assembler + admission filter (mem-75t.7.2, plan §4 P1 + §9.3).

Assembles a `TaskBundle` from a Mapping-shaped WorkRecord (the same JSON shape
`validity.query_from_record` and `assess` read) plus its P0 `ReplayResult`. Three
invariants are enforced here, mechanically:

1. **Admission (plan §9.3).** The trace-derived gold diff is "what the agent did",
   not "the correct fix" -- this corpus has no merged-PR/CI outcome linkage to vouch
   for it. The only structural evidence the work ended well is the record's own
   lifecycle + trace: admit ONLY a bead that is closed (status AND timestamp agree)
   AND whose trace tail is clean -- no unresolved trace_errors in the final segment,
   where "final segment" is each runner's LAST execution in transcript order (an
   early failure fixed by a later pass of the same runner is resolved; a failure
   nothing ran after is not). Two further structural gates make the bundle a real
   eval object: an env anchor (repo + base_commit -- without it the bundle is not
   runnable) and a non-empty gold diff (without it there is no output leg;
   `ReplayResult` itself pins the empty-replay rate to 0.0 as "non-admittable").
   Every rejection is a typed `Rejection` with a `RejectionReason` -- never a
   silent drop, so batch admission stats are computable from the rejections.

2. **Leak guard.** The issue leg (title/body) is agent-readable text; the record's
   high-entropy outcome labels must not appear in it. Reuses
   `grading.leak_guard.assert_no_outcome_leak`, which RAISES -- a planted outcome
   label is a validity bug that must fail the run, not a rejection to tally.

3. **LOO invariant (plan §9.3).** The bundle stores the work_ids any grid run must
   withhold from memory arms: the record itself, its undirected supersedes closure,
   and its convoy/pr/branch siblings -- the exact `validity` exclusion semantics
   (same `query_from_record` boundary), frozen INTO the bundle so enforcement is
   mechanical rather than a per-run convention.

ZFC: pure mechanism -- structural field reads, set arithmetic, no IO, no model
calls. The pass/fail status this module keys on was produced upstream by the
deterministic trace parse (``parse/trace-parse.ts``), not judged here.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from membench.bundle.replay import ReplayResult
from membench.grading.leak_guard import assert_no_outcome_leak, outcome_labels
from membench.harbor.env_recon import DEFAULT_BASE_IMAGE, DEFAULT_BASE_IMAGES
from membench.schemas.bundle import BundleEnv, BundleVerification, TaskBundle
from membench.validity import (
    is_sibling,
    query_from_record,
    supersedes_closure,
    work_ref_from_record,
)


class RejectionReason(StrEnum):
    """Why a record was not admitted. Reasons are checked in this order; the first
    failure wins, so a rejection names the gate that actually fired."""

    NOT_CLOSED = "not_closed"
    NO_TRACE = "no_trace"
    DIRTY_TRACE_TAIL = "dirty_trace_tail"
    MISSING_ENV = "missing_env"
    EMPTY_OUTPUT = "empty_output"


@dataclass(frozen=True)
class Rejection:
    """A typed non-admission -- the anti-silent-drop contract. ``detail`` localizes
    the evidence (which runner failed, which anchor is missing)."""

    work_id: str
    reason: RejectionReason
    detail: str = ""


def _mapping(record: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = record.get(key)
    return value if isinstance(value, Mapping) else {}


def _text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    return value.strip() if isinstance(value, str) else ""


def _issue_body(record: Mapping[str, Any]) -> str:
    """The long-form spec text -- the same forward-shaped ``description``/``body``
    read as `assess._body_text` (current ingest carries only ``title``; the body
    rides in for free once the export carries it)."""
    return _text(record, "description") or _text(record, "body")


def _unresolved_tail_failures(trace: Mapping[str, Any]) -> list[str]:
    """The runners whose FINAL execution failed or still carried errors -- the
    trace's unresolved tail. Transcript order is the resolution evidence: a later
    pass of the same runner resolves its earlier failures; nothing after a failure
    leaves it unresolved.

    A trace with extracted ``errors`` but NO per-execution outcomes offers no
    resolution evidence at all -- conservative direction is dirty (reject), never
    admit-by-ignorance."""
    outcomes = trace.get("tool_outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        return ["<unparsed>"] if trace.get("errors") else []
    final_by_runner: dict[str, Mapping[str, Any]] = {}
    for execution in outcomes:
        if not isinstance(execution, Mapping):
            raise ValueError(f"malformed trace execution (not a mapping): {execution!r}")
        final_by_runner[str(execution.get("runner"))] = execution
    return sorted(
        runner
        for runner, execution in final_by_runner.items()
        if execution.get("status") == "fail" or execution.get("errors")
    )


def admit_record(record: Mapping[str, Any]) -> Rejection | None:
    """The plan-§9.3 admission filter over the record alone; None means admitted.

    Closed requires lifecycle AGREEMENT (status == "closed" AND a closed
    timestamp): the timestamp is what makes the record LOO-eligible for other
    queries, so a status-only close is treated as not closed."""
    work_id = str(record.get("work_id"))
    lifecycle = _mapping(record, "lifecycle")
    if lifecycle.get("status") != "closed" or not lifecycle.get("closed"):
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.NOT_CLOSED,
            detail=f"status={lifecycle.get('status')!r}, closed={lifecycle.get('closed')!r}",
        )
    trace = _mapping(record, "trace")
    if not trace.get("jsonl_path"):
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.NO_TRACE,
            detail="record carries no trace.jsonl_path",
        )
    failing = _unresolved_tail_failures(trace)
    if failing:
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.DIRTY_TRACE_TAIL,
            detail=f"unresolved final-segment failures: {', '.join(failing)}",
        )
    return None


def _env_from_record(record: Mapping[str, Any], base_images: Mapping[str, str]) -> BundleEnv | None:
    """The runnable env anchor, or None when the record carries none. ``outcome``
    (PR-authoritative base) is preferred over ``provenance`` (locally derived,
    commit-by-date approximate) -- the same precedence as `assess`'s env-anchor
    read."""
    for key in ("outcome", "provenance"):
        anchor = _mapping(record, key)
        repo, base_commit = anchor.get("repo"), anchor.get("base_commit")
        if repo and base_commit:
            rig = str(record["rig"])
            return BundleEnv(
                repo=str(repo),
                base_commit=str(base_commit),
                base_image=base_images.get(rig, DEFAULT_BASE_IMAGE),
            )
    return None


def loo_excluded_ids(
    record: Mapping[str, Any], corpus: Iterable[Mapping[str, Any]]
) -> tuple[str, ...]:
    """The bundle-level LOO exclusion set: the record itself + its undirected
    supersedes closure + its convoy/pr/branch siblings over ``corpus``, sorted.
    Exactly the `validity` same-work semantics, materialized as ids so any grid
    run can enforce the boundary without re-deriving it.

    The record's own ref is always part of the adjacency input: its
    ``links.supersedes`` edges must seed the closure even when ``corpus`` omits
    the record itself (the default ``corpus=()``)."""
    query = query_from_record(record)
    refs = [work_ref_from_record(record)] + [work_ref_from_record(r) for r in corpus]
    excluded = {query.work_id}
    excluded |= supersedes_closure(refs, query.work_id)
    excluded |= {ref.work_id for ref in refs if is_sibling(ref, query)}
    return tuple(sorted(excluded))


def assemble_bundle(
    record: Mapping[str, Any],
    replay: ReplayResult,
    *,
    corpus: Iterable[Mapping[str, Any]] = (),
    base_images: Mapping[str, str] = DEFAULT_BASE_IMAGES,
) -> TaskBundle | Rejection:
    """Admit + assemble one record into a `TaskBundle`, or return the typed
    `Rejection`. Raises `OutcomeLeakError` on a planted outcome label in the
    issue text (a validity bug, not an admission outcome) and `ValueError` on a
    record too malformed to evaluate (no LOO boundary, malformed executions)."""
    rejection = admit_record(record)
    if rejection is not None:
        return rejection

    work_id = str(record["work_id"])
    env = _env_from_record(record, base_images)
    if env is None:
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.MISSING_ENV,
            detail="no repo + base_commit anchor on outcome or provenance",
        )
    if not replay.file_diffs:
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.EMPTY_OUTPUT,
            detail="replay produced no gold diff -- nothing to verify against",
        )

    issue_title = _text(record, "title")
    issue_body = _issue_body(record)
    assert_no_outcome_leak(
        {"issue_title": issue_title, "issue_body": issue_body},
        outcome_labels(record),
    )

    return TaskBundle(
        work_id=work_id,
        rig=str(record["rig"]),
        issue_title=issue_title,
        issue_body=issue_body,
        trace_ref=str(_mapping(record, "trace")["jsonl_path"]),
        output=replay,
        env=env,
        loo_excluded_work_ids=loo_excluded_ids(record, corpus),
        verification=BundleVerification(),
    )
