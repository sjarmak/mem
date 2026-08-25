# Executive summary: Memory discovery architecture

## Recommendation

Keep deterministic key order as the compatibility default. Offer structural
ordering only as an experimental, rebuildable strategy; ordinary PageRank is
the strongest candidate from this follow-up, but neither it nor reverse
PageRank cleared the preregistered default gate. Keep query-specific BM25F on
the consumer side for now.

The confirmatory grid is complete: 2,400 authenticated agent observations were
averaged into 1,008 equally weighted task × mode × page-size × policy cells.
The 21 held-out tasks span seven graph families, three corpus sizes, literal
match sets of 12, 24, and 96, six orderings, four page sizes, and search-only
and navigation modes. Selective repeats were chosen by the locked disagreement
rule; repeats do not inflate the independent sample size.

At page size 5 with navigation, task-success estimates were 0.540 for key
order, 0.667 for ordinary PageRank, 0.476 for reverse PageRank, and 0.651 for
BM25F. The corresponding median compact tokens to first useful Memory were
661, 611, 665, and 582. The deterministic oracle put a useful entry point on
page 1 for 14/21 key tasks, 17/21 PageRank tasks, 16/21 reverse-PageRank tasks,
and 21/21 BM25F tasks.

Reverse PageRank fails the default gate: its page-one gain over key was only
9.5 percentage points, its median compact-token saving was zero at page size
5, and it had material family-specific task-success regressions. Ordinary
PageRank was better, but its 14.3-point page-one gain and 8.1% median compact
token saving at page size 5 remained just below the registered 15% and 10%
thresholds, and it was not uniformly safe across families.

BM25F also fails its Beads-ownership gate against the best structural policy.
Versus ordinary PageRank under navigation, BM25F saved a median zero pages and
4.8%, 1.8%, and 0% compact tokens at page sizes 5, 10, and 20. Its paired
task-success deltas were -0.016, -0.079, and -0.079, with intervals crossing
zero. This does not support adding query-specific ranking complexity inside
Beads yet.

## Architectural boundary

- **Beads owns:** literal candidate membership, compact bounded pages,
  deterministic state-bound continuations, references, lifecycle data, and an
  optional derived query-independent ordering hook.
- **Consumer owns:** query-specific reranking until a future workload shows a
  navigation-persistent material gain over the best structural strategy.
- **Rank storage:** derived cache, never Memory state. Rank changes do not
  create Memory versions.
- **Freshness:** invalidate a graph epoch on identity/reference mutation,
  recompute exactly on the next discovery, cache the order, and reject stale
  continuations. Lazy exact refresh matched the exact oracle; periodic-5 and
  periodic-20 missed the 0.9 top-10-overlap guardrail.
- **Controls:** prefer semantic pin/boost/demote and an explicit strategy
  selector for experiments. Do not expose raw numeric rank as Memory state.
- **Plumbing:** do not add a skip list, persistent rank index, daemon, FTS path,
  or specialized dynamic-rank structure. Batch PageRank measured p50/p90
  80/104 ms at 10,000 Memories, so no operational budget miss was shown.

## What pagination changed

Small mechanical pages caused real burial in the 24-match stratum: at page
size 5, key order exposed a useful entry point on page 1 for 0/7 tasks,
ordinary PageRank for 4/7, and BM25F for 7/7. That pattern did not increase
monotonically with raw match count because burial depth, not corpus size alone,
determines the harm. Unbounded visibility sometimes improved mechanical-order
task success, but raised median compact ingestion to 2,926 tokens (p90 11,035)
and did not make ordering irrelevant: position still affected what the agent
selected from the complete list.

The evidence therefore supports bounded discovery with a configurable page
size and explicit continuation. It does not support one universal structural
default or moving BM25F into Beads. The next useful evidence is production
telemetry on real match-set/burial distributions and mutation frequency, not a
larger ranking implementation.
