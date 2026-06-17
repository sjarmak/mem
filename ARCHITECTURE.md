# mem — architecture & open decisions

This is a thinking doc, not a spec yet. The data model below is settled; the
**Open Decisions** at the bottom need Stephanie's input before Phase 1 builds
(she'd rather we resolve an ambiguous spec up front; that's what `/grill-me` is
for).

## Data model — the work-audit record

The atomic unit is a **WorkRecord**, keyed by a bead id, joining the whole audit:

```
WorkRecord {
  work_id:    "gascity-dashboard-tnqw"          # bead id (anchor)
  rig:        "gascity-dashboard"
  title, labels, metadata, priority
  lifecycle:  { created, started, closed, status, status_history[] }
  agents:     [ { agent_id: "gc-339244", role: "claude-2",       # from assignee
                  account, trace_ref } ]                          # → JSONL path
  trace:      { jsonl_path, n_turns, tool_calls[], tool_outcomes[],# parsed signal
                errors[ {tool, file, line, message, severity} ] }  # engram-style
  outcome:    { pr: "#63", merged|closed, commit_sha, ci: pass|fail }
  provenance: { work_dir, repo, base_branch,                        # env baseline
                base_commit, history_state: commit-by-date|unresolved }
  signal:     { deterministic: {...}, semantic: {...} }            # the learned bit
  links:      { deps[], convoy_id, supersedes[] }
}
```

The **outcome** field is what makes this a benchmark substrate: every record has
a real, verifiable label (closed/merged/CI), not a synthetic one.

The **provenance** field is the *environment baseline* (distinct from outcome):
the repo + commit a session started from, so a record can be replayed as a
CodeScaleBench-style git-checkout environment. gc records the work dir and
sometimes the base branch but **never the exact base SHA**, so `base_commit` is
an APPROXIMATION: the newest commit on `base_branch` at or before `started_at`
(`git rev-list -1 --before=<started_at> <base_branch>`), flagged
`history_state: commit-by-date`. It is resolved **only** when a base branch was
recorded; resolving against the work_dir's HEAD would walk the agent's own
feature branch (a train/test leak), so an absent base branch is terminal
`unresolved`, never guessed.

## Pipeline stages → modules

1. `ingest/`: readers per source (dolt bead store, JSONL traces, gh PRs, audit
   logs). Output: raw WorkRecords. Pure IO.
2. `parse/`: deterministic extractor (tool exit states + file:line errors,
   ported from engram `capture.ts`/`reflect.ts`) + a model-backed semantic
   extractor (root-cause + resolution approach) run **batched at ingest, once
   per WorkRecord, append-only** (Decision 9). **ZFC: mechanical in code,
   judgment via model.**
3. `store/`: the WorkRecord graph + extracted signal. (See Decision 2.)
4. `retrieve/`: **failure-triggered** query (Decision 8): when an agent hits a
   build/test/lint error, key on the engram deterministic failure signature
   (normalized `file:line` + error-class) and return distilled prior resolutions
   (Decision 9). (See Decisions 6–9.)
5. `bench/`: the eval harness: run an agent *with vs without* retrieved memory
   on held-out tasks, measure outcome lift under the Decision-6/7 contract
   (temporal leave-one-out, dual-track) with the Decision-10 precision guard.
   Mirrors codeprobe/enterprisebench.

## What we port from engram (already reviewed)

- Deterministic capture from build/test/lint output → recurring-failure signal
  (the one proven, ZFC-clean mechanism). `capture.ts` + `reflect.ts` confidence
  formula (`unique_traces / total`).
- Marker-bounded deterministic render (store is truth; any context file is a
  regenerated projection). Fixes the bloat failure mode.
- **Skip:** bBoN, hybrid retrieval, GUI adapters, the unwired helpful/harmful
  stub, the regex keyword memory-tier classifier (ZFC violation).

## Decisions (resolved 2026-06-04, Stephanie)

1. **Benchmark = outcome lift (headline) + retrieval precision (intermediate).**
   The real question: does retained/retrieved memory improve success rate, cut
   iterations, cut cost on new work. Retrieval precision is an instrument toward it.
