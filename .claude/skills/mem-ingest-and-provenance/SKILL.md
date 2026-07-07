---
name: mem-ingest-and-provenance
description: >
  Runbook for mem's ingest readers and git-provenance stages: the beads spine
  reader (dolt), trace-resolve, repo-resolve, base-commit-by-date provenance,
  the git-native landed / commit-linkage outcome oracle, session-commit
  true-base recovery, provenance events (producer cut/claim/land), and the
  coverage axes that tell you whether an ingest actually populated anything.
  Load when building or diagnosing the .mem/store.db sidecar, when a coverage
  axis reads zero, when adding a rig to the rig→repo map, when wiring a
  producer `cut` event, or when you need to know which ingest stage sets which
  WorkRecord field. NOT for the nightly substrate-ingest procedure (use the
  existing ingest-trace-substrate skill); NOT for schema versions, rebuild
  round-trips, or the append-only tables (use mem-store-schema-and-rebuild);
  NOT for the parse layer's error extraction (use
  mem-deterministic-extraction-zfc); NOT for temporal-LOO exclusions (use
  mem-temporal-loo-and-leak-safety); NOT for why the real-corpus oracle funnel
  collapsed (use mem-failure-archaeology and
  mem-oracle-validity-wall-campaign).
---

# mem ingest and provenance

How real work records and their git/outcome signal get INTO the store. This
skill maps every ingest reader to the field it populates, the coverage axis
that proves it ran, and the traps that make a run silently produce nothing.

Verified against `sjarmak/mem` at `main` @ `4e819e1` on 2026-07-07.

## When NOT to use this skill

| You want                                                                                       | Use instead                                                                     |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Run the recurring/nightly substrate rebuild end-to-end (merged join, archive, verify-and-swap) | `ingest-trace-substrate` (existing repo skill — this skill does not restate it) |
| Schema version bumps, `mem rebuild`, export/import of append-only tables                       | `mem-store-schema-and-rebuild`                                                  |
| How trace errors are extracted (runners, error-extractors, ZFC line)                           | `mem-deterministic-extraction-zfc`                                              |
| Why retrieval excludes convoy/PR/supersedes siblings                                           | `mem-temporal-loo-and-leak-safety`                                              |
| Why only a handful of linkage-recovered oracles replay soundly (settled negatives)             | `mem-failure-archaeology`, `mem-oracle-validity-wall-campaign`                  |
| Running the Python eval over the store                                                         | `mem-eval-harness-run`                                                          |

## Glossary (defined once)

- **Bead** — one work item in the Gas City orchestrator's dolt-backed queue.
  **Rig** — one project's bead database (one database per rig on a shared dolt
  sql-server). **Spine** — the bead-derived skeleton of a WorkRecord (id, rig,
  title, labels, links, lifecycle, assignee), before any trace/git signal.
- **WorkRecord** — the canonical record type (`src/schemas/workrecord.js`);
  everything ingest produces is a validated WorkRecord.
- **Sidecar** — the generated SQLite+FTS5 store at `.mem/store.db`
  (gitignored; rebuildable projection).
- **Coverage axis** — a store-wide count (`mem coverage`) that an ingest stage
  is supposed to lift off zero; the proof a stage actually ran.
- **Base commit** — the commit a session started from; the git-checkout anchor
  a replay needs. **Landed** — what the session left on the integration
  branch (the forward mirror of the base).
- **Oracle** — a verifiable outcome label (did the work land / merge / pass
  CI) used to grade replays.

## The pipeline: stage order inside `build-store`

`mem build-store [--rig <name>] [--with-traces] [--with-provenance] [--store PATH]
[--session-join FILE] [--task-types FILE] [--transcript-archive DIR]`
streams one rig at a time (read → attach → write per rig; peak memory is one
rig, each rig its own transaction; a mid-build failure aborts loudly and
leaves a partial store — re-run to rebuild). Stage order, verified in
`src/cli/commands/build-store.ts` (`buildStoreCommand`):

