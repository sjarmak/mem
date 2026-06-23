"""The `ours` vs semantic-arm retrieval-quality bridge (mem-compare).

Why a bridge is needed: `ours` (`OursMemory`, replay-only) consumes
``RetrievalRequest.query_work`` and retrieves over the work-audit graph; the
semantic arms (`AbstractSemanticArm`: mem0 / a-mem / …) consume
``RetrievalRequest.query_text`` and retrieve over whatever was *written* into their
per-trial scope. Each raises if handed the other family's request. To compare them
head-to-head we put both on ONE task surface:

- the harness LOO-bounded set (`validity.loo_bounded`) is the corpus for both arms;
- `ours` runs through the existing `replay.replay_arm` (no reimplementation);
- the semantic arm is **seeded** with the same LOO set, then queried with a
  `query_text` derived from `B`; both arms' work_ids are scored against one
  authored relevant set and re-checked with `assert_no_leak`.

The id-translation wrinkle: a semantic backend mints its OWN id per write
(`semantic_base.AbstractSemanticArm.write` records ``written_ids``) and keys its
hits off it (`retrieve` payloads), NOT the work_id. So seeding captures a
``backend_id -> work_id`` map and retrieval is translated back through it before any
scoring or leak-check — otherwise every hit would read as an "unknown id" leak.

ZFC: pure mechanism. The relevant set is EXPLICIT INPUT (authored ground truth),
never derived here from either arm's retrieval mechanism — deriving it from one
arm's signature/embedding would silently bias the comparison toward that arm.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from membench.grading.retrieval_leg import RetrievalTarget, score_retrieval_leg
from membench.memory_systems.base import MemorySystem, RetrievalRequest
from membench.replay import replay_arm
from membench.runtime import IdClock, StepContext
from membench.validity import QueryWork, WorkRef, assert_no_leak, loo_bounded

# `ours` is dual-track (Decision 7); the headline comparison fixes one track so the
# two arms line up 1:1. cross_rig is the strict/headline track (retrieve.ts SCOPES).
DEFAULT_OURS_SCOPE = "cross_rig"


class ArmComparison(BaseModel):
    """One arm's retrieval-quality readout for one query work. Metric fields are
    ``None`` when the relevant set is empty (not measured) — never a fabricated
    ``0.0`` (mirrors `grading.retrieval_leg.RetrievalLeg`)."""

    model_config = ConfigDict(frozen=True)

    arm: str
    scope: str | None
    # The arm's retrieved set, already translated to work_ids (semantic backend ids
    # mapped back) so both arms are reported in the same id space.
    retrieved_ids: list[str]
    precision: float | None
    recall: float | None
    mrr: float | None
    ndcg: float | None
    # Decision-10 precision guard: outcome lift is gameable by over-injection, so
    # the injected-context volume is reported alongside, never hidden.
    injected_context_chars: int
    latency_ms: float
    # True once the arm's output passed the harness LOO re-check (`assert_no_leak`).
    leak_checked: bool


class ComparisonResult(BaseModel):
    """Both arms on one query work, plus the shared denominators that make the
    per-arm metrics interpretable and the pinned model identity (V2 confound)."""

    model_config = ConfigDict(frozen=True)

    work_id: str
    retrieval_target: RetrievalTarget
    # Size of the scope-filtered candidate pool both arms drew from (LOO-eligible ∩
    # the track's rig predicate) — distinguishes an empty result from an empty pool.
    eligible_count: int
    # Size of the authored relevant set after intersecting with the LOO set.
    relevant_count: int
    arms: list[ArmComparison]
    # `LocalModelStack.telemetry_dict()` — which models produced the semantic side.
    stack_telemetry: dict[str, str]


def _scope_eligible(eligible: Sequence[WorkRef], query: QueryWork, scope: str) -> list[WorkRef]:
    """The candidate pool BOTH arms draw from under one Decision-7 track: the
    LOO-eligible records filtered by the track's rig predicate, matching what `ours`
    applies internally (retrieve.ts SCOPES). ``cross_rig`` = other rigs only (the
    strict generalization track); ``same_rig_temporal`` = the query's rig only.

    Seeding the semantic arm from THIS pool — not the full LOO set — is what keeps the
    comparison fair: a same-rig record `ours` could never surface on ``cross_rig`` must
    not be reachable by the semantic arm either, or the two arms would be answering
    over different candidate sets and the retrieval-mechanism question is confounded
    with a candidate-pool difference."""
    if scope == "cross_rig":
        return [ref for ref in eligible if ref.rig != query.rig]
    if scope == "same_rig_temporal":
        return [ref for ref in eligible if ref.rig == query.rig]
    raise ValueError(f"unknown scope {scope!r}; expected cross_rig or same_rig_temporal")


def _relevant_within_pool(relevant_ids: Iterable[str], pool: Sequence[WorkRef]) -> tuple[str, ...]:
    """Intersect the authored relevant set with the candidate pool. A relevant id
    outside the pool — LOO-withheld, or the wrong rig for this track — is unreachable
    by construction, so counting it in the recall denominator would understate every
    arm equally and falsely (mirrors `grading.retrieval_leg.gold_relevant_ids`
    subtracting the excluded set)."""
    pool_ids = {ref.work_id for ref in pool}
    return tuple(sorted(set(relevant_ids) & pool_ids))


def seed_semantic_arm(
    arm: MemorySystem,
    eligible: Sequence[WorkRef],
    corpus_text: Mapping[str, str],
    ctx: StepContext,
) -> dict[str, str]:
    """Write each LOO-eligible record's text into ``arm`` under ``ctx.trial_id`` and
    return the ``backend_id -> work_id`` map captured from the write events.

    A missing text entry RAISES: a silently-skipped seed would hand the semantic arm
    a smaller corpus than `ours` sees and quietly bias the comparison. The map is the
    only way to translate the arm's backend-keyed hits back to work_ids."""
    backend_to_work: dict[str, str] = {}
    for ref in eligible:
        text = corpus_text.get(ref.work_id)
        if text is None:
            raise ValueError(
                f"no corpus_text for eligible work_id {ref.work_id!r}; cannot seed the "
                "semantic arm without it (a skipped seed silently biases the comparison)."
            )
        event = arm.write(ref.work_id, text, ctx)
        if not event.written_ids:
            raise ValueError(
                f"semantic arm {arm.name!r} returned no written_ids for {ref.work_id!r}; "
                "cannot map its backend id back to the work_id."
            )
        backend_to_work[event.written_ids[0]] = ref.work_id
    return backend_to_work


