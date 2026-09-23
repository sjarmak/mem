# PRD: DAG-Based Trace Orchestration for Multi-Session Agent Benchmarking

> **STATUS: NO-GO (Phase −1 gate, 2026-06-18).** All three pre-registered gates fired on the existing corpus — within-sequence DAG width median 2 / bead-session width 1, deterministic-setup cost ~0% of token spend, cross-session dependency density 17% (trivial-on-a-chain). Empty-premise exit. Do **not** build the Phase-0 cache/scheduler as a throughput play. See `docs/mem-dag-phase-minus1-negative-result.md` and `.mem/phase-minus1-probe.json`. Surviving thread: the memory-conditioned validity question, gated behind the `mem-7q6e` corpus-substrate fix.


## Problem Statement

Multi-session agent-memory benchmarks (MemoryArena, LongMemEval, LoCoMo, MemoryAgentBench) all execute strictly sequentially — sessions `S1 → S2 → ... → Sn`, with memory state carried forward and the full prefix re-run for every memory configuration or ablation arm. This makes ablation sweeps (e.g., "retrieve top-5 vs top-20", "memory backend A vs B", an information-ladder) cost O(configs × sessions) full re-executions, most of which recompute identical deterministic work and re-pay for expensive agent runs whose inputs did not change.

Every primitive needed to do better is mature in an adjacent domain — content-addressed cache invalidation (Bazel/Buck2, Dagster code×data versioning), incremental change-propagation (Salsa/Adapton/Differential Dataflow), durable replay (Temporal), checkpoint/branch (LangGraph), and prefix-KV reuse (SGLang RadixAttention/KVFlow) — but **no system composes them into incremental-DAG benchmark execution for multi-session agents**. Critically, the genuinely hard problem is *not* DAG scheduling (the cited motivating paper, arXiv:2410.17563, is a real-time multiprocessor scheduling paper that assumes a static DAG — it solves the easy part). The hard, novel problem is **defining a sound cache-invalidation and replay-equivalence relation over nodes whose outputs are stochastic LLM samples conditioned on a mutable memory state.** MEM is unusually well-positioned to attack exactly this narrow core: it already *declares* memory read/write dependencies per step, its append-only `closedBefore`-bounded corpus makes checkpointing an index-seek rather than a state-fold, the expensive nondeterministic node (the Harbor agent run) is already isolated, and its deterministic `trace_error` oracle can serve as ground truth to validate that incremental replay does not distort results.

## Convergence Outcome (post-debate)

A 3-position debate (Builder / Methods-first / Pragmatic-middle) converged in one round on a **phased, measurement-gated plan**. Resolved consensus: build the *narrow* oracle-gated core (not the maximalist compiler); never cache the LLM/Harbor inference node; the `trace_error` oracle is the correctness gate on every cache-reuse decision; the validation harness is itself most of the executor. The systems-vs-methods publishable framing is **deliberately held open and gated on Phase-1 measurements** (DAG-width histogram, declared-vs-observed edge divergence, inference-vs-setup cost split). Parallel scheduler and speculative execution are deferred behind the width-histogram result. Preserved dissent: the Builder maintains the durable category-owning *asset* is undervalued even if the *paper* ends up methods-flavored — revisit if Phase-1 shows wide DAGs.

**Execution phasing:**
- **Phase 0** — build the narrow tool (`compile_dag` + det/stoch coloring + `affected_cone` + two-color cache + soundness harness). Serves ablation sweeps regardless of which paper materializes.
- **Phase 1** — run the three cheap measurements *using the tool as the instrument*.
- **Phase 2** — branch the publishable claim on the data: wide DAGs + low divergence → systems/MLSys paper; high *structured* divergence + stochastic-node instability → methods/validity (NeurIPS/ICML D&B) paper; both → two papers off one artifact.

## Goals & Non-Goals

### Goals

- Recover an explicit dependency DAG from MEM's already-declared (`expected_memory_reads`/`expected_memory_writes`) and observed (`MemoryEvent`) memory edges, with no new instrumentation.
- Color nodes **det** (retrieval, ranking, env/tool setup, deterministic reward, trace-error extraction — pure, content-hashable) vs **stoch** (Harbor agent run, LLM-judge — sampled, not idempotent), and cache each correctly.
- Support **counterfactual / intervention evaluation**: perturb one node (e.g. retrieval `limit` 5→20), recompute only the *affected descendant cone*, reuse everything else — and report the result as Harbor-runs-avoided plus per-arm graded deltas.
- **Validate methodological soundness**: empirically demonstrate that incremental cached-prefix replay reproduces the same `trace_error` / reward outcomes as full from-scratch re-execution, within a stated tolerance and under stated conditions.
- Parallel-execute independent det-nodes via topo-sorted worker pool while preserving causality.

