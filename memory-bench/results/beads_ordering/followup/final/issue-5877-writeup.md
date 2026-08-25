# Follow-up evidence for #5877: bounded discovery helps, but no ranking policy earned the default

We extended the existing ranked-pagination experiment without changing
candidate generation. Every arm used the same frozen Memory corpus/query,
current literal matcher, compact projection, page size, model configuration,
and canonical-ID tie break. Only pre-pagination ordering changed.

## Design

- 21 held-out software-engineering tasks across seven independent graph
  families and 50/100/500-Memory corpora.
- Literal match sets of 12, 24, and 96.
- Key, indegree, outdegree, ordinary PageRank, reverse PageRank, and same-set
  BM25F ordering.
- Page sizes 5, 10, 20, and complete/unbounded visibility.
- Search-only and search-plus-reference-navigation modes.
- Frozen primary, acceptable-entry-point, and distractor labels.
- 1,008 factorial cells and 2,400 authenticated observations after
  preregistered disagreement repeats, averaged within cell before inference.

Candidate and compact-projection parity passed for every compared order.
Server-side candidate matching and ordering time were recorded separately from
model-visible bytes/tokens. Continuations fail after state or rank-epoch drift
instead of mixing two orders.

## Results

At page size 5, the deterministic visibility oracle and repeat-balanced
navigation outcomes were:

| order | useful on page 1 | task success (90% family-clustered CI) | compact tokens p50 / p90 | pages requested p50 / p90 |
|---|---:|---:|---:|---:|
| key | 14/21 | 0.540 [0.397, 0.683] | 661 / 2,174 | 2.3 / 3.3 |
| indegree | 10/21 | 0.524 [0.444, 0.603] | 1,151 / 2,833 | 2.0 / 4.7 |
| outdegree | 12/21 | 0.476 [0.365, 0.587] | 679 / 1,780 | 2.0 / 3.0 |
| PageRank | 17/21 | 0.667 [0.556, 0.778] | 611 / 1,153 | 1.0 / 3.0 |
| reverse PageRank | 16/21 | 0.476 [0.397, 0.556] | 665 / 1,791 | 1.0 / 2.7 |
| BM25F | 21/21 | 0.651 [0.587, 0.714] | 582 / 639 | 1.0 / 1.0 |

Reverse PageRank did not replicate as a safe default. It was brittle for newly
unlinked relevant Memories, disconnected components, and link inflation, and
its bounded-navigation task success lagged key order in several families.
Ordinary PageRank was the most robust structural arm, but still missed the
registered default thresholds: versus key at page size 5 it gained 14.3
percentage points of page-one visibility (threshold 15%) and saved a median
8.1% compact tokens (threshold 10%).

BM25F gave perfect page-one visibility in the oracle, but agent-side navigation
made its incremental cost advantage over ordinary PageRank small. At page
sizes 5/10/20, paired median pages saved were 0/0/0 and median compact-token
savings were 4.8%/1.8%/0%. Task-success deltas were not positive. BM25F
therefore did not clear the registered one-page-or-20%-tokens ownership gate.

Pagination harm depended on burial rather than corpus size alone. The clearest
crossover was the 24-match stratum at page size 5: key exposed a useful result
on page 1 for 0/7 tasks, PageRank for 4/7, and BM25F for 7/7. Unbounded key
order raised task success to 0.825, but median compact ingestion rose to 2,926
tokens (p90 11,035). Complete visibility did not erase position effects.

Correct frozen semantic controls and raw-rank controls each put a useful result
on page 1 for 21/21 oracle tasks. This establishes expressive equivalence in
the tested cases, not that users will reliably supply correct interventions.
Prefer semantic pin/boost/demote over mutable raw scores if experimental control
is exposed.

For freshness, lazy exact recomputation before read matched exact-global order
(top-10 overlap 1.0, zero extra pages). Periodic-5 and periodic-20 fell below
the registered 0.9 overlap guardrail. Every post-mutation continuation was
invalidated. Optimized batch rank computation measured p50/p90 80/104 ms at
10,000 Memories, so the experiment found no need for specialized rank plumbing.

## Recommendation for R6

1. Keep key order as the compatibility default and bounded compact discovery
   with deterministic continuation as the core contract.
2. Permit query-independent structural ordering as an experimental derived
   strategy; ordinary PageRank is the best candidate from this sample, not a
   validated universal default.
3. Keep BM25F and other query-specific rerankers consumer-side until they show
   a material navigation-persistent benefit on real workloads.
4. Store rank as a rebuildable cache keyed by graph epoch; never persist it as
   Memory state or create versions for rank changes.
5. Prefer semantic controls over raw numeric rank and avoid specialized
   maintenance/indexing machinery until production telemetry shows a budget
   miss.

These results test retrieval policy, not candidate-generation quality or
production data structures. They do not compare FTS, embeddings, or semantic
candidate expansion.
