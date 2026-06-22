# Gate-0 Probe (mem-72sj): Does the 356-bundle commit-trailer anchor rank memory configs NON-FLATLY?

**Status:** DECIDED — Stephanie accepted the honest-null (decision fork option **(a)**, 2026-06-21).
Numbers HELD (publication freeze), branch-ready, not pushed. No fresh dashboard grid (b) and no
scix/codeprobe runner build (c) under this bead.
**Bead:** mem-72sj (gates `prd_grounded_factorial_memory_diagnosis_generator` + `mem-hd7f`).
**Outcome:** the factorial PRD `prd_grounded_factorial_memory_diagnosis_generator` stays
**NO-GO / CONDITIONAL** pending forward-capture; corpus-expansion redirect is now the
forward-capture epic **mem-31kz**.
**Conditions honored:** FREE (OAuth `claude -p` + same-family `claude -p` judge, subscription seam, no paid API), reuse-only, numbers HELD.

## Question

The grounded-factorial memory-diagnosis PRD is CONDITIONAL on a Gate-0 that is uncomputable if
the real corpus is flat: **does MEM have a real task set that ranks memory configs
(none-clean / ours / builtin) non-flatly** (rank-correlation ρ ≥ 0.6, span clearing zero)? The
outcome-linkage lever doc found the candidate anchor plausibly already exists: **356 commit-trailer
replayable bundles (285 with tests, 109 CI-verifiable)** — never pointed at the eval.

## Verdict

**FLAT / Gate-0 uncomputable on the only runnable real anchor (gascity_dashboard)** — the
honest-null branch the bead anticipated, not a GO. Two findings carry it.

## Finding 1 — Structural: the runnable graded oracle is single-rig

**The reuse-only graded harness on this branch can score a pass/fail oracle for exactly two rigs:
`gascity_dashboard` and `mem`.** `membench/harbor/repro_live.py` `RIG_TEST_CONFIGS` (the
`LiveReproRunner` producing the load-bearing `repro_passed` / `score_direct` anchor) is defined
only for `gascity_dashboard` (npm) and `mem` (self-repo, excluded as self-referential). Any other
rig falls back to diff-similarity — **no real fail-to-pass oracle**.

| Rig | replayable / test-bearing (anchor) | reuse-runnable graded oracle here? |
|---|---|---|
| gascity_dashboard | 233 / 192 | ✅ (npm vitest/tsx) |
| scix_experiments | 41 / 28 | ❌ no RigTestConfig (pytest unwired) |
| gpk | 17 / 13 | ❌ no RigTestConfig |
| codeprobe | 9 / 5 | ❌ no RigTestConfig (pytest unwired) |
| mem (self) | 56 / 47 | excluded (self-referential) |

The discriminating candidate rigs (codeprobe's behavioral red→green tests, scix's 25 behavioral
ftp in `memory-bench/data/ftp-oracle/`) exist only as a curated oracle listing, not a runnable
graded `RigTestConfig`. The pytest repro runner that would score them (`FtpReproRunner` /
`run_grid_3arm_ftp.py`, mem-bxhh.3.3) lives in an **unmerged worktree**, not on this branch.
Wiring it = new eval/harness logic, which this bead forbids. **So the only sample of the 356
anchor this harness can grade with a real oracle is `gascity_dashboard` — the same rig family the
flat apg.4 (oracle−none = −0.007) and n9 results came from.**

## Finding 2 — Measured: the runnable dashboard anchor is flat on the outcome metric

Source: `.mem/grid-n8/summary-3arm-graded.json` — 8 gascity_dashboard bundles, `claude-sonnet-4-6`
agent + 3-round Sonnet rubric judge, CSB validity-gated (0 excluded), 2026-06-17. `ours` genuinely
retrieved on 4/8 bundles (matched set: 4lf62, dh0gt, g0pff, i1bo2; 87–116 matched items each); the
other 4 had empty retrieval (degenerate none-clean reuse, correctly isolated by `coverage_matched`).

Per-arm means (full 8-bundle pool):

| arm | score_direct | repro_passed | test_ratio | diff_sim | judge_score |
|---|---|---|---|---|---|
| none-clean | 0.000 | 0.000 | 0.042 | 0.081 | 0.531 |
| ours | 0.000 | 0.000 | 0.067 | 0.104 | 0.550 |
| builtin | 0.125 | 0.125 | 0.192 | 0.109 | 0.550 |

On the **matched set** (4 bundles where `ours` truly retrieved), paired deltas vs none-clean:

