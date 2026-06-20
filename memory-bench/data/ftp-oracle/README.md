# Fail-to-pass oracle corpus (mem-bxhh.2 / mem-bxhh.6)

Per-rig fail-to-pass (ftp) oracles produced by `membench curate-ftp <rig>` — the
per-rig curation tool from mem-bxhh.1. Each `<rig>.json` is the rig's sound
landing commits run through SWE-bench-style isolation: the landing test modules
run against both the parent and the landing tree, and `ftp = passes@landing AND
not passes@parent`. A commit is classified **behavioral** (gold test present and
failing at the parent — a real red→green) or **feature-presence** (gold test
collection-errors at the parent, i.e. the tested code did not yet exist).

Regenerate (the `.mem/` store is gitignored — rebuild it first):

```bash
# 1. fresh v8 store for the rig's work_records (work→landing-commit spine)
bin/mem build-store --rig <rig> --store .mem/store-bxhh2-v8.db
# 2. store-derived link-outcomes -> ftp isolation (no --commits handed in)
cd memory-bench && python -m membench.cli curate-ftp <rig> \
  --store ../.mem/store-bxhh2-v8.db --linkages canonical,unique \
  --out data/ftp-oracle/<rig>.json
```

`--linkages canonical,unique` keeps the unambiguous single-commit links (the
canonical `(<work_id>)` landing trailer, or the sole referencing commit) and
drops `multiple` (ambiguous — picks newest, so it can resolve the wrong tree).

## Corpus (2026-06-18)

| rig | lang | commits | ftp | behavioral | note |
|-----|------|--------:|----:|-----------:|------|
| `scix_experiments` | python | 29 | 341 | **25** | external anchor (mem-bxhh.2) |
| `codeprobe` | python | 6 | 107 | **7** | store-derivation leg (mem-bxhh.6) |
| `gascity_dashboard` | node | 0 | 0 | 0 | not curate-able — see below |
| `gpk` (gascity-packs) | config/prompt registry | 0 | 0 | 0 | not curate-able — see below |

32 behavioral ftp across 2 external Python rigs.

## Curate-ability is gated by the toolchain, not the linkage

The mem-bxhh.1 curator is **pytest-only** (it runs `pip install -e . pytest` and
parses `--junitxml`). Two rigs in the requested set yield no behavioral ftp for
structural reasons, not for lack of recovered links:

- **`gascity_dashboard` (node):** 215 sound landing commits resolve, but **0**
  touch a pytest module — the gold tests are `*.test.ts`, which the pytest
  curator does not collect. A node leg (`npm ci && npm test`) would be a separate
  curator extension.
- **`gpk` (gascity-packs):** a prompt/registry repo with no installable Python
  package at the root, so `pip install -e .` fails on the one test-touching
  commit. The curator now isolates that commit (`errored`) instead of aborting
  the rig, and reports 0 curate-able commits honestly.

`mem` is intentionally excluded — it is the self-repo and cannot anchor its own
eval.

## Downstream

This corpus feeds the eval anchor (mem-bxhh) and the synthetic-task calibration
(task E): the behavioral commits are the real red→green shapes a synthetic task
generator is calibrated against.
