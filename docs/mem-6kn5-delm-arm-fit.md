# DeLM as a labeled comparison arm — fit + integration cost (spike)

**Bead:** mem-6kn5 (spike, parked → revived under the overnight mandate).
**Date:** 2026-06-21.
**Upstream:** DeLM — https://github.com/yuzhenmao/DeLM — MIT license (OSI-clean).
**Deliverable type:** writeup branch of the acceptance criteria (fit + integration
cost + what to port). A live DeLM arm is **not** built here: DeLM's own oracle is
SWE-bench Verified × Gemini-3-Flash (paid API), which is outside the no-paid-API
constraint; its *architecture* is benchmarkable on the local stack like every other
arm, and that is what this spike scopes.

> Companion to [`competitive-arms-integration.md`](competitive-arms-integration.md)
> (the mem0/Zep/A-MEM/NAT family) and `docs/architecture-decisions.md` Decision 11
> (the uniform-arm contract). DeLM does **not** join that family unchanged — the
> headline finding below is an integration-point mismatch, not a 90-line adapter.

## Verdict

**DeLM is not a drop-in `MemorySystem` arm.** The existing competitive arms
(mem0/Zep/A-MEM/NAT) are *memory layers* that sit behind the harness-owned
single-agent step loop via the 3-method `retrieve`/`write`/`reset` seam. DeLM is a
*multi-agent orchestrator*: N parallel solver threads with oracle best-of-N
selection, where memory (Local Memory + SharedLessons + Compactor) is **internal to
its own loop**, not a seam the harness drives. So DeLM splits across **three
different mem integration points**, only two of which are cheap:

| DeLM component | mem integration point | Fit | Cost |
|---|---|---|---|
| **SharedLessons** (typed cross-thread notes) | `MemorySystem` arm via `AbstractSemanticArm` / `SemanticMemoryClient` | ✅ clean | ~90-line arm + fake-client tests (the standard arm cost) |
| **Memory Compactor** (trajectory compression) | `ConsolidationCapable` protocol (`consolidate`/`tombstone`) | ✅ clean | small; reuses the consolidation scorers + provenance fields |
| **Local Memory** (per-thread observations) | none — internal scratchpad below any seam | n/a | not a memory the harness can or should score |
| **pass@N / avg@1 + parallel solver threads** | *above* the `MemorySystem` seam: Agent-level multi-thread runner + a new report-layer aggregation | ⚠️ big lift | new orchestration + report methodology; independent of DeLM |

**Recommendation:** port the two cheap pieces as a `delm-shared-lessons` arm (the
benchmarkable memory contribution), and port **pass@N/avg@1 as report-layer
methodology** decoupled from DeLM (it benefits every arm). Defer the full
multi-agent whole-system arm — it needs a new integration point the harness does
not currently expose and would not isolate a *memory* result.

## 1. What DeLM is (grounded)

Decentralized multi-agent system; per-task it spawns `n_solvers` solver threads
(`src/runners/solver_thread.py`) under an orchestrator
(`src/runners/swebench_orchestrator.py`), entry point `bench_swebench.py`. Memory
is three parts:

- **Local Memory** — per-thread observations/actions/results, kept compact so long
  histories don't dominate the prompt. Thread-scoped; no cross-thread visibility.
- **SharedLessons** (`src/shared_lessons.py`) — one store per task that all threads
  write to and read before planning/implementing: *typed* notes (failed attempts,
  observations, claims, patch summaries), not raw trajectories. This is the
  cross-thread learning channel and the part that **is** an agentic-memory layer.
- **Memory Compactor** (`src/memory_compactor.py`) — compresses trajectory history
  to manage the token budget.

Oracle: **pass@N** = oracle best result over the N parallel threads; **avg@1** =
per-thread success rate (the leaderboard number). Reported on SWE-bench Verified
(500 tasks) with Gemini-3-Flash: avg@1 65.7%, pass@2 72.9%, pass@4 77.4%, ~$0.12/task.

## 2. Why it is not a drop-in arm — the integration-point mismatch

The arm contract (`membench/memory_systems/base.py:77`) is a **memory seam inside a
single-agent loop the harness owns**:

```python
class MemorySystem(ABC):
    def reset(self, trial_id) -> None: ...
    def retrieve(self, request: RetrievalRequest, ctx) -> RetrieveResult: ...
    def write(self, memory_id, content, ctx) -> MemoryEvent: ...
```

The harness drives the step loop (`runner/conditions.py:_execute_step`,
~135–237): it calls `system.retrieve()`, then `agent.run_step()`, then
`system.write()`, and scores the result against the leave-one-out boundary
(`validity.assert_no_leak`). That is what makes mem0/NAT/Zep measurable on the same
fairness terms as `ours`.

DeLM does not expose that seam. Its memory is consulted *by its own agents inside
its own multi-thread loop*; there is no point where the harness hands it a
`RetrievalRequest` and scores the returned set. Wrapping all of DeLM as one arm
would mean wrapping the whole orchestrator behind `retrieve`/`write`, which is a
category error — you'd be benchmarking DeLM-the-agent, not DeLM-the-memory, and the
harness's 3-condition single-agent design (`schemas/conditions.py`:
NO_MEMORY / ORACLE_MEMORY / MEMORY_ENABLED) has no slot for a multi-thread agent.

## 3. What is cheaply portable

### 3a. SharedLessons → a `MemorySystem` arm (fits the contract)

SharedLessons **is** a retrieve/write memory: threads `write` typed notes and
`retrieve` peers' notes before acting. It maps onto the existing semantic-arm seam
(`memory_systems/semantic_base.py:108` `AbstractSemanticArm` over the
`SemanticMemoryClient` Protocol, §5 of the companion doc):