2. **First milestone = the work-audit graph builder** (Phase 1 below). Map every
   bead↔agent↔trace↔PR↔outcome across all rigs into a queryable store. Useful as
   an audit tool on its own, before any memory/retrieval exists.
3. **Store = bead store as spine + a sidecar for trace-derived signal.** The dolt
   bead store already holds the work spine; the sidecar holds parsed trace signal
   + a trace index. (Sidecar substrate, SQLite vs a dolt db, decided in P1.5.)
4. **Retrieval v1 = structured/keyword over the work-audit graph.** Cheap,
   deterministic, available now. Embeddings only if structured underperforms
   (and would need an embedding lane; mind the scix no-paid-API constraint).
5. **Eval task source = replay closed historical beads first** (outcomes already
   known = an instant labeled set); live-shadow new beads later.

## Decisions — eval & retrieval contract (resolved 2026-06-04, grill-me r2)

These resolve the load-bearing branches under Decisions 1/3/4/5, the part that
makes the "outcome lift" number real. Grounded in the literature pass (see
*Literature grounding* below).

6. **Eval contract = temporal leave-one-out + duplicate audit.** When evaluating
   bead B, the retrievable set = only WorkRecords for beads **closed strictly
   before `B.started`** ("memory as it existed when the work began"; realistic
   *and* structurally excludes B and any future leak). Also explicitly exclude
   B's **convoy siblings, supersedes-chain, and any bead sharing B's PR/branch**
   (same work, dodges the timestamp filter). **Duplicate audit:** flag held-out
   B whose top retrieved neighbor is a near-duplicate (same `file:line` fix
   signature) and report lift with *and* without that slice. The original merged
   PR/CI is the **oracle** for the fresh replay's outcome, never a label the
   agent sees.
7. **Dual-track, report both.** *Strict/headline* = **cross-rig retrieval only**
   (leak-proof; measures cross-project transfer). *Realistic/secondary* =
   **same-rig temporal-LOO + the duplicate audit** (how memory is actually used;
   where lift should be largest). `bench/` carries a `retrieval_scope:
   cross_rig | same_rig_temporal` knob. Same-rig ≫ cross-rig lift is a *finding*,
   not a flaw.
