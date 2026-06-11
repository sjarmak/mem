---
name: ingest-trace-substrate
description: Run the durable trace-substrate ingest (mem-75t) — resolve transcripts, parse errors + run-metadata, attach git provenance — and report coverage deltas. Use when rebuilding or refreshing the mem `.mem/store.db` sidecar, or when the held-out eval needs `trace_path`/`trace_errors`/`base_commit` populated.
---

# Ingest the trace substrate

`mem ingest-traces` is the packaged, idempotent rebuild of the trace substrate
(epic mem-75t): it takes the bead spine and lifts the five coverage axes the
eval depends on off zero —

| axis | source step | what it unlocks |
|------|-------------|-----------------|
| `with_trace` | P1.3 resolve transcript → JSONL | the conversation history for replay |
| `trace_errors` | P1.6 parse build/test/lint output | the D8 failure signatures the `ours` arm fires on |
| `trace_runs` | P1.2 parse run metadata | tokens / model / harness / tool-calls / turns |
| `with_base_commit` | P1.3 git provenance | the git-checkout anchor for real-exec replay |
| `multi_session` | mem-75t.4 merged session join | iteration N+1 of the same task — the canonical memory consumer |

It is `build-store --with-traces --with-provenance` plus a before/after coverage
diff. The writer upserts records and rebuilds child rows on every write, so the
command is safe to re-run — re-running converges, it never double-counts.

## The merged session join (run FIRST)

The store's multi-row `record_agents` (schema v4) comes from the merged
session<->bead join artifact, built by the Python driver from three sources —
gc events (PRIMARY), dolt assignee history, content scan — with transcript
archival as a side effect (the corpus is a ~6-week rolling window; archival is
the step racing data loss):

```bash
cd /home/ds/projects/mem/memory-bench
uv run python scripts/build_merged_join.py
# writes .mem/merged-session-bead-join.json + archives to .mem/transcript-archive/
```

Then pass it to the ingest with `--session-join`. The artifact pre-resolves
each session's transcript (via the events stream's `session_key` map), so the
slow per-session `gc session logs` shelling only runs for the residue.

## The city-dir requirement (READ THIS FIRST)

Two traps, both of which **fail silently with exit 0** if you get them wrong:

1. **Run it from the gas-city checkout, not from the mem repo.** `--with-traces`
   resolves each session through `gc session logs`, which loads `city.toml` from
   the *working directory*. Run from `/home/ds/projects/mem` and every
   trace / provenance axis resolves to **zero** while the spine still loads — a
   green run that populated nothing.

2. **The entrypoint is `./bin/mem`** (or a linked `mem`). `node dist/main.js`
   only defines the function and exits 0 without running it.

So the canonical invocation uses an **absolute `--store`** pointing back at the
mem repo's sidecar, run from the city dir:

```bash
cd /home/ds/gas-city
mem ingest-traces --store /home/ds/projects/mem/.mem/store.db \
  --session-join /home/ds/projects/mem/.mem/merged-session-bead-join.json
```

Scope to one rig with `--rig <name>` (e.g. `--rig mem`) for a fast incremental
pass; omit it to cover all rigs. Omitting `--session-join` builds a
single-session store (assignee links only) — fine for a quick spine refresh,
wrong for anything consuming multi-session history.

## Reading the coverage report

The command prints `coverage after ingest` (one line per axis; per-record axes
show `n/records`) and a `delta` line — the axes this run lifted. Inspect coverage
any time without rebuilding:

```bash
mem coverage --store /home/ds/projects/mem/.mem/store.db
```

A healthy full run shows non-zero `with_trace`, `trace_errors`, `trace_runs`,
and `with_base_commit`. **If those are still zero, you ran from the wrong cwd**
(trap 1) — do not swap the store into place. `delta: none` on a re-run is
expected and correct: the substrate is already complete.

## Verify before swapping

Build into a scratch path, confirm the counts, then move it over the live store
— never overwrite the canonical sidecar with an unverified build:

```bash
cd /home/ds/gas-city
mem ingest-traces --store /tmp/store.db --json | tee /tmp/ingest.json
# check after.with_trace / after.trace_errors / after.with_base_commit are non-zero
mv /tmp/store.db /home/ds/projects/mem/.mem/store.db
```

The append-only `lessons` table is the one thing a rebuild cannot regenerate;
if you are rebuilding from scratch, carry it across with
`export-lessons` → `build-store` → `import-lessons` (see the repo README).

## Cadence

A nightly cron runs this and reports the coverage delta — see
`.gc/cron/ingest-trace-substrate.md`. The cron is the unattended form of exactly
the verified invocation above.
