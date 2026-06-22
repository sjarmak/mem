# mem-31kz — Forward-capture: write-time memory-event substrate

> Status: mechanism landed, branch-ready (`mem-31kz-forward-capture`). Corpus /
> numbers HELD. Activating the capture hook globally is a config step (below);
> cross-system capture (tom-swe, continuous-learning) is mayor-owned and tracked
> separately.

## Why

The city's exhaust today is agents doing coding/orchestration, **not** agents
USING memory. So neither a realism reference (mem-hd7f) nor a non-flat memory
ranking (mem-72sj Gate-0) is sourceable post-hoc — there is no record of a
memory being read and then used. `capture-provenance.sh` already records
bead-claim GIT provenance; this is the **parallel, NEW** capture path for memory
reads/writes, recorded **at execution time** instead of reconstructed after the
fact (mem's `used` retrieval-causality edge is empty by construction — it cannot
be reconstructed, only captured live).

This is the OpenRath PROJECTOR boundary (`docs/prd-openrath-incorporation.md`):
capture the leak-safe, label-free join keys — never a whole Session, never
memory content, never an outcome field.

## What landed

| Piece                                             | File                                                                  |
| ------------------------------------------------- | --------------------------------------------------------------------- |
| Event schema (strict allow-list)                  | `src/schemas/memory-event.ts`                                         |
| `memory_events` table (schema v8→v9)              | `src/store/schema.ts`                                                 |
| Store surface (record/read/all/import)            | `src/store/memory-events.ts`                                          |
| Capture mechanism (tool→op, path test, projector) | `src/ingest/memory-capture.ts`                                        |
| CLI `mem memory-event capture\|record\|log`       | `src/cli/commands/memory-event.ts`                                    |
| Round-trip `mem export/import-memory-events`      | `src/cli/commands/{export,import}-memory-events.ts`                   |
| PostToolUse capture hook                          | `scripts/hooks/capture-memory-event.sh`                               |
| Tests (store / mechanism / CLI)                   | `tests/{store.memory-events,memory-capture,cli-memory-event}.test.ts` |

A `memory_events` row carries only: `session`, `work_id?`, `op`
(read/write/update/delete/search/…), `backend`, `memory_ref` (which memory —
a path, **never content**), `used_in?` (where used), `concrete_tool?`,
`source`, `occurred_at`, `created_at`. The PK `id` is the dedup key, so a
re-fired hook is an idempotent no-op (append-only).

## The firewall (load-bearing)

Captured sessions feed the **corpus**; whether a row may enter eval **input** is
the firewall's call (`memory-bench/membench/validity.py` `loo_bounded` /
`assert_no_leak`), **not** the capture layer's. Data-generation and scoring stay
separate. Two structural guarantees here:

- **Pre-outcome by construction.** A write-time event is emitted before the
  session succeeds or fails, so it carries no label.
- **Strict allow-list, not deny-list.** `MemoryEventSchema` is `.strict()`: a
  producer that grows a novel field (e.g. a smuggled `commit_sha`) **raises**
  rather than adding an unscanned, possibly outcome-correlated column. Test:
  `store.memory-events.test.ts` → "rejects an event carrying a novel field".
- **No outcome columns exist** in `memory_events` (test asserts `pr`/
  `commit_sha`/`base_commit`/`outcome`/`ci`/`landed_state` are absent).

## ZFC boundary

Event recording + tagging is **mechanism** (tool→op map, structural memory-path
test, deterministic id). Semantically labeling a memory op — "useful recall"
vs "scratch read", memory type — is **model-delegated**, downstream, and never
happens at capture time.

## Round-trip (append-only, like lessons)

`memory_events` is runtime exhaust a store rebuild **cannot** regenerate — the
SECOND such table after `lessons`. A schema bump is:

```
mem export-memory-events --out events.ndjson    # before rebuild
# … rebuild the store …
mem import-memory-events --file events.ndjson    # after
```

## Activating the capture hook

The hook is a thin bash pre-filter that pipes into `mem memory-event capture`
(the authoritative projector). It is best-effort by contract — a capture miss
never blocks a tool call (every failure path exits 0).

Add to `~/.claude/settings.json` (PostToolUse), scoped to the file tools:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Read|Write|Edit|NotebookEdit|Grep|Glob",
        "hooks": [
          {
            "type": "command",
            "command": "/home/ds/projects/mem/scripts/hooks/capture-memory-event.sh"
          }
        ]
      }
    ]
  }
}
```

Config via env:

- `MEM_STORE` — store to record into (default `~/projects/mem/.mem/store.db`).
- `MEM_BIN` — the mem binary (default `mem` on PATH, then `~/.mem-cli/bin/mem`).
  The pinned `~/.mem-cli` build must include the `memory-event` command — re-pin
  it after this lands (the same gotcha as the provenance capture path).
- `MEM_MEMORY_DIRS` — colon-separated path substrings marking a memory path
  (default: structural — `/brains/`, a claude `/memory/` dir, or `MEMORY.md`).
- `MEM_WORK_ID` / `GC_BEAD_ID` / `GC_WORK_ID` — the work_id to tag (best-effort;
  absent ⇒ event is session-keyed, work_id resolved later from claim provenance).

## Scope / next

- In-rig (mem-worker filesystem memory) only. mcp/agent memory tools (tom-swe,
  continuous-learning) are CROSS-SYSTEM = mayor-owned, tracked separately.
- The membench consumer (project `memory_events` → the `used` edge / a `ours`
  retrieval signal) is downstream of the firewall extension (OpenRath PRD
  Phase 2) and deliberately NOT wired here — capture feeds the corpus, scoring
  is separate.
