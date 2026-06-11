# mem — agentic memory, benchmarked on multi-agent orchestration traces

A multi-agent orchestrator running across eighteen project rigs leaves behind
6,691 work items, 874 resolved session transcripts, and the build/test/lint
failures inside them. `mem` builds agentic memory from that record and
benchmarks it on the same record: does retained, parsed, retrieved memory
measurably improve future agent work (success rate, iterations, cost), and
which retention and retrieval strategies win?

## Work records beat session prose as a memory corpus

Most agentic-memory work learns from a single agent's session prose. A
multi-agent orchestrator produces something richer: a continuous stream of
real work where every unit carries a lifecycle label (created, started,
closed) and a full trace of how it got there, so the labels come from work
that actually happened rather than from synthetic tasks. One honest caveat
shapes the whole evaluation design: external outcome linkage is sparse in
practice (roughly 1 in 6,000 records carries a PR reference), so the
benchmark's oracles rest on the lifecycle labels, the traces themselves, and
trace-derived gold diffs, not on a merged-PR or CI signal.

## The data already exists

It is the orchestrator's own audit; nothing needs to be generated:

| Source | What it gives | Where |
|---|---|---|
| **Work-item store** (all projects) | the work spine: `id`, title, status, **assignee (embeds the agent/session id)**, notes, external ref, labels, metadata, timestamps | shared store |
| **Session traces** (JSONL) | full agent transcripts: tool calls, tool outputs, decisions, errors | per-session JSONL files |
| **Audit / scanner logs** | dispatch, reaper, supervisor events | orchestrator logs |
| **Convoy / workflow records** | how work fanned out and composed | orchestrator state |
| **PRs / commits** | the external outcome, where a linkage exists (rare; see above) | GitHub, via the work item's external ref or branch |
| **Git provenance** | `repo` + session-start `base_commit` per record, so a run can be replayed as a checkout | resolved at ingest (`--with-provenance`) |

## The work-audit graph (the core mapping)

Everything keys off a **work id** and joins outward. This graph is the dataset:

```
            ┌──────────── deps / convoy ───────────┐
            ▼                                       │
   Work item (work_id) ─assignee─▶ Agent/Session ───▶ Trace (jsonl)
   labels,                  │      (agent_id)         tool calls, errors,
   metadata                 │                         decisions, outputs
            └─external-ref/branch─▶ PR / Commit ──▶ Outcome
                                                    merged | closed | CI pass/fail
```

- **Work id** = the work-item id. The anchor.
- **Agent id** = the session embedded in the item's `assignee`, which resolves
  to that session's trace JSONL.
- **Outcome** = the verifiable label. In practice this is the item's
  lifecycle status plus the deterministic failure record in its trace; PR/CI
  labels exist in the schema but are rarely populated. This is what makes it
  a *benchmark*, not just a log.

## Pipeline

The pipeline mirrors a small set of stages, each a module under `src/`. The
extraction split follows a strict boundary: mechanical signal is read in code,
semantic signal is read by a model.

1. **Ingest** — harvest the work-item audit, trace JSONLs, and PR/outcome data
   into a store, keyed by work id. Pure IO.
2. **Parse / extract** — *deterministic* signal from tool output (build, test,
   and lint exit states, plus `file:line` errors). *Semantic* signal (approach,
   decisions) from a model, run once per record at ingest, append-only. The
   deterministic capture is ported from a prior failure-capture tool; it is the
   one mechanism we trust to be free of hidden judgment.
3. **Retain** — the work-audit graph plus extracted signal in a queryable store.
4. **Retrieve** — given a new task, surface relevant prior work. Retrieval v1
   fires on failure: when an agent hits a build, test, or lint error, key on the
   deterministic failure signature (normalized `file:line` plus error class) and
   return distilled prior resolutions, not raw traces.
5. **Benchmark** — run the agent *with and without* retrieved memory and measure
   outcome lift (success, iterations, cost), with retrieval precision and
   injected-context volume as a first-class guard so over-injection can't fake a
   win.

