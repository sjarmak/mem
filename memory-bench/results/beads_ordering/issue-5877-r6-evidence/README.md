# R6 Memory retrieval evidence for issue #5877

This package contains the privacy-safe outputs from a focused experiment about
ordering before pagination. It asks:

> With one frozen Memory corpus, query, literal candidate set, compact result
> shape, agent protocol, and budget, does ordering change the cost or success of
> reaching a useful Memory?

It does not compare candidate-generation methods, embeddings, or storage-engine
microbenchmarks.

## Executive conclusion

The evidence supports a deliberately small R6 contract:

1. Beads should expose compact, bounded Memory discovery, an unbounded control,
   explicit complete recall, stable positions, and truthful completeness.
2. Continuation must be bound to the query, ordering configuration, page size,
   scorer configuration, and matching candidate snapshot. If matching Memory
   state changes, continuation must fail explicitly instead of mixing pages.
3. A deterministic key/alphabetical order is an adequate mechanical contract
   and fallback. This study does **not** establish a query-independent graph
   prior as the default.
4. Query-specific BM25F reliably improves first-page placement over the tested
   mechanical and structural orders, especially in large controlled match sets.
   Its downstream savings were usually modest and its task-success effects were
   heterogeneous, so it did **not** clear the preregistered threshold for Beads
   to own query-specific ranking.
5. Query-independent graph ordering plus navigation remains a useful optional
   policy to explore, but its benefit did not increase monotonically with link
   richness and did not replicate as a model-invariant task-success advantage.
6. Keep query-specific reranking and graph-traversal policy at the consumer
   boundary for now. Do not infer a need for FTS5, embeddings, persisted scores,
   or a service from this ordering experiment.

## Evidence at a glance

- The original page-size experiment analyzed 768 complete randomized cells and
  317 accepted targeted repeats across page sizes 5, 10, 20, and unbounded.
- The confirmatory density/linkage study analyzed 5,254 usable observations from
  21 base tasks. Its 2,610-cell locked repeat matrix completed with no active
  failures; two earlier infrastructure failures remain visible and excluded.
- All four preregistered confirmatory gates were false: candidate-density
  behavioral materiality, link-dependent structural benefit, support for a
  structural default, and support for Beads-owned query-specific ranking.
- With navigation and five-result pages, BM25F put an acceptable Memory on page
  1 in every 10-, 40-, and 150-candidate cell. Key ordering did so in two thirds
  of tasks. The structural prior ranged from 57.1% to 100%, depending on links
  and candidate count.
- BM25F versus the structural prior saved a median 0 pages and about 8% compact
  tokens at the 150-candidate decision edge. Task-success intervals were wide.
- The 378-cell cross-model replication paired 189 identical cells per model
  with no failures. Compact-token effects were broadly repeatable; task-success
  effects were not. In the native-link condition, the relative advantages of
  structural and BM25F ordering changed materially between models.
- Privacy-safe telemetry scanned 200 real project workspaces, 163 with Memories.
  Native lexical probes had match-count p50 1, p90 6, and max 59. The observed
  tail is large enough for pagination to matter sometimes, but most match sets
  were much smaller than the controlled 40- and 150-candidate stress cells.
  Canonical reference density was unavailable in the observed legacy surface
  and is not treated as zero.

## Interpretation for R6

Pagination is a real exposure constraint: small mechanically ordered pages can
bury useful candidates, and relevance ordering can repair first-page inclusion.
The measured agent cost did not scale one-for-one with burial, however. Agents
often stopped after a useful entry point, followed a reference, or absorbed the
extra page without a reliable task-success change. Conversely, unbounded
visibility removed burial while substantially increasing compact-result input.

The safest architectural reading is therefore to standardize the retrieval
primitives and correctness properties, not a ranking policy. A consumer can
reorder the exact same candidate records with BM25F or a graph prior without
changing Beads' lexical membership semantics.

## Package contents

- `ISSUE_COMMENT.md` — concise issue-ready write-up.
- `DECISION.md` — proposed R6 architectural decision and revisit triggers.
- `REPRODUCE.md` — exact experimental and analysis commands using placeholders.
- `data/` — machine-readable analyses, reports, cell estimates, and sanitized
  per-run metrics. Queries, model answers, failure diagnostics, and agent streams
  are excluded.
- `fixtures/` — frozen design, task/relevance manifest, and preregistration.
- `plots/` — both PNG and SVG renderings.
- `provenance/` — source manifests, selection manifests, continuation evidence,
  exact Git SHAs, binary hashes, and quality-gate results.
- `patches/` — deterministic gzip files containing exact reversible diffs for
  the experimental Beads branch and the `mem` harness.
- `SHA256SUMS` and `PACKAGE-MANIFEST.json` — complete integrity inventory.

## Limits

The controlled corpora are realistic but authored, the confirmatory study has
21 base tasks, and the model replication covers one decision-edge subset. Real
project telemetry measures lexical match-set shape, not task relevance or link
density. Results support a boundary decision, not a claim that one ranker is
universally best. Candidate generation remains out of scope.
