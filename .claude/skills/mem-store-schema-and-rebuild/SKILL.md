---
name: mem-store-schema-and-rebuild
description: The mem SQLite store's data contract — schema v11, the projections-rebuilt-from-record-JSON rule, the three append-only non-regenerable tables (lessons, memory_events, producer provenance_events), the no-in-place-migration rule, the `mem rebuild` round-trip (export-all → fresh build → import-all), and the `--json` envelope. Use when bumping SCHEMA_VERSION, editing src/store/schema.ts, migrating or rescuing a store whose version openStore refuses, running mem rebuild, debugging "schema version X, expected 11" errors, or parsing mem CLI --json output. NOT for running the recurring trace ingest or coverage axes — use ingest-trace-substrate / mem-ingest-and-provenance; NOT for retrieval exclusions or temporal LOO semantics — use mem-temporal-loo-and-leak-safety; NOT for setting up node/python toolchains — use mem-build-test-env.
---

# mem store: schema and rebuild

The store is the SQLite+FTS5 sidecar at `.mem/store.db` (default; every command
takes `--store <path>`). This skill is the data contract: what is truth, what is
projection, what a rebuild can and cannot regenerate, and how to bump the schema
without losing the rows that cannot be rebuilt.

Jargon, defined once:

- **WorkRecord** — one validated JSON document per work item (bead), the unit
  the store holds. Schema: `src/schemas/workrecord.ts`.
- **Projection** — a column or child table derived from the stored WorkRecord
  JSON purely so SQL can index/filter it. Rebuilt on every upsert; never truth.
- **Non-regenerable table** — a table whose rows did NOT come from the bead
  spine, so re-ingesting cannot recreate them. There are exactly three.
- **Spine** — the dolt bead store (`bd`) the ingest reads WorkRecords from.
- **Round-trip** — export the non-regenerable rows out of an old store, build a
  fresh store, import them back. `mem rebuild` does it mechanically.
- **Envelope** — the one-line JSON object every command prints on stdout under
  `--json`: `{apiVersion, cmd, ok, data?, errors?}`.

## When NOT to use this skill

| You want to…                                                  | Go to                                    |
| ------------------------------------------------------------- | ---------------------------------------- |
| Run the recurring trace-substrate ingest / lift coverage axes | `ingest-trace-substrate` (in-repo skill) |
| Understand ingest readers, landed oracle, provenance capture  | `mem-ingest-and-provenance`              |
| Understand `closedBefore` / exclusions / leak safety          | `mem-temporal-loo-and-leak-safety`       |
| Set up node/npm/python toolchains, run CI gates               | `mem-build-test-env`                     |
| Orient in the repo at all                                     | `mem-orientation`                        |

## 1. The one rule that explains the whole schema

`work_records.record` holds the full validated WorkRecord JSON — **the single
source of truth**. Every other column of `work_records` and every child table
is a projection of that JSON, promoted only so queries can index it. The writer
(`src/store/writer.ts`) deletes and rebuilds all child rows for a record on
every upsert, so projections can never drift from the JSON.

Consequences you must respect:

- **Never write projections directly** (no hand-`UPDATE` of `work_records`
  columns, no manual inserts into child tables). The next upsert erases your
  write. Change the record; let the writer project it.
- The store is disposable BY DESIGN — a mid-build failure leaves a partial
  store, and the fix is "re-run the build". Except for the three tables below.

Schema authority: `src/store/schema.ts`. Current version (verified 2026-07-07):

```
export const SCHEMA_VERSION = 11;   // src/store/schema.ts:28
```

### Table inventory (schema v11)

