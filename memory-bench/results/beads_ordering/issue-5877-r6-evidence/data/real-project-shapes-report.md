# Real-project Memory shape telemetry

Only aggregate numeric distributions are retained. Workspace paths, Memory identifiers, keys, compact text, derived probes, and command diagnostics are discarded.

## Method

- Sampling frame: recursively discovered `.beads` workspaces beneath operator-supplied roots; generated, dependency, cache, and experiment-result trees were excluded.
- Probe derivation: bounded deterministic key tokens and title/excerpt bigrams from the compact discovery projection.
- Snapshot deduplication: identical compact candidate snapshots were counted once.
- Failure handling: only fixed aggregate categories survive; diagnostics are discarded.

## Results

- Workspace candidates: 225
- Workspaces scanned: 200
- Workspaces with Memory records: 163
- Corpus size p50/p90: 5.0/79.39999999999998
- Native-probe match size p50/p90: 1.0/6.0
- Canonical Memory-reference density: unavailable in the observed legacy surface; it is not reported as zero.
