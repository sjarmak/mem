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
   extractor (approach, decisions). **ZFC: mechanical in code, judgment via model.**
3. `store/` — the WorkRecord graph + extracted signal. (See Decision 2.)
4. `retrieve/` — query: given a task/context, return relevant prior WorkRecords.
   (See Decision 3.)
5. `bench/` — the eval harness: run an agent *with vs without* retrieved memory
   on held-out tasks, measure outcome lift. Mirrors codeprobe/enterprisebench.

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
