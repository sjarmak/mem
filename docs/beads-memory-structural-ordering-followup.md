# Structural Memory ordering follow-up

This follow-up extends the completed bounded-discovery experiment. It does not
replace its corpus, results, or causal question. The locked design is
[`structural-followup-preregistration.json`](../memory-bench/fixtures/beads_ordering/structural-followup-preregistration.json).

The new decision is whether reverse PageRank generalizes as a default
query-independent ordering, how fresh that derived rank must be, and whether
Beads should expose semantic controls, method selection, or raw rank values.
The previous finding is the motivation, not evidence in the new confirmatory
sample.

## Causal boundaries

- Within a corpus/query/snapshot, every retrieval arm receives the exact same
  literal-match candidate IDs and compact projection.
- Structural policies and operator controls are query-independent. BM25F may
  only reorder the same literal candidates.
- The model, prompt, tool budget, page size, and navigation permissions are
  matched within each comparison.
- Relevance and operator-intent labels are frozen before held-out ranks or
  agent outcomes are inspected.
- Mutations may change candidate membership between snapshots, but every
  policy compared at one snapshot receives identical membership.
- Rank changes invalidate active continuations explicitly; pages from two rank
  epochs are never combined.

## Minimal change plan

1. Preserve the original fixture, results, package, and recorded diff digest.
   Freeze a separate follow-up corpus with seven independently authored or
   sanitized-real-derived software-engineering graph families.
2. Reuse the existing `beads_ordering` models, Beads client, neutral tool,
   runner, scorer, and report pipeline. Add follow-up metadata and analysis in
   that package instead of building a parallel benchmark framework.
3. Materialize query-independent structural positions during fixture freeze,
   as the existing experiment does. Keep Memory identity and content in the
   experimental Beads workspace and invoke it only through explicit
   `BEADS_BIN`.
4. Add a chronological replay layer that applies Memory/reference/lifecycle
   events, snapshots exact and stale ranks, and records computation separately
   from model ingestion. Implement an incremental arm only if the existing
   experimental scorer permits it without a production dependency or service.
5. Encode automatic, semantic pin/boost/demote, strategy-selection, and raw
   numeric-rank controls as frozen query-independent order configurations.
   Controls cannot alter candidate membership or compact content.
6. Validate candidate/projection parity, graph-label completeness,
   determinism, rank-epoch continuation failure, and provenance before any
   agent run. Write these tests before implementation.
7. Run the full deterministic rank/mutation grid first. Run the matched agent
   grid only after validation, then add targeted repeats according to the
   preregistered disagreement rule.
8. Analyze at the task-within-family level with clustered bootstrap confidence
   intervals, p50/p90 distributions, and explicit per-family failure tails.

No new production search index, Memory schema, FTS candidate generator,
ranking daemon, or specialized rank-maintenance data structure is part of this
plan. The simplest exact and periodic implementations establish the workload
before plumbing is evaluated.

## Confirmatory workload

The retrieval matrix covers key, indegree, outdegree, ordinary PageRank,
reverse PageRank, and same-candidate BM25F at page sizes 5, 10, 20, and
unbounded, in both search-only and navigation modes. Seven graph families
contribute one development task and three held-out tasks each. The held-out
set therefore has 21 independent tasks; repeats estimate model variance but do
not inflate the independent sample size.

Every family includes realistic overlapping lexical candidates and one
targeted structural failure: an archived or stale hub, a new unlinked relevant
Memory, a reference cycle, a disconnected component, a high-outdegree
distractor, a superseding chain, or link inflation. Neutral matched tasks
measure whether controls damage cases that require no intervention.

## Mutation and control workload

Each family receives a deterministic 40-event chronology containing Memory
creation, content edits, reference additions/removals, archival, supersession,
and restoration. Exact recomputation is the oracle. Periodic refresh after 5
and 20 mutations and lazy refresh at read time quantify tolerated staleness.
The compute-only curve additionally covers 2,000 and 10,000 Memories; agent
runs remain at the realistic 50/100/500 sizes used by the original experiment.

Control interventions are authored from frozen operator intent, not selected
by the agent or tuned from held-out outcomes. Automatic ordering is compared
with semantic pin/boost/demote, selectable policy, and direct numeric rank.
The analysis records repaired failure cases, neutral-task regressions, number
of interventions, stale overrides, and implementation complexity.

## Decision oracles

- **Default ordering:** reverse PageRank must be task-success non-inferior,
  materially cheaper than key order, and avoid a severe family-specific tail.
- **Query-specific ownership:** BM25F must retain at least a one-page median or
  20% median compact-token advantage after navigation without reducing task
  success.
- **Freshness:** choose the least eager policy within the registered success,
  page, token, top-10 overlap, and continuation-integrity guardrails.
- **Control surface:** expose only controls that repair at least 25% of targeted
  failures without harming neutral tasks; prefer semantic controls when they
  remain within five percentage points of raw-rank efficacy.
