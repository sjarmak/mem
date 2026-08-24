# Beads Memory pre-pagination ordering experiment

Status: implementation plan, frozen before code changes

## Decision question

Given one frozen Memory corpus and one frozen literal query, does ordering the
same matched candidates before fixed-size pagination reduce the agent's cost to
reach a useful Memory? The decision is whether a more complex ordering policy is
worth carrying inside Beads. Candidate-generation changes are explicitly out of
scope until this experiment is complete.

## Inspection findings

- `mem` already owns the appropriate evaluation seams: frozen synthetic worlds,
  uniform memory arms, real headless-agent execution, deterministic graders,
  normalized memory/tool telemetry, raw reports, and paired-distribution helpers.
  This experiment will extend `memory-bench/`; it will not create a second eval
  framework.
- Current Beads `origin/main` routes the legacy keyed-memory plane through
  `memoryops.Memories`. `List` owns matching: case-insensitive literal substring
  over key or complete stored value. It returns an unordered map; `cmd/bd` sorts
  keys alphabetically for deterministic display. There is no page or rank.
- Issue #5877 R6 calls for compact deterministic discovery with continuation
  bound to the original query and ordering. The ranking comment separates
  candidate inclusion from ranking and proposes an in-memory BM25F pass on the
  already-matched candidates.
- `origin/feat/memory-beads` is an architecture-spike branch for version/history,
  migration, and interchange. It does not provide discovery ranking or a
  persistent navigation rank. No local branch or open PR implements ranking or
  pagination. PR #5964 only corrects empty-value presence and does not overlap
  this experiment.
- The structural-order source is invoked at fixture-freeze time to materialize
  six global query-independent graph orders. Its ranking implementation remains
  outside `mem`; the fixture records only corpus-level rank positions and the
  exact source commit.
- The legacy schema has key and body only. For an R6-shaped experimental corpus,
  structured frontmatter inside the stored body will carry title, aliases,
  lifecycle, references, provenance, and an authored query-independent
  `navigation_rank`. This is corpus data, not a Beads schema change. The literal
  matcher will continue to inspect the exact same key/value bytes in every arm.

## Minimal Beads change

Create a clean worktree from current `origin/main` on
`exp/memory-pagination-ordering-5877`. Leave the user's existing dirty checkout
untouched.

Add experimental flags to `bd memories`, active only when a page size or ordering
is requested:

```text
--experimental-order key|navigation|indegree|outdegree|pagerank|reverse-pagerank|hits-authority|hits-hub|bm25f
--page-size 3|5|10|20|50|all
--continuation TOKEN
--bm25f-key-weight FLOAT
--bm25f-alias-weight FLOAT
--bm25f-title-weight FLOAT
--bm25f-body-weight FLOAT
```

The ordinary command and legacy `--json` map stay byte-compatible when none of
these flags is used. The experimental structured JSON projection is identical
for all arms:

```text
id, key, title, lifecycle, excerpt, matched_fields, rank,
total_matched, page_size, complete, continuation
```

Implementation constraints:

1. Call `memoryops.List` once with the raw query. This freezes the baseline
   candidate-generation semantics in one existing role.
2. Parse frontmatter only after matching, for projection and ordering features.
   A key is the canonical experimental Memory ID because legacy Memories have no
   separate Bead identity.
3. `key` orders by key ascending (the shipped CLI baseline).
4. `navigation` orders by ascending authored `navigation_rank`, then canonical
   ID. The rank is query-independent and identifies curated graph entry points;
   missing ranks sort after ranked records. It is neither learned nor persisted
   as Memory state outside the corpus body.
5. Each structural-prior arm orders by its frozen global corpus rank, then
   canonical ID. Search terms never alter those ranks; missing or duplicate
   materialized ranks fail explicitly.
6. `bm25f` scores only the `memoryops.List` candidate map. Tokenization,
   normalization, field weights, length normalization, saturation, and any
   phrase/exact boosts are explicit in the JSON run manifest. Final ties use
   canonical Memory ID.
7. A continuation contains the query, ordering/config digest, candidate-state
   digest, and next offset. On every page Beads reloads, rematches, reorders, and
   verifies both digests. Any relevant mutation or incompatible reuse fails
   explicitly.
8. No schema migration, index, daemon, embedding, persisted score, or Memory
   version is introduced.

