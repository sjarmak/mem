"""Bundle assembler + admission filter (mem-75t.7.2, plan §4 P1 + §9.3).

Assembles a `TaskBundle` from a Mapping-shaped WorkRecord (the same JSON shape
`validity.query_from_record` and `assess` read) plus its P0 `ReplayResult`. Three
invariants are enforced here, mechanically:

1. **Admission (plan §9.3 + the mem-75t.7.1 validation-derived gates,
   docs/mem-75t.7.1-replay-validation.md).** The trace-derived gold diff is "what
   the agent did", not "the correct fix" -- this corpus has no merged-PR/CI outcome
   linkage to vouch for it. The only structural evidence the work ended well is the
   record's own lifecycle + trace: admit ONLY a bead that is closed (status AND
   timestamp agree) AND whose trace tail is clean -- no unresolved trace_errors in
   the final segment, where "final segment" is each runner's LAST execution in
   transcript order (an early failure fixed by a later pass of the same runner is
   resolved; a failure nothing ran after is not). Further structural gates make the
   bundle a real, trustworthy eval object:

   - **not a shared trace** -- a transcript the corpus maps to OTHER work_ids is a
     multi-bead mega-session (the validation found one spanning 9 work_records);
     replaying the full stream against one bead's base mixes nine beads' edit
     streams. Per-bead segmentation is explicitly deferred -- rejection only.
   - **an env anchor** (repo + base_commit -- without it the bundle is not
     runnable);
   - **base does not predate the tree** -- a first-touch FILE_ABSENT Edit
     (`ReplayResult.base_predates_tree`) means the timestamp-approximate
     base_commit resolves to a tree OLDER than the session's;
   - **a non-empty gold diff** (without it there is no output leg; `ReplayResult`
     itself pins the empty-replay rate to 0.0 as "non-admittable");
   - **replay fidelity at oracle grade** -- ``adjusted_replay_success_rate`` (the
     out-of-repo auto-memory writes excluded from the denominator) at least
     `MIN_ADJUSTED_REPLAY_SUCCESS_RATE`: a partial replay is a gold diff with
     MISSING hunks, a corrupted oracle that is worse than no bundle.

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

from collections.abc import Mapping, Sequence
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

# Validation-derived admission threshold (docs/mem-75t.7.1-replay-validation.md):
# a partial replay means a gold diff with MISSING hunks -- a corrupted oracle, worse
# than no bundle. On the validation sample, >= 0.9 ADJUSTED admitted 3/8 beads at
# oracle grade; the 0.6-0.9 band stays context-only (trace-as-context rungs don't
# need an exact diff). Thresholded on `adjusted_replay_success_rate`, never the raw
# rate: OUTSIDE_WORK_DIR calls are out-of-repo by construction (see ReplayResult).
MIN_ADJUSTED_REPLAY_SUCCESS_RATE: float = 0.9


class RejectionReason(StrEnum):
    """Why a record was not admitted. Reasons are checked in this order; the first
    failure wins, so a rejection names the gate that actually fired. The replay-side
    gates are ordered most- to least-diagnostic: BASE_PREDATES_TREE explains WHY a
    diff came out empty (the zg4da/041jz shape), so it outranks EMPTY_OUTPUT, which
    outranks the generic LOW_REPLAY_FIDELITY."""

    NOT_CLOSED = "not_closed"
    NO_TRACE = "no_trace"
    DIRTY_TRACE_TAIL = "dirty_trace_tail"
    SHARED_TRACE = "shared_trace"
    MISSING_ENV = "missing_env"
    BASE_PREDATES_TREE = "base_predates_tree"
    EMPTY_OUTPUT = "empty_output"
    LOW_REPLAY_FIDELITY = "low_replay_fidelity"


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
        final_by_runner[str(execution.get("runner") or "<unknown>")] = execution
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


def _shared_trace_work_ids(
    record: Mapping[str, Any], corpus: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    """The OTHER work_ids in ``corpus`` whose record points at this record's
    transcript -- non-empty means a multi-bead mega-session (mem-75t.7.1 found one
    transcript the store maps to 9 work_records). The record itself (same work_id)
    never counts as a sharer."""
    own_id = str(record.get("work_id"))
    trace_ref = _text(_mapping(record, "trace"), "jsonl_path")
    return tuple(
        sorted(
            {
                str(other.get("work_id"))
                for other in corpus
                if str(other.get("work_id")) != own_id
                and _text(_mapping(other, "trace"), "jsonl_path") == trace_ref
            }
        )
    )


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
    record: Mapping[str, Any], corpus: Sequence[Mapping[str, Any]]
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
    corpus: Sequence[Mapping[str, Any]] = (),
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
    sharers = _shared_trace_work_ids(record, corpus)
    if sharers:
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.SHARED_TRACE,
            detail=(
                "transcript also maps to work_ids: "
                f"{', '.join(sharers)} -- replaying the full stream against one bead's "
                "base mixes edit streams; per-bead segmentation deferred"
            ),
        )
    env = _env_from_record(record, base_images)
    if env is None:
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.MISSING_ENV,
            detail="no repo + base_commit anchor on outcome or provenance",
        )
    if replay.base_predates_tree:
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.BASE_PREDATES_TREE,
            detail=(
                "first mutation is an Edit of file(s) absent at base: "
                f"{', '.join(replay.first_edit_absent_paths())} -- the "
                "timestamp-approximate base_commit predates the session tree"
            ),
        )
    if not replay.file_diffs:
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.EMPTY_OUTPUT,
            detail="replay produced no gold diff -- nothing to verify against",
        )
    if replay.adjusted_replay_success_rate < MIN_ADJUSTED_REPLAY_SUCCESS_RATE:
        return Rejection(
            work_id=work_id,
            reason=RejectionReason.LOW_REPLAY_FIDELITY,
            detail=(
                f"adjusted replay success rate {replay.adjusted_replay_success_rate:.2f} "
                f"< {MIN_ADJUSTED_REPLAY_SUCCESS_RATE} -- a partial replay is a gold diff "
                "with missing hunks (corrupted oracle)"
            ),
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
        trace_ref=_text(_mapping(record, "trace"), "jsonl_path"),
        output=replay,
        env=env,
        loo_excluded_work_ids=loo_excluded_ids(record, corpus),
        verification=BundleVerification(),
    )
