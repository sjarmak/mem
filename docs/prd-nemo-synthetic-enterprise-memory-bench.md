# PRD — NeMo Synthetic Enterprise Workflows for Memory Benchmarking

Status: proposed · 2026-06-18 · author: session (mem)

## Problem

mem's real-trace benchmark hit a hard ceiling: of a large mined corpus, only
~8–9 oracles are *sound* (`mem-apg.11`, `mem-wanz`/`mem-58rp`), and over those
the memory arm shows **no measured lift** (`ours +0.000`). The binding
constraint is **oracle validity and bundle assembly from real traces**, not the
retrieval method. We cannot answer the core research question on real data
because we cannot manufacture enough tasks where memory is *provably required*.

## Core hypothesis (falsifiable)

> A synthetic enterprise world, where memory-dependency is authored by
> construction, lets us measure when working memory must become persistent
> memory — and quantify retrieval's effect on downstream task success — at an N
> the real corpus cannot reach.

The hypothesis is **falsified** if, on generated tasks, the `ORACLE_MEMORY` arm
does not beat `NO_MEMORY` (i.e. the task is solvable from context alone and
memory is not actually required). Phase 0 exists to catch exactly this.

## Non-goals

- Not a task generator that produces independent tasks. It generates *worlds*
  from which memory-dependent workflows emerge.
- Not a replacement for the real-trace track. Synthetic measures *capability
  ceiling under ideal data*; real measures *deployed reality*. Both run.
- Not building the memory runtime. The system under test (`src/` ingest→store→
  retrieve + the `ours` arm) already exists; this PRD builds the substrate that
  measures it.

## Key reuse finding (do NOT reinvent)

`memory-bench/membench/generators/synthetic_task.py` already implements the
spec's two-stage blueprint→materialize flow: authored `TaskBlueprint`s →
`BenchmarkSequence` with structural memory-dependency (`expected_memory_writes`
/ `expected_memory_reads` / `outcome_checks[].requires_memory`). The generator
policy is already correct and binding:

> WE author the ground truth (latent rule, structural constraints) in pure
> Python, deterministically and seed-reproducibly. A model may LATER fill only
> the NL surface text, offline, into a frozen fixture; CI never calls a model.

NeMo Data Designer is the *offline surface-and-population generator* under that
policy — nothing more. The eval harness (`runner/conditions.py:run_sequence()`,
the 3-condition legs, `metrics/scorers.py`, the validity gates) is **unchanged**.

## Architecture

```
NeMo Data Designer (OFFLINE, one-time, seeded; local NIM / OAuth model)
  ├─ SamplerColumnConfig   → domain, org size, persona set, channel mix,
  │                          staleness rate, distractor density
  └─ LLMTextColumnConfig   → NL surface: PRDs, issue bodies, agent mail,
                             persona voices, doc pages
        ↓ emits world + surface text (data_designer.preview / run → records)
Python materializer  (generators/enterprise_workflow.py — mirrors synthetic_task.py)
  ├─ authors memory-dependency STRUCTURE   (expected_memory_writes/reads)
  ├─ authors ORACLE ground truth           (outcome_checks, latent_rule, disposition)
  ├─ injects distractors                    (→ Confusion metric)
  └─ injects supersedes                     (→ Staleness metric)
        ↓
EnterpriseWorld / Project  (new schemas)  wrapping  BenchmarkSequence (existing)
        ↓  FROZEN FIXTURE  {seed, nemo_model, config_hash, fixture_hash}
run_sequence()  [NO_MEMORY / ORACLE_MEMORY / MEMORY_ENABLED]   ← UNCHANGED
        ↓
metrics + validity gates   ← UNCHANGED  (+ memory_necessity_gate, new)
```

Determinism contract: a frozen fixture + seed reproduces a task instance
*without re-running NeMo*. NeMo's own output is captured in the fixture; CI and
scoring never call NeMo or any model.

