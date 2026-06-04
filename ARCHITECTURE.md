# mem — architecture & open decisions

This is a thinking doc, not a spec yet. The data model below is settled; the
**Open Decisions** at the bottom need Stephanie's input before Phase 1 builds
(she'd rather we resolve an ambiguous spec up front — that's what `/grill-me` is
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
  signal:     { deterministic: {...}, semantic: {...} }            # the learned bit
  links:      { deps[], convoy_id, supersedes[] }
}
```

The **outcome** field is what makes this a benchmark substrate: every record has
a real, verifiable label (closed/merged/CI), not a synthetic one.

## Pipeline stages → modules

1. `ingest/` — readers per source (dolt bead store, JSONL traces, gh PRs, audit
   logs). Output: raw WorkRecords. Pure IO.
2. `parse/` — deterministic extractor (tool exit states + file:line errors,
   ported from engram `capture.ts`/`reflect.ts`) + a model-backed semantic
   extractor (root-cause + resolution approach) run **batched at ingest, once
   per WorkRecord, append-only** (Decision 9). **ZFC: mechanical in code,
   judgment via model.**
3. `store/` — the WorkRecord graph + extracted signal. (See Decision 2.)
4. `retrieve/` — **failure-triggered** query (Decision 8): when an agent hits a
   build/test/lint error, key on the engram deterministic failure signature
   (normalized `file:line` + error-class) and return distilled prior resolutions
   (Decision 9). (See Decisions 6–9.)
5. `bench/` — the eval harness: run an agent *with vs without* retrieved memory
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
   + a trace index. (Sidecar substrate — SQLite vs a dolt db — decided in P1.5.)
4. **Retrieval v1 = structured/keyword over the work-audit graph.** Cheap,
   deterministic, available now. Embeddings only if structured underperforms
   (and would need an embedding lane — mind the scix no-paid-API constraint).
5. **Eval task source = replay closed historical beads first** (outcomes already
   known = an instant labeled set); live-shadow new beads later.

## Decisions — eval & retrieval contract (resolved 2026-06-04, grill-me r2)

These resolve the load-bearing branches under Decisions 1/3/4/5 — the part that
makes the "outcome lift" number real. Grounded in the literature pass (see
*Literature grounding* below).

6. **Eval contract = temporal leave-one-out + duplicate audit.** When evaluating
   bead B, the retrievable set = only WorkRecords for beads **closed strictly
   before `B.started`** ("memory as it existed when the work began" — realistic
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
   keyword on message is a weak tiebreaker. The honest v1 claim: **cuts
   iterations-to-green + raises eventual pass rate**, does not prevent the first
   failure. Task-start *semantic* retrieval deferred to Phase 2 (needs embeddings
   per Decision 4's gate + the scix no-paid-API constraint).
9. **Retrieval payload = distilled structured lesson, not literal diff.** Default
   payload = a model-extracted **root-cause + resolution approach + dep links**
   (the `signal.semantic` field) **+ a citation** (`bead_id` + `commit_sha`) so
   it's auditable and the agent can recover full detail. Extracted **once at
   ingest, append-only, never iteratively rewritten** (continuous LLM rewriting
   degrades consolidated memory — see lit). On the **same-rig track only**,
   additionally attach the literal `file:line` + commit ref (may apply directly),
   still leading with the distilled note. **Never** inject the raw prior trace.
   Keep the lesson reasoning-preserving, not an atomic one-liner.
10. **Precision guard on every lift run.** Returning the whole store gets recall
    1.0 and can pass answer-quality evals, so outcome-lift alone is gameable by
    over-injection. The bench **measures injected-context volume + retrieval
    precision as a first-class guard on each lift run** (not an optional side
    metric). This upgrades Decision 1's "retrieval precision = intermediate" into
    a *required* guard and composes with the Decision-6 duplicate audit.

## Literature grounding (`~/lit_explorers`, agentic-memory pass 2026-06-04)

The eval/retrieval contract above is backed by the memory-systems literature
(explorer source: `~/lit_explorers/build_memory_design_explorer.py`):

- **Distilled-over-literal payload (Decision 9):** Atlas / *"Compiled Memory:
  More Precise Instructions, Not More Information"* (Rhodes & Kang); Slack's
  production system moved to *"distilled truth"* over chat logs (De Simone).
  *Caution* — *"Beyond Atomic Facts"* (Sun et al.): don't over-compress to atomic
  facts; keep the lesson reasoning-preserving.
- **Extract once, never rewrite (Decision 9):** *"Useful Memories Become Faulty
  When Continuously Updated by LLMs"* (Zhang et al.) — iterative LLM rewriting
  degrades consolidated memory; *"Agentic Context Engineering"* names brevity
  bias + context collapse. The citation guards against brevity bias.
- **Batched ingest extraction (parse stage):** RecMem (Dai et al.) — eager
  per-interaction LLM consolidation is the main token-cost driver; batch/defer.
- **Failure-triggered, not retrieve-always (Decision 8):** *"To Retrieve or To
  Think?"* (Chen et al.) — RAG-at-every-step wastes compute and can degrade
  performance.
- **Precision guard (Decision 10):** *"Structured Belief State & the First
  Precision-Aware Benchmark"* (Flynt) — returning the entire store yields recall
  1.0 and passes answer-quality evals, so answer-correctness can't validate
  retrieval. Production threads (*"What are people actually using…"*; Wolff &
  Bennati, Mem0-vs-Graphiti) confirm semantic-closeness-only pulls stale context
  and "the same mistakes recur" — exactly the failure-recurrence thesis.
- **SQLite+FTS prior for the P1.5 sidecar substrate:** *"What I Learned Building
  a Memory System for My Coding Agent"* — SQLite + FTS5 covers a lot, no vector
  DB needed. (Informs but does not pre-decide P1.5.)

## Phase 1 — work-audit graph builder (the backlog, beads `mem-*`)

- **P1.1 scaffold** — TS project (reuse engram's `capture.ts`/`reflect.ts` for the
  deterministic layer), `src/{ingest,parse,store,retrieve,bench}/`, CLI entry.
- **P1.2 ingest/beads** — dolt reader over ALL rigs → WorkRecord spine
  (id, rig, assignee, status, lifecycle, external-ref, labels, metadata).
- **P1.3 ingest/traces** — resolve `assignee → session id → JSONL path`; index
  every trace file; attach `trace_ref` to its WorkRecord.
- **P1.4 ingest/outcomes** — `external-ref`/branch → gh PR/commit → outcome
  (merged|closed, commit_sha, CI pass|fail).
- **P1.5 store** — the WorkRecord graph + sidecar schema + writer; marker-bounded
  deterministic render (store is truth). Decide sidecar substrate here.
- **P1.6 parse/deterministic** — port engram capture/reflect: tool-call outcomes
  + file:line build/test/lint errors from traces; cross-task recurrence confidence.
- **P1.7 mem CLI** — query the graph by work_id / agent / rig / outcome.

Phase 2 (after P1): retrieval + the replay eval harness (with-vs-without memory).
