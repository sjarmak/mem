# Ranked-pagination experiment update for issue #5877

This experiment isolates one decision: after the existing case-insensitive
literal matcher has produced a fixed Memory candidate set, does ordering that
set before pagination materially change an agent's cost to unblock its task?
It does not compare candidate generators.

The experimental `bd memories` path supports bounded pages, an unbounded
control, state-bound continuation, a fixed compact projection, deterministic
key order, six query-independent structural orders, and BM25F over the same
matched set. Structural positions are materialized from a pinned external
implementation when the corpus is frozen; no ranking implementation is copied
into `mem`. The harness verifies candidate/projection parity before any agent
call and records server compute separately from model-visible bytes/tokens.

The frozen suite has 12 realistic software-engineering tasks over nested
50/100/500-Memory corpora. Literal match sets range from 12 to 220. Under key
order, the first labelled useful Memory is outside page 1 at page size 5 for
7/12 tasks; its worst frozen position is rank 159. Those are deterministic
fixture properties, not agent outcomes. Across the same labels, first-page
visibility at page size 5 ranges from 50% for some structural priors to 100% for
BM25F. This establishes that the suite contains the intended burial and policy
disagreement; it does not establish which policy is best.

The checked-in six-cell protocol pilot uses one 50-Memory task, page size 5,
one model sample, and key/PageRank/BM25F in both search-only and navigation
modes. BM25F exposed an acceptable entry on page 1 (654 estimated compact
tokens); key and PageRank reached a useful result on page 3 (1,481 estimated
compact tokens). All six cells produced the correct task decision. Navigation
added recalls and graph hops in this case but did not change success. With
`n=1` task and one sample, these observations validate instrumentation only and
are not evidence for a Beads ranking decision.

The full pre-registered grid crosses all eight default orders with page sizes
3/5/10/20/50/unbounded, search-only/navigation, all 12 tasks, and a pinned
agent/model. Its p50/p90 distributions, burial strata, bounded-vs-unbounded
deltas, per-page costs, and navigation effects are the evidence needed before
claiming that query-specific lexical ranking earns its complexity. Until that
grid is run, the conservative implication for R6 is: bounded pagination can
create substantial deterministic burial in realistic match sets, but neither
the structural-policy nor BM25F ownership boundary is decided by the pilot.
