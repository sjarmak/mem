# Running the Beads Memory ordering experiment

This experiment answers one question: for the same candidates produced by
Beads' existing case-insensitive literal substring matcher, what changes when
those candidates are ordered differently before a bounded page is shown to an
agent?

It does not compare candidate-generation methods. BM25F scores only the map
already returned by `memoryops.List`; it cannot add or remove a candidate.

## Pinned implementation

- mem harness branch: the current `mem` checkout
- Beads worktree: `/home/ds/gastownhall/beads-worktrees/memory-ordering-5877`
- Beads branch: `exp/memory-pagination-ordering-5877`
- experimental binary: `/home/ds/.local/bin/bd-memory-ordering-5877`

Every run row and manifest records the mem SHA, Beads SHA, dirty flags, exact
binary SHA-256, structural-order source SHA, agent model, and Claude CLI version.
Set `BEADS_BIN` explicitly; the harness never imports Beads code.

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
candidate/query/order/config change.

## Build and validate

```bash
cd /home/ds/gastownhall/beads-worktrees/memory-ordering-5877
go test ./cmd/bd -run '^TestExperimental|^TestParseExperimentalMemory|^TestMatchesKnownCommand' -count=1
go build -o /home/ds/.local/bin/bd-memory-ordering-5877 ./cmd/bd

cd /home/ds/projects/mem/memory-bench
export BEADS_BIN=/home/ds/.local/bin/bd-memory-ordering-5877
python3 -m membench.cli beads-ordering-freeze \
  --structural-order-source /home/ds/projects/Artifacts-ranked-searching \
  --out fixtures/beads_ordering/corpus.json --overwrite
python3 -m membench.cli beads-ordering-validate \
  --fixture fixtures/beads_ordering/corpus.json \
  --workspace-root ../.mem/beads-ordering-workspaces-v3 \
  --out results/beads_ordering/validation-structural.json
```

The validation command seeds nested 50/100/500-Memory Beads workspaces, exhausts
all default arms, and refuses unless candidate IDs, count, digest, and compact
projection are identical modulo rank/order.

## Experiment 1: ordering × page size

The commands below use the local OAuth credential only to authenticate the
fresh neutral Claude config. Its contents are copied with mode 0600 and are
never logged or hashed.

```bash
python3 -m membench.cli beads-ordering-run \
  --fixture fixtures/beads_ordering/corpus.json \
  --workspace-root ../.mem/beads-ordering-workspaces-v3 \
  --beads-repo /home/ds/gastownhall/beads-worktrees/memory-ordering-5877 \
  --beads-bin "$BEADS_BIN" \
  --model claude-haiku-4-5-20251001 \
  --claude-credentials /home/ds/.claude/.credentials.json \
  --arms key,indegree,outdegree,pagerank,reverse-pagerank,hits-authority,hits-hub,bm25f \
  --page-sizes 3,5,10,20,50,all \
  --mode search-only --repeats 1 --order-seed 5877 --max-tool-calls 12 \
  --out results/beads_ordering/search-only
```

Run one arm reproducibly by changing `--arms` and choosing a distinct output
directory. A stopped command is
resumable against the same manifest; completed cell artifacts are validated and
reused.

## Experiment 2: navigation hypothesis

```bash
python3 -m membench.cli beads-ordering-run \
  --fixture fixtures/beads_ordering/corpus.json \
  --workspace-root ../.mem/beads-ordering-workspaces-v3 \
  --beads-repo /home/ds/gastownhall/beads-worktrees/memory-ordering-5877 \
  --beads-bin "$BEADS_BIN" \
  --model claude-haiku-4-5-20251001 \
  --claude-credentials /home/ds/.claude/.credentials.json \
  --arms key,indegree,outdegree,pagerank,reverse-pagerank,hits-authority,hits-hub,bm25f \
  --page-sizes 3,5,10,20,50,all \
  --mode navigation --repeats 1 --order-seed 5877 --max-tool-calls 12 \
  --out results/beads_ordering/navigation
```

Search-only mode allows recall only for Memories already shown in discovery.
Navigation mode additionally allows recall of references exposed by a successful
recall. The wrapper enforces both policies and logs every followed edge; the
Beads matcher, order, compact projection, page size, and model stay fixed.

## Regenerate analysis

```bash
python3 -m membench.cli beads-ordering-analyze \
  --raw results/beads_ordering/search-only/raw-results.jsonl \
        results/beads_ordering/navigation/raw-results.jsonl \
  --out results/beads_ordering/combined
```

Outputs are `raw-results.jsonl`, per-run retrieval logs and Claude streams,
`manifest.json`, `analysis.json`, `report.md`, and one page-size SVG per mode.
Candidate-generation and ordering milliseconds are server-side measures;
compact bytes/tokens, pages, recalls, and tool calls measure agent ingestion and
round trips. The roughly bytes/4 token estimate is explicit and is not presented
as provider tokenizer ground truth.
