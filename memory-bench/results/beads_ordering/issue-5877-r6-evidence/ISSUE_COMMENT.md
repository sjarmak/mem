I ran a focused ordering-before-pagination experiment for R6. Candidate
membership was frozen: every arm used the same Memory corpus, query, literal
predicate, compact result projection, page size, agent protocol, and budget.
Only the order before pagination changed.

The evidence now includes the original page-size study, a 5,254-observation
density/linkage study with a completed 2,610-cell repeat matrix, privacy-safe
match-set telemetry from real projects, continuation-mutation conformance, and
a 378-cell paired cross-model replication.

Main findings:

- Pagination can bury useful results. With navigation and page size 5, key order
  put an acceptable Memory on page 1 in about two thirds of controlled tasks.
  BM25F did so in every 10-, 40-, and 150-candidate cell.
- Better first-page placement did not translate into a stable downstream winner.
  At the 150-candidate decision edge, BM25F versus the tested structural prior
  saved a median 0 pages and about 8% compact tokens. Task-success intervals
  were wide, so the preregistered Beads-ownership gate did not clear.
- The structural prior helped some burial cases but did not clear its default
  gate either. Its advantage was not monotonic with link richness.
- Cross-model replication preserved the modest compact-token effects but not a
  consistent task-success ordering. One native-link comparison materially
  reversed, which argues against standardizing a winner from this sample.
- Real-project lexical probes were usually small (match-count p50 1, p90 6),
  with a tail to 59. Pagination matters in the tail, but the largest controlled
  stress cells are not typical of this sample.
- Unbounded visibility removes burial but increases model ingestion. The earlier
  page-size study makes 10 a reasonable starting point, not portable semantics.
- Continuation conformance now proves that unchanged candidate state produces
  complete pages without duplicates/skips, while matching content, lifecycle,
  references, order, additions, or removals fail closed. Nonmatching writes do
  not invalidate the cursor.

My architectural read is conservative:

- R6 should require compact bounded discovery, an explicit unbounded control,
  deterministic documented order, stable positions, truthful completeness,
  state-bound continuation, and explicit recall.
- Key/alphabetical order is an adequate mechanical contract and fallback. The
  data does not justify prescribing the tested graph prior as the default.
- Query-specific BM25F and graph navigation should remain consumer/experimental
  policy for now. A consumer can rerank the same compact candidates without
  changing Beads' lexical membership semantics.
- This experiment does not justify FTS5, embeddings, persistent relevance
  scores, or a service, and it says nothing about alternative candidate
  generation.

The attached package contains sanitized per-run metrics, clustered confidence
intervals and p50/p90 tails, PNG and SVG plots, frozen manifests, exact Git and
binary provenance, reversible patches, checksums, and reproduction commands.
It excludes queries, model answers, agent streams, credentials, failure
diagnostics, local paths, and conversation content.
