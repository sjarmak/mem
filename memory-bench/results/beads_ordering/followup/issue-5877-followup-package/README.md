# Public follow-up evidence for Beads issue #5877

This package extends the original ranked-pagination evidence with an independent
graph-robustness follow-up, a complete agent grid, rank-freshness replay,
operator-control oracles, and compute scaling. The experiment compares
retrieval policies, not candidate-generation engines or data-structure
microbenchmarks.

## Bottom line

No tested policy earned the Beads default under the locked thresholds.
Ordinary PageRank was the strongest structural strategy on small bounded pages,
but remained heterogeneous across graph families. Reverse PageRank did not
replicate as a safe default. BM25F produced the best deterministic page-one
visibility, but versus ordinary PageRank under agent navigation it saved a
median zero pages and only 4.8%/1.8%/0% compact tokens at page sizes 5/10/20,
below the registered one-page-or-20% ownership gate.

Recommended boundary:

1. Keep literal candidate generation, bounded compact discovery, deterministic
   state-bound continuation, references, and lifecycle data in Beads.
2. Keep key order as the compatibility default. Treat query-independent
   structural orders as optional, rebuildable experiments.
3. Keep query-specific reranking consumer-side until real workloads show a
   material navigation-persistent benefit.
4. Cache derived structural rank by graph epoch and recompute exactly on first
   read after mutation; reject stale continuations.
5. Prefer semantic pin/boost/demote over raw numeric Memory rank, and do not add
   specialized maintenance plumbing until a measured workload misses its
   budget.

## Contents

- `ISSUE_COMMENT.md` — concise issue-ready design, results, limits, and recommendation.
- `DECISION.md` — executive architectural recommendation.
- `REPRODUCE.md` — portable commands using explicit path variables.
- `data/followup-agent-*` — sanitized per-run metrics, equally weighted cell
  estimates, full aggregate analysis, and provenance manifest for 2,400
  observations / 1,008 cells.
- `data/targeted-repeat-selection.json` — locked rule and selected repeat groups.
- `data/supplemental-scope-density.json` — post-hoc scope audit and independently
  reproduced unbounded-cell reasoning-load signal.
- `data/oracle-*` — 840 deterministic ordering/page-size/control cells.
- `data/mutation-*` — 4,200 chronological mutation/rank-refresh snapshots.
- `data/rank-scaling-*` — 105 rank-compute measurements through 10,000 Memories.
- `data/prior-agent-analysis.json` — aggregate analysis from the original
  1,085-observation experiment; no transcripts.
- `fixtures/` — seven frozen generated graph-family corpora and checksum manifest.
- `plots/` — SVG and PNG deterministic and repeat-balanced agent curves.
- `patches/` — three reversible author-free Beads diffs.
- `provenance/` — preregistration and a sanitized provenance summary.

## Privacy and scope

This is a public-sanitized package. It contains no conversation messages,
prompts, model responses, agent streams, credentials, authentication
diagnostics, personal filesystem paths, or author identity headers. Generated
task instructions remain inside synthetic fixtures because they are benchmark
data, not user messages.

The package tests ordering after fixed literal candidate generation. It does
not compare FTS, embeddings, semantic expansion, persistent search indexes, or
production ranking data structures. The primary Memory is a literal candidate
by construction; candidate-generation recall needs a separate lexical-miss
experiment. The supplemental density observation is explicitly exploratory
because corpus size, match-set size, and task identity co-varied.
