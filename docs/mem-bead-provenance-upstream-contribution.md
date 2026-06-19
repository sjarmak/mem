# Upstream contribution: provenance event log in beads + gascity

**Status:** contribution spec · **Date:** 2026-06-19 · **Companion to:** `mem-bead-provenance-event-log.md` (the layering rationale)

This is the concrete shape of the upstream work: exact bd schema + CLI surface, the gascity producer, the external-user surface, and how the rationale gets documented in each repo's own idiom. Grounded in the actual sources:

- **beads** — Go, `github.com/steveyegge/beads`, `/home/ds/gastownhall/beads/`. Cobra CLI (`/cmd/bd/*.go`), embedded numbered SQL migrations (`/internal/storage/schema/migrations/`, currently at **0039** → next is **0040**), MIT, ADRs in `/docs/adr/`, one-issue-per-PR, ZFC.
- **gascity** — Go, `github.com/gastownhall/gascity`, `/home/ds/gascity/` (fork `sjarmak/gascity`). Talks to bd by shelling the `bd` CLI via `bdCommandRunnerForCity()` in `/cmd/gc/bd_env.go`. Already has a session-event system (`/internal/events/events.go` → `.gc/events.jsonl`). RFCs in `/engdocs/design/`, conformance tests as executable spec, `make check`, ZFC.

## What the codebase reality changes

Two findings move the design off the earlier sketch:

1. **bd already has an append-only `events` table** (migration 0005: `id` UUID, `issue_id`, `event_type`, `actor`, `old_value`, `new_value`, `comment`, `created_at`, FK→issues CASCADE). mem already reads it for `status_history` (`src/ingest/beads.ts`). The provenance log must *not* duplicate this — but it also can't *reuse* it: that table's shape is a two-value field-mutation (`old_value`/`new_value`/`comment`), whereas a provenance fact is a typed binding to a structured external artifact (a `land` event is commit + PR + CI, three fields). **bd's own precedent settles it:** bd created a separate `wisp_events` table (migration 0031) rather than overloading `events`. We follow that precedent with a sibling `provenance_events` table.

2. **gc does not create the worktree or perform the merge.** Worktrees are a `WorkDir` template the agent populates itself; landing is entirely external to gc. So even in the gascity world, `cut` (base SHA) and `land` (commit+CI) are **git/CI-boundary** events, not gc events. gc owns exactly what it performs: `claim` and session lifecycle (which it *already* emits to `.gc/events.jsonl`). This tightens the gascity PR to a narrow, honest scope and pushes `cut`/`land` to the git-hook surface for *everyone* — which is what makes the primitive genuinely runtime-agnostic.

---

## Contribution 1 — beads (the primitive)

One focused PR. Adds a table, store methods, a `bd provenance` command group, tests, and an ADR. Nothing reads or writes it unless a producer opts in, so it's inert-by-default — the key argument against "too app-specific."

### 1a. Migration — `0040_create_provenance_events.{up,down}.sql`

Matches bd column idioms exactly (`VARCHAR(255)` issue ids, `CHAR(36) DEFAULT (UUID())` PK, FK CASCADE, `DATETIME` + `CURRENT_TIMESTAMP`):

```sql
-- 0040_create_provenance_events.up.sql
CREATE TABLE provenance_events (
  id          CHAR(36)     NOT NULL DEFAULT (UUID()),
  issue_id    VARCHAR(255) NOT NULL,
  kind        VARCHAR(32)  NOT NULL,  -- cut|claim|suspend|resume|handoff|commit|land|used  (open vocab)
  actor       VARCHAR(255),           -- OPAQUE runtime identity; bd does not interpret
  ref         TEXT,                   -- OPAQUE pointer: a SHA, PR url, work_id, transcript path
  ref_kind    VARCHAR(32),            -- git-sha|pr|work_id|transcript|...  namespaces `ref`
  payload     JSON,                   -- kind-specific structured extras
  source      VARCHAR(64)  NOT NULL,  -- gascity|git-hook|ci-webhook|user-cli
  occurred_at DATETIME     NOT NULL,  -- EVENT-time (hooks may record after the fact)
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- ingest-time
  PRIMARY KEY (id),
  CONSTRAINT fk_provenance_issue FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE
);
CREATE INDEX idx_provenance_issue_time ON provenance_events (issue_id, occurred_at);
CREATE INDEX idx_provenance_ref        ON provenance_events (ref);
CREATE INDEX idx_provenance_kind       ON provenance_events (kind);
```

```sql
-- 0040_create_provenance_events.down.sql
DROP TABLE IF EXISTS provenance_events;
```

