# mem: agentic memory, benchmarked on multi-agent orchestration traces

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
that actually happened rather than from synthetic tasks. One caveat constrains
the whole evaluation design: external outcome linkage is sparse in practice
(roughly 1 in 6,000 records carries a PR reference), so the benchmark's oracles
rest on the lifecycle labels, the traces themselves, and trace-derived gold
diffs, not on a merged-PR or CI signal.

The pieces fit together as a pipeline: ingest the orchestrator's audit into a
queryable work-audit graph, parse deterministic failure signal out of the
traces, distill prior resolutions into retrievable lessons, then benchmark
memory systems by replaying held-out tasks under controlled memory conditions
on Harbor. The TypeScript half (`src/`) builds and serves the store; the Python
half (`memory-bench/`) runs the eval.

## Status

**Store (schema v6), populated from the bead spine:** 6,691 work records, 874
resolved transcripts (per-run metadata in `trace_runs`), deterministic trace
errors parsed across the error-bearing slice of the corpus, git provenance on
482 records (113 with both a trace and a checkoutable base commit), and a
canonical `repo` resolved on every record via the deterministic rig→repo map.
Gates green: 1,297 Python + 332 TypeScript tests pass.

**Headline = ablation score-vs-information curve (Decision 17).** The corpus
does not carry the bead→PR→commit linkage a merged-PR/CI outcome oracle needs;
across 5,977 closed records exactly one has a usable external ref, so the
merged-PR outcome-lift headline is *structurally uncomputable at scale*. The
headline is therefore env- and label-independent: the agent is its own control
across an information ladder, and the saturation point plus minimum-useful
information combination are read off the curve. Per-rung reward is a
deterministic check on data the corpus has (did the run avoid or resolve the
held-out task's known `trace_error`) plus a calibrated OSS LLM-judge for
semantic quality. The merged-diff oracle is opportunistic validation on the
handful of beads that carry PR/commit metadata, never the headline.

**Where the grid stands.** A pre-admission oracle-soundness gate (`mem-1eph`)
plus oracle-repair waves carried the native pool to 8 sound dashboard oracles
(`mem-qarg`), and a `mem` test config admitted one mem-rig bundle for 9
(`mem-us6j`). The 3-arm graded grid (`none` / `ours` / `builtin`) runs on those
9; distilling the dashboard rig's lessons brought the `ours` arm to 4-of-8
retrieval coverage. Details and competitive-arm wiring in `memory-bench/README.md`.

## Roadmap

Near-term work, roughly in priority order:

- **Lift lesson coverage corpus-wide.** The `ours` arm injects distilled
  lessons, and the distiller has run over the dashboard rig so far (236 lessons,
  lifting the running grid to 4-of-8 coverage). Distilling the remaining
  error-bearing records across rigs is the single biggest lever on the headline.
- **Land the N=9 graded headline.** The 3-arm grid is executing on Harbor; the
  open step is reading the per-arm graded result with confidence intervals and
  the small-N caveat.
- **Grow the sound-oracle pool past 9.** Repairing the four replay-rejected
  bundles proved unsound (corpus-extraction drift, not a fixable bug), so the
  lever moves upstream: capturing each session's true per-worktree base commit so
  more bundles replay cleanly. Trace-substrate work, larger than a harness patch.
- **Finish the failure-recurrence track.** Its generator, a frozen 369-anchor
  matched-pair fixture (temporal-LOO-clean), and real Harbor task-dir emission
  are done; the soundness-gated runner and matched-pair effort-delta scorer are
  what stands between it and a number.
- **Wire the synthetic dataset.** The deterministic synthetic-task generator and
  its pilot validity filter are built and tested but not yet run through an eval;
  generating, pilot-filtering, and running a batch on the sequence track scales
  the dataset past the thin real pool.

## How it works

### The data already exists

It is the orchestrator's own audit; nothing needs to be generated:

| Source | What it gives | Where |
|---|---|---|
| **Work-item store** (all projects) | the work spine: `id`, title, status, **assignee (embeds the agent/session id)**, notes, external ref, labels, metadata, timestamps | shared store |
| **Session traces** (JSONL) | full agent transcripts: tool calls, tool outputs, decisions, errors | per-session JSONL files |
| **Audit / scanner logs** | dispatch, reaper, supervisor events | orchestrator logs |
| **Convoy / workflow records** | how work fanned out and composed | orchestrator state |
| **PRs / commits** | the external outcome, where a linkage exists (rare; see above) | GitHub, via the work item's external ref or branch |
| **Git provenance** | session-start `base_commit` per record (commit-by-date from a recorded base branch; an absent base branch stays `unresolved`, never guessed), so a run can be replayed as a checkout | `--with-provenance` |
| **Repo identity** | canonical `repo` (`owner/name`) on every record via a deterministic rig→repo map; `repo_source` records how it resolved (`outcome` / `rig-map` / `unmapped`), and umbrella rigs that span many forks stay `unmapped` rather than mislabeled | resolved at ingest, always on |

### The work-audit graph (the core mapping)

Everything keys off a **work id** and joins outward. This graph is the dataset:

```
   Work item (work_id)          deps / convoy ── link sibling work items
   │  labels, metadata
   ├── assignee ──────────────▶ Agent / Session ─▶ Trace (jsonl)
   │                            (agent_id)         tool calls, errors,
   │                                               decisions, outputs
   └── external-ref / branch ─▶ PR / Commit ─────▶ Outcome
                                                   merged | closed | CI pass/fail
```

- **Work id** = the work-item id. The anchor.
- **Agent id** = the session embedded in the item's `assignee`, which resolves
  to that session's trace JSONL.
- **Outcome** = the verifiable label. In practice this is the item's lifecycle
  status plus the deterministic failure record in its trace; PR/CI labels exist
  in the schema but are rarely populated. That is what makes it a *benchmark*,
  not just a log.

### Pipeline

The pipeline mirrors a small set of stages, each a module under `src/`. The
extraction split follows a strict boundary: mechanical signal is read in code,
semantic signal is read by a model.

1. **Ingest.** Harvest the work-item audit, trace JSONLs, and PR/outcome data
   into a store, keyed by work id. Pure IO.
2. **Parse / extract.** *Deterministic* signal from tool output (build, test,
   and lint exit states, plus `file:line` errors); *semantic* signal (approach,
   decisions) from a model, run once per record at ingest, append-only. The
   deterministic capture is ported from a prior failure-capture tool, the one
   mechanism we trust to be free of hidden judgment.
3. **Retain.** The work-audit graph plus extracted signal in a queryable store.
4. **Retrieve.** Given a new task, surface relevant prior work. Retrieval v1
   fires on failure: when an agent hits a build, test, or lint error, it keys on
   the deterministic failure signature (normalized `file:line` plus error class)
   and returns distilled prior resolutions, not raw traces.
5. **Benchmark.** Run each multi-session sequence under three conditions
   (`no_memory` / `oracle_memory` / `memory_enabled`) on Harbor and read the
   gaps. Because merged-PR/CI outcome linkage is structurally absent from this
   corpus (see *Status*, Decision 17), the headline is an *ablation
   score-vs-information curve* rather than merged-PR outcome lift; retrieval
   precision and injected-context volume stay a first-class guard so
   over-injection cannot fake a win. The Python harness lives under
   `memory-bench/`.

### Data model

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

The **outcome** field is the benchmark label, but read it precisely: the labels
actually present on every record are the lifecycle status and the deterministic
trace failures. The `pr` / `commit_sha` / `ci` fields exist in the schema but
are populated on only a handful of records (Decision 17), so the eval rests on
lifecycle plus trace-derived signal, never a synthetic label and never a
merged-PR/CI label at scale.

When the benchmark evaluates a record, the retrievable set is bounded in time:
only records for work closed strictly before the target started, with the
target's convoy siblings, supersedes-chain, and any item sharing its PR or
branch excluded. That holds the retrieved set to memory as it existed when the
work began and structurally blocks future leakage.

### Building the store

The P1.5 sidecar is a generated artifact (gitignored at `.mem/store.db`). Build
it from the bead spine, then query / retrieve / replay against it:

```bash
mem build-store [--rig <name>] [--with-traces] [--with-provenance] [--store .mem/store.db]
mem query   --store .mem/store.db [--rig R] [--json]      # read the graph
mem retrieve <work_id> --store .mem/store.db --scope cross-rig|same-rig --json
```

`mem retrieve` also speaks the engram progressive-disclosure layers via
`--format`: `index` lists each ranked item with its citation URI
(`mem://lesson/<work_id>[/<commit_sha>]`) and the estimated token cost of
hydrating it; `details --pick a,b` hydrates only the chosen items; the default
`full` is the original flat payload. Retrieval is deterministic, so an index
call and the details call that follows it see the same ranking, and the agent,
not the pipeline, chooses how many tokens to spend.

Two invocation traps fail silently with exit 0. The CLI entrypoint is
`./bin/mem` (`node dist/main.js` only defines the function and does nothing),
and `--with-traces` resolves sessions through `gc session logs`, which loads
`city.toml` from the working directory, so a build run from this repo resolves
zero traces. Run the full rebuild from the gas-city checkout with an absolute
`--store` path, then verify the `trace_path`, `repo`, and `base_commit` counts
before swapping the store into place.

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
with `--with-provenance` it attaches each record's git baseline (session-start
commit, resolved by date). Canonical `repo` identity is resolved from the
rig→repo map on every build (no flag), and `build-store` reports
`records_with_repo` on its coverage line so the residual `unmapped` rate stays
observable. The spine-only default stays fast (no `gc`/transcript/git IO). The
Python harness loads the store through `mem query` (`memory-bench/`).