## Data model

The atomic unit is a **WorkRecord**, keyed by a work-item id, joining the whole
audit:

```
WorkRecord {
  work_id:    work-item id (anchor)
  project:    source project
  title, labels, metadata, priority
  lifecycle:  { created, started, closed, status, status_history[] }
  agents:     [ { agent_id, account, trace_ref } ]   # from assignee → JSONL path
  trace:      { jsonl_path, n_turns, tool_calls[], tool_outcomes[],
                errors[ {tool, file, line, message, severity} ] }
  outcome:    { pr, merged|closed, commit_sha, ci: pass|fail }
  signal:     { deterministic: {...}, semantic: {...} }   # the learned bit
  links:      { deps[], convoy_id, supersedes[] }
}
```

The **outcome** field is what makes this a benchmark substrate: every record
carries a real label from work that happened (lifecycle status, deterministic
trace failures), never a synthetic one.

When the benchmark evaluates a record, the retrievable set is bounded in time:
only records for work closed strictly before the target started, with the
target's convoy siblings, supersedes-chain, and any item sharing its PR or
branch excluded. That keeps "memory as it existed when the work began" honest
and structurally blocks future leakage.

## Building the store

The P1.5 sidecar is a generated artifact (gitignored at `.mem/store.db`). Build
it from the bead spine, then query / retrieve / replay against it:

```bash
mem build-store [--rig <name>] [--with-traces] [--with-provenance] [--store .mem/store.db]
mem query   --store .mem/store.db [--rig R] [--json]      # read the graph
mem retrieve <work_id> --store .mem/store.db --scope cross-rig|same-rig --json
```

Two invocation traps, both of which fail silently with exit 0: the CLI
entrypoint is `./bin/mem` (`node dist/main.js` only defines the function and
does nothing), and `--with-traces` resolves sessions through `gc session
logs`, which loads `city.toml` from the working directory, so a build run
from this repo resolves zero traces. Run the full rebuild from the gas-city
checkout with an absolute `--store` path, then verify the `trace_path`,
`repo`, and `base_commit` counts before swapping the store into place.

The store has no in-place schema migration: a version bump means rebuilding from
the bead spine. The append-only `lessons` table is the one thing a rebuild
cannot regenerate, so it is carried across explicitly:

```bash
mem export-lessons --store .mem/store.db --out lessons.ndjson
mem build-store --store .mem/store.db            # fresh schema
mem import-lessons --file lessons.ndjson --store .mem/store.db   # idempotent
```

`build-store` reuses the ingest readers and the store writer; it adds no
substrate, only the wiring that lands real WorkRecords in the store the
retrieval/eval path reads. With `--with-traces` it additionally resolves each
record's transcript (P1.3) and parses its deterministic build/test/lint failure
signatures (P1.6) into the store, so the failure-triggered `ours` arm fires;
with `--with-provenance` it attaches each record's git baseline (repo plus
session-start commit, resolved by date). The spine-only default stays fast (no
`gc`/transcript/git IO). The Python harness loads the store through `mem query`
(`memory-bench/`).

## Status

The store is at schema v3 and populated: 6,691 work records, 874 resolved
transcripts (with per-run metadata in `trace_runs`), 321 deterministic trace
errors across 77 records, and git provenance on 482 records, 113 of which
carry both a trace and a checkoutable base commit. That last set is the pool
the task-bundle builder (`mem-75t.7`) mines: the Python harness under
`memory-bench/` now assembles evaluable bundles (issue → trace → gold diff →
oracle context) by replaying a transcript's edit calls against a checkout of
the record's base commit, a design validated on 8 real beads (134/199 calls
replayed cleanly, 6/8 beads yielding applyable multi-file diffs; see
`docs/mem-75t.7.1-replay-validation.md`). The next gate before the full
oracle-curation and dual-verifier ports is a dynamic-range probe: run a
zero-memory agent against a cheap oracle-context rung on the first bundle
batch and confirm the eval has headroom before investing further.
