# memory-bench — Agentic Memory Evaluation Harness (Phase-1 skeleton)

Python harness that runs one multi-session benchmark sequence under the three
evaluation conditions and captures normalized traces + core metrics.

Governing spec: [`.gc/memory-eval-harness-spec.md`](../.gc/memory-eval-harness-spec.md)
(§3 system model, §4 conditions, §6.2 normalized memory ops, §8 trace, §12 metrics,
§14 Harbor, §16 Phase 1). Reconciliation: [`docs/phase-2.5-plan.md`](../docs/phase-2.5-plan.md)
§A. The decisions it implements are ARCHITECTURE.md D11–16.

This is the Phase-1 **skeleton** — the plumbing, proven end-to-end with a
deterministic reference agent and reference memory systems. It is intentionally
*mechanism only* (ZFC): Harbor orchestration, schema validation, deterministic
memory-op mapping, and deterministic metric arithmetic. Semantic judgment (the
trace→memory extractor, LLM-as-judge scoring) is a documented seam for later phases.

## What's here

| area | module | spec |
|---|---|---|
| conditions | `membench/schemas/conditions.py` | §4 (no_memory / oracle_memory / memory_enabled) |
| system model | `membench/schemas/config.py` | §3 (experiment / agent / memory configs) |
| sequence | `membench/schemas/sequence.py` | §9.2 (multi-session sequence + steps) |
| memory events | `membench/schemas/memory_event.py` | §6.2 (10 normalized operations) |
| op mapper | `membench/mapper/memory_op_mapper.py` | §6.2 (concrete tool → canonical op) |
| trace | `membench/schemas/trace.py` | §8 |
| metrics | `membench/schemas/metrics.py` | §12 (task/efficiency/retrieval/retention; privacy/interruption stubbed) |
| memory systems | `membench/memory_systems/` | §14 reference set: none / oracle / filesystem |
| runner | `membench/runner/` | §4 + §15 MVP (run one sequence under 3 conditions) |
| telemetry | `membench/telemetry/` | OTel GenAI spans (primary) + ATIF (derived) |
| Harbor adapter | `membench/harbor/adapter.py` | §14 (real task-dir shape; reward → `/logs/verifier/reward.txt`) |
| report | `membench/report/comparison.py` | §4 interpretation table |

## Run

```bash
# In-process, deterministic, no Docker / no paid API — produces the 3-condition report:
python3 -m membench.cli run-sequence \
  fixtures/sequences/gascity_backend_conventions.json --out reports/

# Emit Harbor task dirs for a real `harbor run` (paid Claude path):
python3 -m membench.cli gen-tasks \
  fixtures/sequences/gascity_backend_conventions.json --out tasks/

pytest -q
```

## Boundaries (this bead)

- The agent-under-test is Claude (Code / Opus / Sonnet / Haiku) on our account via
  OAuth — that paid path is approved (plan §A, DIV-1). It runs through the emitted
  Harbor tasks, not in-process. `ScriptedAgent` is the in-process reference agent
  for the skeleton + tests.
- no-paid-API stays on the **memory** stack (backends / embeddings / extractor /
  judge = OSS / self-hosted).
- The TS work-audit graph builder (`../src/`) is a **data source** that exports
  sequences/fixtures as JSON (plan §A, DIV-8) — not rewritten here.
- **Not built here** (later phases / `mem-lvp`): competitive memory systems
  (a-mem / mem0 / graphiti / nat), `ours` (retrieval-v1, `mem-di8`), the synthetic
  generator, the full ≥10-sequence dataset, and the full metrics/diagnostics suite.
