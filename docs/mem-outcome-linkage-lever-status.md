# Outcome-Linkage: Both Levers Measured (CI enrichment + ambiguous-window recovery)

**Date:** 2026-06-18. Measured against `.mem/store-v7-linked.db` + `freeze/2026-06-17/`. Reproducible commands at bottom.
**Headline:** The "sound tier = 31 records (0.4%)" status was measuring the **wrong oracle**. The commit-trailer oracle already recovers **356 replayable bundles (285 with a test)** — and the CI freeze can verify 109 of them. The discriminating real anchor the synthetic-generator PRD needs plausibly **already exists in the substrate**; it was just never persisted as the headline tier or pointed at the eval.

## Lever A — CI enrichment (dashboard merged-PR/CI oracle)

- The freeze **exists**: `freeze/2026-06-17/dashboard-ci.raw.json`, 118 merged PRs, 112 with check-runs fetched. `dashboardCi.ts` (mem-wanz.5) reads it (by design, never a live `gh` call).
- Rolling up check-runs with the fail-closed rules: **109 CI-GREEN / 3 failure / 6 UNKNOWN(no-checks)**.
- So **109 PRs are T1-elevatable** (merge SHA + ci=pass). The store shows `ci = 0%` only because the `dashboardCi` pass output was **never persisted into `store-v7-linked.db`** — not because data is missing.
- **Action to realize:** run the `dashboardCi` elevation pass and persist `ci`/`link_tier=T1` for the matched records. Bounded by PR→work_record match (79 pr-link records today; up to ~109 via headRefName→bead-slug).

## Lever B — Ambiguous-window recovery

The 4,910 `ambiguous-window` records come from the **time-window** oracle (`landed.ts`), which bails when sessions overlap on a checkout. But the **commit-trailer** oracle (`commitLinkage.ts` + `validate-linked-bundles.mjs`) sidesteps the window entirely: it matches the `(work-id)` token the orchestrator wrote into each landing commit, then sets **base = the landing commit's parent** (exact by construction) and **gold = the landing diff**. No timestamp approximation, no overlap ambiguity.

| Oracle | Sound outcomes |
|---|---|
| Time-window (`landed_state='landed'`) | **31** (0.4%) |
| Commit-trailer (`validate-linked-bundles`) | **356 replayable bundles** |
| ...of those, carrying a test (fail-to-pass oracle possible) | **285** |

- Of the 4,910 ambiguous-window records, **444 (9%)** already carry a recovered `commit_sha` (the disambiguation key); the trailer oracle turns those + others into exact bundles.
- Near-zero rejects (1 merge, 1 root, 0 empty-diff, 0 diff-failed) — the recovered links are clean.
- **gascity (2,585 ambiguous, 0 with sha) is the big unrecovered mass** — its landing commits don't carry the recognizable trailer, so it stays unrecoverable by this method (a real ceiling, not a bug).

## Combined: the anchor substrate

| Rig | replayable | with test |
|---|---|---|
| gascity_dashboard | 233 | 192 |
| scix_experiments | 41 | 28 |
| gpk | 17 | 13 |
| codeprobe | 9 | 5 |
| mem (self-repo — **exclude** as self-referential) | 56 | 47 |
| **External total (no mem)** | **300** | **238** |

**238 external test-bearing replayable bundles across 4 rigs** — vs the **N=9 single-rig** pool the flat headlines (mem-apg.4 oracle−none=−0.007, mem-n9 1/24) were computed on. The flat results predate this recovery maturing.

## Honest caveats (what this does NOT prove)

- "Replayable + has a test" = a fail-to-pass oracle you **can score**. It is **necessary but not sufficient** for "discriminates memory configs." Whether memory *helps* on these is the open question — it requires actually running the 3-arm harness (none/ours/builtin) on the pool, graded.
- These 356 are sound TASK→OUTCOME links but are **not yet persisted as the store's scored tier**, and the eval harness has not been pointed at them.
- The CI-verified subset (Lever A) raises *integrity* (T1), not necessarily *discrimination*.

## Next step

Run the **anchor-existence check** on this 238-bundle external pool: do the 3 arms produce a non-flat config ranking with a CI clearing zero, under the graded metric? This is the experiment that decides whether the synthetic-generator Gate 0 is computable — and it is now runnable on a real, 26×-larger substrate than the flat N=9 pool.

## Reproduce
```
python3 -c "...CI rollup on freeze/2026-06-17/dashboard-ci.raw.json..."   # 109 green
node scripts/validate-linked-bundles.mjs --rigs gascity_dashboard,mem,scix_experiments,codeprobe,gpk
```