| Table                                 | Class                                          | What it holds                                                                                                                                |
| ------------------------------------- | ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `work_records`                        | record JSON = truth; other columns projected   | one row per work item; `record` column is the JSON                                                                                           |
| `record_agents`                       | projection                                     | one row per session iteration (multi-row since v4)                                                                                           |
| `record_labels`                       | projection                                     | bead labels                                                                                                                                  |
| `record_links`                        | projection                                     | intra-corpus `dep`/`supersedes`/`parent` adjacency (LOO exclusion keys)                                                                      |
| `links`                               | projection                                     | PROV-O tiered provenance edges (T1/T2/T3); T3 floor rebuilt inline, T1/T2 by post-ingest stages that re-run after a rebuild                  |
| `trace_errors` (+ `trace_errors_fts`) | projection                                     | deterministic file:line failure signatures; FTS5 over `message`. `AUTOINCREMENT` id is load-bearing (FTS content rowid must never be reused) |
| `trace_runs`                          | projection                                     | per-transcript run metadata (tokens, model, turns); `UNIQUE(work_id, session_uuid)`                                                          |
| `lessons`                             | **APPEND-ONLY, NON-REGENERABLE**               | distilled lessons (Decision 9)                                                                                                               |
| `memory_events`                       | **APPEND-ONLY, NON-REGENERABLE**               | write-time memory-op exhaust (mem-31kz forward capture)                                                                                      |
| `provenance_events`                   | **APPEND-ONLY; producer rows NON-REGENERABLE** | causal event log (`cut`/`claim`/…/`land`/`used`)                                                                                             |

## 2. The three non-regenerable tables, and why

All three deliberately have **no foreign key to `work_records`** — their rows
must survive a record delete/re-ingest, and may even land before the record
does. `NON_REGENERABLE_TABLES` is exported from `src/store/sqlite.ts:20`.

1. **`lessons`** — model-distilled lessons. Append-only per Decision 9: INSERT
   only, no update/delete path exists in the writer (continuous LLM rewriting
   degrades consolidated memory). The citation (`work_id` + `commit_sha`) is
   **snapshotted at append time, never joined live** — a live join would let a
   re-ingested outcome silently rewrite an existing citation. A rebuild cannot
   regenerate them: distillation is model spend against transcripts that may no
   longer exist.
2. **`memory_events`** — runtime exhaust a real memory-USING session emits at
   execution time. Not a projection of the spine at all; a rebuild has nothing
   to derive it from. Rows carry leak-safe join keys only (op / backend /
   memory_ref / used_in / session / work_id) — never memory content, never an
   outcome field. `id` PK is the dedup key; writes are INSERT OR IGNORE.
3. **`provenance_events`** — one immutable row per causal fact
   (`cut|claim|suspend|resume|handoff|commit|land|used`). Two populations,
   split by `source`:
   - **producer rows** (`source != 'ingest-backfill'`) — recorded by hooks /
     the `mem provenance` CLI at event time. NON-REGENERABLE; these are what
     the round-trip carries. (`BACKFILL_SOURCE = 'ingest-backfill'`,
     `src/schemas/provenance-event.ts:29`.)
   - **backfilled rows** (`source = 'ingest-backfill'`) — re-derived
     deterministically from records on every build (`deriveProvenanceEvents`).
     Never exported; `import-provenance-events` **refuses** them outright, so a
     stale reconstruction can never be resurrected into a corpus that no longer
     supports it.

Live-store shape for calibration (`.mem/store.db`, 2026-07-07): user_version
11; 9,423 work_records; 37 lessons; 0 memory_events; 19,632 provenance_events
of which **31 producer** / 19,601 backfilled. So a rebuild today carries 68
rows and regenerates everything else. These counts drift — re-check with the
inspect script (§6) before relying on them.

## 3. No in-place migration — ever

`openStore` (`src/store/sqlite.ts:48`) reads `PRAGMA user_version`:

- `0` → fresh file: applies the full DDL, stamps `SCHEMA_VERSION`.
- `== SCHEMA_VERSION` → opens.
- anything else → **throws**, and the error message inventories the
  non-regenerable rows the store holds, so a blind `rm` never silently strands
  them:

```
Store at .mem/store.db has schema version 9, expected 11. No in-place migration
exists — run `mem rebuild` to re-ingest into a fresh store; it round-trips the
non-regenerable tables this store holds (lessons: 37 row(s), ...).
```

There is deliberately no migration framework: the store is a rebuildable
projection of the spine, so a version bump means re-ingesting into a fresh
store, not ALTERing an old one. The only escape hatch is
`openStoreForExport` (`src/store/sqlite.ts:87`): read-only, bypasses the
version gate, exists precisely so the export half of the round-trip can read a
store the current binary refuses to open. Exports re-validate every row through
zod on the way out; all write paths stay behind the gate.

**Do not** hand-edit `user_version`, copy tables between stores with raw SQL,
or delete a mismatched store before checking its non-regenerable inventory.