### Non-Goals

- **NOT** building a general-purpose "Bazel + Temporal + LangGraph" agent-trace compiler as a standalone product/infra platform (rejected as a mis-scoped multi-year systems project competing with funded infra teams).
- **NOT** caching the LLM inference node to "save cost" on a memory eval — caching the inference defeats the measurement. Cost savings claimed only for deterministic scaffolding (env setup, retrieval, tool I/O).
- **NOT** claiming intra-trajectory DAG parallelism as the headline win without first beating the embarrassingly-parallel-across-tasks baseline.
- **NOT** counterfactual off-policy *estimation* (importance sampling) as a replacement for re-execution inside the affected cone — the affected cone is actually recomputed, not estimated.
- **NOT** a snapshot store or event-replay engine — the append-only corpus already is the event log; a Merkle index over the retrieval-visible slice suffices.

## Requirements

### Must-Have

- **Requirement: DAG compiler from declared + observed edges.** A pure function `compile_dag(sequence | bead_sessions) → DiGraph` that builds producer→consumer edges where edge `Sj → Si` exists iff `Si.expected_memory_reads ∩ Sj.expected_memory_writes ≠ ∅` (and `j < i`), augmented with observed `MemoryEvent.written_ids`/`retrieved_ids` edges. Node granularity = memory-op (not session); sessions fused only when they share a write-set with no internal read dependency.
  - Acceptance: On the existing N=9 replay grid, `compile_dag` emits a DiGraph whose edge set equals the hand-verified producer→consumer relation for a fixture sequence; a unit test asserts exact edge-set equality and that two independent writes in one session produce two unfused nodes.

- **Requirement: Affected-cone recompute.** `affected_cone(dag, perturbed_node) → set[node]` returns the descendant closure; `replay_incremental(dag, intervention, cache)` recomputes only det-nodes in the cone and reuses cached results outside it.
  - Acceptance: For a top-5→top-20 retrieval intervention on a fixture DAG, the set of recomputed nodes equals the descendant closure of the retrieval node (asserted by test), and nodes outside the cone are served from cache (cache-hit counter > 0, recompute count == |cone ∩ det-nodes|).

- **Requirement: Two-color content-hash cache.** det-node key = `H(node_kind, scope, rig, closedBefore, merkle_root(visible_ids), params)`; stoch-node key = same root + `(model_id, seed, temperature)` storing a **set of sampled results with seeds** (never last-write-wins).
  - Acceptance: Re-running an identical det-node twice yields a cache hit with byte-identical result; a stoch-node re-run with a new seed appends to the sample set rather than overwriting (test asserts `len(sample_set)` increments and prior samples retained).

- **Requirement: Merkle index over retrieval-visible slice.** Leaf = `H(content)`, root = hash over live ids visible at boundary T; earlier slices are prefixes of later ones (structural sharing). Checkpoint = setting `closedBefore = start(S_k)` (index seek).
  - Acceptance: For two boundaries T1 < T2 over the same corpus snapshot, the visible-id set at T1 is a subset of T2's, and the diff of their Merkle roots identifies exactly the changed leaves (test on fixture corpus). Retrieval at a fixed boundary over a fixed snapshot is bit-identical across two runs.

- **Requirement: Soundness validation harness.** A script that runs M fixture trajectories both (a) full from-scratch and (b) via incremental cached-prefix replay, and compares deterministic outcomes.
  - Acceptance: Across the fixture set, incremental replay reproduces the from-scratch `trace_error` / deterministic-reward outcome for ≥ a pre-registered fraction (target reported with CI); any divergence is logged with the node where det/stoch coloring was violated. The harness emits a pass/fail report file.

### Should-Have (Phase 1 — gating measurements; run BEFORE the scheduler)

These three measurements decide the publishable framing and whether the scheduler is built at all. Run them on the 874 transcripts using the Phase-0 tool as the instrument.

- **Requirement: Empirical DAG-shape report.** Measure (i) declared-vs-observed read-set divergence rate and (ii) the session-DAG width histogram.
  - Acceptance: A report file reporting both distributions. Decision rule recorded in-report: median width > 2 AND low divergence → greenlight scheduler + systems-paper framing; high *structured* divergence → greenlight methods/validity-paper framing; median width ≤ 2 → de-prioritize scheduler.

