# mem-apg.7 — convoy/epic multi-session carving: pool-construction result

Run 2026-06-13. Execution of the city-native lever Stephanie authorized (Slack
2026-06-13): extend the multi-session population to fanned-out convoys / bead
epics so convoy work counts as multi-session task-bundles instead of being
rejected wholesale. This is the direct follow-on to mem-apg.6
(`docs/mem-apg.6-multi-session-feasibility.md`), which found the **flat**
alias-guarded multi-session pool admits N=2 (both `codeprobe`, 0
gold-test-anchorable) and declared the graded 3-arm grid *not constructible*.

## Headline

**The convoy/epic carving lever takes the admitted pool from N=2 to N=6, and —
the part that matters — from 0 gold-test-anchorable to 4.** All four new
admissions are `gascity_dashboard` carves: focused fanout-2/4 sub-bundles whose
gold diffs each carry multiple gold **test** files, on the `node:22-bookworm`
toolchain base image. This **overturns mem-apg.6's "not constructible" verdict**:
a graded multi-session 3-arm grid is now constructible at N=4 anchored bundles
on the current rig roster, with no external benchmark repos.

| | mem-apg.6 (flat) | mem-apg.7 (convoy-epic) |
|---|---:|---:|
| admitted N (post fanout-guard) | 2 | **6** |
| — flat (per-work_id ≥2 agents) | 2 | 2 |
| — convoy/epic-carved (new) | 0 | **4** |
| gold-test-anchorable | **0** | **4** |
| anchorable rig | — | `gascity_dashboard` (`node:22-bookworm`) |

The 2 flat admissions are unchanged (`codeprobe-3l6tb`, `codeprobe-g1cp2` — one
docs-only, one on the `ubuntu:24.04` fallback; still 0 anchorable, exactly as
mem-apg.6 reported). **Every gain is attributable to the lever**: all four new
admits are convoy/epic-only (each touched by a *single* agent on its own bead, so
invisible to the flat per-work_id definition; surfaced only by the group-level
≥2-agent count across the shared issue).

## What "carving" means here

