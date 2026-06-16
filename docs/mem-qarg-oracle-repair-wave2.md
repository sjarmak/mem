# mem-qarg — Oracle-repair wave 2: materialize the native pool through the soundness gate

Continues the oracle-repair-first lever (mem-2hdb; Stephanie 2026-06-15) on top of the
mem-1eph pre-admission soundness gate (branch `mem-1eph-oracle-soundness-gate`, commit
f93310f). mem-1eph re-carved the 10 already-materialized dashboard bundles → 3 sound.
This wave materialized the **full native ranked pool** and ran it through the two-stage
gate (scope + CSB oracle soundness) to report a cumulative sound-oracle denominator.

Reproduce (run from `memory-bench/`, on the mem-1eph gate):

```
PYTHONPATH=. python scripts/assemble_batch.py --limit 113 \
  --bundles-dir /home/ds/projects/mem/.mem/bundles-qarg --report-out <...>
PYTHONPATH=. python scripts/admit_batch_guarded.py --write \
  --bundles-dir /home/ds/projects/mem/.mem/bundles-qarg \
  --manifest /home/ds/projects/mem/.mem/grid-ready-pool-qarg.json --report-out <this>
```

Isolated artifacts (gitignored data, not the shared live pool):
`.mem/bundles-qarg/` and `.mem/grid-ready-pool-qarg.json`.

## Headline: cumulative native sound-oracle yield = 8

| stage | count |
|---|---|
| ranked native candidates (SELECT) | 113 (105 dashboard / 4 mem / 2 gascity / 2 gpk) |
| with non-zero mutation signal (assembler input) | 73 |
| **assembled** (admissible TaskBundle) | **23** (22 dashboard + 1 mem) |
| scope-admitted (stage 1) | 18 |
| **oracle-sound + grid-ready (stage 2)** | **8** (all dashboard) |

8 sound oracles, up from 3 in mem-1eph's first re-carve (5 new: `acda2`, `dh0gt`,
`g0pff`, `i1bo2`, `yqu2p`; the original 3 were `4lf62`, `j18zz`, `jai2y`).

**The binding constraint is bundle ASSEMBLY, not the soundness gate.** Of 105 ranked
dashboard candidates only ~22 assemble into an admissible bundle; the other ~83 are
typed-rejected upstream (`base_predates_tree` 16, `low_replay_fidelity` 10,
`empty_output` 10, `shared_trace` 8, `dirty_trace_tail` 5, `no_rig_clone` 1). The
"~95 untested dashboard candidates" runway was real as candidates but collapses to ~22
assembled bundles — the corpus simply does not hold many more replayable single-bead
coding tasks for this rig.

## Broken-oracle taxonomy (the 10 stage-2 rejects) + dispositions

Diagnosed with a live per-test capture (`validity_gate` over `LiveReproRunner`). Every
failure is a **corpus-extraction / decomposition limitation, not a repairable test
fixture** — so per the bead ("if genuinely not a benchmarkable change, discard with
reason; do NOT force it") these are discarded, not hand-patched.

| mode | bundles | root cause | disposition |
|---|---|---|---|
| gold-not-reproducing — replay hunk drop | `zhy00`, `8n3to`, `ytvbs` | replay `old_string_missing`: the agent's sequential edits don't rebase onto base_commit, so impl/test hunks are dropped → gold diff incomplete → gold tests fail | discard; recoverable only by replay-engine work |
| gold-not-reproducing — cross-workspace truncation | `e9y0d` | replay `outside_work_dir`: real edits to `backend/test/…` + `shared/src/…` dropped because work_dir inference rooted at `frontend/`; the cross-workspace task is truncated | discard; needs multi-workspace work_dir inference |
| gold-not-reproducing — shared test over-coverage | `tkhkg` | gold test `Beads.render.test.tsx` asserts features (dependency nav, writable controls, supervisor-failure) whose impl is in **sibling beads**; this bundle carries only a slice → 4/10 tests fail under gold | discard; the fanout-scope confound at the test layer |
| empty-passing (not fail-to-pass) | `km0wj` (ratio 0.6), `bkpp3` (empty fully reproduces) | some/all gold tests pass on the empty diff → the gold tests are not actually fail-to-pass for this slice | discard; "which tests are the oracle" is a lift-definition call → mem-pl |
| gold carries no test files | `hrtgt`, `ubt45` | the replayed gold diff has no test file at all → no possible fail-to-pass oracle | genuine discard (untestable change) |
| no rig test config | `mem-us6j` | `repro_live.RIG_TEST_CONFIGS` only maps `gascity_dashboard`; the `mem` rig has no test command wired | infra gap, not a broken oracle (see below) |

## mem / gascity rigs: blocked on a missing RigTestConfig (infra gap)

The bead scoped "the mem (~4) / gascity (~2) pools" too, but: gascity/gpk contributed
**0** assembled bundles (gpk `no_rig_clone`; gascity candidates did not survive
assembly), and the **1** mem bundle that did assemble (`mem-us6j`) cannot be
oracle-validated because `repro_live.RIG_TEST_CONFIGS` only carries
`gascity_dashboard`. Wiring a `mem` `RigTestConfig` would validate at most that one
bundle — below the ROI bar for this wave, and a per-rig harness extension rather than
oracle repair. Noted, not attempted.

## Decision needed (the bead's stop condition)

Native sound-oracle yield is **8** dashboard bundles after the full pool + repair
triage. Repair cannot cheaply raise it: the broken oracles are corpus/decomposition
limitations whose only recovery lever is replay-engine improvement (sequential-edit
rebasing + multi-workspace work_dir inference) — a separate, substantial piece, not
in-scope here. Whether N=8 clears a defensible headline for the 3-arm graded grid (vs.
apg.9's N=2) is a validity/scope call: **does the scix robustness arm (mem-e3h2) become
load-bearing?** Flagged to mem-pl per the bead's stop instruction. HALT at branch-ready.