8. **Retrieval trigger v1 = failure-triggered spine.** Memory fires when the
   agent hits a build/test/lint error, keyed on the engram deterministic failure
   signature (rig-agnostic by construction → works cross-rig; the one ZFC-clean
   mechanism we trust). Structured fields (tool, language, severity) filter;
   keyword on message is a weak tiebreaker. The v1 claim: **cuts
   iterations-to-green + raises eventual pass rate**, does not prevent the first
   failure. Task-start *semantic* retrieval deferred to Phase 2 (needs embeddings
   per Decision 4's gate + the scix no-paid-API constraint).
9. **Retrieval payload = distilled structured lesson, not literal diff.** Default
   payload = a model-extracted **root-cause + resolution approach + dep links**
   (the `signal.semantic` field) **+ a citation** (`bead_id` + `commit_sha`) so
   it's auditable and the agent can recover full detail. Extracted **once at
   ingest, append-only, never iteratively rewritten** (continuous LLM rewriting
   degrades consolidated memory; see lit). On the **same-rig track only**,
   additionally attach the literal `file:line` + commit ref (may apply directly),
   still leading with the distilled note. **Never** inject the raw prior trace.
   Keep the lesson reasoning-preserving, not an atomic one-liner.
10. **Precision guard on every lift run.** Returning the whole store gets recall
    1.0 and can pass answer-quality evals, so outcome-lift alone is gameable by
    over-injection. The bench **measures injected-context volume + retrieval
    precision as a first-class guard on each lift run** (not an optional side
    metric). This upgrades Decision 1's "retrieval precision = intermediate" into
    a *required* guard and composes with the Decision-6 duplicate audit.

## Decisions — competitive arms + 5-axis controller telemetry (reconciled 2026-06-05 to the eval-harness spec)

> **Authoritative eval-harness spec.** `.gc/memory-eval-harness-spec.md` (the
> 17-section *Agentic Memory Evaluation Harness* spec, Stephanie 2026-06-05) is now
> the **authoritative** spec for the eval work. Decisions 1–10 remain the locked
> retrieval/eval contract, reconciled to the spec via `.gc/docs/phase-2.5-plan.md` §A
> (concept map + divergence register); where they conflict, the spec governs.
> Decisions 11–16 below were reconciled to the spec and resolved by Stephanie
> (§A.5, 2026-06-05).

11. **Competitive-arm contract.** External memory systems run as arms behind one
    uniform `ingest`/`retrieve` interface, on identical replay tasks / oracle /
    `retrieval_scope` / precision-guard / telemetry. The harness owns the
    **LOO-bounded ingest set** (validity constraint V1: each arm ingests only
    WorkRecords closed strictly before `B.started`, with convoy siblings /
    supersedes-chain / shared-PR beads excluded); arms never read the store.
    OSS / self-hosted only (Decision 4); an arm that can't run without a paid API
    is dropped and the drop is documented (per the sourcing pass: none dropped).
    Per-arm token + latency overhead is **reported, not hidden** (V4). Reconciled
    to the spec (§A): arms map to the spec's **conditions**
    (`no_memory` / `oracle` / `memory_enabled`) + `memory_systems/` entries
    (DIV-6); the new **`oracle`** condition is the task-validity gate
    (`oracle ≈ no_memory ⇒ reject task`, DIV-3); **Harbor** is the execution
    substrate, with our harness logic as adapters + scorers (DIV-7).
12. **Versioned 5-axis telemetry schema.** One durable, versioned telemetry record
    per replay run and per live-shadow event, measuring all five controller axes
    (task-perf, token-budget, latency, privacy, interruption) from the first lift
    run, including privacy and interruption, which v1 **measures but does not act
    on**. Reconciled to the spec (§A, DIV-4): the canonical schema is the spec's
    `memory_event` + `trace` + `metrics` (task / efficiency / retrieval / retention
    / synthesis / action_impact), **serialized as OpenTelemetry GenAI spans
    (primary) + ATIF derived** (interop, no single-vendor lock-in), **extended**
    with additive `privacy_metrics` + `interruption_metrics` groups (the two
    north-star axes the spec §12 omits). The record also logs the per-stage
    controller decisions and the full **`rerank_features`** vector (relevance /
    recency / importance / trust / task-fit + procedural / relational), plus
    `agent_type` and `storage_tier` (G1–G4 vocabulary, naming only), as measurable
    features, the learned controller's inputs, captured from run one.
13. **Memory taxonomy, SUPERSEDED by the spec's two-level model.** DIV-5: the flat
    four-type list (semantic / episodic / preference / reflection) drafted pre-spec
    is **superseded** by the spec's two-level taxonomy: **representation**
    {filesystem / vector / kg} × **`candidate_memory.type`** {episodic / semantic /
    procedural / preference / entity / relationship / failure_pattern}. The store
    represents these distinctly (representation is itself a lever); retrieval is
    multi-type and `memory_types_retrieved` is logged. The pre-spec "reflection"
    folds into procedural + episodic; "preference" stays. Each external arm's type
    coverage (§1a) is part of the comparison, confirmed empirically during adapter
    build.
14. **Controller-loop framing + write/reflect interface + agent-type conditioning.**
    The controller is a **6-stage loop** (need-classification → query-formation →
    multi-type retrieval → reranking → minimal-useful injection → post-task write
    decision); v1 fills each learnable stage with a heuristic / LLM-judge and logs
    its decision. The controller is **exposed as an MCP server**
    (`retrieve` / `write` / `reflect` tools) so it drops into any agent loop
    unchanged. The post-task write/reflect interface (§1b) is designed now:
    **append-only** (Decision 9, never iteratively rewrites); replay writes go to a
    **per-run scratch store**, never the LOO-bounded corpus. `agent_type` is a
    conditioning variable and an eval-breakdown dimension; city traces are a
    coding/orchestration workflow and results are reported as such, with PA /
    research generalization treated as a distinct future axis.
15. **NVIDIA-stack posture (consume OSS / contribute / avoid).** **Adopt** the
    verified self-hostable pieces (§1c): NAT as a bake-off arm + optional harness
    (local LLM via `_type: openai`; **Redis / custom local memory backend only**;
    its Mem0/Zep-cloud defaults are NOT no-paid-API-clean and are not our route to
    the mem0/graphiti arms); OTel-primary + ATIF-derived telemetry (Decision 12);
    the G1–G4 storage-tier vocabulary on the latency axis; the controller exposed as
    an MCP server (Decision 14); **Nemotron-3 Nano** as a self-hosted local judge
    candidate (NVIDIA Open Model License, not OSI-OSS); eval legibility via
    **NeMo-Evaluator** / **lm-evaluation-harness** with **RULER** as the
    long-context yardstick; trace-curation filters pre-ingest. **Avoid** all GPU /
    NIM / paid-gated NVIDIA components (vocabulary/reference only). **Contribute**
    our differentiation: the multi-agent-orchestration memory benchmark, the
    **privacy + interruption** axes NVIDIA's published material doesn't evaluate,
    and beyond-lexical procedural/relational graph rerank.
16. **No-paid-API scope + eval object + roadmap re-baseline (§A.5 resolutions).**
    **DIV-1 (RESOLVED, Stephanie, 2026-06-08):** `no-paid-API` (Decision 4) is
    scoped to the **memory stack only** (backends / embeddings / extractor / judge =
    OSS / self-hosted). It does **NOT** cover the **agent-under-test**, Harbor, or
    Docker. Harbor (Apache-2.0) and Docker are free. The agent-under-test (Claude
    Code / Opus / Sonnet / Haiku) runs on our Claude **OAuth subscription**, which is
    flat-rate and already paid; it is **not** a metered paid API and carries **no
    per-run marginal cost**. Therefore running Claude agents through Harbor/Docker for
    the eval is **not a "paid" action, not a cost/defensibility fork, and not a
    wake-me / escalation trigger**. Do not re-litigate this: building the
    closed-bead→Harbor execution + verifier path and running the conditions across the
    held-out set is in-scope mechanism work, not a paid-infra ask. **DIV-2:** the eval object is **multi-session
    sequences** (Step1→…→Goal, fresh context per step, memory persists); bead replay
    becomes **one source** feeding sequence construction, with the Decision-6
    temporal-LOO / no-leak discipline preserved as the per-step context reset.
    **DIV-10:** the roadmap is **re-baselined onto the spec's 5 phases** (skeleton →
    real dataset → metrics/diagnostics → synthetic generator → research loop);
    retrieval-v1 (`mem-di8`) is the first `ours` memory_system under that frame.
