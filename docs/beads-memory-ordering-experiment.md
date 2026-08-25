# Running the Beads Memory ordering experiment

This experiment answers one question: for the same candidates produced by
Beads' existing case-insensitive literal substring matcher, what changes when
those candidates are ordered differently before a bounded page is shown to an
agent?

It does not compare candidate-generation methods. BM25F scores only the map
already returned by `memoryops.List`; it cannot add or remove a candidate.

## Pinned implementation

- mem harness branch: the current `mem` checkout
- Beads worktree: set explicitly as `BEADS_WORKTREE`
- Beads branch: `exp/memory-pagination-ordering-5877`
- experimental binary: set explicitly as `BEADS_BIN`

Every run row and manifest records the mem SHA, Beads SHA, dirty flags, SHA-256
of each tracked worktree diff, exact binary SHA-256, structural-order source
SHA, fixture digest, agent model, and Claude CLI version. Set `BEADS_BIN`
explicitly; the harness never imports Beads code.

## Ordering conventions

- `key`: canonical Memory key in ascending bytewise order, matching the current
  deterministic CLI presentation.
- `navigation`: ascending authored `navigation_rank`, then canonical Memory ID.
  The rank lives in fixture frontmatter and is query-independent. It represents
  a simple query-independent navigation-oriented control; no pre-existing
  Beads implementation was found locally.
- `indegree`, `outdegree`, `pagerank`, `reverse-pagerank`, `hits-authority`, and
  `hits-hub`: descending global query-independent graph score, then canonical
  Memory ID. The pinned structural-order implementation materializes these
  corpus-level positions when the fixture is frozen; Beads only consumes the
  resulting positions during the experiment.
- `bm25f`: descending global BM25F score over the exact literal-match candidate
  set, then canonical Memory ID. Defaults are key 6, aliases 5, title 3, body 1,
  `k1=1.2`, and `b=0.75`; every value is a CLI option and manifest field.

`--page-size all` returns the complete matching set in one response. Integer
page sizes use a state-bound continuation; Beads refuses continuation after a
candidate/query/order/config change. The agent sees only whether continuation is
available; the neutral wrapper retains and supplies the exact opaque cursor so
cursor transcription errors cannot masquerade as retrieval failures.

## Build and validate

```bash
export MEM_REPO=/path/to/mem
export BEADS_WORKTREE=/path/to/experimental-beads-worktree
export BEADS_BIN=/path/to/bd-memory-ordering-5877
export STRUCTURAL_ORDER_SOURCE=/path/to/pinned/structural-order-source
export AGENT_CREDENTIALS=/path/to/authenticated-profile.json

cd "$BEADS_WORKTREE"
go test ./cmd/bd -run '^TestExperimental|^TestParseExperimentalMemory|^TestMatchesKnownCommand' -count=1
go build -o "$BEADS_BIN" ./cmd/bd

cd "$MEM_REPO/memory-bench"
python3 -m membench.cli beads-ordering-freeze \
  --structural-order-source "$STRUCTURAL_ORDER_SOURCE" \
  --out fixtures/beads_ordering/corpus.json --overwrite
python3 -m membench.cli beads-ordering-validate \
  --fixture fixtures/beads_ordering/corpus.json \
  --workspace-root ../.mem/beads-ordering-workspaces-v5 \
  --out results/beads_ordering/validation-heldout.json
```

The frozen fixture contains 12 development-only tasks and 24 held-out tasks,
eight at each 50/100/500-Memory corpus size. Twelve held-out tasks preserve
sanitized shapes from real local software-engineering records; only a
non-identifying source hash and shape category are retained. The validation
command seeds nested workspaces, exhausts every arm, and refuses unless
candidate IDs, count, digest, and compact projection are identical modulo
rank/order.

The six structural priors were screened mechanically on the 12 development
tasks. Before held-out ranks were materialized,
`results/beads_ordering/preregistration.json` locked `reverse-pagerank` as the
entry-point finalist and `hits-hub` as the branch-heavy finalist. The held-out
agent matrix does not revisit that selection.

## Experiment 1: ordering × page size

The commands below use the local OAuth credential only to authenticate the
fresh neutral Claude config. Its contents are copied with mode 0600 and are
never logged or hashed.

```bash
python3 -m membench.cli beads-ordering-run \
  --fixture fixtures/beads_ordering/corpus.json \
  --workspace-root ../.mem/beads-ordering-workspaces-v5 \
  --beads-repo "$BEADS_WORKTREE" \
  --beads-bin "$BEADS_BIN" \
  --model claude-haiku-4-5-20251001 \
  --claude-credentials "$AGENT_CREDENTIALS" \
  --task-split heldout \
  --arms key,reverse-pagerank,hits-hub,bm25f \
  --page-sizes 5,10,20,all \
  --mode search-only --repeats 1 --order-seed 5877 --max-tool-calls 12 \
  --out results/beads_ordering/heldout-pass-1-search-only
```

Run one arm reproducibly by changing `--arms` and choosing a distinct output
directory. A stopped command is
resumable against the same manifest; completed cell artifacts are validated and
reused.

## Experiment 2: navigation hypothesis

```bash
python3 -m membench.cli beads-ordering-run \
  --fixture fixtures/beads_ordering/corpus.json \
  --workspace-root ../.mem/beads-ordering-workspaces-v5 \
  --beads-repo "$BEADS_WORKTREE" \
  --beads-bin "$BEADS_BIN" \
  --model claude-haiku-4-5-20251001 \
  --claude-credentials "$AGENT_CREDENTIALS" \
  --task-split heldout \
  --arms key,reverse-pagerank,hits-hub,bm25f \
  --page-sizes 5,10,20,all \
  --mode navigation --repeats 1 --order-seed 5877 --max-tool-calls 12 \
  --out results/beads_ordering/heldout-pass-1-navigation
```

Search-only mode allows recall only for Memories already shown in discovery.
Navigation mode additionally allows recall of references exposed by a successful
recall. The wrapper enforces both policies and logs every followed edge; the
Beads matcher, order, compact projection, page size, and model stay fixed.

## Regenerate analysis

```bash
python3 -m membench.cli beads-ordering-analyze \
  --raw results/beads_ordering/heldout-pass-1-search-only/raw-results.jsonl \
        results/beads_ordering/heldout-pass-1-navigation/raw-results.jsonl \
  --out results/beads_ordering/heldout-pass-1-combined
```

Outputs are `raw-results.jsonl`, per-run retrieval logs and Claude streams,
`manifest.json`, `analysis.json`, `report.md`, and one page-size SVG per mode.
Candidate-generation and ordering milliseconds are server-side measures;
compact bytes/tokens, pages, recalls, and tool calls measure agent ingestion and
round trips. The roughly bytes/4 token estimate is explicit and is not presented
as provider tokenizer ground truth.

The combined analysis computes paired deltas within task/repeat/mode/page size,
then clusters repeats by task before reporting distributions. It refuses to
issue a mechanism verdict until all 768 initial cells are present. BM25F earns
an ownership recommendation only if it saves at least one median page or 20%
median compact tokens versus both structural finalists, does not regress task
success, and retains the material advantage with navigation. Otherwise the
registered recommendation is query-independent ordering plus navigation.
