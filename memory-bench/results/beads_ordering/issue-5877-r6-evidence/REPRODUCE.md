# Reproduction commands

Use explicit paths. The experimental Beads source stays in its own worktree;
no Beads implementation is copied into `mem`.

```bash
export MEM_REPO=/path/to/mem
export BEADS_WORKTREE=/path/to/experimental-beads-worktree
export BEADS_BIN="$BEADS_WORKTREE/bd"

cd "$BEADS_WORKTREE"
make build
sha256sum "$BEADS_BIN"

cd "$MEM_REPO/memory-bench"
python3 -m membench.cli beads-ordering-density-linkage-materialize \
  --help
python3 -m membench.cli beads-ordering-density-linkage-run \
  --help
python3 -m membench.cli beads-ordering-density-linkage-agent-analyze \
  --help
```

The frozen design and factor assignments are in:

```text
fixtures/beads_ordering/density-linkage-preregistration.json
fixtures/beads_ordering/density-linkage-agent-sharding-amendment.json
fixtures/beads_ordering/density-linkage-replication-amendment.json
fixtures/beads_ordering/density-linkage/manifest.json
```

## One-arm run shape

The exact full commands are preserved by the CLI help and shard manifests. A
single run uses the explicit binary and one selected order; page size, model,
mode, and task selection remain frozen inputs:

```bash
python3 -m membench.cli beads-ordering-density-linkage-run \
  --manifest fixtures/beads_ordering/density-linkage/manifest.json \
  --workspace-root results/beads_ordering/density-linkage/reproduction-workspaces \
  --beads-repo "$BEADS_WORKTREE" \
  --beads-bin "$BEADS_BIN" \
  --arms key \
  --page-sizes 5 \
  --modes navigation \
  --model MODEL_ID \
  --max-tool-calls 12 \
  --shard-index 0 \
  --shard-count 1 \
  --out results/beads_ordering/density-linkage/reproduction-key-p5
```

Replace `key` with `pagerank` or `bm25f` without changing any other factor.
Use page size `all` for the complete-candidate control. Authentication inputs
are intentionally absent from this public package.

## Primary analysis

The final primary analysis combines the complete initial shards and the locked
repeat shards. Supply their `raw-results.jsonl` files and matching manifests to
the evidence command, preserving the locked repeat manifest:

```bash
python3 -m membench.cli beads-ordering-density-linkage-agent-analyze \
  --manifest fixtures/beads_ordering/density-linkage/manifest.json \
  --raw RAW_RESULT_PATHS \
  --shard-manifests SHARD_MANIFEST_PATHS \
  --locked-repeat-manifest LOCKED_REPEAT_MANIFEST \
  --bootstrap-seed 5879 \
  --bootstrap-resamples 5000 \
  --out results/beads_ordering/density-linkage/agent-analysis-final

python3 -m membench.cli beads-ordering-density-linkage-plot \
  --analysis results/beads_ordering/density-linkage/agent-analysis-final/analysis.json \
  --out results/beads_ordering/density-linkage/agent-analysis-final/plots
```

## Cross-model replication

The bridge and secondary selection manifests contain the exact same 189 run
IDs. Compare their three shards per wave with:

```bash
python3 -m membench.cli beads-ordering-density-linkage-replication-compare \
  --manifest fixtures/beads_ordering/density-linkage/manifest.json \
  --bridge-raw BRIDGE_RAW_PATHS \
  --secondary-raw SECONDARY_RAW_PATHS \
  --bridge-manifests BRIDGE_MANIFEST_PATHS \
  --secondary-manifests SECONDARY_MANIFEST_PATHS \
  --bootstrap-seed 5880 \
  --bootstrap-resamples 5000 \
  --out results/beads_ordering/density-linkage/secondary-replication/comparison
```

## Continuation conformance

```bash
cd "$BEADS_WORKTREE"
./scripts/test.sh -run \
  '^(TestExperimentalContinuationIsStableAndFailsAfterMutation|TestExperimentalContinuationRejectsIncompatibleReuse)$' \
  ./cmd/bd/...

BEADS_TEST_EMBEDDED_DOLT=1 go test -count=1 \
  -run '^TestEmbeddedExperimentalMemoryContinuationConformance$' ./cmd/bd
```

## Quality gates

```bash
cd "$MEM_REPO/memory-bench"
python3 -m ruff check .
python3 -m black --check .
python3 -m mypy --strict membench
python3 -m pytest

cd "$MEM_REPO"
npm run check
```

All shareable JSONL files are projections of the source results. Reanalysis of
agent text is intentionally impossible from this package because queries,
answers, streams, and failure diagnostics are not included.

The exact source diffs can be inspected or applied without metadata expansion:

```bash
gzip -dc patches/beads-experimental.patch.gz > /tmp/beads-experimental.patch
gzip -dc patches/mem-experiment-harness.patch.gz > /tmp/mem-experiment-harness.patch
```
