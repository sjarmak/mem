# Memory candidate-density and linkage extension

This study follows the completed ranked-pagination experiment without changing
its result. The locked protocol is
[`density-linkage-preregistration.json`](../memory-bench/fixtures/beads_ordering/density-linkage-preregistration.json).

## Why this extension exists

The prior ordering experiment intentionally guaranteed that the useful Memory
was in the literal candidate set. A post-hoc audit then found failures even when
the complete candidate set was visible and the useful Memory was recalled
first. That is a useful warning, but not a causal density result: task identity,
corpus size, match-set size, ordering, and within-response position co-varied.

The earlier graph families likewise covered sparse hubs, disconnected
components, cycles, high-outdegree distractors, and inflated links. They show
structural failure modes, but they do not isolate link richness. Each topology
used different tasks and relevance placement.

This extension crosses two controlled factors over the same 21 frozen base
tasks:

- matched-candidate count: 10, 40, or 150;
- reference graph: sparse, native, or enriched.

Every variant contains 500 Memories. Within a variant, key order, PageRank, and
BM25F receive the exact same literal candidates and compact projection.

## Minimal change plan

1. Reuse the existing `beads_ordering` fixture models, Beads client, workspace
   seeding, agent runner, scorer, and raw-result schema.
2. Add one pure transformation module that materializes task-scoped variants
   from the frozen seven-family fixtures. Store a small manifest of recipes,
   candidate IDs, graph metrics, and corpus hashes rather than duplicating full
   500-Memory corpora in Git.
3. Build nested candidate sets deterministically. The primary and acceptable
   entry points never change; added candidates receive one bounded historical
   distractor surface, while nonselected Memories have the exact query phrase
   removed.
4. Build nested reference graphs. Sparse retains frozen useful navigation paths
   and a deterministic quarter of other native edges; native is unchanged;
   enriched adds reciprocal backlinks. No condition invents an answer-specific
   link for an originally unlinked primary.
5. Recompute query-independent ranks as derived fixture data using the existing
   locally verified scorer with aggregated dangling mass (damping 0.85, 100
   iterations). Do not change the experimental Beads implementation.
6. Add task-scoped primary-first ranks only as an unbounded position control.
   This distinguishes candidate-ingestion load from burial; it is not a proposed
   retrieval policy.
7. Validate all invariants and run the deterministic oracle before agent calls.
   Run agent cells with explicit `BEADS_BIN`, model, credential profile, and Git
   provenance exactly as in the existing harness.
8. Analyze density effects and ordering-by-linkage interactions at the base-task
   level. Repeats estimate model variance and do not increase the independent
   sample size.

No new production schema, FTS index, embedding system, ranking service, or
specialized graph data structure is part of this extension.

## What the linkage axis means

“Richness” here means reference coverage under one explicit, nested graph
construction—not link quality in general:

- **sparse:** useful paths retained where they already exist, plus 25% of other
  native links selected by stable hash;
- **native:** the frozen authored graph;
- **enriched:** native links plus their reciprocal backlinks.

The report therefore includes edge count, nonisolated-node fraction, degree
distribution, component structure, primary indegree, useful reachability, path
length, and candidate-to-useful reachability. A result cannot be generalized to
all “rich graphs” merely from the level name.

## Interpretation rules

- A token increase from 10 to 150 candidates is mechanically expected. Density
  becomes a behavioral recommendation only if paired task-success or
  correct-use failures clear the registered threshold under the primary-first
  unbounded control.
- PageRank is link-dependent only if its advantage over key order changes
  materially from sparse to enriched under navigation.
- If PageRank helps only after aggressive enrichment, it remains optional and
  should be gated by observed graph coverage rather than made the default.
- Candidate-generation recall remains a separate experiment. Every variant in
  this study still includes the primary Memory literally by construction.

## Agent sharding correction

The first 34 authentication-smoke cells exposed a pre-outcome assignment flaw:
lexicographic modulo sharding aligned each reference level with one OAuth
account. Those pilot traces are retained but excluded from all estimates. The
locked replacement is a three-way Latin rotation over task-within-family,
candidate count, and linkage level. Every account now receives exactly 21
variants at each candidate count, 21 at each linkage level, and nine from each
graph family. See
[`density-linkage-agent-sharding-amendment.json`](../memory-bench/fixtures/beads_ordering/density-linkage-agent-sharding-amendment.json).

## Reproduction shape

The implementation adds commands to freeze and validate the recipe manifest,
materialize one portable task fixture for an agent shard, run the deterministic
grid, and aggregate raw agent traces. Exact commands and hashes are written with
the generated evidence after the tests and CLI surface are complete.