## Eval-metric → runtime-stage mapping (why this targets the right gaps)

The benchmark must probe the runtime's **weak/absent** stages, not just Recall.

| Metric | Runtime stage status | Generator lever |
|---|---|---|
| Recall | retrieval EXISTS | baseline; `expected_memory_reads` |
| Confusion | distractors NOT wired (FTS tiebreaker only) | `distractor_memories` |
| Staleness | supersede-exclusion only, NO decay | `superseded_memory_ids` |
| Recovery | controller ABSENT | drop-a-session variant (Phase 3) |
| Continuity | cross-task linkage ABSENT | `Project`-level memory (Phase 3) |
| Completion | EXISTS | `outcome_checks[].requires_memory` |

## Phases (tracked as beads under the epic)

**Phase 0 — Memory-necessity gate (FIRST; construct-validity backbone).**
A generated task is admitted only if `ORACLE_MEMORY − NO_MEMORY ≥ τ` on the
3-condition run. Reusable gate over the existing `synthetic_task` generator
first (proves the eval discriminates memory before any NeMo work). Lives beside
the other validity gates. Directly answers "when does memory become required".

**Phase 1 — NeMo world generator + `EnterpriseWorld`/`Project` schemas.**
New frozen Pydantic schemas (`schemas/world.py`) wrapping `BenchmarkSequence`.
NeMo config (`generators/nemo/world_config.py`) via `DataDesignerConfigBuilder`
+ sampler + LLM-text columns, model alias pointing at local NIM / OAuth. Output
to `fixtures/worlds/<seed>/`. Never invoked in CI.

**Phase 2 — Python materializer.** `generators/enterprise_workflow.py`,
mirrors `synthetic_task.py`. Transpiles one NeMo world → N linked
`BenchmarkSequence`s. Wires `distractor_memories` (Confusion) and
`superseded_memory_ids` (Staleness) — the two runtime gaps. Seed → byte-
identical output. Register in `generators/__init__.py`.

**Phase 3 — Cross-task / cross-session continuity + recovery.** `Project`-level
memory: tasks depending on memory written by *earlier tasks*; a missing-context
variant (drop a session) probing Recovery. Extends what `BenchmarkSequence`
does not model today.

**Phase 4 — Determinism manifest + freeze integration.** Hook into existing
`freeze/`+`verify/` + `scripts/day0-freeze.mjs`. Per-world manifest
`{seed, nemo_model, config_hash, fixture_hash}`; reproducible without NeMo.

## Risks / premortem

- **Memory-helpful ≠ memory-required.** Mitigation: Phase 0 gate is mandatory;
  no task ships without passing it.
- **Synthetic worlds feel synthetic** (low transfer to real agents). Mitigation:
  NeMo surface diversity + keep the real-trace track as the external-validity
  anchor; report both.
- **NeMo as a heuristic backdoor.** NeMo only fills *surface text*; all ground
  truth stays in Python (ZFC). A reviewer must be able to read the oracle in
  Python without consulting any model output.
- **Generation-time model cost.** Resolved: local NIM / OAuth model; offline;
  frozen fixtures run once. No paid memory-stack API.
- **Skill generation (runtime stage 11) is absent and tempting.** Out of scope
  here; gate it behind a *demonstrated* retrieval lift on this substrate.

## Success criteria

1. Phase 0 gate produces a non-empty admitted set where `ORACLE_MEMORY` beats
   `NO_MEMORY` by ≥ τ (else the hypothesis is falsified for the current
   generators — a publishable negative result).
2. A seeded world reproduces byte-identically from its frozen fixture with no
   model call.
3. Generated N (admitted, memory-required tasks) materially exceeds the
   real-corpus ceiling (N≈9).
4. The benchmark exercises Confusion + Staleness (not just Recall), measured by
   non-trivial `ORACLE − NONE` deltas on the distractor/supersede variants.