- `score_direct`: **[0,0,0,0]**, mean 0.0 — zero outcome-anchor lift (ours AND builtin).
- `repro_passed`: **[0,0,0,0]**, mean 0.0.
- soft signals only: `diff_sim` mean +0.046 (ours, 4/4 > base), `judge_score` mean +0.0375 (1/4),
  `test_ratio` +0.05 (1/4); efficiency lower for ours but sign-mixed.

The single nonzero outcome cell is `builtin` passing **one** bundle (acda2) — and acda2 is a
bundle where `ours` retrieval was **empty**, so it is a baseline-condition pass, not
memory-attributable. No arm achieves outcome-anchor lift over none-clean.

### The apparent "non-flat" pass was an R1 noise artifact — found and fixed

`scripts/gate0_nonflat_probe.py` (untracked prep artifact) initially reported
`verdict_non_flat=True, ρ=1.0` on this data. **False positive:** half_a (containing acda2) ranks
builtin>none≈ours; **half_b is entirely flat (all arms 0.0)** and `_ranking_order`'s alphabetical
tiebreak coincidentally matched half_a → ρ=1.0. A flat half has no real ranking — correlating its
tiebreak is exactly the PRD R1 spurious-pass the guard was meant to catch.

**Fix:** per-split-half flat-anchor guard (`_is_flat`): if either half has no arm separation
(max−min mean < `DEGENERATE_SPAN`), ρ is undefined (`None`), never computed off the tiebreak.
Post-fix verdict on the n8 data: correctly **FLAT** (`split_half_flat=True`, ρ=None, span 0.125
driven by one non-attributable bundle). Regression test `tests/test_gate0_nonflat_probe.py`
(3 cases: real n8 shape, genuine non-flat pass, degenerate full span). ruff/black/mypy(membench)/
pytest green. Report: `.mem/grid-72sj/gate0-report-n8.json`.

## Bottom line

The discriminating real anchor the factorial PRD's Gate-0 needs is **not demonstrated** by the
runnable substrate. The single runnable rig (dashboard) is flat on the outcome metric under the
graded instrument — consistent with apg.4 and n9. The honest-null ("real agent-memory corpora are
signal-poor; here is the spec a discriminating corpus must meet") is the supported headline on
present evidence.

## Decision (Stephanie, 2026-06-21): accept the honest-null — option (a)

**Resolved: (a).** The honest-null from existing legit graded data (this report) is the Gate-0
answer. The factorial PRD `prd_grounded_factorial_memory_diagnosis_generator` stays
**NO-GO / CONDITIONAL pending forward-capture**; the corpus-expansion redirect is the
forward-capture epic **mem-31kz**. Options (b) and (c) were considered and **declined under this
bead**:

- **(a) Accept the honest-null** — ✅ **CHOSEN.** Lowest cost, supported by present evidence.
- **(b) Confirmatory fresh grid** on the 5 gold-test-anchorable dashboard bundles (2a7lh, 4lf62,
  6yy76, yz7x1, km0wj), free OAuth + Claude judge, ~multi-hour. **Declined** — low expected info
  gain (re-tests dashboard, already flat). (Would have needed to serialize after the mem-pjh8.2
  trusted N=8 run; both lean on OAuth `claude -p` and concurrent runs risk usage-limit 429s the
  harness classifies as EmptyRunError.)
- **(c) Build pytest `RIG_TEST_CONFIGS` for scix_experiments + codeprobe** — the only path that
  could change the verdict (true multi-rig breadth). **Declined under this bead** — new harness
  code beyond scope; would need a separate bead + the rigs cloned + the unmerged `FtpReproRunner`
  landed. Re-openable later if forward-capture (mem-31kz) does not yield a discriminating corpus.

## Reproduce
```bash
cd /home/ds/projects/mem/memory-bench
uv run python scripts/gate0_nonflat_probe.py \
  --summary ../.mem/grid-n8/summary-3arm-graded.json \
  --out ../.mem/grid-72sj/gate0-report-n8.json

# Fork (b) confirmatory fresh grid (gated on trusted-N8 finishing):
export CLAUDE_CODE_OAUTH_TOKEN=$(python3 -c "import json;print(json.load(open('$HOME/.claude/.credentials.json'))['claudeAiOauth']['accessToken'])")
M=/home/ds/projects/mem/.mem
uv run python scripts/run_grid_3arm_graded.py \
  --bundles-dir $M/bundles-ce --manifest $M/grid-ready-pool-anchorable.json \
  --probe-dir $M/probe-ce --grid-dir $M/grid-72sj \
  --store $M/store-bxhh2-v8.db --mem-bin /home/ds/projects/mem/bin/mem \
  --cli-version 2.1.185 --judge-rounds 1
uv run python scripts/gate0_nonflat_probe.py --summary $M/grid-72sj/summary-3arm-graded.json
```
