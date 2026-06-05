# memory-bench — agentic memory evaluation harness (Phase-1 skeleton)

Python harness that runs one multi-session benchmark sequence under three
evaluation conditions and captures normalized traces plus core metrics.

## What this measures

The harness answers one question per task family: does accumulated memory change
the agent's *action* in a way that improves outcome, efficiency, or reliability
versus a stateless baseline? A task counts only if memory materially affects
execution — choosing the right tool because a prior session established it,
avoiding a known failure mode, applying a project convention set earlier. Pure
"what did I say earlier?" recall, and tasks solvable equally well without memory,
are out of scope by design.

The dataset is not a flat prompt set. Each benchmark item is an ordered
multi-session sequence (Step 1 → … → Step N → Goal). Every step starts with fresh
context; the persistent memory store is the only thing that survives between
steps.

### The three conditions

Each sequence runs under all three, and the gap between them is the signal:

- **no_memory** — memory disabled. The agent gets only the current step's
  context and its non-memory tools. Establishes stateless performance.
- **oracle_memory** — the harness injects the exact relevant memory. Sets the
  ceiling and proves the task is actually memory-sensitive. If oracle ≈
  no_memory, the task doesn't discriminate and gets redesigned.
- **memory_enabled** — the full memory system runs through its normal retrieve,
  write, and consolidation path. This is the real system's score.

The report reads the gaps directly: `oracle > memory > no_memory` means
retrieval or ranking is leaving gains on the table; `memory < no_memory` means
memory is injecting noise or stale state.

### Metrics

Each trial emits a normalized trace and a metrics bundle. Phase 1 carries four
live families — **task outcome**, **efficiency** (tokens, tool calls, latency,
cost, retries), **retrieval** (precision/recall/rank over available memory), and
**retention** (write hit/miss, scope, supersession). Two more groups — privacy
and interruption — are stubbed now and filled in later.

Concrete memory tools differ across systems (filesystem read/write, MCP
`add_memory`/`search_memories`, vector upsert/search), so every invocation maps
through a single canonical operation set (`read`, `write`, `update`, `delete`,
`search`, `consolidate`, `promote`, `forget`, `classify`, `discard`) before it's
scored. That keeps systems comparable.

## Substrate

Harbor is the execution substrate: a framework for running agent evaluations in
containerized environments against agents and models such as Claude Code. This
harness supplies the dataset, the adapters, and the scorers; Harbor runs them.
`harbor/adapter.py` emits real task-dir shapes and writes each trial's reward to
the verifier path Harbor expects.

## What's here

This is the Phase-1 **skeleton**: the plumbing, proven end-to-end with a
deterministic reference agent and reference memory systems. It is mechanism only
— Harbor orchestration, schema validation, deterministic memory-op mapping, and
deterministic metric arithmetic. Semantic judgment (the trace-to-memory
extractor and LLM-as-judge scoring) is a documented seam left open for later
phases.

| area | module |
|---|---|
| conditions | `membench/schemas/conditions.py` (no_memory / oracle_memory / memory_enabled) |
| system model | `membench/schemas/config.py` (experiment / agent / memory configs) |
| sequence | `membench/schemas/sequence.py` (multi-session sequence + steps) |
| memory events | `membench/schemas/memory_event.py` (10 normalized operations) |
| op mapper | `membench/mapper/memory_op_mapper.py` (concrete tool → canonical op) |
| trace | `membench/schemas/trace.py` |
| metrics | `membench/schemas/metrics.py` (task / efficiency / retrieval / retention; privacy + interruption stubbed) |
| memory systems | `membench/memory_systems/` (reference set: none / oracle / filesystem) |
| runner | `membench/runner/` (run one sequence under 3 conditions) |
| telemetry | `membench/telemetry/` (OTel GenAI spans, primary; ATIF, derived) |
| Harbor adapter | `membench/harbor/adapter.py` |
| report | `membench/report/comparison.py` (3-condition interpretation table) |

## Run

```bash
# In-process, deterministic, no Docker, no paid API — produces the 3-condition report:
python3 -m membench.cli run-sequence \
  fixtures/sequences/gascity_backend_conventions.json --out reports/

# Emit Harbor task dirs for a real `harbor run` (paid Claude path):
python3 -m membench.cli gen-tasks \
  fixtures/sequences/gascity_backend_conventions.json --out tasks/

pytest -q
```

## Boundaries

- The agent under test is Claude (Code / Opus / Sonnet / Haiku) on our own
  account via OAuth — the one approved paid path. It runs through the emitted
  Harbor tasks, not in-process. `ScriptedAgent` is the in-process reference agent
  for the skeleton and tests.
- The no-paid-API rule applies to the **memory** stack: backends, embeddings,
  extractor, and judge stay OSS or self-hosted.
- The TypeScript work-audit graph builder in `../src/` is a **data source**. It
  exports sequences and fixtures as JSON; it is not rewritten here.
- **Not built here** (later phases): competitive memory systems, an embedding
  retrieval lane, the synthetic sequence generator, the full ≥10-sequence
  dataset, and the complete metrics and diagnostics suite.