Page size is crossed with ordering rather than treated as a tuning constant.
Every task runs at 3, 5, 10, 20, 50, and `all`; `all` is a first-class experimental
value that returns the complete matched set in one response, not a disguised
large integer. This separates the cost of hiding candidates behind continuation
from the cost of presenting many candidates at once. The primary mechanical
control is canonical key ascending; navigation rank remains the second
query-independent arm; BM25F globally scores the same complete matched set and
only then applies the requested bound.

Each Beads response records candidate-generation time and post-match
ordering/scoring time separately. The harness records whole tool latency as a
third value. BM25F may scan and score the matched set in this PoC; results will
describe that observed implementation cost without treating a scan as a
production requirement or comparing it to an unimplemented inverted index.

Tests are written first for candidate-set parity, deterministic tie-breaking,
page boundaries, unchanged continuation, mutation refusal, projection equality,
configurable weights, Unicode-safe bounded excerpts, and unchanged legacy JSON.

## `mem` harness extension

Add a focused `membench beads-ordering` command and modules under a single
`membench/beads_ordering/` package. Reuse the existing headless-agent invocation,
tool-call parsing, paired-run provenance, metric schemas where compatible, and
report conventions. The harness invokes only the binary named by `--beads-bin`
or `BEADS_BIN`; it never imports or copies Beads code.

The run object will pin and hash:

- mem SHA and dirty state;
- Beads SHA and dirty state reported from the binary worktree;
- corpus/task/label digests;
- query, page size, ordering, and BM25F configuration;
- agent CLI version, exact model, prompt/config digest, budget, and repetition;
- start/end timestamps and host timing source.

Before any agent call, a parity gate exhausts every arm's pages and refuses the
run unless candidate IDs, compact fields, total counts, and corpus/query digests
are identical modulo order/rank/continuation. Gold labels are loaded from frozen
fixtures and are never placed in the agent prompt.

The agent gets a task-specific tool protocol matching natural behavior:

```text
search -> inspect compact page -> recall or continue -> optionally follow refs
       -> answer/perform the task
```

It is not told the arm. Page size, agent/model/config, corpus, query, and budget
are paired and fixed. The harness does not force continuation after the agent
chooses a result or abstains.

## Frozen corpus and task design

Generate one nested, deterministic software-factory corpus with prefixes of 50,
100, and 500 Memories. A shared topic catalogue supplies realistic deployment,
testing, CI, storage, API, migration, and agent-operation knowledge. Each task
fixture freezes:

```text
primary_relevant
acceptable_entry_points
distractors
```

Task cases cover overlapping terms, lexical distractors, weak key/title with a
relevant body, corrections/follow-ups, archived records, human/agent provenance,
and explicit Memory/task references. Queries are selected before arm runs and
must produce nontrivial match-set and burial-depth strata. Corpus construction is
deterministic and its manifest is checked into `memory-bench/fixtures/`.

## Measurements and outcomes

Raw JSONL contains one record per paired task/arm/repetition plus the complete
agent transcript artifact. Mechanical metrics include all requested discovery,
recall, reference-hop, token, tool-call, latency, stop/abstention, relevance, and
task-result fields. `cost_to_first_useful_memory` is reported as a vector, not a
weighted scalar: pages, compact tokens/bytes, retrieval calls, retrieval latency,
and elapsed time.

Analysis reports distributions (including p50/p90), paired deltas and bootstrap
intervals where sample size permits, stratified by corpus size, match-set size,
and baseline burial depth. It explicitly distinguishes lower retrieval cost from
improved end-task success and reports candidate-parity failures separately from
agent failures.

For every ordering, the page-size curve reports page-1 acceptable visibility,
pages to first useful Memory, compact tokens, tool calls, time to first useful,
recalls, and task success. Bounded conditions are compared directly with `all`.
The analysis also searches for the first match-set/burial stratum where smaller
key-ordered pages are materially worse than BM25F pages, reporting absence of a
supported crossover rather than manufacturing one from sparse cells.

Experiment 2 reuses the same fixtures and runner. Search-only permits recall only
of a Memory previously shown in discovery; navigation additionally permits a
reference exposed by a successful recall. The wrapper enforces and logs this
boundary while measuring whether graph traversal closes the initial-order
cost/success gap for each structural prior versus BM25F.

## Deliverables and stop rule

The repository will contain raw run artifacts, machine-readable summaries,
SVG/Markdown plots and tables, exact commands, and a conservative issue #5877
write-up. The write-up will recommend added Beads complexity only if candidate
parity holds and paired cost reductions are material without a task-success or
latency regression. Candidate-generation comparisons remain a separately named
future experiment and will not appear in these results.
