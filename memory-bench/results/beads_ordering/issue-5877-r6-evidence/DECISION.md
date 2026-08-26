# Proposed R6 retrieval-boundary decision

## Decision

Keep the portable Memory retrieval contract policy-light:

```text
active Memory state
  -> frozen lexical candidate predicate
  -> documented deterministic order
  -> compact bounded page or explicit unbounded control
  -> state-bound continuation
  -> explicit complete recall
  -> consumer-selected reference navigation and optional reranking
```

For an initial implementation, key/alphabetical order is the mechanical
baseline. Query-independent graph priors and query-specific BM25F should remain
selectable experiments or consumer transformations until stronger evidence
shows a stable downstream advantage.

## Required behavior

- Keep candidate membership identical when comparing order policies.
- Return the same compact fields and excerpt bound for every policy.
- Report canonical ID, key when present, title, lifecycle, match provenance,
  rank/position, total matches, completeness, and continuation.
- Bind continuation to query, order, page size, scorer parameters, and the
  complete matching candidate snapshot.
- Continue without duplicates or skips when that snapshot is unchanged.
- Fail closed when matching content, lifecycle, references, materialized order,
  additions, or removals change the snapshot. A nonmatching write need not
  invalidate it.
- Keep ranking state ephemeral. Retrieval policy changes must not create Memory
  versions or mutate durable Memory content.
- Measure server computation separately from records/tokens shown to the model.

## Why this boundary

BM25F decisively improved page-one visibility in the controlled stress cells,
but typically saved a median of zero pages once navigation was available. Its
compact-token saving at the 150-candidate decision edge was about 8%, below the
locked 20% threshold, and success nonregression did not hold consistently.

The tested structural prior also improved some burial cases, but its benefit was
not monotonic with link richness and its task-success effects changed under the
secondary model. That is evidence that graph ordering is policy, not yet a
portable R6 semantic.

Real-project probes were usually small (p50 1, p90 6), with a meaningful tail
to 59. This favors bounded retrieval with a configurable page size, but weakens
the case for putting a more complex ranker in the core before observing broader
production task evidence.

## Page-size guidance

The earlier page-size study found that five-result mechanical pages frequently
buried useful candidates, while unbounded visibility added substantial compact
input. Page size 10 was a reasonable experimental starting point and often
matched unbounded task success in that sample. It is implementation guidance,
not a normative portable default; consumers should be able to choose bounded or
unbounded discovery deliberately.

## Revisit triggers

Reconsider Beads-owned query-specific or structural ranking when a broader,
preregistered replication demonstrates all of the following against the simple
mechanical baseline and the strongest policy-light alternative:

1. at least one median page saved or at least 20% median compact-token savings;
2. no material task-success regression;
3. the benefit remains with reference navigation enabled;
4. the effect holds across models/configurations and realistic match-set tails;
5. operational update cost and invalidation behavior are measured separately
   from model ingestion cost.

Candidate-generation comparisons—literal matching versus indexed BM25,
semantic, or hybrid retrieval—must remain a separate experiment.
