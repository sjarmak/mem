# mem — agentic memory, benchmarked on multi-agent orchestration traces

**One line:** build and *benchmark* agentic memory using a multi-agent
orchestrator's own work traces, across every project, as the evaluation corpus.

## The bet

Most agentic-memory work learns from a single agent's session prose. A
multi-agent orchestrator produces something richer: a continuous stream of real
work where every unit has a verifiable outcome (work item closed, PR merged, CI
green or red) and a full trace of how it got there. That makes a good substrate
for studying memory, because the labels are real rather than synthetic.

`mem` turns that exhaust into a benchmark. Does retained, parsed, retrieved
memory measurably improve future agent work (success rate, iterations, cost),
and which retention and retrieval strategies win?

## What we collect (already exists)

The data is the orchestrator's own audit. Nothing needs to be generated:

| Source | What it gives | Where |
|---|---|---|
| **Work-item store** (all projects) | the work spine: `id`, title, status, **assignee (embeds the agent/session id)**, notes, external ref, labels, metadata, timestamps | shared store |
| **Session traces** (JSONL) | full agent transcripts: tool calls, tool outputs, decisions, errors | per-session JSONL files |
| **Audit / scanner logs** | dispatch, reaper, supervisor events | orchestrator logs |
| **Convoy / workflow records** | how work fanned out and composed | orchestrator state |
| **PRs / commits** | the external outcome | GitHub, via the work item's external ref or branch |

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
- **Outcome** = the verifiable label (item status, PR merged or closed, CI
  result). This is what makes it a *benchmark*, not just a log.

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
carries a real, verifiable label (closed, merged, CI), never a synthetic one.

When the benchmark evaluates a record, the retrievable set is bounded in time:
only records for work closed strictly before the target started, with the
target's convoy siblings, supersedes-chain, and any item sharing its PR or
branch excluded. That keeps "memory as it existed when the work began" honest
and structurally blocks future leakage.

## Building the store

The P1.5 sidecar is a generated artifact (gitignored at `.mem/store.db`). Build
it from the bead spine, then query / retrieve / replay against it:

```bash
mem build-store [--rig <name>] [--with-traces] [--store .mem/store.db]
mem query   --store .mem/store.db [--rig R] [--json]      # read the graph
mem retrieve <work_id> --store .mem/store.db --scope cross-rig|same-rig --json
```

`build-store` reuses the ingest readers and the store writer — it adds no
substrate, only the wiring that lands real WorkRecords in the store the
retrieval/eval path reads. With `--with-traces` it additionally resolves each
record's transcript (P1.3) and parses its deterministic build/test/lint failure
signatures (P1.6) into the store, so the failure-triggered `ours` arm fires; the
spine-only default stays fast (no `gc`/transcript IO). The Python harness loads
the store through `mem query` (`memory-bench/`).

## Status

Greenfield. The TypeScript work-audit graph builder under `src/`
(ingest / parse / store / retrieve / bench) is scaffolded with tests under
`tests/`. The Python evaluation harness lives in `memory-bench/` (see its
README); it runs end-to-end on the real store via `membench replay`. Curating
the held-out eval set and the first lift read are the next phase.
