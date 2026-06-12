# mem-apg.3 — Ablation grid execution: efficiency-vs-information over the admitted pool

Run 2026-06-12. The headline grid per the resolved decision (mem-bfk → dec-gck
option a): **efficiency-vs-information is the sole mem-apg axis**, scored on task
bundles with the dual verifier (mem-75t.7.5) — efficiency leg (tokens / turns /
tool calls) as headline, gold-test reproduction as the quality guard — reported as
**per-bundle paired deltas, never pooled means** (mem-75t.7.6 gate instruction).

Pool: the 9 fanout-guard-admitted bundles (`.mem/grid-ready-pool.json`,
mem-75t.7.7; e29gw rejected). Agent runs: the gate probe's cached real executions
(20 Docker/OAuth Claude runs, 2026-06-11) — this grid adds NEW scoring, not new
agent runs: every (bundle, condition) candidate was re-harvested from its persisted
transcript and scored by the dual verifier with the **live gold-test repro runner**
(`membench/harbor/repro_live.py`, built this session). Artifacts:
`.mem/grid/<work_id>.<condition>.json`, `.mem/grid/summary.json`.

## Repro-harness validation (before any scoring)

On 4lf62's base commit: gold tests against the full gold diff → **58/58 pass**;
gold tests against the bare base → **5/5 test files fail**. The fail-to-pass
instrument discriminates. In the grid itself, 17/18 runs scored in primary
`test_repro` mode; the single fallback (km0wj oracle) is a run that made **zero
replayable file edits** (42,330 output tokens of exploration, empty candidate
diff → diff-similarity 0.0 by definition, reason recorded).

## Per-bundle paired deltas (oracle − none)

Efficiency (headline): negative = the file-list oracle rung was cheaper.

| bundle | Δturns | Δtool-calls | Δout-tokens | Δin-tokens | repro none→oracle | Δartifact-F1 |
|---|---:|---:|---:|---:|---|---:|
| 4lf62 | −26 | −32 | −1,656 | +2,702 | F→F | −0.083 |
| 8n3to | +5 | +8 | −870 | −829 | F→F | −0.158 |
| e9y0d | **−116** | −110 | −542 | −730 | **P→P** | −0.109 |
| j18zz | +8 | +3 | −4 | −14,410 | F→F | +0.067 |
| jai2y | −24 | −14 | −821 | −1,356 | F→F | +0.029 |
| km0wj† | +63 | +16 | **+36,619** | +422 | F→(no edits) | 0.000 |
| tkhkg | **+138** | +88 | +6,789 | +4,167 | F→F | −0.067 |
| ytvbs | −78 | −58 | −3,128 | −78 | F→F | 0.000 |
| zhy00 | **+212** | +149 | +7,670 | +216 | F→F | +0.190 |

† km0wj survived the fanout guard (admitted at fanout 2) but reproduces its gate
confound: the oracle arm burned 42k output tokens and edited nothing.

Aggregates (read direction per metric; medians less km0wj-dominated than means):
output tokens median **−542** (oracle cheaper on 6/9), turns median **+5** (oracle
fewer on 4/9), mean Δout-tokens +4,895 — dominated by km0wj/zhy00/tkhkg where the
hint induced *more* work. The effect is **bundle-conditional in both magnitude and
sign**, exactly the .7.6 finding, now confirmed under dual-verifier scoring.

## Quality guard: gold-test reproduction is FLAT across arms

- Repro pass: **none 1/9, oracle 1/9 — the same bundle (e9y0d), Δ = 0 on every
  pair.** The cheap file-list rung neither buys nor costs gold-test quality
  anywhere in the pool.
- The guard therefore holds in both directions: no quality regression hides
  behind the efficiency deltas, and no quality gain rides them. What the
  information rung moves on this pool is **where the effort goes** (tokens/turns),
  not whether the gold tests pass.
- e9y0d passing on BOTH arms (including zero-memory) plus 8/9 failing on both
  says gold-test repro has little *headroom between these two rungs* — the
  none-rung agent on this rig already reads the issue text well enough that a bare
  file list cannot flip test outcomes. This echoes the .7.6 provisional read
  ("quality flat, efficiency clear") with a harder instrument.

## Rung availability (what the information ladder can execute today)

| rung | status |
|---|---|
| none | executed (cached real runs) |
| oracle (file list) | executed (cached real runs) |
| **ours** | **not executable — structurally empty**: the store carries **0 lessons** (no distiller has ever run; `.mem/lessons-export-v3.jsonl` is empty), and retrieval-v1 returns 0 items for 7/9 bundles, citation-only items (no lesson payloads) for 2/9 (4lf62: 10 items, km0wj: 10 items, all `lessons: []`). The `ours` arm injects citation+lessons only (D9), so the injected payload would carry no information — running it would burn ~9 agent runs to measure noise, the exact dead-axis-by-construction trap mem-apg.3.1 documented. Per-bundle evidence in `summary.json → rung_availability.ours.evidence`. |
| curated | degenerate: single consensus backend collapses curated context to the gold-diff file list ≡ the oracle condition (docs/mem-75t.7.3); needs a second backend (SG indexing or TS-AST) |
| builtin, ours+builtin | deferred to mem-whi (agent built-in memory, paid Harbor path) |

Curve readouts: with two executable rungs, `floor_lift`/`ceiling_gap` (need
`ours`) and `saturation_point`/`min_useful_combo` (need ≥4 rungs, architect H2)
are correctly REFUSED by `grading/curve.py` rather than fabricated. The grid's
headline artifact at this ladder depth is the per-bundle paired-delta table above.

## What unlocks the next rungs

1. **ours** — a lessons distiller over the work-audit corpus (D9 lessons are
   produced externally and imported; none exists). Follow-up bead: mem-uts.
2. **builtin / ours+builtin** — mem-whi.
3. **curated as a distinct rung** — a second oracle backend for consensus.

The grid machinery (`scripts/run_grid.py`, resumable; `bundle_grid.py` +
`repro_live.py`, tested) takes any new condition the moment its payload exists —
the marginal cost of a new rung is 9 agent runs plus zero new scoring code.