`mem ingest-traces` is the packaged, idempotent form for the recurring rebuild.
It is `build-store` with `--with-traces --with-provenance` always on, wrapped in
a before/after coverage diff so a run reports exactly which axes it lifted off
zero. `mem coverage` prints that same report for the live store without
rebuilding. The `/ingest-trace-substrate` skill documents the city-dir
requirement and the verify-before-swap workflow, and `.gc/cron/` ships the
nightly cadence.

```bash
cd /home/ds/gas-city   # --with-traces resolves transcripts from the city cwd
mem ingest-traces --store /home/ds/projects/mem/.mem/store.db   # rebuild + delta
mem coverage       --store /home/ds/projects/mem/.mem/store.db   # read-only report
```

### Eval harness (`memory-bench/`)

Each multi-session sequence runs under three conditions on **Harbor** as the
execution substrate: `no_memory` (stateless floor), `oracle_memory` (exact
memory injected, the ceiling and the task-validity gate, where
`oracle ≈ no_memory` rejects the task), and `memory_enabled` (the real system).
Competitive arms (mem0, A-MEM, graphiti, NAT, filesystem, plus the
failure-triggered `ours`) run behind one uniform ingest/retrieve interface, all
under a temporal leave-one-out leak guard, a CodeScaleBench fail-to-pass
oracle-soundness gate, the precision guard, and a *raw* 5-axis telemetry vector
(task, efficiency, latency, privacy, interruption) emitted as OpenTelemetry
GenAI spans (ATIF derived). A ≥10 real-sequence dataset MVP gate is enforced; a
synthetic sequence generator (deterministic oracle authored in code, a local
model used only for the natural-language surface and frozen offline) scales the
dataset past the thin real pool. Details in `memory-bench/README.md`.
