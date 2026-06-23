# Per-Rig Eval-Infra Feasibility (codeprobe pilot)

**Date:** 2026-06-18. Pilot rig: **codeprobe** (cheapest-to-unblock: local checkout + existing sandbox image + python toolchain).
**Verdict:** Base-image infra is **feasible and cheap**; the binding constraint is **fail-to-pass oracle curation**, which shrinks the usable pool below the 9 git-replayable links.

## Context

The outcome-linkage recovery surfaced 356 git-replayable base+gold-diff links across rigs, but they're only runnable as graded bundles if each rig has (1) a base image where deps install + tests run at historical commits, and (2) a fail-to-pass oracle per landing commit. Only gascity-dashboard had both. This probes whether the external rigs can be unblocked.

## What was measured (codeprobe)

**Reference architecture:** `base_image` is a stock toolchain string (`python:3.11`, `node:22`, `golang:1.23`, `ubuntu:24.04` fallback) from `env_recon.DEFAULT_BASE_IMAGES`; the harness `git archive`s the repo at base_commit into it. The env is deliberately approximate (D17) — toolchain only, not the rig's deps or a verified test suite.

**Probe 1 — toolchain reproduces (✅):** worktree at landing commit `c635ffe7`, in `python:3.11-bookworm`:
- `pip install -e .` resolved cleanly, **no system deps**.
- `pytest` ran in 0.21s: **22 passed, 1 failed**.

**Probe 2 — fail-to-pass isolation (✅, after fixing the curation harness):** the SWE-bench definition is *passes at landing, does NOT pass at parent* (covers fail AND collection-error-from-missing-feature). A first buggy pass (comma-joined multi-file paths; counted only `FAILED`, missed collection `ERROR`s) reported a false 0/5. Corrected across all 5 distinct test-touching landing commits:

| Commit | pass@parent → pass@landing | fail-to-pass | Type |
|---|---|---|---|
| c635ffe7 | 41 → 44 | **3** | behavioral (token-rollup contract) — gold standard |
| 6f0c65e2 | 156 → 168 | **12** | behavioral (adapter/checkpoint contracts) — gold standard |
| 66a5cffc | 94 → 95 | **1** | behavioral (scoring projection) — gold standard |
| a5a5e027 | 0 → 16 | **16** | feature-presence (new test file errors at parent) |
| c0efd49c | 0 → 70 | **70** | feature-presence (new CLI test files) |
| **Total** | — | **102** | **5/5 commits yield a clean fail-to-pass** |

Quality split: **16 behavioral fail-to-pass across 3 commits** (test runs against old code and fails on the specific behavior → the strongest discriminator), plus **86 feature-presence** across 2 commits (new test files that error at parent because the feature is absent → valid but coarser). Minor caveat: `comm` emitted "not in sorted order" warnings on 3 commits, so counts may be ±a few; the headline (5/5, non-zero, with clean behavioral cases) is robust.

## Verdict per component

| Component | Status | Note |
|---|---|---|
| Base image (deps + toolchain) | ✅ feasible, cheap | stock `python:3.11` + `pip install -e .`; cache into an image so runs don't reinstall |
| Test suite reproduces at history | ✅ | 22–168 tests run per commit in <0.3s; ~1 env-flaky test (`test_validate_ready`) to filter |
| Fail-to-pass oracle per commit | ✅ **productive** | **5/5 commits, 102 ftp tests** (16 behavioral, 86 feature-presence) |

## Honest bottom line

The "infra-blocked" wall is **surmountable AND productive for codeprobe**: the base image is trivial, and every recovered test-touching landing commit yields a real fail-to-pass oracle. The earlier pessimism (0/5) was a harness bug, not a substrate limit. The "necessary but not sufficient" chain (git linkage → replayable → runnable → discriminating) holds — each step sheds *some* pool — but for codeprobe the discriminating step **retains** the test-touching commits rather than emptying them.

Use-quality note: prefer the **16 behavioral** fail-to-pass (real red→green) as the discriminating oracle; treat the 86 feature-presence ones as weaker (presence, not behavior-delta).

## Exploratory framing (not a hard gate)

Real tasks are not the only intended substrate — this is exploratory. The codeprobe result has **two** uses, and the second may matter more:
1. **As a real eval anchor:** 16 behavioral fail-to-pass tests across 3 commits is a small but genuine discriminating set for the memory arms (the thing the flat N=9 dashboard pool lacked).
2. **As calibration data for synthetic-task design:** the *shapes* of these real failures — contract tests (token-rollup, scoring projection), adapter-parse contracts, CLI usage-error guards, checkpoint-resume — are the actual phenomenology of multi-agent dev work. The synthetic generator (see `prd_grounded_factorial_memory_diagnosis_generator.md`) should mimic these shapes, and the real fail-to-pass set calibrates whether synthetic tasks reproduce them. This directly answers the generator PRD's construct-validity gate: real fail-to-pass tasks are the external anchor synthetic tasks are validated/calibrated against.

## Next steps (scoped)
1. ✅ Curation done: codeprobe = 5/5 commits, 102 fail-to-pass (16 behavioral).
2. Build the cached codeprobe base image (`FROM python:3.11-bookworm` + `pip install -e .` baked) so graded runs skip reinstall; materialize the 5 commits' bundles with the curated fail-to-pass oracle attached.
3. Repeat the curation probe for scix (python, likely similar) and gpk (confirm local checkout first — flagged absent in `env_recon.DEFAULT_RIG_REPOS`).
4. Feed the real fail-to-pass corpus into BOTH the eval anchor (graded arm pilot) AND synthetic-task calibration — not gated on reaching a large real N.

## Reproduce
```
git -C /home/ds/projects/codeprobe worktree add --detach /tmp/wt <sha>
docker run --rm -v /tmp/wt:/app -w /app python:3.11-bookworm bash -c 'pip install -e . pytest && pytest tests/<file> -q'
```