- **Requirement: Cost-breakdown measurement.** Per-trajectory breakdown of inference vs env/tool-setup vs wall-clock.
  - Acceptance: A report showing setup-fraction of total cost; confirms/denies the "cache-the-deterministic-boundary" value prop (threshold pre-registered, e.g. setup ≥ 10%).

- **Requirement: Parallel det-node scheduler (DEFERRED behind the width histogram).** `schedule(dag, max_workers=N)` via Kahn topo-sort runs ready det-nodes concurrently while preserving causal edges. Build only if the DAG-shape report greenlights it (median width > 2).
  - Acceptance: On a fixture DAG with width ≥ 3, wall-clock with `max_workers=4` is measurably less than `max_workers=1` for the det-node portion, and output results are identical across worker counts (determinism preserved).

### Nice-to-Have

- **Requirement: Speculative stoch-node execution with rollback.** Speculatively launch the most-likely downstream branch on an unresolved data-dependent edge; validate predicted read-set against observed `MemoryEvent` on parent completion; discard on misprediction (free, since stoch results are a sample set, not committed state).
  - Acceptance: On a fixture with a known mispredicted edge, the speculative result is discarded and final output equals the non-speculative output (test asserts equality and that a misprediction was counted).

- **Requirement: Prefix-KV reuse hook (SGLang/KVFlow-style).** Where multiple configs share session prefixes S1…Sk, expose the shared prefix to a prefix-cache-aware inference layer.
  - Acceptance: A documented integration point + a micro-benchmark showing shared-prefix reuse reduces prefill tokens on a 2-config fixture (measured, even if the inference backend is mocked).

## Design Considerations

**Central tension (bullish build vs contrarian scope-down).** Agents 1+2 see a real, buildable gap with MEM well-positioned; Agent 3 argues the maximalist framing is mis-anchored because (a) parallelism reduces wall-clock not dollars, (b) the cost-saving mechanism (caching inference) is methodologically invalid for memory evals, and (c) counterfactual replay risks "frankenstein trajectories." This PRD resolves the tension by **scoping to the narrow defensible core**: det/stoch separation + affected-cone recompute + soundness validation. The value proposition is reframed from "faster and cheaper benchmarking" to **"incremental counterfactual evaluation that avoids re-running the agent, with a validated guarantee it doesn't distort results"** plus cheaper/reproducible re-runs of *deterministic scaffolding only*.

**Two regimes, do not conflate.** (a) replay/intervention over a *recorded* trace — DAG known, counterfactual = recompute affected cone (cheap, sound, the 90% case, build first); (b) speculative *fresh* execution — DAG predicted, validate edges at runtime, roll back mispredictions (defer until the DAG-shape report justifies it).

**Value is inverted from the brief.** The costly node (Harbor agent run) is nondeterministic and can't be cached for free; the parallelizable nodes (retrieval/grading) are already deterministic and microsecond-cheap. So the headline is counterfactual reuse of expensive stoch samples outside the affected cone — not parallel execution.

**Frankenstein-validity is the load-bearing risk.** Splicing a new downstream decision onto a cached upstream prefix may evaluate a trajectory the real policy would never produce. Mitigation: only cache deterministic boundary nodes; treat stoch nodes as sampled with seeds; and *empirically prove* via the `trace_error` oracle that incremental replay matches from-scratch outcomes before claiming any result.

## Open Questions

- What is the real declared-vs-observed read-set divergence rate across the 874 transcripts? (Gates how much speculation machinery to build — empirically answerable now.)
- How wide is a typical session DAG? If width is 1–3, parallelism payoff is small and the value is purely caching/counterfactual.
- Is MEM's memory-update function deterministic enough for content-hash invalidation when memory is a learned/compressed artifact (LLM-driven summarization, Mem0/Letta-style)? If summarization is stochastic, the memory-write node itself becomes a stoch-node.
- Hidden/implicit dependencies: git working-dir state, wall-clock leakage at the `started` boundary, external tool state on Harbor, RNG. Policy when a cached tool result's external precondition may have changed — conservative re-run vs external-state fingerprint per call?
- Counterfactual cascade under nondeterminism: the affected cone is an upper bound on *what* changed, but the magnitude inside is itself a random variable. Report deltas with CIs over the stoch sample set, or pin seeds and report a single trajectory?
- Is the addressable population (teams running multi-session memory benchmarks at scale) large enough that this is a contribution, not just a MEM-internal tool?

