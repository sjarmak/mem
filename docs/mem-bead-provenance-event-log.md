# Provenance as a first-class primitive: beads vs gascity vs the user's setup

**Status:** design sketch · **Date:** 2026-06-19

## The question

mem reconstructs work provenance after the fact — base SHA by date, session→commit by time-window, work→commit by `(work_id)` trailer scan. Each reconstruction is lossy; some recover information destroyed at work-time. To make provenance a **first-class primitive** instead of a reconstruction, we have to decide *which layer owns each fact*:

- **beads (bd)** — the durable work ledger. General-purpose, dolt-backed, used by gascity but not owned by it.
- **gascity (gc)** — the orchestrator: creates worktrees, claims beads to sessions, routes convoy/mayor work, knows the gc↔Claude session bridge.
- **the user's setup** — for someone on plain beads with their *own* harness: their git repo, CI, commit conventions, retrieval layer.

The primitive only counts as first-class if a user with **no gascity** still gets provenance. That constraint drives every boundary below.

## The decision principle

> A fact is **owned** by the layer that is its system of record. It is **produced** by the layer that performs the action that makes it true. **beads stores only the binding** of that fact to a `work_id` — beads is the join table of provenance, not the source of any individual fact.

Applying it:

- **git** is system-of-record for `base_commit`, `commit_sha`, `landed_commit`, ancestry. beads must *capture* these, never invent them.
- **the runtime** (gascity, or the user's harness) is system-of-record for session lifecycle — claim/suspend/resume/handoff — because it *performs* those acts. "Session" is a runtime concept; beads stays agnostic.
- **the retrieval layer** (mem, or the user's RAG) is the only thing that sees what was read. `used`/`wasInformedBy` can only be produced there.
- **beads** is system-of-record for `work_id`, deps/supersedes, status — and, newly, for the **provenance event log** that binds all of the above to a `work_id`.

The corollary that prevents over-reach: **beads must take *opaque* identifiers.** Not `session_uuid` (Claude-specific), not `gc-335825` (gascity-specific) — an opaque `actor_id` / `run_id` / `ref`. The moment beads' schema knows what a Claude session is, it has stopped being a primitive and become a gascity client.

## The primitive: `bead_events` (beads-owned)

An append-only, immutable event log keyed by `work_id`, with an **open vocabulary** and **runtime-agnostic fields**. This is the only new thing beads owns.

```sql
CREATE TABLE bead_events (
  event_id    TEXT PRIMARY KEY,   -- ulid; monotonic, sortable
  work_id     TEXT NOT NULL,
  event_kind  TEXT NOT NULL,      -- open vocab: 'cut'|'claim'|'suspend'|'resume'
                                  -- |'handoff'|'commit'|'land'|'used' ... extensible
  ts          TEXT NOT NULL,      -- ISO-8601, EVENT-time (not ingest-time)
  actor_id    TEXT,              -- OPAQUE runtime identity; bd does not interpret it
  ref         TEXT,              -- OPAQUE pointer: a SHA, a PR url, a work_id, a path
  ref_kind    TEXT,              -- 'git-sha'|'pr'|'work_id'|'transcript'|... namespacing for ref
  payload     JSON,              -- kind-specific extras, schema-validated by bd
  source      TEXT NOT NULL      -- who appended: 'gascity'|'git-hook'|'ci-webhook'|'user-cli'
);
```

beads ships **three things and no more**: this table, a write API (`bd event append`), and a read API (`bd event log <work_id>`, `bd event by-ref <sha>`). beads validates *structure* (kinds are known, `ref_kind` matches, append-only) — never *meaning*. It does not poll git, does not know what a session is, does not resolve anything. That keeps it a primitive.

## Responsibility matrix

| Provenance fact | System of record | Producer — **gascity world** | Producer — **plain-beads user** | Stored in beads as |
|-----------------|------------------|------------------------------|-------------------------------|--------------------|
| `base_commit` (fork point) | git | gc WorktreeCreate hook → `bd event append cut` | user's worktree script / `post-checkout` hook | `cut` event, `ref`=SHA |
| session lifecycle (claim/suspend/resume/handoff) | the runtime | gc claim + lifecycle | user's harness; absent → one implicit actor | lifecycle events, opaque `actor_id` |
| session → commit attribution | **git** (the commit) | commit-msg trailer `Bead-Session:` (gc installs hook) | same trailer (user installs hook) | ingested `commit` event — but durable home is the **trailer in git** |
| `landed_commit` + `pr` + `ci` | git + CI | gc merge hook / CI → gc → `bd event append land` | user's CI webhook / merge hook | `land` event → enables sound **T1** at close |
| retrieval causality (`used`/`wasInformedBy`) | the retrieval layer | mem harness emits `used` | user's RAG emits `used` | `used` edge, `ref`=work_id/entity |

Read top to bottom: **beads owns the right-hand column only.** Every producer is gascity-or-user. Every system-of-record is git/runtime/retrieval. beads is the spine they all write to.

### The load-bearing insight: don't duplicate git

Session→commit attribution (the ~2,836 `ambiguous-window` records) should live as a **git commit trailer** (`Bead-Session: <actor_id>`), not primarily as a beads event. Reasons:

- git is the system of record for commits; the trailer travels with the commit forever, survives beads loss, and is readable by anyone with the repo.
- beads *ingests* it into a `commit` event for query convenience, but the trailer is the source of truth.
- This is the cleanest example of the principle: the producer stamps the fact into *its own system of record*, and beads merely binds it to `work_id`.

A git hook that stamps the trailer is a ~10-line, gascity-agnostic artifact — exactly the kind of thing a plain-beads user can adopt without any orchestrator.

## What stays OUT of beads

To keep the primitive clean, these remain in gascity or the consumer:

- **gascity:** the meaning of a gc session, `gc-NNNNNN` id format, the gc↔Claude `session_uuid` bridge, mayor/convoy routing, suspend/resume policy. gascity maps all of these *down* to opaque `bead_events` fields.
- **mem (consumer):** time-window attribution, `build_merged_join.py`, CI dashboard snapshots, the tier ladder projection. With the event log present, mem **stops reconstructing and starts reading** — it becomes a pure consumer that projects `bead_events` into its `record_agents` / `trace_runs` / `links` tables.

If any of these leak into beads' schema, beads is no longer a primitive — it's a gascity-or-mem-specific store.

## The three contracts

1. **gascity → beads:** gc's worktree/claim/lifecycle/merge hooks call `bd event append` with opaque ids. Everything gascity-specific stays gascity-side and is flattened to generic fields at the boundary.
2. **user → beads:** a documented event vocabulary + the `Bead-Session:` trailer convention + thin git-hook/CI-webhook templates. A user wires their own runtime to the *same* API gascity uses. This is what makes it a primitive rather than a gascity feature.
3. **beads → consumer (mem):** `bd event log` / `bd event by-ref`. mem reads, never writes; projects events into its store and drops the reconstruction stages (`provenance.ts` date-heuristic, merged-join, trailer-scan in `commitLinkage.ts`).

## What this collapses (mem-side, once events exist)

- `provenance.ts:193-211` date-heuristic → read `cut.ref`; `commit_state` becomes `'recorded'`.
- `ambiguous-window` (~2,836) → exact join on commit-trailer `actor_id`.
- `commitLinkage.ts` 3-confidence scan → single `land` event; **T1 writable at close** instead of the post-hoc `dashboardCi` elevation.
- `build_merged_join.py` → fold of lifecycle events by `ts`; sidesteps the singular `trace.run`.
- reserved-but-unwritten `used`/`wasInformedBy` (`writer.ts`) → populated by `used` events — the one signal *absent* today, not merely lossy.

## Recommended split

- **beads gets:** `bead_events` table + append/read API + the open event vocabulary + structural validation. Nothing else. This is the primitive; it's small and upstreamable.
- **gascity gets:** the producer hooks (worktree/claim/lifecycle/merge) and the `Bead-Session:` git-hook installer, plus the opaque-id mapping. gascity is the *reference producer* that proves the API.
- **the user gets:** the same append API + git-hook/CI-webhook templates + the trailer convention. A plain-beads user wires their own runtime and gets identical provenance with zero gascity.
- **mem stays a consumer:** reads events, projects, deletes its reconstruction stages.

## Open questions

- **Upstream sequencing.** The `bead_events` primitive must land in bd *before* gascity can produce or mem can consume. Is the bd schema change in scope, or do we prototype the event log mem-side first and upstream once the vocabulary stabilizes?
- **Backfill horizon.** Events are forward-only; ~6000 historical beads keep their reconstructions as permanent fallback. Is a forward-only corpus large enough for the oracle?
- **Trailer vs event for attribution.** Committing to the `Bead-Session:` trailer as the system of record (with beads ingesting) vs storing it primarily as a beads event. The trailer is more durable and more gascity-agnostic — favored — but requires the commit-time hook to know the actor.
- **`used` producer.** In mem's `ours`/`none` arms, memory is injected differently per arm — does `used` get stamped by the harness at injection, or inferred from the retrieval call log? This decides whether retrieval causality is observable in the `none` arm at all.
