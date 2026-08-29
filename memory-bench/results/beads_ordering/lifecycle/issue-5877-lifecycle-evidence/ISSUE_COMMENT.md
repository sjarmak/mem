I completed the preregistered lifecycle/control addendum for R6 without changing candidate generation.

The control matrix is decision-ready: 1,424 observations, 672 equally weighted policy cells, 21 held-out tasks across seven graph families, and all 94 frozen disagreement groups completed with no infrastructure failures. Semantic controls repaired page-one placement in bounded affected cases, but the preregistered neutral-task check cleared only 2 of 8 mode/page cells and failed every navigation cell. Semantic controls were also not equivalent to raw rank within the registered ±5-point task-success margin in any of 8 cells. The registered all-cell control gate therefore fails.

The 4,200-snapshot mutation replay reaches the same conservative architecture result. Exact and exact-on-read rank preserve top-10 order/useful-page behavior and fail changed continuations closed. Periodic-5 and periodic-20 reduce amortized compute to about 20.9 ms and 5.2 ms per mutation, but both violate the 0.9 top-10 freshness guardrail (mean/min overlap 0.844/0.0 and 0.718/0.0). Exact refresh costs p50/p90 10.8/324.1 ms across 50/100/500-Memory families. A separate workload-rate model shows when lazy read-time coalescing could save compute, but it is explicitly illustrative because no production read/write rate was measured.

The optimized compute-only curve reaches p50/p90 131.5/159.6 ms at 10,000 Memories, but it does not preserve the pinned reference top 10 (mean overlap 0.819, minimum 0.0 through size 500). It cannot support an efficiency claim before behavior parity, and there is no measured operational budget miss.

My R6 conclusion:

- do not standardize persistent structural-rank maintenance, periodic refresh, a rank service, raw rank, strategy controls, or semantic pin/boost/demote controls;
- if a consumer continues experimenting, use exact recomputation at the read boundary with candidate/order/rank-epoch-bound continuations that fail closed;
- keep that as consumer policy, not a Beads discovery/storage contract;
- retain the earlier portable R6 surface: compact bounded discovery, deterministic documented order, stable positions, truthful completeness, explicit unbounded control, state-bound continuation, and explicit recall.

The addendum includes privacy-safe per-run metrics, repeat-balanced cells, clustered intervals, p50/p90 strata, frozen provenance, an illustrative workload model, checksums, and reproduction commands. It excludes raw model text, queries, agent streams, credentials, local paths, identities, and private infrastructure.
