# Reproduce the structural-ordering follow-up

Set local paths explicitly; the experiment never copies Beads source into `mem`.

```bash
export MEM_REPO=/path/to/mem
export BEADS_WORKTREE=/path/to/experimental-beads-worktree
export BEADS_BIN=/path/to/bd-memory-ordering-5877
export STRUCTURAL_SOURCE=/path/to/pinned-structural-order-source

cd "$BEADS_WORKTREE"
go test ./cmd/bd -run 'TestParseExperimentalMemory|TestExperimentalControlOrders|TestExperimentalStructuralPrior|TestExperimentalOrdering|TestExperimentalContinuation|TestExperimentalUnbounded' -count=1
go build -o "$BEADS_BIN" ./cmd/bd

cd "$MEM_REPO/memory-bench"
python3 -m membench.cli beads-ordering-followup-freeze \
  --out fixtures/beads_ordering/followup \
  --structural-order-source "$STRUCTURAL_SOURCE" --overwrite

mkdir -p results/beads_ordering/followup/validation \
  results/beads_ordering/followup/workspaces
for fixture in fixtures/beads_ordering/followup/*.json; do
  family=$(basename "$fixture" .json)
  test "$family" = manifest && continue
  python3 -m membench.cli beads-ordering-validate \
    --fixture "$fixture" --beads-bin "$BEADS_BIN" \
    --workspace-root "results/beads_ordering/followup/workspaces/$family" \
    --task-split all \
    --arms key,indegree,outdegree,pagerank,reverse-pagerank,bm25f,control-automatic,control-semantic,control-strategy,control-raw \
    --out "results/beads_ordering/followup/validation/$family.json"
done

python3 -m membench.cli beads-ordering-followup-oracle \
  --fixture-dir fixtures/beads_ordering/followup \
  --validation-dir results/beads_ordering/followup/validation \
  --workspace-root results/beads_ordering/followup/workspaces \
  --beads-repo "$BEADS_WORKTREE" --beads-bin "$BEADS_BIN" \
  --arms key,indegree,outdegree,pagerank,reverse-pagerank,bm25f,control-automatic,control-semantic,control-strategy,control-raw \
  --page-sizes 5,10,20,all \
  --out results/beads_ordering/followup/oracle

python3 -m membench.cli beads-ordering-followup-mutations \
  --fixture-dir fixtures/beads_ordering/followup \
  --preregistration fixtures/beads_ordering/structural-followup-preregistration.json \
  --beads-repo "$BEADS_WORKTREE" --beads-bin "$BEADS_BIN" \
  --sizes 50,100,500 --event-count 40 --page-size 10 --seed 5878 \
  --out results/beads_ordering/followup/mutation-replay-final

python3 -m membench.cli beads-ordering-followup-rank-scaling \
  --fixture-dir fixtures/beads_ordering/followup \
  --beads-repo "$BEADS_WORKTREE" --beads-bin "$BEADS_BIN" \
  --sizes 50,100,500,2000,10000 --repeats 3 \
  --out results/beads_ordering/followup/rank-scaling-final
```

Run the confirmatory agent grid once per family and mode. Use a distinct output
directory for every shard; completed cells are resumable and validated against
the shard manifest.

```bash
export AGENT_MODEL=claude-haiku-4-5-20251001
export AGENT_CREDENTIALS=/path/to/authenticated-profile.json

for fixture in fixtures/beads_ordering/followup/*.json; do
  family=$(basename "$fixture" .json)
  test "$family" = manifest && continue
  for mode in search-only navigation; do
    python3 -m membench.cli beads-ordering-run \
      --fixture "$fixture" \
      --workspace-root "results/beads_ordering/followup/workspaces/$family" \
      --beads-repo "$BEADS_WORKTREE" --beads-bin "$BEADS_BIN" \
      --model "$AGENT_MODEL" --claude-credentials "$AGENT_CREDENTIALS" \
      --task-split heldout \
      --arms key,indegree,outdegree,pagerank,reverse-pagerank,bm25f \
      --page-sizes 5,10,20,all --mode "$mode" \
      --repeats 1 --order-seed 5878 --max-tool-calls 12 \
      --out "results/beads_ordering/followup/agent-grid/$family/$mode"
  done
done
```

Select repeat groups from repeat-zero results using the preregistered
task-success-disagreement/infrastructure-failure rule, run repeat indices 1 and
2 for every policy in each selected task × mode × page group, then aggregate
initial and repeat shards with equal weight per policy cell:

```bash
mapfile -t raw_files < <(find \
  results/beads_ordering/followup/agent-grid \
  results/beads_ordering/followup/agent-targeted-repeats \
  -name raw-results.jsonl -type f | sort)

python3 -m membench.cli beads-ordering-followup-agent-analyze \
  --fixture-dir fixtures/beads_ordering/followup \
  --raw "${raw_files[@]}" \
  --out results/beads_ordering/followup/agent-analysis-final
```

The supplied final analysis contains 2,400 observations and 1,008 equally
weighted policy cells. Its public per-run projection omits queries, model text,
failure diagnostics, credentials, and machine-local paths.