- **Plumbing:** specialized maintenance earns consideration only after the
  simple implementation misses an observed operational budget and an
  alternative provides at least a twofold p95 improvement with retrieval
  parity.

These thresholds map measurements to architectural actions. Results that do
not clear them remain evidence for experimentation, not a production default.

## Post-run interpretation boundary

The candidate-parity gate makes the primary Memory a literal candidate by
construction. That is required for a clean ordering comparison, but means this
experiment cannot estimate candidate-generation recall. A recovered post-hoc
analysis of 192 original-study unbounded cells also found 49 task failures even
though the primary Memory was visible in every cell; 35 of those failures
recalled it first. Treat this as an exploratory reasoning-load signal, not a
causal density result, because corpus size, match-set size, and task identity
co-varied.

The architecture verdict is unchanged. The next controlled behavior study
should vary matched-candidate density near 10/40/150 at a fixed 500-Memory
corpus. A lexical-miss candidate-generation study remains separate so it cannot
confound the ordering experiment.

## Preregistration history

An amendment-v2 proposal was registered at `2026-08-25T02:10:46Z` and committed
as `8715068e404de7122f5bd9bdeb8a3a5f21f2e141`, but that commit remained on the
unmerged `worktree-followup-amendment-v2` branch. It proposed 35 held-out tasks,
four paid arms (including reverse PageRank), and page sizes 5, 20, and
unbounded. Its fixture SHA-256 was
`62cebd8f82ccc98d28e36274b4d1f8f117c64343fc3f954716913b604dfec684`.
It was not the operative preregistration for the subsequently sealed R6
evidence and is retained only as abandoned design history.

The operative design was the narrower
[`density-linkage-preregistration.json`](../memory-bench/fixtures/beads_ordering/density-linkage-preregistration.json),
registered at `2026-08-25T12:21:48Z` and committed by
`d3bbf53f2ff8d286194ee38edd30078f93926f97`. It reused the 21 frozen base tasks
and preregistered candidate counts 10/40/150, sparse/native/enriched linkage,
key/PageRank/BM25F policies, and page sizes 5 and unbounded. The R6 evidence
sealed by `661b22ce5aea2fbddd7cc1529cd35200b5356ee4` and packaged by
`04a6cf9f9fb031f47065d0fd2297e0430d931374` cites that fixture's SHA-256,
`a78d9c8b5b68fb5396ce9d30ef935219c388564193384b0f6b751307c4bc28ac`,
in both its frozen fixture manifest and primary-analysis provenance. The
lifecycle/control addendum at
`1a35b6ebcb63f80938004441bfa49e24b1fdeea1` did not change that provenance.

## Freeze the follow-up inputs

Use the pinned local checkout that implements the registered structural priors;
the command invokes that checkout and stores only derived rank positions in the
fixtures.

```bash
export MEM_REPO=/path/to/mem
cd "$MEM_REPO/memory-bench"
export STRUCTURAL_ORDER_SOURCE=/path/to/pinned/structural-order-source
python3 -m membench.cli beads-ordering-followup-freeze \
  --structural-order-source "$STRUCTURAL_ORDER_SOURCE" \
  --out fixtures/beads_ordering/followup \
  --seed 5878 --overwrite
```

The deterministic `manifest.json` inventories all seven fixture files, their
SHA-256 digests, task counts, failure cases, and structural-order source SHA.

## Replay mutations and rank freshness

The behavior replay uses the exact pinned update order at agent-facing corpus
sizes. It applies the same 40-event chronology to exact, periodic-5,
periodic-20, lazy-on-read, and explicitly unsupported incremental policies.

```bash
cd "$MEM_REPO/memory-bench"
export BEADS_BIN=/path/to/experimental/bd
export BEADS_REPO=/path/to/experimental/beads-worktree
python3 -m membench.cli beads-ordering-followup-mutations \
  --fixture-dir fixtures/beads_ordering/followup \
  --beads-repo "$BEADS_REPO" --beads-bin "$BEADS_BIN" \
  --sizes 50,100,500 --event-count 40 --page-size 10 --seed 5878 \
  --out results/beads_ordering/followup/mutation-replay
```

Compute-only scaling uses algebraically equivalent aggregated dangling mass at
2,000 and 10,000 Memories. This avoids benchmarking the pinned scorer's
quadratic dangling-node loop as though it were inherent to PageRank. The
arithmetic boundary is explicit in every row and manifest.

```bash
python3 -m membench.cli beads-ordering-followup-rank-scaling \
  --fixture-dir fixtures/beads_ordering/followup \
  --beads-repo "$BEADS_REPO" --beads-bin "$BEADS_BIN" \
  --sizes 50,100,500,2000,10000 --repeats 3 \
  --out results/beads_ordering/followup/rank-scaling
```

Mutation latency, exact-oracle computation, amortized refresh cost, rank age,
top-10 overlap, continuation invalidation, and first-useful page deltas are
recorded separately. The local experimental scorer is differentially checked
against all frozen structural positions before these results are interpreted.
