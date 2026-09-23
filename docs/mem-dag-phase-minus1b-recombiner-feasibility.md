# Phase −1b Probe: Grounded Multi-Session Isolation-DAG Generator — FEASIBLE (structural)

**Status:** GO (structural feasibility passed all 4 checks). Proceed to a fresh PRD; gate it on the behavioral/construct-validity pilot.
**Probe:** `memory-bench/scripts/phase_minus1b_recombiner_probe.py` → `.mem/phase-minus1b-recombiner-probe.json`
**Context:** follow-up to the Phase −1 NO-GO (`mem-dag-phase-minus1-negative-result.md`). That probe killed *mining* DAGs from recorded chains; this one tests *authoring* them.

## The reframe

The DAG is the **experiment-design graph**, not an execution-optimization plan. Optimize for **variable isolation** (independent branches each varying one factor → clean causal attribution); DAG **parallelization falls out for free** because isolated factors are independent. Do NOT optimize for parallelization directly — that biases the suite toward easy, separable workloads and destroys construct validity.

## What was tested (cheap, structural, no model calls)

Extended the existing spine (`membench.generators.synthetic_task`, already structurally memory-dependent by design) to a multi-session, width-K, single-factor-factorial recombiner grounded in the real 432-bead cost distribution. Results:

| Check | Pre-registered bar | Result | Pass |
|---|---|---|---|
| C1 width | median antichain width ≥ 3 | **median 5** (3–6); recorded corpus was width 1 | ✅ |
| C2 structural discrimination | all tasks clear `pilot_filter` | **60/60 admitted**, median delta **1.0** | ✅ |
| C3 single-factor isolation | one factor varies, topology fixed | interference 0→2→4→8, width **invariant at 4** | ✅ |
| C4 grounded realism | generated cost ≈ real | gen turns median **145** vs real **142** | ✅ |

## Behavioral anchor (existing evidence, not re-run)

`mem-p3w` (3-arm clean-room pilot) is the real behavioral signal and it is **favorable with conditions**:
- **Efficiency discriminates** where retrieval has coverage: `ours` beats `builtin` on 6/9 bundles, median **−1,028** output tokens; km0wj **−3,082** (the 2/9 with real retrieval payload).
- **Binary gold-test quality is FLAT** (1/9 pass, all arms) — but this is a *metric-coarseness* + *retrieval-coverage* artifact, not a null effect of memory.
- **Both binding limiters are generator-controllable**: a generator can (i) use a graded metric (`mem-g6a`) instead of binary, and (ii) author the planted fact to be *guaranteed retrievable*, removing the 2/9 coverage ceiling by construction.

## The load-bearing risk (replaces "frankenstein")

**Construct validity:** does an authored benchmark predict *real* memory-system behavior, or is it self-consistent fantasy? Specific warnings: the B-1 finding (`external_anchor.py` header) that MEM's own coding corpus "can't carry a shared-latent-rule signal," and the mem-apg.4 flat reward curve. Required defense — the generator must pass an **external-rank-correlation** gate: config rankings on the synthetic suite must correlate with rankings on a held-out *real* task set (e.g. `scix_experiments`).

## Decision

**GO** to a fresh PRD (`diverge → converge → premortem`) for a *grounded factorial isolation-DAG benchmark generator for causal diagnosis of memory-system failure/benefit modes*. The behavioral pilot (graded metric + guaranteed coverage + external-rank correlation) is the PRD's **first gate**, not assumed.

## Reproduction
```
cd memory-bench && .venv/bin/python scripts/phase_minus1b_recombiner_probe.py
```
