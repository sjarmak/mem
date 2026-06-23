# OpenRath (arXiv 2606.19409) — incorporation analysis

Research note for Stephanie's `/research-project`: how OpenRath maps onto
`~/brains` + provenance tracking + `mem` + multi-agent orchestration.

## The paper in one line

**OpenRath: Session-Centered Runtime State for Agent Systems** (Wen, Wang,
Xu). A unified `Session` abstraction makes all runtime state a first-class,
composable value — transcripts, tool evidence, sandbox placement,
**lineage/branch provenance**, token usage, **replay info, and memory-event
records** — so that **fork / merge / replay become explicit runtime
operations rather than states reconstructed from external traces**
(PyTorch-tensor-inspired composition). Supporting types: Sandbox, Tool,
Agent, Memory, Workflow, Selector. The authors explicitly leave **memory
effectiveness and quantitative evaluation to future work.**

## Why this matters to mem specifically

mem's whole substrate is the **work-audit graph** — `bead → agent/session →
trace JSONL → PR/commit → outcome`, **reconstructed post-hoc** from the
city's external exhaust. OpenRath is the **runtime-native dual** of that
graph: it captures the same lineage/provenance/memory signal *at execution
time* as a first-class value, instead of mem re-deriving it later. Two
consequences:

1. **It kills mem's reconstruction tax.** mem has burned real cycles
   reconstructing join keys that OpenRath would hand over natively:
   true fork-point / base-SHA (the `capture-provenance.sh` merge-base hook;
   the `ingest-SHA-capture` work in mem-75t.15), ~89% null `repo`,
   `convoy/pr ~0` populated, `record_links` empty. OpenRath's *lineage
   metadata and branch provenance* as a runtime op makes those keys
   **authoritative (runtime truth)** rather than **inferred (merge-base
   guess)**.

2. **mem is the benchmark OpenRath is missing.** OpenRath defers *memory
   effectiveness* — which is mem's headline question verbatim (does
   retained/retrieved memory measurably improve success rate / iterations /
   cost). Clean positioning: **OpenRath = the capture/runtime layer; mem =
   the empirical memory-effectiveness benchmark over real outcomes.** They
   compose; they don't compete.

## Incorporation across the four targets

| Target | OpenRath element | Concrete incorporation |
|---|---|---|
| **provenance** | first-class lineage/branch provenance, fork/merge as runtime ops | Replace the hook-scraped merge-base capture with runtime-authoritative fork/merge points; feeds mem-75t durable trace ingest + the wanz linkage substrate with real, not inferred, edges. |
| **mem** | replay as a runtime op; memory-event records | Phase-2 replay harness (replay closed historical beads with-vs-without memory) becomes a native runtime call instead of a jsonl rebuild; memory-event records give a clean per-step retrieval/write signal. |
| **~/brains** | Memory abstraction + memory-event records | Standardize brains' write/recall events into the Session record so mem can later score brains' memory effectiveness on real city outcomes. |
| **orchestration** | Workflow + Selector + fork/merge/replay | The city already does warm-fork (mem-0ut A/B) and convoy/DAG dispatch; OpenRath gives a typed runtime abstraction over what's currently bespoke. Selector (control-flow → runtime-routed) maps onto convoy routing. |

## The validity fork (mem-pl flag — this is the part that needs a decision)

OpenRath bundles `memory event records` **and** `replay information` **and**
outcome-bearing lineage into **one Session value that flows as agent
input**. For mem *as a benchmark* that's a **contamination vector**: if mem
ever ingests Session objects as the eval corpus, the outcome label
(merged/closed/CI) can ride **inside the input**, destroying the
benchmark's validity. mem's current post-hoc reconstruction is, by
construction, a validity **feature** — input and label are separated.

- **Safe to adopt now:** OpenRath at the **capture layer** (runtime
  provenance + memory-event schema). Pure upside, kills the reconstruction
  tax, no leakage.
- **Needs an eval-design firewall:** adopting **Session-as-input**
  wholesale. A leakage firewall (strip replay/outcome lineage from anything
  used as benchmark input) is mandatory before that.

## Recommendation

Adopt OpenRath's **lineage/provenance + memory-event schema at the capture
layer** (runtime-authoritative join keys — direct, immediate win for
provenance + mem-75t + wanz), and **position mem as the memory-effectiveness
benchmark OpenRath explicitly defers**. Do **not** feed whole Session values
as benchmark input without a leakage firewall — that's an eval-design /
validity decision for Stephanie, not a plumbing call.

First step: a thin adapter that reads OpenRath Session `lineage` +
`memory_event_records` into the existing WorkRecord spine (no new store
substrate), proving the runtime keys match the reconstructed ones on a
sample of closed beads — that single diff tells us how much reconstruction
tax we're actually paying.