| #   | Stage                 | Module                                                                        | Flag gate                                                 | Sets on the record                                                            |
| --- | --------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 1   | Read spine            | `src/ingest/beads.ts`                                                         | always                                                    | work_id, rig, title, labels, links, lifecycle, agents, metadata, external_ref |
| 2   | Repo identity         | `src/ingest/repo-resolve.ts`                                                  | always (no IO)                                            | `repo`, `repo_source`                                                         |
| 3   | Task typing           | `src/ingest/task-type.ts`                                                     | artifact via `--task-types` (mechanical rules always run) | `task_type`, `task_type_source`, `molecule_id`                                |
| 4   | Session join          | `src/ingest/session-merge.ts`                                                 | `--session-join`                                          | multi-row `agents`, pre-resolved `trace_ref`s                                 |
| 5   | Trace resolve + parse | `src/ingest/trace-resolve.ts` → `src/parse/trace-parse.ts` (`attachAndParse`) | `--with-traces`                                           | `trace.jsonl_path`, agents' `trace_ref`, trace errors/runs                    |
| 6   | Git baseline          | `src/ingest/provenance.ts` (+ read-first `src/ingest/provenance-from-log.ts`) | `--with-provenance`                                       | `provenance` (work_dir, base_branch, `base_commit`, `history_state`)          |
| 7   | Commit linkage        | `src/ingest/commitLinkage.ts`                                                 | `--with-provenance`                                       | `outcome.commit_sha` (+ `pr`, `pr_state`) for sound linkage only              |
| 8   | Landed                | `src/ingest/landed.ts`                                                        | `--with-provenance`                                       | `landed` (landed_state, landed_commit, n_commits)                             |
| 9   | Session commits       | `src/ingest/sessionCommits.ts`                                                | `--with-traces` AND `--with-provenance` (no-op otherwise) | `session_commits` (local SHAs, `true_base`, `base_state`)                     |
| 10  | Write + backfill      | `buildStoreFromRecords` → `writeRecords` + `deriveProvenanceEvents`           | always                                                    | store rows + backfill-source `provenance_events` (cut/claim/land)             |

After the loop, `checkRecordLinks` enforces the mem-qgdz guard: a build that
wrote records but zero `record_links` edges **throws on a full-corpus build
but only WARNS on a single `--rig` build** — a broken dependency ingest on a
`--rig` build does not fail loudly. Check the `record_links` count yourself
on `--rig` builds.

The flagless default is spine-only (stages 1–4 only, no `gc`/transcript/git
IO) and fast — keep it that way. `mem ingest-traces` is `build-store` with
both flags forced on plus a before/after coverage diff; `mem ingest-beads`
is a read-only spine dump (no store write).

## The readers, one by one

### 1. Beads spine (`src/ingest/beads.ts`)

Reads the shared Gas City dolt sql-server: connection defaults to
`127.0.0.1` with the port read from `.beads/dolt-server.port` in the cwd
(fallback 29620), user `root`, empty password. A "rig" is any database on
that server with an `issues` table, MINUS system schemas, the known non-rig
databases (`__gc_probe`, `dolt_pkg_shared`), and leaked test databases
excluded by name prefix (`testdb_`, `test_cloud_auth_`, `test_federation_`,
`test_guard_`, `fixdepkeys_`) — a test fixture's beads must never enter the
canonical store.

Structural mappings to know before touching this file:

- `parseAssignee`: `<role>-<session>` → `{agent_id: 'gc-NNNN', role}`; an
  assignee with no session id becomes the whole `agent_id`.
- Dependency rows: `parent-child` → `links.parent`; `tracks` →
  `convoy_id`; `supersedes` → `links.supersedes`; everything else →
  `links.deps`. Conflicting parent/convoy rows: sorted-first wins, with a
  warning — deterministic, never silent.