def semantic_replay(
    arm: MemorySystem,
    query: QueryWork,
    query_text: str,
    corpus: Sequence[WorkRef],
    corpus_text: Mapping[str, str],
    *,
    relevant_ids: Sequence[str],
    scope: str = DEFAULT_OURS_SCOPE,
    target: RetrievalTarget = "canonical",
    clock: IdClock | None = None,
) -> ArmComparison:
    """Seed ``arm`` with the scope-filtered candidate pool (the same pool `ours` draws
    from under ``scope``), query it with ``query_text``, translate its backend-keyed
    hits to work_ids, re-check the boundary, and score against ``relevant_ids``.
    ``relevant_ids`` is taken as already pool-intersected by the caller
    (`compare_arms`)."""
    pool = _scope_eligible(loo_bounded(corpus, query), query, scope)
    trial_id = f"{query.work_id}-{arm.name}-{scope}"
    arm.reset(trial_id)

    # Separate clock for seeding so the seed writes never perturb the retrieve event's
    # ids/latency (the conditions.py seeding convention).
    seed_ctx = StepContext(
        trial_id=trial_id, session_id=query.work_id, step_id="seed", clock=IdClock()
    )
    backend_to_work = seed_semantic_arm(arm, pool, corpus_text, seed_ctx)

    ctx = StepContext(
        trial_id=trial_id,
        session_id=query.work_id,
        step_id="replay",
        clock=clock or IdClock(),
    )
    result = arm.retrieve(RetrievalRequest(query_text=query_text), ctx)

    # Translate backend ids -> work_ids. An unmapped id stays as-is so it surfaces as
    # an "unknown id" leak in assert_no_leak rather than being silently dropped.
    retrieved_work_ids = [backend_to_work.get(bid, bid) for bid in result.payloads]
    assert_no_leak(retrieved_work_ids, corpus, query)

    leg = score_retrieval_leg(retrieved_work_ids, relevant_ids, target=target)
    return ArmComparison(
        arm=arm.name,
        scope=scope,
        retrieved_ids=retrieved_work_ids,
        precision=leg.precision,
        recall=leg.recall,
        mrr=leg.mrr,
        ndcg=leg.ndcg,
        injected_context_chars=sum(len(v) for v in result.payloads.values()),
        latency_ms=result.event.latency_ms,
        leak_checked=True,
    )


def ours_replay(
    arm: MemorySystem,
    query: QueryWork,
    corpus: list[WorkRef],
    *,
    relevant_ids: Sequence[str],
    scope: str = DEFAULT_OURS_SCOPE,
    target: RetrievalTarget = "canonical",
) -> ArmComparison:
    """Run `ours` through the existing `replay.replay_arm` (which already re-checks
    the boundary), then score its work_ids against ``relevant_ids``."""
    r = replay_arm(arm, query, corpus, scope=scope)
    leg = score_retrieval_leg(r.retrieved_ids, relevant_ids, target=target)
    return ArmComparison(
        arm=r.arm,
        scope=r.scope,
        retrieved_ids=r.retrieved_ids,
        precision=leg.precision,
        recall=leg.recall,
        mrr=leg.mrr,
        ndcg=leg.ndcg,
        injected_context_chars=r.injected_context_chars,
        latency_ms=r.latency_ms,
        leak_checked=True,
    )


def compare_arms(
    query: QueryWork,
    query_text: str,
    corpus: list[WorkRef],
    corpus_text: Mapping[str, str],
    *,
    ours: MemorySystem,
    semantic: MemorySystem,
    relevant_ids: Sequence[str],
    scope: str = DEFAULT_OURS_SCOPE,
    target: RetrievalTarget = "canonical",
    stack_telemetry: Mapping[str, str] | None = None,
) -> ComparisonResult:
    """Compare `ours` against one semantic arm on a single query work, scoring both
    against the same relevant set, drawn from the same scope-filtered candidate pool,
    under the same LOO boundary."""
    pool = _scope_eligible(loo_bounded(corpus, query), query, scope)
    relevant = _relevant_within_pool(relevant_ids, pool)
    arms = [
        ours_replay(ours, query, corpus, relevant_ids=relevant, scope=scope, target=target),
        semantic_replay(
            semantic,
            query,
            query_text,
            corpus,
            corpus_text,
            relevant_ids=relevant,
            scope=scope,
            target=target,
        ),
    ]
    return ComparisonResult(
        work_id=query.work_id,
        retrieval_target=target,
        eligible_count=len(pool),
        relevant_count=len(relevant),
        arms=arms,
        stack_telemetry=dict(stack_telemetry or {}),
    )