Design notes the ADR must defend:
- **`occurred_at` separate from `created_at`** — the existing `events` table has only `created_at` because audit events are recorded inline. Provenance events are recorded by hooks that may fire after the fact (a `cut` captured at worktree setup, a `land` from a CI webhook minutes later), so event-time ≠ ingest-time and both matter for ordering. This is the one place we deviate from the `events` idiom, and it's deliberate.
- **`actor`/`ref` opaque** — no `session_uuid`, no `gc-NNNNNN`. The moment bd's schema understands a Claude session it stops being a primitive. `actor` mirrors the existing `events.actor` column name for consistency.
- **append-only** — no UPDATE/DELETE path is exposed (see CLI). Corrections are new rows.

### 1b. Types — `/internal/types/types.go`

Alongside the existing `Event`/`EventType` definitions:

```go
type ProvenanceEvent struct {
    ID         string          `json:"id"`
    IssueID    string          `json:"issue_id"`
    Kind       string          `json:"kind"`
    Actor      string          `json:"actor,omitempty"`
    Ref        string          `json:"ref,omitempty"`
    RefKind    string          `json:"ref_kind,omitempty"`
    Payload    json.RawMessage `json:"payload,omitempty"`
    Source     string          `json:"source"`
    OccurredAt time.Time       `json:"occurred_at"`
    CreatedAt  time.Time       `json:"created_at"`
}

const (
    ProvKindCut     = "cut"
    ProvKindClaim   = "claim"
    ProvKindSuspend = "suspend"
    ProvKindResume  = "resume"
    ProvKindHandoff = "handoff"
    ProvKindCommit  = "commit"
    ProvKindLand    = "land"
    ProvKindUsed    = "used"
)
```

bd validates that `kind` is a known constant and `ref_kind` is in the allowed set — **structural** validation only (ZFC: bd never interprets what a SHA *means*).

### 1c. Store methods — extend `/internal/storage/dolt/events.go`

Sit beside the existing `GetEvents` / `GetAllEventsSince`:

```go
RecordProvenanceEvent(ctx context.Context, ev types.ProvenanceEvent) (id string, err error)
GetProvenanceEvents(ctx context.Context, issueID, kindFilter string) ([]types.ProvenanceEvent, error)
GetProvenanceByRef(ctx context.Context, ref string) ([]types.ProvenanceEvent, error)
```

Add to the `Storage` interface so non-dolt backends must implement (or explicitly no-op) it.

### 1d. CLI — new `/cmd/bd/provenance.go` (cobra, mirrors `/cmd/bd/audit.go`)

```
bd provenance record --issue <id> --kind <kind> --source <src>
       [--actor <id>] [--ref <r>] [--ref-kind <k>] [--payload <json>] [--at <iso8601>] [--json]
bd provenance log <issue-id> [--kind <k>] [--json]
bd provenance by-ref <ref> [--json]
```

- alias `bd prov`.
- **No `update`/`delete` subcommand** — append-only is enforced by absence of a mutating verb, not by a runtime guard.
- `--at` defaults to now when the producer doesn't supply an event time.
- JSON output via the existing `FatalErrorRespectJSON` / `commandDidWrite.Store(true)` idioms.
- Named `provenance`, not `event`, to avoid colliding with the `events`/audit surface.

### 1e. Tests — `/cmd/bd/provenance_test.go` (`//go:build cgo`)

Follow `children_test.go`: `t.TempDir()` → `newTestStore(t, db)` → create issue → `record` several kinds → assert `log`/`by-ref` ordering and filtering. Plus an append-only test (no mutate verb exists) and a kind-validation test (unknown kind rejected).

---

## Contribution 2 — gascity (the reference producer)

Separate, smaller PR, landed *after* the bd PR. Proves the API by wiring the events gc actually owns — and **only** those.

### Scope (honest, narrow)

| Event | gc emits? | Where |
|-------|-----------|-------|
| `claim` | **yes** | at the `bd update <id> --claim` site (controller reconciliation / routing) |
| `suspend`/`resume`/`handoff` | **yes** | mirror existing `events.SessionSuspended`/`SessionWoke` + `cmd/gc/cmd_handoff.go` into bd |
| `cut` (base SHA) | **no** | agent does its own git setup; → git-hook surface |
| `land` (commit+CI) | **no** | landing is external to gc; → CI-webhook surface |

### Mechanism — one wrapper, reusing the existing bd-runner idiom

Add to `/cmd/gc/bd_env.go` beside `bdCommandRunnerForCity`:

```go
func bdAppendProvenance(cityPath, issueID, kind string, opts provOpts) error {
    runner := bdCommandRunnerForCity(cityPath)
    args := []string{"provenance", "record",
        "--issue", issueID, "--kind", kind, "--source", "gascity", "--json"}
    // append --actor (session name), --ref, --ref-kind, --at from opts
    _, err := runner(cityPath, "bd", args...)
    return err
}
```