17. **mem-apg headline is ABLATION-based, not merged-PR/CI outcome-lift (Stephanie,
    2026-06-08).** First-principles data finding (bead `mem-apg.5`): the work-audit
    corpus does **not** carry the bead→PR→repo→commit linkage the merged-PR/CI oracle
    needs. Across 5977 closed records only ~14 have a PR number, ~7 a commit_sha, ~1–2
    a repo, and exactly **1** an `external_ref`. So the locked outcome-success-rate
    headline (Decision 5/`mem-6sl`) is **structurally uncomputable for this corpus at
    scale**. This is the mem-ml9 wall diagnosed at the data layer, not a wiring gap
    (`mem-bme` wired the schema + mapper, but the inputs were never recorded).
    Therefore the **mem-apg headline is the ablation score-vs-information curve**
    (saturation point + minimum-useful information combination), which is
    env/label-independent: the agent is its own control across an information ladder,
    so it needs neither a reconstructed env nor a ground-truth outcome label. The
    **merged-diff oracle is opportunistic validation** on the ~handful of beads that do
    carry PR/commit metadata, never the headline. Outcome-ingest wiring (`mem-apg.5`)
    stays **deferred**; do not re-attempt the gh re-ingest; the source data is absent,
    not unwired. **Ablation scoring (resolved 2026-06-08):** per-rung `reward` is a
    *deterministic* check on data the corpus actually has: did the agent's run
    avoid/resolve the held-out task's known `trace_error` (the signal the held-out set
    is defined by). It is joined by an OSS/self-hosted, calibrated LLM-judge `rubric_score`
    for semantic completion quality (spec §12.1: deterministic-where-possible, OSS
    judge for semantic only; no-paid-API applies to the judge). The headline curve is
    reward-vs-information-rung; saturation point + minimum-useful information
    combination are read off it. Scores use data the corpus *has* (trace errors), not
    data it *lacks* (PR outcomes); ZFC-clean (deterministic signature match +
    delegated semantic judgment).

