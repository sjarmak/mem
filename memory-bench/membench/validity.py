"""V1 leave-one-out (LOO) leakage guard — the harness-owned validity invariant.

This is the contract D6 / D11 boundary, owned by the harness rather than by any
single arm. When a query work `B` is evaluated, the *only* records an arm may
ingest are WorkRecords closed strictly before `B.started`, minus the records that
are "the same work dodging the timestamp filter": `B` itself, its convoy
siblings, its supersedes-chain, and anything sharing `B`'s PR or branch
(`external_ref`). No arm — `oracle`, `ours`, `builtin`, or a later competitive
arm — may touch the raw store directly: every arm consumes the set this module
produces, and every arm's output is re-checked against it by `assert_no_leak`.

The semantics mirror the retrieval-v1 surface exactly so the harness and the
substrate agree:

- `closedBefore` is strict and null-safe: a record with no `closed` timestamp is
  never eligible (`store/reader.ts` `queryRecords`: `closed_at IS NOT NULL AND
  closed_at < ?`).
- the supersedes closure is **undirected** and transitive — ancestors *and*
  descendants are "the same work" for the LOO exclusion (`store/reader.ts`
  `supersedesClosure`).
- the sibling test is null-safe: a comparison only fires when the *query* side
  names a value, so absence never matches absence (`retrieve/exclusions.ts`
  `isSibling`).

Pure mechanism (ZFC): deterministic set arithmetic with explicit ordering, no
semantic judgment. No outcome label can enter an arm's input through this path.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkRef:
    """The LOO-relevant projection of a WorkRecord (the rest is irrelevant to the
    boundary). Sourced from the TS work-audit graph export (plan §A, DIV-8)."""

    work_id: str
    rig: str
    # lifecycle.closed — None when the work is still open (never eligible).
    closed: str | None = None
    convoy_id: str | None = None
    pr: str | None = None
    external_ref: str | None = None
    # links.supersedes — the work_ids this record supersedes (edges are treated
    # as undirected for the closure).
    supersedes: tuple[str, ...] = ()
    # Provenance marker: "real" for a city-ingested WorkRecord, "synthetic" for a
    # generator-materialized one (D-J SHARE — one schema, distinguished only here).
    # Defaults "real" so every existing record and inline WorkRef keeps its meaning.
    origin: str = "real"


@dataclass(frozen=True)
class QueryWork:
    """The held-out query work `B`. `started` is the D6 boundary and is required:
    the caller must state when "memory as it existed" is measured."""

    work_id: str
    rig: str
    started: str
    convoy_id: str | None = None
    pr: str | None = None
    external_ref: str | None = None


class LeakageError(AssertionError):
    """Raised when an ingest / retrieval set violates the LOO invariant — a
    validity bug that must fail the run, never be silently filtered away."""

    def __init__(self, offenders: list[str], reason: str) -> None:
        self.offenders = offenders
        super().__init__(f"LOO leakage ({reason}): {', '.join(offenders)}")


def supersedes_closure(corpus: Iterable[WorkRef], work_id: str) -> set[str]:
    """Undirected, transitive supersedes closure of `work_id` over the corpus's
    edges, excluding `work_id` itself (self-exclusion is the caller's own rule).
    Mirrors `store/reader.ts` `supersedesClosure`."""
    # Adjacency from the undirected supersedes edges (a.supersedes ∋ b ⇒ a ~ b).
    adjacency: dict[str, set[str]] = {}
    for ref in corpus:
        for target in ref.supersedes:
            adjacency.setdefault(ref.work_id, set()).add(target)
            adjacency.setdefault(target, set()).add(ref.work_id)

    seen: set[str] = set()
    frontier = [work_id]
    while frontier:
        current = frontier.pop()
        for neighbor in adjacency.get(current, ()):
            if neighbor not in seen:
                seen.add(neighbor)
                frontier.append(neighbor)
    seen.discard(work_id)
    return seen


def is_sibling(ref: WorkRef, query: QueryWork) -> bool:
    """Null-safe same-work test: a record is `query`'s sibling when it shares the
    query's convoy, PR, or branch (`external_ref`). Each comparison only fires
    when the query side names a value. Mirrors `retrieve/exclusions.ts`."""
    return (
        (query.convoy_id is not None and ref.convoy_id == query.convoy_id)
        or (query.pr is not None and ref.pr == query.pr)
        or (query.external_ref is not None and ref.external_ref == query.external_ref)
    )


def _is_eligible(ref: WorkRef, query: QueryWork, chain: set[str]) -> bool:
    return (
        ref.closed is not None  # null closed → never eligible (strict, null-safe)
        and ref.closed < query.started  # D6 strict temporal cut
        and ref.work_id != query.work_id  # self-exclusion
        and ref.work_id not in chain  # supersedes-chain exclusion
        and not is_sibling(ref, query)  # convoy / pr / branch exclusion
    )


def loo_bounded(corpus: Iterable[WorkRef], query: QueryWork) -> list[WorkRef]:
    """The LOO-bounded ingest set for `query` — every record an arm is allowed to
    see, deterministically ordered by `work_id`. This is the only door to the
    corpus for an arm."""
    refs = list(corpus)
    chain = supersedes_closure(refs, query.work_id)
    eligible = [ref for ref in refs if _is_eligible(ref, query, chain)]
    return sorted(eligible, key=lambda r: r.work_id)


def assert_no_leak(
    retrieved_ids: Iterable[str], corpus: Iterable[WorkRef], query: QueryWork
) -> None:
    """Re-check that every `retrieved_ids` work_id is inside the LOO-bounded set —
    the harness's independent audit that an arm (or the substrate it delegates to)
    honored the boundary. Raises `LeakageError` on any record that leaked.

    Unknown ids (not in the corpus at all) are also leaks: an arm must not return
    work the harness cannot account for against the boundary."""
    eligible_ids = {ref.work_id for ref in loo_bounded(corpus, query)}
    offenders = sorted({wid for wid in retrieved_ids if wid not in eligible_ids})
    if offenders:
        raise LeakageError(offenders, f"not in LOO set for {query.work_id}")


def work_ref_from_record(record: Mapping[str, Any]) -> WorkRef:
    """Project a WorkRecord (the TS export's JSON shape) onto a `WorkRef`."""
    lifecycle = record.get("lifecycle") or {}
    links = record.get("links") or {}
    outcome = record.get("outcome") or {}
    return WorkRef(
        work_id=record["work_id"],
        rig=record["rig"],
        closed=lifecycle.get("closed"),
        convoy_id=links.get("convoy_id"),
        pr=outcome.get("pr"),
        external_ref=record.get("external_ref"),
        supersedes=tuple(links.get("supersedes", ())),
        # Carried through the SAME reader so synthetic and real work share one corpus;
        # a record with no marker is real by construction.
        origin=str(record.get("origin") or "real"),
    )


def query_from_record(record: Mapping[str, Any]) -> QueryWork:
    """Build the query context from a closed WorkRecord — replay mode (Decision 5).
    The boundary is the record's `started`, falling back to `created` (earlier, so
    strictly leak-safe) when the work never recorded a start. Mirrors
    `retrieve/retrieval.ts` `queryFromRecord`."""
    lifecycle = record.get("lifecycle") or {}
    started = lifecycle.get("started") or lifecycle.get("created")
    if started is None:
        raise ValueError(
            f"WorkRecord {record.get('work_id')!r} has neither started nor created "
            "— cannot establish a leak-safe LOO boundary."
        )
    links = record.get("links") or {}
    outcome = record.get("outcome") or {}
    return QueryWork(
        work_id=record["work_id"],
        rig=record["rig"],
        started=started,
        convoy_id=links.get("convoy_id"),
        pr=outcome.get("pr"),
        external_ref=record.get("external_ref"),
    )


# Re-exported for callers that build corpora inline rather than from records.
__all__ = [
    "LeakageError",
    "QueryWork",
    "WorkRef",
    "assert_no_leak",
    "is_sibling",
    "loo_bounded",
    "query_from_record",
    "supersedes_closure",
    "work_ref_from_record",
]