- `epicParent`: dotted ids (`mem-lvp.12` → parent `mem-lvp`) backfill the
  epic parent when no explicit edge exists. This feeds the D6 sibling
  exclusions — see `mem-temporal-loo-and-leak-safety`.
- Malformed bead metadata degrades that one record to `{}` with a warning;
  a broken spine shape still throws.

### 2. Repo resolve (`src/ingest/repo-resolve.ts` + `src/ingest/rig-repo-map.ts`)

Pure lookup, always on, no flag. Precedence: `outcome.repo` (a real resolved
PR) → `RIG_REPOS[rig].slug` → `unmapped` (recorded, **never guessed**;
`repo_source` carries which). `gc.work_dir` is deliberately NOT a source
here (its basename is not `owner/name`).

`RIG_REPOS` maps each rig to its GitHub `owner/name` slug plus a durable
LOCAL checkout `dir` and integration `branch` (default `main`). The `dir`
paths are absolute machine-local paths — this map is operational config for
the machine that hosts the rig checkouts, not portable code; on any other
machine every git-dependent stage degrades to `unresolved`/no-linkage
(readers return null/[] on non-zero git exits). To add a rig: add the entry,
then run the fail-closed preflight
`node scripts/verify-rig-checkouts.mjs` (read-only; asserts checkout exists,
origin remote matches the slug, and no two rigs share one object store).
A `multi: true` rig (e.g. `gc`) has no single repo and stays unmapped.

### 3. Trace resolve (`src/ingest/trace-resolve.ts`)

Chain: assignee → session id (`/\bgc-\d+/`) → `gc session logs <id> --json`
→ transcript JSONL path. Memoized per session id. A pre-set `trace_ref`
(from the session-join artifact) skips the `gc` shell entirely. The
transcript-archive fallback (`src/ingest/trace-archive.ts`,
default root co-located with the store, `--transcript-archive` overrides)
rewrites reaped paths to durable restored copies.

**The deliberate error asymmetry (memorize this):** `gc session logs`
exiting non-zero = "unknown session" = a normal unresolved outcome → null.
A MISSING `gc` binary (or any non-exit failure) **propagates** — that is a
misconfiguration and must never be swallowed. But see the cwd trap below:
a present `gc` with the wrong cwd is the case that fails silently.

### 4. Provenance: base commit by date (`src/ingest/provenance.ts`)

Reconstructs the environment baseline: `work_dir` (from `gc.work_dir` /
`work_dir` metadata, else the rig-map checkout — work_dir is a rig constant),
`base_branch` (from `gc.var.base_branch` metadata, else the rig's mapped
integration branch), then `base_commit` = **the newest commit on the base
branch at or before session start** (`git rev-list -1 --before=<start>`),
recorded as `history_state: 'commit-by-date'`.

Three properties you must not break:

1. **It is an approximation, and says so.** The stored base is a
   timestamp-approximate main-tip, not the session's true per-worktree fork
   SHA. This is the root cause of the replay-fidelity wall
   (`docs/mem-7q6e-replay-engine-null.md`) — do not "fix" replay by
   loosening this; see `mem-failure-archaeology` before touching anything
   here. Funnel counts are held under the mem-0rrf publication freeze.
2. **Leak-safety: the base is NEVER resolved against the work_dir's HEAD**,
   which would walk the agent's own feature branch (whose history may
   contain the solution). No branch known → `history_state: 'unresolved'`,
   never guessed.
3. **Timestamps are pinned to UTC** (`toGitUtc`): git's approxidate reads a
   TZ-less timestamp in host-local time, which would make the resolved
   commit host-dependent. Branch names from the DB are passed after
   `--end-of-options` so a hostile value cannot inject a git flag.

**Read-first upgrade:** when a producer has recorded the exact fork SHA as a
`cut` event in `provenance_events`, `build-store --with-provenance` prefers
it (no git call) and marks `history_state: 'recorded'`
(`src/ingest/provenance-from-log.ts`). Honesty guard: events written by the
ingest backfill projector itself are excluded from the read — otherwise the
date approximation would be laundered into "recorded/exact". The producer
surface is:

```bash
./bin/mem provenance record --issue <work-id> --kind cut \
  --ref <40-hex-sha> --ref-kind git-sha --source git-hook \
  --store /home/ds/projects/mem/.mem/store.db
./bin/mem provenance log <work-id> --store .mem/store.db      # read back
./bin/mem provenance by-ref <sha>  --store .mem/store.db
```

Append-only, idempotent (deterministic event id; re-recording is a no-op).
`--source ingest-backfill` is rejected (reserved). Malformed git-sha refs
are rejected at write time. Ref-less kinds require `--at <iso>` so the
caller, not the clock, owns the dedup key.

### 5. Commit linkage — the git-native outcome oracle (`src/ingest/commitLinkage.ts`)

The corpus is direct-to-main (Decisions 17/18 in
`docs/architecture-decisions.md`: the merged-PR/CI oracle is inapplicable by
construction — do not re-litigate). The recoverable signal is that the
orchestrator writes each work id into its landing commit message
(`... (mem-abc.7) (#104)` on PR rigs, a bare `(<work_id>)` trailer on
direct-commit rigs). This stage reads each rig's integration-branch
`git log` ONCE per rig and matches known work ids as exact whole tokens.

Linkage confidence, and the rule that matters:

| Linkage     | Meaning                                     | Stored?                                                                                                       |
| ----------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `canonical` | subject ends with the `(<work_id>)` trailer | yes                                                                                                           |
| `unique`    | exactly one commit references the id        | yes                                                                                                           |
| `multiple`  | several non-canonical commits reference it  | **NO** — counted in the `records_multiple_linkage` residual, never stored as a newest-commit guess (mem-ahb2) |

`mem link-outcomes <rig> [--store PATH] [--json]` emits the per-rig
`{work_id, commit_sha, linkage}` report read-only (the input to the Python
fail-to-pass curator). It errors for a rig with no local checkout in
`RIG_REPOS`.

### 6. Landed — the forward mirror (`src/ingest/landed.ts`)

Where provenance dates the branch tip at session START, landed dates it at
session CLOSE and asks "what did this session leave on the branch, and did
it survive". Candidates need a resolved `base_commit` + `base_branch` + a
parseable close time. Window = commits in `base..close-tip`.

| `landed_state`     | Meaning                                                                                                                                                             |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `landed`           | forward commits exist, close-tip still an ancestor of the branch, none reverted                                                                                     |
| `reverted`         | a later `This reverts commit <sha>` trailer names a window commit                                                                                                   |
| `abandoned`        | close-tip no longer an ancestor (history rewritten)                                                                                                                 |
| `empty-window`     | branch tip never moved over the session — nothing landed                                                                                                            |
| `ambiguous-window` | two sessions' windows overlap on the same checkout+branch with commits to contest — attribution by time alone is impossible, so it is left ambiguous, never guessed |
| `unresolved`       | close tip (or the window) cannot be dated/computed against the checkout                                                                                             |

An overlapping record whose own window is EMPTY still resolves
deterministically to `empty-window` (nothing to contest).

### 7. Session commits — the true per-worktree base (`src/ingest/sessionCommits.ts`)

Recovers each session's OWN local commit SHAs from the transcript text: git
prints `[<branch> <sha>] <subject>` (also the `detached HEAD` and
`(root-commit)` forms) only when it actually creates a commit, so that line
is a deterministic record even though the corpus commits through a wrapper.
`true_base = parent(first local commit)` resolved against the rig clone —
independent of the upstream squash. `base_state: 'resolved'` when the commit
still exists in the clone; `'commit-absent'` when squashed/rebased away (the
SHAs are still recorded; the base is never invented). Needs BOTH flags
(trace text + rig checkout). This is the mem-75t.15 recovery path — the only
per-record signal that survives the squash wall.

