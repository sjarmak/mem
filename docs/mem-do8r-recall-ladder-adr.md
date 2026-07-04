# mem-do8r.1 — Recall-ladder experimental design (ADR)

**Bead:** mem-do8r.1 (parent mem-do8r, label aiewf-2026) · **Status:** DESIGN-DRAFT
only, branch-ready — options + one recommendation per item, for Stephanie to lock ·
**Date:** 2026-07-03

Adopt the Sakana (AIEWF 2026, Stefania Druga) recall ladder to pin mem's open
headline metric: **fix the model, vary ONLY the recall arm** across four rungs —
none / vector-RAG / ranked decisions-ledger / oracle. Sakana's result: a
ranked-only decisions ledger beat vector-similarity RAG for long-horizon recall;
recall policy is a first-class metric. Sources: `~/brain/AIEWF 2026/08 - Sakana
AI - Memory Harnesses (Stefania Druga).md`, `~/brain/AIEWF 2026/Concepts/Action
Plan - Our Setup.md` (mem section).

Nothing in this ADR is implemented; the tree stays under the mem-0rrf
publication freeze. Every DECISION below is Stephanie's to accept or redirect.

## Verdict at a glance

| # | Decision | Recommendation | One-line why |
|---|----------|----------------|--------------|
| 1 | Arm mapping | **All four rungs have existing arms — build nothing new.** none=`none`, vector-RAG=`nemo-embed` (primary) with `mem0` as replicate, ledger=`ours`, ceiling=`oracle`. | Registry already ships 14 arms behind one interface; the only missing wiring is a vector-RAG rung in the grid driver, plus a distill pass so the ledger is non-empty. |
| 2 | Held-out task set | **Two-track: synthetic gated worlds primary, small real pool as credibility anchor — never pooled.** | Only the synthetic track can supply enough memory-DEPENDENT tasks (necessity gate rejects oracle≈none); every real pool we've run has saturated (pjh8 lift=1.0 at N=8) or was ceiling-limited (warm-vs-cold neutral). |
| 3 | Leak-guard / anti-cheat | **Reuse the existing stack unchanged; pin the LLM judge OUT of the pass/fail loop.** | Strict LOO + exclusions + `assert_no_leak` + `leak_guard` + per-trial store isolation already cover the ladder; the SWE-Marathon lesson says the verifier is the attack surface, so pass/fail stays deterministic. |
| 4 | Cost columns | **Instrument from run one using existing fields; no new plumbing.** | `EfficiencyMetrics` + per-event token/latency + `injected_context_chars` already exist; the ladder report just has to surface them per rung. The fits-in-context cost delta is a headline cell (mem-1m0s). |
| 5 | Cohort / stats | **≥30 gated tasks × 4 rungs × 3 repeats; lift = paired per-task pass-rate delta vs `none` with bootstrap CI; cost is the co-primary axis.** | Paired deltas, never pooled means (standing gate instruction); no conclusion promoted off a single run. |

## 1. Arm mapping — which existing arms realize each rung

The bead's candidate mapping is **confirmed with two corrections** (vector-RAG
arm choice; ledger prerequisite). Registry: `memory_systems/__init__.py:82-116`
(14 arms, one `MemorySystem` interface at `base.py:77-121`). No rung lacks a
clean existing arm.

