# mem-apg.8 — CSB / EB / scix methodology check: are these records real dev-work or eval-noise?

Run 2026-06-13. Carved from mem-e3h2 step 3 (Stephanie, Slack 2026-06-13: "do
both checks"). This is the **gate** on whether external benchmark clones are ever
wired as rigs; mem-e3h2 steps 1/2/4/5 (register clones, build base images, admit)
stay gated on this finding. **Report only: no wiring, no base images, no admit,
read-only on the clones, no corpus mutation.** The legitimacy call per record is
semantic (ZFC: model judgment over the record's nature, not a regex classifier).

## Question

CodeScaleBench (8 candidates), EnterpriseBench (1), scix_experiments (3) surfaced
in mem-apg.6 as the richest *unwired* multi-session anchored candidates. They are
attractive precisely because they are benchmark repos structured as
(issue → failing test → fix), which is also exactly why their "work records"
might be **eval-harness runs, not real development**. Folding them in changes what
"gas-city's own exhaust" means for the headline, so it must be answered first.

## Headline

**Rig-specific, not uniform. The records are NOT the benchmarks' own eval runs;
they are gas-city `mol-focus-review` agent sessions. But what those sessions
*worked* splits sharply by rig:**

| rig | clone nature | candidate work = | verdict |
|---|---|---|---|
| **CodeScaleBench** | benchmark harness | 8/9 = Docker base-image **digest-pinning infra** on the harness (one fanned-out epic); 1 real feature | **mostly harness-infra exhaust — do NOT wire as dev-work** |
| **EnterpriseBench** | benchmark harness (+paper) | both = eval artifacts (an MCP-lift **study**, an eval **run-set audit**) | **eval-noise — do NOT wire; contributes 0 dev bundles** |
| **scix_experiments** | applied research project ("SciX Agent") | diverse real feature/bugfix/refactor/infra/triage on a live codebase | **real dev-work — the one legitimate candidate** |

The shared structural fact: every candidate's own record is `task_type=formula`,
`mol-focus-review`, i.e. *our* worker pool running *our* focus-review formula
against an issue bead in that clone. So legitimacy hinges entirely on the **issue
bead** each session worked, not on the trace being "an eval run." The evidence
below reads the issue beads.

## Evidence — per rig

### CodeScaleBench — 9 candidates, issue task_types {infra: 8, feature: 1}

The clone is a benchmark harness (`base_images/ benchmarks/ calibration/
observatory/ results/ runs/ schemas/`). 8 of 9 candidates worked siblings of a
**single epic** `co-wv7`, *"Epic: pin task image digests + dependencies for
reproducibility"*:

| candidate | issue | task_type | issue title |
|---|---|---|---|
| co-7qdo | co-wv7.15 | infra | Pin base image family: numpy (1 image) |
| co-kgm8 | co-wv7.10 | infra | Pin base image family: camel (1 image) |
| co-l9yw | co-wv7.13 | infra | Pin base image family: curl (1 image) |
| co-oxhs | co-wv7.3 | infra | Pin base image family: kafka (2 images) |
| co-coog | co-wv7.11 | infra | Pin base image family: postgres (1 image) |
| co-2cvn | co-wv7.1 | infra | Pin base image family: django (2 images) |
| co-iktp | co-wv7.9 | infra | Pin base image family: flink (1 image) |
| co-nszn | co-wv7.2 | infra | Pin base image family: k8s (2 images) |
| co-uv5b | co-7ac | feature | Postrun validation/quarantine for direct-harness + OpenHands paths |

Reading: 8/9 are **mechanical, self-similar digest pins on the benchmark's own
reproducibility tooling** (Dockerfile/lockfile edits, one template applied per
image family, all under one fanned-out epic). This is not (issue → failing test →
fix) application development; it is maintenance of the eval *harness*. Admitting
them would (a) measure "does memory help pin a Docker digest" (near-zero
reasoning surface) and (b) inject 8 near-duplicate convoy siblings, inflating N
with self-correlated bundles. The lone genuine dev item (`co-uv5b`, a harness
feature) is real but is N=1.

### EnterpriseBench — 2 candidates, issue task_types {research: 1, triage: 1}

The clone is a benchmark harness with a paper (`benchmarks/ paper/ results/ runs/
schemas/`). Both candidates are **eval-process artifacts**:

| candidate | issue | task_type | issue title |
|---|---|---|---|
| EnterpriseBench-nguj5 | EnterpriseBench-323 | research | MCP-lift study: top-10 EB + top-10 CSB tasks, Sonnet 4.6+SG-MCP vs Fable-no-MCP (quality, time, cost, IR metrics) |
| EnterpriseBench-6w1yw | EnterpriseBench-dph | triage | Audit locked N=105 run set: invalid-run triage, per-task run counts, … |

Reading: one is **running a benchmark comparison study**, the other is **auditing
an eval run set**. Neither is software development; they are eval *orchestration
and bookkeeping*. Admitting them would measure memory-on-running-evals: textbook
headline contamination. EB contributes **0** legitimate dev-work bundles.

### scix_experiments — 9 candidates, issue task_types {feature: 3, infra: 2, bugfix: 1, refactor: 1, triage: 1, research: 1}

The clone is **not a benchmark**; it is "SciX Agent", an applied scientific
literature ingestion/retrieval research project (only `results/` reads as
benchmark-ish; the rest is `data/ eval/ embed/graph/ingest pipelines,
checkpoints/`). The candidates worked **distinct, organic issues** across the full
dev spectrum:

| candidate | issue | task_type | issue title |
|---|---|---|---|
| scix_experiments-gwxbp | …dbl.16 | feature | Ingest PDS Atlas + IAU minor-body designations as datasets/targets |
| scix_experiments-7p3zi | …dbl.17 | feature | Ingest Materials Project + NOMAD entities for cond-mat papers |
| scix_experiments-2g5hp | …xz4.7 | feature | Populate entity_relationships from PhySH concept hierarchy |
| scix_experiments-35e5x | …12rp | bugfix | Reconcile diskann rebuild-script DDL with benchmarked halfvec variant |
| scix_experiments-2el2o | …hu37 | refactor | lafia.py writes document_entities directly, bypassing M13 resolver … |
| scix_experiments-9b4n8 | …8m0a | infra | PG→Qdrant outbox sync worker (daily_sync step 7) |
| scix_experiments-1rs3e | …q000 | infra | Qdrant snapshot-to-NAS automation (script + systemd timer) |
| scix_experiments-i8ldj | …3eaq | triage | Triage 23 F841 unused-variable lint hits — several look like incomplete error-tracking |
| scix_experiments-dngi2 | …4skc | research | Valid rerank re-eval: INDUS cross-encoder on real … |

Reading: these are **real development tasks on a live codebase**: feature builds,
a DDL bugfix, a resolver-bypass refactor, sync-worker infra. Distinct issues
(not one fanout), heterogeneous scope. This is genuine gas-city dev-work exhaust.
Caveat: one record (`…4skc`, a rerank re-eval) is eval-adjacent, and scix is
research-*infrastructure* (vector DBs, ingestion) rather than product code, but
the lift it would measure is real memory-on-dev lift, not benchmark replay.

## Verdict per rig (the gate answer)

1. **CodeScaleBench: do NOT wire as a dev-work rig.** Its multi-session
   candidates are overwhelmingly (8/9) self-similar digest-pinning infra on the
   benchmark's *own* reproducibility tooling, under one fanned-out epic. Admitting
   them measures near-zero-reasoning maintenance and inflates N with correlated
   convoy siblings. CSB is itself a benchmark; its "issue→test→fix" shape is the
   eval, not the work. (1 genuine feature exists; N=1 is not a rig.)
2. **EnterpriseBench: do NOT wire.** Both candidates are eval-run/eval-audit
   artifacts (an MCP-lift study, a run-set audit). Pure benchmark-orchestration
   exhaust; 0 legitimate dev-work bundles. Folding EB in would directly
   contaminate the headline with eval-replay.
3. **scix_experiments: the one legitimate candidate.** Diverse real
   feature/bugfix/refactor/infra dev-work on a live applied-research codebase, not
   benchmark replay. If any external clone is wired, scix is the defensible one.
   Caveats for Stephanie's call: (a) it is research-infra, not product code; (b)
   ~1/9 records are eval-adjacent and should be screened; (c) wiring still needs a
   per-rig test-toolchain base image (the gold-test floor) before any of these can
   anchor the graded instrument, unchanged from mem-apg.6.

**Net:** the "richest unwired candidates" framing from mem-apg.6 was driven by CSB
(8) and scix (9) raw counts. On inspection, CSB's 8 collapse to harness-infra
fanout and EB's contribution is eval-noise; only scix carries real dev-work. The
benchmark repos (CSB, EB) should stay unwired on methodology grounds, independent
of the toolchain/base-image cost. This **narrows** mem-e3h2's lever: the external
clone worth the base-image investment is **scix_experiments**, not the
benchmark-named ones.

## Recommendation (Stephanie's call, not made here)

- Hold CSB and EB unwired (methodology: harness-infra + eval-noise).
- If pursuing any external rig, scope a base-image + admission pass for
  **scix_experiments only**, with a pre-screen that drops eval-adjacent records
  (e.g. the rerank re-eval). Expected legitimate dev-work bundles from scix:
  ~7–8 of 9, pending the gold-test repro floor.
- This does not, on its own, reach a grid-ready anchored pool; the toolchain
  base-image floor (mem-apg.6) still binds. It only tells us *which* clone is
  worth that effort.

## Fences honored

- Read-only on all three clones; no corpus mutation; no wiring; no base images;
  no admit. Evidence + recommendation only.
- ZFC: per-record legitimacy is a model judgment over the issue beads' nature;
  no regex/keyword legitimacy classifier was added to the pipeline.
- HALT at branch-ready (no push).

## Method / reproduction

```bash
# candidate set (per-rig multi-session anchored candidates) from mem-apg.6:
python3 - <<'PY'
import json
r = json.load(open('.mem/select-ranking-ms.json'))['ranking']
# group by rig prefix; CSB=co-*, EB=EnterpriseBench-*, scix=scix_experiments-*
PY
# each candidate's worked issue + that issue's task_type/title, from the live store:
sqlite3 'file:.mem/store.db?mode=ro' \
  "SELECT work_id, json_extract(record,'$.metadata.\"gc.var.issue\"') FROM work_records WHERE work_id=?"
```

Candidate work_ids inspected: CSB {co-7qdo,co-kgm8,co-l9yw,co-oxhs,co-coog,
co-2cvn,co-iktp,co-uv5b,co-nszn}; EB {EnterpriseBench-nguj5,EnterpriseBench-6w1yw};
scix {i8ldj,gwxbp,7p3zi,35e5x,2g5hp,9b4n8,dngi2,1rs3e,2el2o}. Issue beads read for
task_type + title; clone trees characterized read-only.