## Literature grounding (`~/lit_explorers`, agentic-memory pass 2026-06-04)

The eval/retrieval contract above is backed by the memory-systems literature
(explorer source: `~/lit_explorers/build_memory_design_explorer.py`):

- **Distilled-over-literal payload (Decision 9):** Atlas / *"Compiled Memory:
  More Precise Instructions, Not More Information"* (Rhodes & Kang); Slack's
  production system moved to *"distilled truth"* over chat logs (De Simone).
  *Caution*, *"Beyond Atomic Facts"* (Sun et al.): don't over-compress to atomic
  facts; keep the lesson reasoning-preserving.
- **Extract once, never rewrite (Decision 9):** *"Useful Memories Become Faulty
  When Continuously Updated by LLMs"* (Zhang et al.): iterative LLM rewriting
  degrades consolidated memory; *"Agentic Context Engineering"* names brevity
  bias + context collapse. The citation guards against brevity bias.
- **Batched ingest extraction (parse stage):** RecMem (Dai et al.): eager
  per-interaction LLM consolidation is the main token-cost driver; batch/defer.
- **Failure-triggered, not retrieve-always (Decision 8):** *"To Retrieve or To
  Think?"* (Chen et al.): RAG-at-every-step wastes compute and can degrade
  performance.
- **Precision guard (Decision 10):** *"Structured Belief State & the First
  Precision-Aware Benchmark"* (Flynt): returning the entire store yields recall
  1.0 and passes answer-quality evals, so answer-correctness can't validate
  retrieval. Production threads (*"What are people actually using…"*; Wolff &
  Bennati, Mem0-vs-Graphiti) confirm semantic-closeness-only pulls stale context
  and "the same mistakes recur"; exactly the failure-recurrence thesis.
- **SQLite+FTS prior for the P1.5 sidecar substrate:** *"What I Learned Building
  a Memory System for My Coding Agent"*: SQLite + FTS5 covers a lot, no vector
  DB needed. (Informs but does not pre-decide P1.5.)

## Phase 1 — work-audit graph builder (the backlog, beads `mem-*`)

- **P1.1 scaffold**: TS project (reuse engram's `capture.ts`/`reflect.ts` for the
  deterministic layer), `src/{ingest,parse,store,retrieve,bench}/`, CLI entry.
- **P1.2 ingest/beads**: dolt reader over ALL rigs → WorkRecord spine
  (id, rig, assignee, status, lifecycle, external-ref, labels, metadata).
- **P1.3 ingest/traces**: resolve `assignee → session id → JSONL path`; index
  every trace file; attach `trace_ref` to its WorkRecord.
- **P1.4 ingest/outcomes**: `external-ref`/branch → gh PR/commit → outcome
  (merged|closed, commit_sha, CI pass|fail).
- **ingest/provenance**: `gc.work_dir`/`gc.var.base_branch` metadata → repo +
  session-start `base_commit` (commit-by-date), the git-checkout env baseline.
  Wired into `build-store --with-provenance`.
- **P1.5 store**: the WorkRecord graph + sidecar schema + writer; marker-bounded
  deterministic render (store is truth). Decide sidecar substrate here.
- **P1.6 parse/deterministic**: port engram capture/reflect: tool-call outcomes
  + file:line build/test/lint errors from traces; cross-task recurrence confidence.
- **P1.7 mem CLI**: query the graph by work_id / agent / rig / outcome.

Phase 2 (after P1): retrieval + the replay eval harness (with-vs-without memory).