### 8. PR outcomes (`src/ingest/outcomes.ts`) and the rest

`resolveBranchOutcome` maps `gh pr list` JSON → Outcome (merged/closed,
merge commit, CI rollup where any failure wins and CANCELLED/TIMED_OUT count
as failures). On this corpus it is largely inapplicable (D17/D18) — kept for
rigs that do use PRs. Two further specialized readers exist and are indexed
here only: `src/ingest/dashboardCi.ts` (mem-wanz.5, dashboard merged-PR/CI
T1 tier from the Day-0 frozen snapshot) and `src/ingest/liveRef.ts`
(mem-wanz.6, re-measuring the live-ref join). `src/ingest/memory-capture.ts`
is the mem-31kz forward-capture write path, not a corpus reader.

## Coverage axes: proving an ingest did anything

`mem coverage --store <path>` (read-only) and the `ingest-traces` delta
print the same 8 axes (`src/store/reader.ts` `coverageReport`,
`src/cli/commands/coverage.ts`):

| Axis               | Counts                                                   | Lifted by stage    |
| ------------------ | -------------------------------------------------------- | ------------------ |
| `records`          | work_records rows (the denominator)                      | spine              |
| `with_trace`       | records with a resolved transcript path                  | trace-resolve (5)  |
| `trace_errors`     | parsed deterministic failure-signature rows (bare count) | parse after (5)    |
| `trace_runs`       | run-metadata rows (bare count)                           | parse after (5)    |
| `with_base_commit` | records with a git base anchor                           | provenance (6)     |
| `with_commit_sha`  | records with a landing/outcome SHA                       | commit linkage (7) |
| `multi_session`    | records with ≥2 non-suspect session iterations           | session join (4)   |
| `with_task_type`   | records with a task_type                                 | task typing (3)    |

`build-store --json` additionally reports per-run counters not in the store
report: `records_with_repo` (chase the `unmapped` residue),
`records_recorded_base` (read-first wins), `records_multiple_linkage`
(dropped ambiguous linkage), `records_landed`, `records_with_session_base` /
`records_with_session_commits`, `records_provenance_events`, and
`record_links` (the D6 exclusion substrate — zero on a real corpus means the
dependency ingest broke).

**A healthy full run has non-zero `with_trace`, `trace_errors`,
`trace_runs`, and `with_base_commit`. All-zero trace/provenance axes with a
normal-looking record count = the cwd trap below, not an empty corpus.**

## THE trap: cwd / `city.toml` silent exit 0

> PROVISIONAL pending Stephanie (discovery Q1): the gas-city-cwd requirement
> below is stated as an **operational precondition** of this deployment, not
> a portable code path. On a clone without a Gas City installation,
> `--with-traces` has no resolver to shell and full-substrate ingest is not
> reproducible; spine-only builds still work against any reachable dolt
> server.

`--with-traces` shells `gc session logs`, which loads `city.toml` from the
**working directory**. Run from `/home/ds/projects/mem` (or anywhere without
a `city.toml`), every session resolves to "unknown": the spine still loads,
the command **exits 0**, and every trace/provenance axis is zero — a green
run that populated nothing. The distinction is deliberate: an unknown
session (non-zero `gc` exit) is a normal unresolved outcome; only a MISSING
`gc` binary propagates as an error. Wrong cwd looks exactly like 6,000
unknown sessions.

Consequences, in order:

1. Run full rebuilds from the gas-city checkout with an **absolute
   `--store`** path back into this repo.
2. Build into a scratch path, check the coverage axes, and only then swap —
   never overwrite the live sidecar with an unverified build.
3. `delta: none` on a re-run is correct (idempotent writer), not a failure.
4. Second entrypoint trap: `node dist/main.js` only defines `main` and exits
   0 doing nothing. The entrypoint is `./bin/mem` — which runs `dist/`, so
   `npm run build` first after any TS edit.