## 4. The rebuild round-trip

`mem rebuild [--store PATH] [build-store flags…]` (`src/cli/commands/rebuild.ts`)
is the enforced schema-bump cycle. What it does, in order:

1. **Export** the three non-regenerable populations from the old store, reading
   across the version mismatch (`allLessons`, `allMemoryEvents`,
   `producerProvenanceEvents`). A table an older schema never had exports
   honestly zero.
2. **Move the old store aside** (plus `-wal`/`-shm` sidecars) to
   `<path>.pre-rebuild-<ISO-stamp>` — preserved, never deleted; you remove it
   after verifying the rebuilt store.
3. **Import** the exported rows into a fresh store **BEFORE the build runs**.
   Ordering is load-bearing: the carried tables have no FK to `work_records`,
   and build-store's read-first provenance path prefers producer-recorded `cut`
   events already present in the target store over the git date heuristic.
4. **Build** via the normal `build-store` path. All build-store flags pass
   through: `--rig`, `--with-traces`, `--with-provenance`, `--session-join`,
   `--task-types`, `--transcript-archive`.

Failure semantics: if anything fails after the export, the error names the
backup path; the fresh store at the canonical path is partial. Fix the cause
and re-run `mem rebuild` (safe: the partial fresh store already holds the
imported rows, so the re-run's export step recovers them), or restore the
backup by renaming it back. `mem rebuild` on a path with no store refuses — a
first build is plain `mem build-store`.

### Preconditions checklist

- [ ] `npm run build` first — `./bin/mem` runs `dist/`, not `src/`; stale
      `dist/` silently runs old code (`bin/mem:3`).
- [ ] The dolt bead server is reachable: connection is `127.0.0.1` with the
      port read from `./.beads/dolt-server.port` **relative to your cwd**,
      falling back to 29620 (`src/ingest/beads.ts:35`).
- [ ] If passing `--with-traces`: run from the gas-city checkout with an
      **absolute** `--store` path — trace resolution shells `gc session logs`,
      which loads `city.toml` from the cwd, and a missing `city.toml` exits 0
      with zero traces resolved (no error). PROVISIONAL pending Stephanie (Q1):
      this cwd requirement is stated here as an operational precondition of the
      current deployment (`/home/ds/gas-city`), not a portable code contract.
- [ ] Flagless rebuilds are spine-only and fast; that is the default on purpose.

### Run it

```bash
cd /home/ds/projects/mem
npm run build
./bin/mem rebuild --store .mem/store.db --json   # add build-store flags as needed
```

### Verify after

```bash
# exported == imported.appended for all three tables? build coverage sane?
./bin/mem rebuild ... --json | # inspect data.exported / data.imported / data.build
node .claude/skills/mem-store-schema-and-rebuild/scripts/inspect-store.mjs .mem/store.db
./bin/mem coverage --store .mem/store.db          # read-only coverage report
```

The `--json` result (`RebuildResult`) reports `store`, `backup`, `exported`
(row counts read out) and `imported` (`{appended, skipped}` per table —
`skipped` means already-present, imports are idempotent), plus the full
`build` result. Only after these check out do you delete the
`.pre-rebuild-*` backup.

### Manual per-table pairs (surgical use)

For rescuing a single table, moving rows between stores, or auditing an export
before committing to a rebuild:

```bash
./bin/mem export-lessons           --store OLD.db --out lessons.ndjson
./bin/mem export-memory-events     --store OLD.db --out mem-events.ndjson
./bin/mem export-provenance-events --store OLD.db --out prov-events.ndjson  # producer rows only

./bin/mem build-store --store NEW.db    # fresh schema

./bin/mem import-lessons           --file lessons.ndjson     --store NEW.db
./bin/mem import-memory-events     --file mem-events.ndjson  --store NEW.db
./bin/mem import-provenance-events --file prov-events.ndjson --store NEW.db
```

All exports read across a version mismatch and emit NDJSON with `--out` (or
ride the `--json` envelope without it). All imports take `--file` or stdin and
are idempotent: lessons dedup on byte-equal full content (source-store `id` is
dropped; the destination assigns ids), memory/provenance events dedup on the
`id` PK via INSERT OR IGNORE. `import-provenance-events` throws on any
`ingest-backfill` row. Imports require the destination store to already exist —
they never silently materialize one.

## 5. The `--json` envelope contract

Every command supports `--json` (`src/schemas/envelope.ts`,
`src/cli/index.ts`). The Python harness consumes the store exclusively through
this contract (`mem query --json`), so treat it as an API:

```json
{"apiVersion": "v1", "cmd": "<command>", "ok": true,  "data": { ... }}
{"apiVersion": "v1", "cmd": "<command>", "ok": false, "errors": ["..."]}
```

Rules (all verified against `runCli`):

- **stdout is the envelope, stderr is for humans.** Under `--json`, stdout
  carries exactly one line of JSON; all human-readable progress goes to
  `console.error`. Without `--json`, stdout stays empty and the human text
  still goes to stderr. Parse stdout only.
- `apiVersion` is currently always `"v1"`. `data` is the command's typed result
  (e.g. `RebuildResult`, `QueryResult {count, records}`); it is outbound-only,
  no runtime validation.
- Errors (including unknown commands) print an error envelope and **exit 1**;
  success exits 0.
- Flag parsing trap: `--flag value` binds the value, but `--flag` followed by
  another `--option` (or nothing) binds `true`. A forgotten value after
  `--store` therefore silently falls back to the default `.mem/store.db`
  rather than erroring — double-check `data.store` in the output when scripting.

Verify the shape live:

```bash
./bin/mem version --json
# {"apiVersion":"v1","cmd":"version","ok":true,"data":{"name":"mem","version":"0.1.0"}}
```

## 6. Read-only diagnostics

`scripts/inspect-store.mjs` (in this skill's directory) prints the schema
version, every table's row count, its class (truth / projection /
non-regenerable), and the producer-vs-backfill provenance split — without
opening a write handle and without the version gate, so it works on stores the
current binary refuses:

```bash
cd /home/ds/projects/mem
node .claude/skills/mem-store-schema-and-rebuild/scripts/inspect-store.mjs [store.db]
```

Use it before deleting any store file, before and after a rebuild, and when
triaging a version-mismatch error.

## 7. Bumping the schema: the checklist

1. Edit `SCHEMA_DDL` in `src/store/schema.ts` and bump `SCHEMA_VERSION`,
   adding a comment above the constant saying **what changed and why a rebuild
   is forced** (the file's existing convention — see the v11 comment on
   `record_links` adjacency + `toIsoUtc` timestamp projection).
2. If the change adds a table: decide its class explicitly. A projection gets
   added to `CHILD_TABLES` in `src/store/writer.ts` (cleared + rebuilt per
   upsert). A new append-only table must be added to `NON_REGENERABLE_TABLES`
   in `src/store/sqlite.ts` **and** wired into the rebuild round-trip
   (export/import functions + `rebuild.ts`) — otherwise `mem rebuild` silently
   drops its rows on the next bump.
3. Tests ship with the change (`tests/` — vitest; the store/rebuild tests
   exercise the round-trip against `:memory:` and injected build runners).
4. `npm run check` green, then `npm run build`, then `mem rebuild` (§4).
5. A schema change is store-half plumbing, but if it touches eval-visible
   fields (timestamps, exclusion keys, oracle columns) it touches eval
   validity: HALT branch-ready and get Stephanie's sign-off. PROVISIONAL
   pending Stephanie (Q4): the conservative gate — anything touching temporal
   LOO, oracle soundness, or publishable numbers is sign-off territory.

## Provenance and maintenance

Authored 2026-07-07 against `/home/ds/projects/mem`, branch `main`, HEAD
`4e819e1`. Every command, path, line ref, and count above was verified against
that checkout on that date. Volatile facts (re-verify before trusting):

```bash
grep -n 'SCHEMA_VERSION = ' src/store/schema.ts        # currently 11 (line 28)
grep -n 'NON_REGENERABLE_TABLES' src/store/sqlite.ts    # the three tables (line 20)
grep -n 'BACKFILL_SOURCE' src/schemas/provenance-event.ts  # 'ingest-backfill' (line 29)
./bin/mem help --json                                   # live command list (24 commands on 2026-07-07)
node .claude/skills/mem-store-schema-and-rebuild/scripts/inspect-store.mjs  # live store version + counts
git log --oneline -3 -- src/store/schema.ts             # has the schema moved since 4e819e1?
```