- `write(memory_id, content)` → append a typed note (the note *type* —
  failed-attempt / claim / patch-summary — rides in the payload or a structured
  prefix; DeLM's `src/prompts/note_rules.py` defines the taxonomy to mirror).
- `retrieve(request)` → peer-note lookup. DeLM's native read is "consult lessons
  before planning"; mapped to the harness's `query_text`/`requested_ids` path with
  `top_k`, re-validated against the LOO boundary like every other arm.
- `reset(trial_id)` → fresh per-trial namespace (the §5b fresh-`group_id`/scope
  pattern; no destructive purge needed).

Cost: the standard ~90-line arm + a deterministic fake-client test suite (CI stays
model-free/network-free), then register in the `build_memory_system` factory
(`memory_systems/__init__.py:79`) next to `mem0`/`a-mem`/`nat`/`graphiti`. The
typed-note taxonomy is the one DeLM-specific wrinkle vs the generic semantic arms;
it is payload shape, not new harness machinery.

**This is the answer to the contribution question:** does DeLM's *typed
cross-thread lesson store* beat `ours` (deterministic, zero-API, failure-triggered)
and the semantic arms on recurring build/test/lint failures — measured on the same
corpus, same LOO boundary, same precision guards (D10)?

### 3b. Memory Compactor → `ConsolidationCapable` (fits the optional protocol)

DeLM's trajectory compaction is exactly the optional consolidation pass
(`memory_systems/consolidation.py:56`):

```python
class ConsolidationCapable(Protocol):
    def consolidate(self, ctx) -> ConsolidationResult: ...   # items, tombstoned_ids, background_tokens, notes
    def tombstone(self, memory_id) -> None: ...
```

Compaction = `consolidate` (compress N notes → fewer `ConsolidatedItem`s carrying
`source_trace_ids` provenance); superseded notes = `tombstone`. The
consolidation scorers already exist (`metrics/schema_scorers.py`: `schema_recall`,
`confabulation_findings`), so a `delm-shared-lessons` arm that implements this
protocol gets its compaction graded for free — including a confabulation check
(does compaction invent facts not in the source notes?), which is a *real* risk for
an LLM-driven compactor and a publishable axis.

## 4. What is the big lift (defer)

The whole-system pass@N advantage lives **above** the memory seam: N parallel
solver threads + oracle best-of-N selection. Admitting that needs two things mem
does not have:

1. **An Agent-level multi-thread runner.** The harness `Agent` protocol
   (`runner/agent.py:36`) is single `run_step(step, available_memory, ctx)`; there
   is no fan-out-N-then-select. A DeLM whole-agent arm would be a new runner
   alongside `conditions.py`, not a `MemorySystem`.
2. **pass@N / avg@1 in the report layer.** Confirmed **absent** today — aggregation
   is per-condition *mean* (`report/comparison.py:_summarize`,
   `report/synthetic_arms.py` `lift = arm_reward - none_reward`). No best-of-k, no
   selection over replicas.

Item 2 is worth porting **on its own, decoupled from DeLM**: pass@N/avg@1 over K
replica trials per (sequence × condition) is a general report-layer aggregation
that strengthens *every* arm's headline (it separates "memory raises the ceiling"
from "memory raises the average"). It is the highest-value methodology import here
and does not require DeLM's orchestrator at all.

Item 1 (the multi-thread whole-agent arm) is deferred: it benchmarks
DeLM-the-agent, not DeLM-the-memory, and the parallel-thread cost (N× LLM calls)
collides with the local-stack budget. Park it behind the SharedLessons arm result.

## 5. Tie-in to the unified-scorer contract

A `delm-shared-lessons` arm scores through the *same* `MetricsBundle`
(`schemas/metrics.py:128`) as every arm — no new scorer needed:

- **`TaskMetrics.reward` / `pass_`** — the headline, identical to `ours` and the
  semantic arms.
- **`RetrievalMetrics`** — peer-note recall/precision@k, MRR/nDCG, distractor &
  stale rates on the SharedLessons read path.
- **`RetentionMetrics`** — typed-note write hit/miss, over-retention (does DeLM
  hoard lessons?), supersession correctness via the compactor's tombstones.
- **Consolidation scorers** (`schema_recall`, `confabulation_findings`) — grade the
  Compactor pass per §3b.
- **pass@N / avg@1** — a *new report-layer aggregation* (§4 item 2), **not** a new
  scorer; it sits in `report/` over replica trials, orthogonal to the per-trial
  `MetricsBundle`.

Fairness holds by construction: DeLM's notes go through `validity.assert_no_leak`
like any arm, so its typed store cannot leak future work even if its own index
would return it.

## 6. Integration cost summary & build order

| Work item | Integration point | Lift | Paid? | Order |
|---|---|---|---|---|
| `delm-shared-lessons` arm | `AbstractSemanticArm` + factory | ~90 LOC + fakes | no (local stack) | 1 — the memory contribution |
| Compactor as `ConsolidationCapable` | consolidation protocol + existing scorers | small | no | 2 — folds into the same arm |
| pass@N / avg@1 report methodology | `report/` aggregation over K replicas | moderate, **DeLM-independent** | no | 3 — benefits all arms; do regardless |
| Whole-agent multi-thread DeLM arm | new Agent-level runner | large | yes (N× LLM) | defer — benchmarks the agent, not the memory |

**Net:** the portable, no-paid-API, contract-fitting slice of DeLM is the
SharedLessons typed-note store (graded by the existing unified scorer) plus its
Compactor (graded by the existing consolidation scorers). pass@N/avg@1 is a
valuable methodology import that stands on its own. The full multi-agent
best-of-N system is a different integration point and a paid run; defer it behind
the SharedLessons arm result.