A fanned-out milestone (e.g. `gascity-dashboard-uzhr`, *"M2: project readOnly
into shared DashboardRuntimeConfig + disable SPA mutating controls"*) was
dispatched as many independent `mol-focus-review` sessions, each producing its
own focused gold diff. mem-apg.6's flat definition counts distinct agents
**per work_id**, so a 32-session milestone whose siblings each had one agent did
not register as multi-session at all — only the one bead worked twice
(`gascity-dashboard-86kwb`) got in, and the fanout-scope guard correctly rejected
it (its issue leg over-describes a one-file slice).

The lever changes the **population definition**, not the admission integrity:

- **Carving = group-level eligibility.** A work_id is convoy/epic multi-session
  iff its `gc.var.issue` group spans ≥2 distinct non-suspect agents in total
  (`select_rank.py --ms-population convoy-epic`; `CONVOY_EPIC_SQL`). This admits
  the individual member beads — the per-bead replayable slices — as candidates
  rather than discarding the whole fanout. The extension is **additive** (never
  drops a flat candidate) and preserves the alias guard (`suspect=0`).
- **The scope guard is still the carving validity gate.** Each carved member
  flows through the unchanged mem-75t.7.7 `fanout_scope_guard`: its
  `claude -p` scope-match judge keeps a member whose gold diff coheres with the
  issue's scope and rejects one whose issue over-describes the slice. The judge
  reads the issue **title** + gold-diff file list (this corpus ingests bead
  titles only — `issue_body` is empty on all 7,381 records), the same contract
  mem-75t.7.7 has always used.

No new admission machinery was added: the lever is one population-SQL extension
plus the existing guard. (An empty-body mechanical pre-gate was prototyped and
discarded — it would have rejected the focused fanout-2 carves the judge
legitimately keeps, regressing mem-75t.7.7 and undercounting N.)

## The pipeline, stage by stage (convoy-epic restriction)

Live store: the converged mem-qw5 expansion (7,381 records). Run via
`select_rank.py --multi-session --ms-population convoy-epic` →
`assemble_batch.py` → `admit_batch_guarded.py` (real `claude -p` scope judge).

| stage | tool | result |
|---|---|---|
| flat multi-session population | `record_agents`, `suspect=0`, ≥2 distinct agents per work_id | 1,974 |
| convoy/epic group members | ≥2 distinct non-suspect agents across the `gc.var.issue` group | 150 |
| **convoy-epic population (union)** | flat ∪ group members | **2,068** (+94 new) |
| bundle-eligible (`trace∧base_commit∧closed`) ∩ population | `select_rank --ms-population convoy-epic` | **90** (flat 46 + 44 new) |
| non-zero mutation (replayable) | `top_candidates` (mut>0) | 54 processed |
| assemble | `assemble_batch.py` | **13 admitted** (pre-guard) |
| fanout scope guard | `admit_batch_guarded.py` (real judge) | **6 admitted** |
| gold-test-anchorable under current infra | gold diff carries a test + non-fallback toolchain image | **4** |

### Scope-guard verdicts (the carving in action)

The judge reviewed all 11 fanout-≥2 bundles and **discriminated** — it is not
rubber-stamping:

**Admitted (4 carved, all `gascity_dashboard`, all gold-test-bearing):**

| work_id | issue (focused) | fanout | gold files / tests | replay |
|---|---|---:|---|---:|
| `6yy76` | M1: server-enforced read-only proxy gate | 4 | 12 / 7 | 0.95 |
| `2a7lh` | Health tab: recommended-versions panel | 2 | 9 / 3 | 1.00 |
| `4lf62` | R11+R13: pending-decision in-place accept/decline | 2 | 15 / 5 | 0.94 |
| `yz7x1` | Runs tab: load race + stale blocked latch | 2 | 12 / 4 | 1.00 |

**Rejected (7):** the five `uzhr` milestone members (fanout 32 — *"a single
logging test file does not implement the issue's shared-config projection plus SPA
control changes"*), plus two fanout-2 confounds the judge caught
(`c95q2`/`bqey`: issue spans R5 ranking **and** R17 dedup, diff covers one;
`km0wj`/`035r`: issue bundles three distinct requirements).

## Are the 4 gold-test-anchorable?

Yes, by the same bar mem-apg.6 used to call `gascity-dashboard-86kwb`
"gold-test-bearing **and** toolchain-having": (a) the gold diff includes real
test files (TypeScript `*.test.ts`/`*.test.tsx`, 3–7 per bundle), and (b) the
`gascity_dashboard` rig has a non-fallback toolchain base image
(`node:22-bookworm`), unlike `codeprobe`'s `ubuntu:24.04` fallback. The four
bundles span four **distinct** focused issues (`z8n7`, `nfyw`, `gye8`, `4xcv`),
so the anchored set is not one issue replayed.

**Caveat (honest boundary).** "Anchorable" here means the env + gold-test
*exist on a real toolchain image*. The **live gold-test repro** (`npm ci && test`
inside `node:22-bookworm`) and the CSB validity gate are *not executed in this
run* — that is mem-apg.6 scope items 2–4 (standing up and running the grid),
explicitly separate from this feasibility check. This run establishes the pool;
it does not yet run the arms. The judge also scored on **title-only** scope
(empty bodies), the established mem-75t.7.7 contract — richer issue bodies would
sharpen the scope decision but are not ingested today.

## Conclusion

**N is now adequate to stand up the graded multi-session 3-arm grid.** The
binding constraint mem-apg.6 hit — 0 gold-test-anchorable bundles — is resolved:
the convoy/epic lever yields **4 anchored, focused, single-issue bundles on a
real toolchain**, all from gas-city's own exhaust. Re-running the graded 3-arm
grid over these 4 (ours / built-in / none-clean) is mem-apg.6 scope items 2–4 and
proceeds separately.

Two honest qualifications on size and reach:

1. **N=4 is a floor, not a headline-grade sample.** It clears "constructible"
   (>0 anchored) but is below the ~10-bundle target the mem-75t.7.6 probe used;
   per-bundle paired deltas on 4 bundles carry wide intervals. It is enough to
   *run* the grid and report a directional signal, not to settle the headline.
2. **The reach is still clone-bound, as mem-apg.6 found.** Of the 90-candidate
   convoy-epic pool, the richer issue-group members sit in unwired rigs
   (`CodeScaleBench`, `scix_experiments`, `gpk`, `EnterpriseBench` — 17
   `NO_RIG_CLONE` this run). Wiring those (mem-e3h2) remains the lever that would
   take N from a floor to a headline-grade sample. The convoy/epic extension and
   the clone-wiring lever compose: this run shows the population is there; the
   clones are what cap how much of it is runnable.

## Independent re-mine verification (mem-apg.7 review gate)

An independent re-run of the full pipeline (same live store, same 54 non-zero-
mutation candidates) confirms the core result and pins down its one moving part.
The flat/carved split, the five `uzhr` rejections, the `node:22-bookworm`
anchoring, and the clone-bound reach all reproduce exactly. The admitted count,
however, is **judge-variance-sensitive at one bundle**: the re-run admitted a
fifth carve, `km0wj` (issue `035r`, fanout 2), which the run above rejected.

- **Swing bundle: `km0wj`/`035r`.** The `claude -p` scope judge read its 14-file
  diff as *one cohesive attention subsystem* (compose/registry/routeHighlight/
  panel + tests → ADMIT) on the re-run, and as *three distinct requirements* →
  REJECT above. Same input, different verdict — the documented non-determinism
  of the scope seam, on a genuinely borderline candidate.
- **Reconciled range: N = 6–7 admitted, 4–5 gold-test-anchorable.** Every other
  verdict is stable across runs; `c95q2`/`bqey` rejects in both.

This **strengthens** the conclusion rather than weakening it: the headline (lever
overturns mem-apg.6's 0-anchorable null; ≥4 focused, single-issue, toolchain-
anchored carves from gas-city's own exhaust) holds under judge variance. It also
reinforces the "N is a floor, not a headline-grade sample" qualification — with
the count swinging ±1 on a single borderline judge call at this size, the case
for widening the pool via clone-wiring (mem-e3h2) is unchanged. The
implementation (`select_rank.py` population SQL, alias guard, read-only store)
verified clean under independent code review; one test-fixture robustness fix
(`json.dumps` for the synthetic store) landed alongside this note.

## Reproduce

```bash
cd memory-bench
PYTHONPATH=. python scripts/select_rank.py --multi-session --ms-population convoy-epic \
  --json-out ../.mem/select-ranking-ce.json --report-out /tmp/sr-ce.md
PYTHONPATH=. python scripts/assemble_batch.py --ranking ../.mem/select-ranking-ce.json \
  --bundles-dir ../.mem/bundles-ce --report-out /tmp/asm-ce.md --limit 54
PYTHONPATH=. python scripts/admit_batch_guarded.py --bundles-dir ../.mem/bundles-ce \
  --manifest ../.mem/grid-ready-pool-ce.json --report-out /tmp/guard-ce.md --write
```

Artifacts: `.mem/select-ranking-ce.json`, `.mem/bundles-ce/*.json`,
`.mem/grid-ready-pool-ce.json` (all gitignored).