## Risk Registry (post-premortem)

Five independent failure lenses (full analysis in `premortem_dag_trace_orchestration_agent_benchmarking.md`). All grounded in the actual repo.

| # | Failure mode | Sev × Lik | Score | Top mitigation |
|---|---|---|---|---|
| R1 | det/stoch coloring leaks — LLM-driven consolidation + un-pinned semantic-arm retrieval colored "det" but stochastic; harness blind (only recomputes cone) | C × H | 12 | Color per node-*instance* (probe `background_tokens`/injected client); fail closed on det key for any model-calling node |
| R2 | Unpinned externals (Harbor image, judge model) + backfill scripts rewriting closed records break the append-only prefix invariant → incomparable generations silently merged | C × H | 12 | Key on resolved identities (digests), not name strings; append-only contract guard around `backfill-commit-linkage.mjs`/`commitLinkage` |
| R4 | Built the wrong thing — real interventions (backend/model/ladder-arm swaps) all hit the stoch node → cone = whole graph, cache cold; token cost untouched | C × H | 12 | Run cost-split FIRST as NO-GO; build & beat the embarrassingly-parallel-across-tasks baseline |
| R5 | Validity/publishability — corpus already N=9 single-repo, hard oracle 1/24; systems framing preempted by Shepherd (arXiv:2605.10913, ⚠ verify) | C × H | 12 | Fix per-worktree base-SHA defect (`mem-7q6e`) + add real external repo before any claim; reframe as memory-conditioned validity result |
| R3 | Measurement inconclusive — width median 1, divergence trivial, setup <10% all fire "de-prioritize" at once; 6 months sunk on an empty premise | H × H | 9 | **Phase −1 probe** before Phase 0; pre-register an explicit "empty premise → stop" exit |

**Dominant cross-cutting theme: measure before you build.** Four of five lenses converged on the same structural error — the plan gates the *paper* on Phase-1 measurements but gates *nothing* on running them before building the cache machinery. Prior repo work (`mem-apg.6`: multi-session grid "not constructible"; `mem-n9`: N=9 single-repo, hard oracle 1/24; `mem-1eph`: 3/10 admitted; `mem-7q6e`: corpus-substrate defect) already endangers both the DAG-width premise and the validity claim's discriminating signal.

### Mandatory plan change (supersedes phasing above)

**Insert Phase −1 (one week, ~30-line stand-in, no cache code):** run width-histogram + declared-vs-observed divergence + cost-split on the existing corpus. **Hard NO-GO gates** (pre-registered): median width ≤ 2 → do not build scheduler; setup-fraction < 10% → caching value prop fails; all three null → write the negative-result note and **stop**. Only proceed to Phase 0 build if a gate greenlights it. Also fix the per-worktree base-SHA corpus defect (`mem-7q6e`) before any validity claim, and verify the Shepherd preemption (arXiv:2605.10913) directly before committing to a systems framing.

## Research Provenance

Three independent lenses contributed:

- **Prior Art & Industry Patterns** (Medium-High confidence): confirmed motivating paper is unrelated (real-time DAG scheduling); confirmed target benchmarks run sequentially (MemoryArena verbatim); produced a 15-system capability matrix; identified Dagster code×data versioning and SGLang/KVFlow prefix reuse as directly portable; no system composes the primitives.
- **First-Principles Technical Design** (Medium-High confidence): showed MEM already declares DAG edges and that checkpointing is an index-seek over the append-only corpus; introduced the **det/stoch node coloring** as the economically and methodologically correct decomposition; proposed the minimal build (compile_dag → affected_cone → replay_incremental); inverted the brief's emphasis from parallelism to counterfactual sample-reuse.
- **Strategic Novelty & Contrarian** (Medium-High on the contrarian thesis): NO-GO on the maximalist compiler; located the only defensible research nugget in distribution-correct counterfactual replay validity; flagged parallelism-saves-wall-clock-not-dollars and the frankenstein-trajectory objection; identified MEM's `trace_error` oracle as the empirical backbone for a correctness claim; noted fast-closing white space (Sherlock, Agentic Plan Caching, LLMCompiler, Justitia).

**Convergence:** motivating paper mis-anchored; gap real but primitives mature; hard problem = stochastic memory-conditioned nodes, not scheduling; cache only the deterministic boundary.
**Divergence:** build-the-system (A1/A2) vs scope-to-a-methods-contribution (A3) — resolved here toward the narrow validated core.
