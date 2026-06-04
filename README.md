# mem — agentic memory, benchmarked on Gas City orchestration traces

**One line:** build and *benchmark* agentic memory using Gas City's own
multi-agent orchestration traces — across every rig — as the evaluation corpus.

## The bet

Most agentic-memory work learns from a single agent's session prose. Gas City
already produces something richer and more structured: a continuous stream of
**real multi-agent work**, where every unit of work has a verifiable outcome
(bead closed, PR merged, CI green/red) and a full trace of how it got there.
That is a near-ideal substrate for studying memory — the labels are real, not
synthetic.

`mem` turns that exhaust into a benchmark: *does retained, parsed, retrieved
memory measurably improve future agent work* (success rate, iterations, cost),
and *which retention/retrieval strategies win*?

## What we collect (already exists in the city)

The data is the city's own audit. Nothing needs to be generated:

| Source | What it gives | Where |
|---|---|---|
| **Bead store** (dolt, all rigs) | the work spine: `id`, title, status, **assignee (embeds the agent/session id)**, notes, `external-ref`, labels, metadata, timestamps | shared dolt server |
| **Session traces** (JSONL) | full agent transcripts — tool calls, tool outputs, decisions, errors | `~/.claude-homes/account*/.claude/projects/<proj>/<session>.jsonl` |
| **Audit / scanner logs** | dispatch, reaper, supervisor events | `.gc/*.log`, supervisor log |
| **Convoy / wisp / workflow** | how work fanned out + composed | `gc convoy` / `gc workflow` |
| **PRs / commits** | the external outcome | `gh` (via bead `external-ref` / branch) |

## The work-audit graph (the core mapping)

Everything keys off a **work id** and joins outward. This graph IS the dataset:

```
            ┌──────────── deps / convoy ───────────┐
            ▼                                       │
   Bead (work_id) ──assignee──▶ Agent/Session (agent_id) ──▶ Trace (jsonl)
   gc-1920          polecat-gc-335825                tool calls, errors,
   labels,                │                          decisions, outputs
   metadata               │
            └─external-ref/branch─▶ PR / Commit ──▶ Outcome
                                    #1858            merged | closed | CI pass/fail
```

- **Work id** = bead id (`gc-1920`, `gascity-dashboard-tnqw`). The anchor.
- **Agent id** = the live session embedded in `bead.assignee`
  (`polecat-gc-335825` → session `gc-335825`) → resolves to the trace JSONL.
- **Outcome** = the verifiable label (bead status, PR merged/closed, CI result) —
  this is what makes it a *benchmark*, not just a log.

## Pipeline (engram-informed; see `ARCHITECTURE.md`)

1. **Ingest** — harvest bead audit + trace JSONLs + PR/outcome data into a store, keyed by work id.
2. **Parse / extract** — *deterministic* signal from tool output (build/test/lint exit states, file:line errors — engram's one proven mechanism) + *semantic* signal (approach, decisions) via a model. ZFC boundary: mechanical in code, judgment in the model.
3. **Retain** — the work-audit graph + extracted signal in a queryable store.
4. **Retrieve** — given a new task/context, surface relevant prior work/memory.
5. **Benchmark** — A/B the agent *with vs without* retrieved memory; measure outcome lift (success, iterations, cost). This mirrors how `codeprobe` / `enterprisebench` already run evals in the city.

## Status

Greenfield. Rig scaffold only. See `ARCHITECTURE.md` for the data model and the
open design decisions that need to land before Phase 1 (the work-audit graph
builder) is built.