gc already records these to `.gc/events.jsonl` via `/internal/events/recorder.go`; the producer is a thin mirror call at those same sites. **ZFC-clean:** pure IO/plumbing, no judgment — exactly the kind of orchestration code gascity's CONTRIBUTING.md sanctions. The `actor` passed is gc's stable session *name* (opaque to bd); gc keeps the `gc-NNNNNN` ↔ session-name mapping on its side.

### Conformance

gascity uses conformance tests as the executable spec (the "29 expectations"). Add a case asserting that a claim emits exactly one `provenance record` call with `kind=claim source=gascity` and the session name as `actor`.

---

## Contribution 3 — the external-user surface (no PR; it's why this is a primitive)

The proof that provenance is first-class and not a gascity feature: a plain-beads user with their own harness gets the same thing by wiring git/CI to the same `bd provenance record` CLI. These ship as **example hook templates in bd's docs**, not as code in either repo:

- **`post-checkout` / worktree-create** → `bd provenance record --kind cut --ref $(git rev-parse HEAD) --ref-kind git-sha --source git-hook` — captures the base SHA at the one moment it's knowable.
- **`commit-msg`** → stamps a `Bead-Session: <actor>` trailer into the commit (git is the system of record for commits; the trailer outlives bd), which a later `bd provenance record --kind commit` ingests.
- **`post-merge` / CI webhook** → `bd provenance record --kind land --ref <pr-url> --ref-kind pr --payload '{"commit":"…","ci":"pass"}' --source ci-webhook`.

Same API gc uses. That symmetry is the whole point.

---

## How to handle architecture docs & rationale

Each repo has a canonical home; the cross-repo essay does **not** get upstreamed wholesale.

### In beads — an ADR is the rationale home

Write **`/docs/adr/0003-provenance-event-log.md`** in bd's existing ADR format (Status / Date / Decision Drivers / Context / Decision / Considered Alternatives). This is where the *cross-repo layering rationale lives*, because beads owns the primitive and must justify the opaque-id design to its **widest** audience — every bd user, not just gascity. The "Considered Alternatives" section must pre-empt the two questions a bd maintainer will ask:

1. **"Why not extend the existing `events` table?"** → two-value mutation shape vs. structured binding; different reason-to-change (SRP); `wisp_events` precedent for purpose-specific event tables; keeps the hot audit-write path untouched.
2. **"Why opaque `actor`/`ref` instead of typed session/commit columns?"** → keeps bd a primitive usable without gascity; runtime defines identity, bd only binds it.

Then update **`/docs/ARCHITECTURE.md`** (add `provenance_events` to the table inventory + a short "Provenance" subsection on the append-only event model) and **`/docs/CLI_REFERENCE.md`** (the `bd provenance` commands + the hook templates from Contribution 3).

### In gascity — an RFC + a doc update

Write **`/engdocs/design/provenance-event-emission.md`** (RFC for *future/proposed* producer work, gascity's convention for forward-looking design) describing only what gc emits and why it deliberately does **not** emit `cut`/`land`. Update **`/engdocs/architecture/beads.md`** (or `event-bus.md`) to note the mirror once it lands. The RFC **references the bd ADR** for the layering rationale rather than restating it — single source of truth.

### In mem — the source-of-truth design stays internal

The companion doc (`mem-bead-provenance-event-log.md`) is the long-form cross-repo reasoning. It stays in mem and is **distilled down** into the two upstream artifacts above. Don't push the essay upstream; push the focused, repo-shaped versions and let each link to the bd ADR.

### Sequencing & PR hygiene

1. **beads PR first** — the primitive must exist before anything produces or consumes it. One issue per PR (bd rule): the table + types + store + CLI + tests + ADR are one coherent feature.
2. **gascity PR second** — the producer, depends on the bd CLI shipping. `make check` + conformance + ZFC-clean.
3. **mem becomes a consumer third** — separate, mem-internal: read `bd provenance log`/`by-ref`, project into `record_agents`/`trace_runs`/`links`, delete the reconstruction stages (`provenance.ts` date-heuristic, `build_merged_join.py`, `commitLinkage.ts` trailer-scan).

## Risks to flag before opening anything

- **bd maintainer may see it as app-specific.** Mitigation: inert-by-default (no writes unless a producer opts in), generic agent-provenance framing, `wisp_events` precedent, opaque ids. If the maintainer still balks, fall back to prototyping `provenance_events` mem-side against bd's dolt and upstream once the vocabulary has stabilized (this is the safer sequencing and worth pre-deciding).
- **`occurred_at` deviation** from the `events`-table single-timestamp idiom needs the explicit ADR justification above or it reads as inconsistency.
- **Backfill is forward-only** — ~6000 historical beads keep mem's reconstructions as permanent fallback; the event log never has to be retrofitted, but the oracle's N is forward-only until enough events accrue.