The full verified invocation, the merged-session-join-first ordering, the
~6-week JSONL rolling-window race, and the verify-before-swap workflow are
owned by the existing **`ingest-trace-substrate`** skill — use it for the
actual procedure. The unattended form is `scripts/ingest-trace-substrate.sh`
(env-overridable paths; detects the zero-trace wrong-cwd case and aborts the
swap), documented in `scripts/ingest-trace-substrate.cron.md`. Note
(2026-07-07): README's pointer to a `.gc/cron/` cadence is stale — that
directory does not exist; the cron doc above is the real one.

## Quick runbooks

**Spine-only build (fast, from this repo, any cwd with dolt reachable):**

```bash
cd /home/ds/projects/mem && npm run build
./bin/mem build-store --store /tmp/spine.db --json
# expect: count > 0, record_links > 0, records_with_repo close to count
```

**Read coverage of the live store (read-only, no rebuild):**

```bash
cd /home/ds/projects/mem
./bin/mem coverage --store .mem/store.db
```

**Diagnose "everything is zero":** run
`bash .claude/skills/mem-ingest-and-provenance/scripts/ingest-preflight.sh`
(read-only) — it checks the entrypoint/dist staleness, `gc` + `city.toml`
visibility from the cwd, dolt reachability, and prints the coverage axes of
a store you point it at.

**Count instead of eyeballing** (long listings truncate under the rtk
proxy):

```bash
./bin/mem coverage --store .mem/store.db --json   # exact counts, one line
git -C /home/ds/projects/mem rev-list --count main
```

## Change discipline for this area

- Anything that changes how `base_commit`, linkage, or landed states are
  derived changes what the eval can replay and grade → treat as
  HALT-branch-ready (Stephanie sign-off, tests in the same commit;
  PROVISIONAL pending Stephanie, discovery Q4 — conservative gating until
  she states the exact scope). See
  `mem-git-and-dispatch-workflow` and `mem-decision-ledger-and-architecture-contract`.
- Never add semantic/keyword heuristics to any reader — ingest is pure IO +
  mechanical mapping (ZFC); unresolved stays unresolved, `None` is never a
  fabricated value.
- Do not re-attempt the gh/PR outcome re-ingest or "recover" broken replay
  bundles from approximate bases — settled negatives; read
  `mem-failure-archaeology` first.
- Real-corpus counts and headline numbers are under the mem-0rrf publication
  freeze — quote them only with their validity caveats and the freeze named.

## Provenance and maintenance

Authored 2026-07-07 against `main` @ `4e819e1` (checkout
`/home/ds/projects/mem`, on main at authoring time). Corpus-size figures
(6,691 beads / 874 transcripts) are as of the 2026-07-07 discovery report
and drift daily. Re-verify before trusting:

```bash
git -C /home/ds/projects/mem log --oneline -1                                  # pin drift
grep -n "SCHEMA_VERSION" /home/ds/projects/mem/src/store/schema.ts             # schema v11 at authoring
/home/ds/projects/mem/bin/mem help                                             # command list (24 commands at authoring)
grep -n "registerCommand" /home/ds/projects/mem/src/main.ts                    # registry ground truth
grep -n "COVERAGE_AXES" /home/ds/projects/mem/src/cli/commands/coverage.ts     # 8 axes at authoring
grep -n "gc.work_dir\|gc.var.base_branch" /home/ds/projects/mem/src/ingest/provenance.ts  # metadata keys
grep -c "slug:" /home/ds/projects/mem/src/ingest/rig-repo-map.ts               # 21 matches at authoring (20 rig entries + the interface field)
node /home/ds/projects/mem/scripts/verify-rig-checkouts.mjs                    # rig-map ↔ checkout drift (read-only)
ls /home/ds/projects/mem/scripts/ingest-trace-substrate.cron.md                # cron doc still the real cadence home
```