| Rung | Arm | What it does today | What it still needs |
|------|-----|--------------------|---------------------|
| 1 none | `none` (`none_system.py:13`) | Stateless floor; empty retrievals, writes unsupported. | Nothing. |
| 2 vector-RAG | `nemo-embed` primary (`nemo_embed_system.py:213`); `mem0` replicate (`mem0_system.py:152`) | Real dense-embedding top-k. nemo-embed = exact in-process cosine over NeMo embeddings, no external daemon; mem0 = Qdrant ANN + Ollama embedder. | A vector-RAG rung in the grid driver: `run_grid` rungs default to `("none","ours","oracle")` (`harbor/grid.py:147`) and there is no semantic-arm payload injector analogous to `ours_payloads`. This is the one net-new wiring item. |
| 3 ranked ledger | `ours` (`ours_system.py:90`) | Failure-triggered ranked retrieval via `mem retrieve --json` over the work-audit graph; payload = citation + append-only `lessons` per work_id — literally a ranked decisions ledger (`ours_system.py:54-64,149-150`). | **A populated ledger.** The eval store's `lessons` table is empty (verified 2026-07-03: `store-bxhh2-v8.db` lessons=0; canonical `store.db`=17), which is exactly why `bundle_grid.py:18-24` marks `ours` NOT runnable. Prerequisite: `mem distill-lessons` over the ladder's store, with the export/import round-trip discipline (lessons don't survive rebuilds). |
| 4 oracle | `oracle` (`oracle_system.py:14`) | Harness-injected exact ground truth; perfect id-exact recall (`oracle_system.py:31-49`). Bundle assembly exists: gold-diff required tier + consensus-curated reference tier, 50-file cap (`oracle/build.py:52-243`). | Nothing structural; oracle bundles are built per task. |

**Options considered for rung 2:**

- (a) `nemo-embed` — deterministic exact top-k, no daemon, local GPU only.
  Cleanest attribution: any rung-2 loss is the recall policy's, not ANN
  nondeterminism or infra flake. **Recommended primary.**
- (b) `mem0` — the canonical third-party "vector RAG" readers will expect;
  needs Qdrant + Ollama. **Recommended as a replicate arm** (one confirmation
  run set), not the primary.
- (c) `a-mem` / `nat` / `graphiti` — heavier infra (ChromaDB / redis-stack /
  FalkorDB+reranker); they answer "which vector system", not "does vector-RAG
  as a policy beat a ranked ledger". Annex, not ladder.
- (d) `lexical` (`lexical_system.py:36`) — cheap lexical control; optional
  annex column, useful to show the rung-2 result isn't embedding-specific.

**Deliberate exclusions from the ladder:** `builtin` (agent-native memory
varies the agent channel, not the recall policy — violates fix-the-model);
`ours-live` (adds a write policy; the ladder is a replay/read eval — rung 3 is
read-only `ours`); `consolidating`/`retention_scheduled` (write-management
arms, same reason). The existing ablation rung set
(`grading/ablation.py:22-28`: none/ours/builtin/ours+builtin/oracle) is the
Sakana ladder with builtin in place of vector-RAG — this design swaps that one
rung and keeps everything else.

**Query-form bridge (known seam, existing solution):** `ours` is
failure-triggered (`query_work` + scope), vector arms are `query_text`-driven.
`membench/compare/retrieval_compare.py` already puts both families on one
LOO-bounded surface with backend-id→work_id translation — reuse it; do not
invent a second bridge.

## 2. Held-out task set + temporal LOO boundary

**LOO machinery: reuse unchanged.** Reader `closedBefore` is strict
(`closed_at < B.started`, `src/store/reader.ts:31-51`); supersedes closure is a
recursive CTE (`reader.ts:142-155`); convoy/PR/branch sibling exclusion is
`src/retrieve/exclusions.ts:18-24`; the Python harness enforces all of it via
`validity.loo_bounded` / `assert_no_leak` (`replay.py:30`). Weakening any of
these leaks the answer; the ladder changes none of them.

**Task-pool options:**

- (a) **Real ftp corpus** (`memory-bench/data/ftp-oracle/`): 32 behavioral
  fail-to-pass tasks (scix 25, codeprobe 7). Real and executable, but two
  disqualifiers as the primary pool: it's too small to power four arms, and
  the mem-bxhh.3 validity fork found ftp targets are **eval anchors, not
  work-records** — rung 3 retrieves nothing for them, degenerating the ladder.
  Usable only for the record-anchored subset.
- (b) **Real closed WorkRecords with sound oracles** (workrecord_adapter
  ladder): the mem-apg path; cumulative sound N=8, and that pool saturated
  (mem-pjh8.1: lift 1.0 on both channels at N=8 — trivial tasks reproduce a
  null). Credibility anchor, not primary.
- (c) **Synthetic gated worlds** (`generators/`, frozen under
  `fixtures/worlds/`): memory-dependence is enforced **by construction** — the
  necessity gate runs each sequence under exactly (no_memory, oracle) with the
  deterministic ScriptedAgent and admits it only if oracle beats no-memory by
  > epsilon (`generators/memory_necessity_gate.py:58-76`,
  `pilot_filter.py:34-64`). This is the mechanical counter to the saturation
  failure mode. Current inventory is thin — one frozen world, 2 sequences —
  so world generation to ~30+ gated sequences is the pool-side work item.

**Recommendation: two-track.** Primary = (c) synthetic worlds, all sequences
through the necessity gate, frozen with the determinism manifest
(`world_manifest.py`) before any arm runs. Secondary = the record-anchored
real subset from (a)+(b), reported alongside as the external-validity anchor,
**never pooled** with the synthetic numbers. Synthetic realism stays flagged
(mem-ovi: structural KS fail, semantic pass) — the real track is what keeps
the headline honest.

## 3. Leak-guard / anti-cheat — rung isolation

Existing stack, all reused as-is:

- **Store isolation between arms:** each semantic arm owns its store, scoped
  per-trial (`semantic_base.py:88-92,134-145` asserts globally-unique trial
  ids; unique on-disk store paths per run — the mem-lvp.12 fix). `oracle` is
  a harness-injected dict; `none` has nothing. `ours` is the exception — it
  reads the shared SQLite sidecar — and is bounded by strict LOO +
  `assert_no_leak` (`ours_system.py:9-16`). No arm can read another's store.
- **Task-construction leak scan:** `grading/leak_guard.py:51-86` scans every
  agent-readable file for the high-entropy outcome identifiers
  (`pr`, `commit_sha`, `base_commit`) before any task dir is written
  (`workrecord_adapter.py:125`); raises loudly on hit.
- **Condition isolation:** oracle content appears only in the oracle
  condition's `instruction.md` (`harbor/adapter.py:39-56`); per-condition
  continuity roots (`conditions.py:348-351`) keep arms from sharing state.
- **Verifier hardening (the SWE-Marathon lesson — a weak verifier in a loop is
  an attack surface):** the pass/fail oracle stays **deterministic** —
  `repro_passed` gold-test reproduction (`harbor/grid_action_impact.py:5`,
  dual verifier per mem-75t.7.5). The LLM judge (`grading/judge.py`) remains
  an L3 report-only column, never the loop's reward; safety gates
  (`safety_gates.py`) stay OUTSIDE `metrics()` as today. The agent never sees
  or writes verifier state; rewards land in the verifier's own channel
  (`/logs/verifier/reward.txt`).

**Recommendation:** no new anti-cheat machinery. Lock two pins: (i) necessity
gate is mandatory for pool admission (§2), (ii) judge-out-of-loop as above.

## 4. Cost columns per arm (mem-1m0s)

Everything needed already exists — the work is surfacing it per rung in the
ladder report, not new instrumentation:

- Per run: `EfficiencyMetrics` (`schemas/metrics.py:33-47`) — `total_tokens`,
  `input_tokens`, `output_tokens`, `wall_clock_latency_ms`, `model_latency_ms`,
  `tool_latency_ms`, `cost_usd`, `turns`, tool-call counts.
- Per memory event: `latency_ms`, `token_count_in/out`
  (`schemas/memory_event.py:48-50`) — stamped by every arm.
- Per arm×track: `injected_context_chars` (`replay.py:50-52`) and
  `token_budget_chars` (`report/arm_vector.py:31-77`) — the injected-volume
  guard, so over-injection can't fake a win.

**Recommended report columns per (task, rung, repeat):** pass, tokens
(in/out/total), wall-clock + model latency, injected context volume, memory-op
count. Plus one derived headline cell: the **fits-in-context cost delta** —
on tasks the `none` rung already passes, report each memory rung's token/latency
delta at zero capability gain ("bad memory is expensive" as a measured negative
result). Overflow-gated activation (the second half of mem-1m0s) is a policy
change, not a measurement — out of ladder v1, stays on the sibling bead.

## 5. Cohort / stats plan

- **Fixed model:** one agent model + config across all rungs (the headless
  runner), pinned in the run manifest. Any model change is a new cohort.
- **N:** target ≥30 gated synthetic sequences × 4 rungs × **3 repeats**
  (`repeat_idx`, keyed `(work_id, rung, repeat_idx)` — `harbor/grid.py:150`),
  ≈360 runs, plus the small real anchor track. Local stack is serial-ish
  (Sakana's own caveat: local evals take days) — 3 repeats is the floor that
  still supports pass^3; 5 if wall-clock allows.
- **Lift (options):** (a) paired per-task pass-rate delta vs `none`;
  (b) efficiency delta (tokens/turns) at equal pass — the current bundle-grid
  headline shape; (c) cost-adjusted "true cost" = tokens ÷ pass rate.
  **Recommendation:** (a) is primary on the gated pool — the necessity gate
  guarantees headroom by construction, so success-rate delta is meaningful
  again; (c) is the co-primary cost axis (one number per rung); (b) stays as
  report columns. Per-task **paired deltas, never pooled means** (the
  mem-75t.7.6 gate instruction), bootstrap CI over tasks; consistency via
  pass^N (the mem-ap16 ADOPT).
- **Promotion rule:** no conclusion off a single run; a rung ordering is
  reported as a finding only when the paired-delta CI excludes zero. Expected
  shape if Sakana replicates: `none < vector-RAG < ours ≤ oracle`, with
  `oracle` NOT at 1.0 (it supplies memory but doesn't force use) — an
  oracle ceiling well below 1.0 is itself reportable, not a bug.
- **ZFC boundary:** rung-3 ranking stays the mechanical retrieval-v1 contract
  (failure-keyed, deterministic). Any learned-utility rerank (outcome-weighted
  retrieval, mem-pu2s) is model-delegated and **out of ladder v1** — it would
  change the rung definition mid-benchmark.

## Stephanie's locks (accept/redirect, one minute each)

1. **Rung-2 arm:** nemo-embed primary + mem0 replicate? (Or mem0 primary for
   external legibility, accepting ANN/infra variance.)
2. **Ledger prerequisite:** approve a distill pass over the ladder store so
   rung 3 is live (lessons currently 0 in the eval store) — with
   export/import round-trip. This touches store content, not source, but is a
   run under the freeze, so it's your call.
3. **Task pool:** two-track (synthetic-gated primary + real anchor) as
   recommended? And the target of ~30 gated sequences (means new world
   generation work).
4. **Lift definition:** paired pass-rate delta primary + true-cost co-primary?
5. **Repeats:** 3 (floor) vs 5 (budget permitting).
6. **Headline framing:** pin the ARCHITECTURE status table's open metric as
   "recall-policy lift curve: pass-rate and cost vs rung (none / vector-RAG /
   ranked ledger / oracle)".
