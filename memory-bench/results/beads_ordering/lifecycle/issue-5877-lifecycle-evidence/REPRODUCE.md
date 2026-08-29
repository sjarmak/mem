# Reproduce the R6 lifecycle/control addendum

These commands use placeholders and do not require or expose the credential paths used for the original agent collection.

## Verify pinned inputs

```bash
cd "$MEM_REPO"
git rev-parse HEAD
sha256sum \
  memory-bench/fixtures/beads_ordering/followup/manifest.json \
  memory-bench/fixtures/beads_ordering/structural-followup-preregistration.json \
  "$BEADS_BIN"
git -C "$BEADS_REPO" rev-parse HEAD
```

Expected mem, Beads, binary, fixture, and preregistration values are listed in `README.md` and the sealed manifests.

## Reanalyze the privacy-safe control observations

The sanitized observations are sufficient to regenerate the decision analysis; excluded prompt/model/failure fields are not analysis inputs.

```bash
cd "$MEM_REPO/memory-bench"
python3 -m membench.cli beads-ordering-followup-agent-analyze \
  --fixture-dir fixtures/beads_ordering/followup \
  --raw results/beads_ordering/lifecycle/issue-5877-lifecycle-evidence/data/control-agent-analysis/sanitized-observations.jsonl \
  --out /tmp/r6-control-reanalysis

cmp /tmp/r6-control-reanalysis/analysis.json \
  results/beads_ordering/lifecycle/issue-5877-lifecycle-evidence/data/control-agent-analysis/analysis.json
cmp /tmp/r6-control-reanalysis/cell-estimates.jsonl \
  results/beads_ordering/lifecycle/issue-5877-lifecycle-evidence/data/control-agent-analysis/cell-estimates.jsonl
cmp /tmp/r6-control-reanalysis/report.md \
  results/beads_ordering/lifecycle/issue-5877-lifecycle-evidence/data/control-agent-analysis/report.md
```

This byte-identical sanitized reanalysis was verified before sealing the package.

## Regenerate deterministic mutation evidence

Run from a clean checkout at the pinned mem SHA:

```bash
cd "$MEM_REPO/memory-bench"
python3 -m membench.cli beads-ordering-followup-mutations \
  --fixture-dir fixtures/beads_ordering/followup \
  --preregistration fixtures/beads_ordering/structural-followup-preregistration.json \
  --beads-repo "$BEADS_REPO" \
  --beads-bin "$BEADS_BIN" \
  --sizes 50,100,500 \
  --event-count 40 \
  --page-size 10 \
  --seed 5878 \
  --out /tmp/r6-mutation-replay
```

Runtime measurements vary with host load; candidate/rank behavior, registered gates, provenance inputs, and output schema are the reproducible claims.

## Regenerate the compute-only scaling curve

```bash
python3 -m membench.cli beads-ordering-followup-rank-scaling \
  --fixture-dir fixtures/beads_ordering/followup \
  --beads-repo "$BEADS_REPO" \
  --beads-bin "$BEADS_BIN" \
  --sizes 50,100,500,2000,10000 \
  --repeats 5 \
  --arithmetic aggregated-dangling-mass \
  --out /tmp/r6-rank-scaling
```

The aggregated-dangling-mass curve is compute-only. Do not interpret it as a behavior-preserving replacement unless the pinned-order parity field passes.

## Verify package hashes

```bash
cd "$MEM_REPO/memory-bench/results/beads_ordering/lifecycle"
sha256sum --check issue-5877-lifecycle-addendum/CHECKSUMS.sha256
```
