/**
 * Sidecar schema v1 (P1.5). Substrate: SQLite + FTS5 (decided 2026-06-05).
 *
 * Layout: `work_records.record` holds the full validated WorkRecord JSON — the
 * single source of truth for nested fields. Every other column and child table
 * is a *projection* of that JSON, promoted purely so queries can index it
 * (rig/temporal filters for the Decision-6/7 eval contract, failure signatures
 * for Decision-8 retrieval). The writer rebuilds all projections on every
 * upsert, so they can never drift from the JSON.
 *
 * Eval-contract support (Decisions 6–10): this schema *provisions* the
 * exclusion keys — `started_at`/`closed_at` (temporal leave-one-out),
 * `convoy_id`, `pr` AND `external_ref` (a branch-sibling without a PR yet must
 * still be excludable), and `record_links` supersedes adjacency. The functional
 * LOO queries (NULL-safe pr-or-branch sibling match, recursive-CTE supersedes
 * closure) belong to the Phase-2 retrieve/bench layers; today's ingest does not
 * yet populate convoy/supersedes, so those columns carry data only when
 * upstream provides it.
 */
export const SCHEMA_VERSION = 9;

export const SCHEMA_DDL = `
CREATE TABLE work_records (
  work_id      TEXT PRIMARY KEY,
  rig          TEXT NOT NULL,
  title        TEXT NOT NULL,
  status       TEXT NOT NULL,
  priority     INTEGER,
  external_ref TEXT,
  created_at   TEXT NOT NULL,
  started_at   TEXT,
  closed_at    TEXT,
  convoy_id    TEXT,
  pr           TEXT,
  pr_state     TEXT,
  commit_sha   TEXT,
  ci           TEXT,
  trace_path   TEXT,
  n_turns      INTEGER,
  -- Canonical repository identity (mem-bme; see workrecord.ts repo/repo_source,
  -- projected from record.repo by ingest/repo-resolve). repo is the
  -- owner/name grouping + retrieval key; repo_source records HOW it resolved
  -- (outcome | rig-map | unmapped), so the build coverage line can report
  -- the residual null-rate. NULL repo means repo_source = unmapped (or the
  -- resolve stage has not run). Distinct from the bare work_dir basename that
  -- lives only inside record.provenance.repo.
  repo         TEXT,
  repo_source  TEXT,
  -- Git-provenance projection (locally-derived env baseline; see workrecord.ts
  -- ProvenanceSchema). base_commit is the git-checkout anchor for a future
  -- real-exec replay; commit_state records whether it is a commit-by-date
  -- approximation ('commit-by-date') or absent ('unresolved'). Distinct from
  -- commit_sha above, which is the verifiable GitHub outcome SHA.
  base_commit  TEXT,
  commit_state TEXT,
  -- Landed-outcome projection (ingest/landed; see workrecord.ts LandedSchema).
  -- The forward mirror of the provenance baseline: landed_state is the
  -- work->landed-commit verdict for the direct-to-main corpus
  -- (landed | reverted | abandoned | empty-window | ambiguous-window |
  -- unresolved), landed_commit the integration-branch tip at session close, and
  -- n_commits the count in base_commit..landed_commit. NULL when --with-provenance
  -- did not run (landed is its forward stage). landed_state is indexed so the
  -- eval/analysis layer can filter the outcome-grounded subset directly.
  landed_state  TEXT,
  landed_commit TEXT,
  n_commits     INTEGER,
  -- Task typing (mem-75t.11). task_type_source says HOW the type was
  -- assigned: 'formula' (molecule/step beads, mechanical), 'structural'
  -- (machine-generated title grammars, mechanical), 'model' (classified by a
  -- model via the --task-types artifact, which records model id + timestamp).
  -- molecule_id groups a formula run's step beads with their root.
  task_type        TEXT,
  task_type_source TEXT CHECK (task_type_source IN ('formula', 'structural', 'model')),
  molecule_id      TEXT,
  -- Provenance-link projection (mem-wanz.3, the links table below). link_tier is
  -- the record's best soundness tier (T1|T2|T3) across its links; link_source the
  -- '+'-joined sources that established them (the record_agents convention). Both
  -- NULL until the link stage runs; link_tier is indexed so the sound-tier-only
  -- headline can filter the eligible population directly.
  link_tier        TEXT,
  link_source      TEXT,
  record       TEXT NOT NULL
);
CREATE INDEX idx_records_task_type ON work_records(task_type);
CREATE INDEX idx_records_molecule  ON work_records(molecule_id);
CREATE INDEX idx_records_rig          ON work_records(rig);
CREATE INDEX idx_records_status       ON work_records(status);
CREATE INDEX idx_records_started      ON work_records(started_at);
CREATE INDEX idx_records_closed       ON work_records(closed_at);
CREATE INDEX idx_records_pr           ON work_records(pr);
CREATE INDEX idx_records_external_ref ON work_records(external_ref);
CREATE INDEX idx_records_repo         ON work_records(repo);
CREATE INDEX idx_records_repo_source  ON work_records(repo_source);
CREATE INDEX idx_records_landed_state ON work_records(landed_state);
CREATE INDEX idx_records_link_tier    ON work_records(link_tier);

-- Genuinely multi-row per work_id since v4 (mem-75t.9 phase 2 / mem-75t.4):
-- one row per session iteration, ordered by sequence, tagged with the join
-- sources that established the link (events|dolt-history|content-scan|
-- assignee, '+'-joined when several agree). suspect=1 marks an assignee-only
-- link whose transcript content contradicts it (wrong-conversation
-- resolution) — kept for audit, excluded from analysis populations.
CREATE TABLE record_agents (
  work_id    TEXT NOT NULL REFERENCES work_records(work_id),
  agent_id   TEXT NOT NULL,
  role       TEXT,
  account    TEXT,
  trace_ref  TEXT,
  sequence   INTEGER,
  started_at TEXT,
  ended_at   TEXT,
  sources    TEXT,
  suspect    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_agents_work  ON record_agents(work_id);
CREATE INDEX idx_agents_agent ON record_agents(agent_id);

CREATE TABLE record_labels (
  work_id TEXT NOT NULL REFERENCES work_records(work_id),
  label   TEXT NOT NULL
);
CREATE INDEX idx_labels_work ON record_labels(work_id);

CREATE TABLE record_links (
  work_id   TEXT NOT NULL REFERENCES work_records(work_id),
  kind      TEXT NOT NULL CHECK (kind IN ('dep', 'supersedes')),
  target_id TEXT NOT NULL
);
CREATE INDEX idx_links_work   ON record_links(work_id);
CREATE INDEX idx_links_target ON record_links(target_id);

-- PROV-O provenance links (mem-wanz.3, PRD §4): the TASK->AGENT->OUTCOME audit
-- graph. Deliberately SEPARATE from record_links above (which is intra-corpus
-- dep|supersedes adjacency only, no tier/confidence) by SRP — this table carries
-- the tiered, confidence-scored provenance edges the eval measures, and
-- wasInformedBy is the memory edge the headline scores. tier (T1|T2|T3) is the
-- soundness tier (sound-tier-only headline); provenance is the '+'-joined sources
-- and suspect=1 a contradicted edge, reusing the record_agents convention. The
-- unique key collapses one logical edge re-derived from several sources into a
-- single row whose provenance accretes those sources.
CREATE TABLE links (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id      TEXT NOT NULL REFERENCES work_records(work_id),
  session_uuid TEXT,
  relation     TEXT NOT NULL CHECK (relation IN ('wasGeneratedBy', 'wasAssociatedWith', 'used', 'wasInformedBy')),
  entity_ref   TEXT NOT NULL,
  entity_kind  TEXT NOT NULL,
  key_type     TEXT NOT NULL,
  tier         TEXT NOT NULL CHECK (tier IN ('T1', 'T2', 'T3')),
  confidence   REAL,
  provenance   TEXT,
  suspect      INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  UNIQUE(work_id, entity_ref, relation, key_type)
);
CREATE INDEX idx_provlinks_work     ON links(work_id);
CREATE INDEX idx_provlinks_session  ON links(session_uuid);
CREATE INDEX idx_provlinks_relation ON links(relation);
CREATE INDEX idx_provlinks_tier     ON links(tier);
CREATE INDEX idx_provlinks_entity   ON links(entity_ref);

-- AUTOINCREMENT is load-bearing here: trace_errors.id is the FTS content
-- rowid, so ids must never be reused. If the FTS index ever held a stale
-- entry, a reused rowid would re-associate the old message with a new row
-- (a wrong search result); with monotonic ids a stale entry can only ever
-- join to nothing.
CREATE TABLE trace_errors (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id     TEXT NOT NULL REFERENCES work_records(work_id),
  signature   TEXT NOT NULL,
  tool        TEXT NOT NULL,
  severity    TEXT NOT NULL,
  file        TEXT NOT NULL,
  line        INTEGER NOT NULL,
  col         INTEGER,
  error_class TEXT NOT NULL,
  message     TEXT NOT NULL
);
CREATE INDEX idx_errors_work      ON trace_errors(work_id);
CREATE INDEX idx_errors_signature ON trace_errors(signature);

CREATE VIRTUAL TABLE trace_errors_fts USING fts5(
  message,
  content = 'trace_errors',
  content_rowid = 'id'
);
CREATE TRIGGER trace_errors_ai AFTER INSERT ON trace_errors BEGIN
  INSERT INTO trace_errors_fts(rowid, message) VALUES (new.id, new.message);
END;
CREATE TRIGGER trace_errors_ad AFTER DELETE ON trace_errors BEGIN
  INSERT INTO trace_errors_fts(trace_errors_fts, rowid, message)
  VALUES ('delete', old.id, old.message);
END;
CREATE TRIGGER trace_errors_au AFTER UPDATE ON trace_errors BEGIN
  INSERT INTO trace_errors_fts(trace_errors_fts, rowid, message)
  VALUES ('delete', old.id, old.message);
  INSERT INTO trace_errors_fts(rowid, message) VALUES (new.id, new.message);
END;

-- Run-level metadata projection (one row per session transcript), rebuilt on
-- upsert from record.trace.run like the other child tables. Keyed by the
-- natural (work_id, agent_id, session_uuid): a record may carry several agents,
-- but the run row is attributed to the agent whose trace_ref is this transcript.
-- tool_calls_by_type is the parsed {name: count} map stored as JSON — a small
-- per-run shape, not a queryable index, so it stays inline rather than fanning
-- out to its own table.
CREATE TABLE trace_runs (
  work_id               TEXT NOT NULL REFERENCES work_records(work_id),
  agent_id              TEXT,
  session_uuid          TEXT NOT NULL,
  model                 TEXT,
  harness_version       TEXT,
  input_tokens          INTEGER NOT NULL,
  output_tokens         INTEGER NOT NULL,
  cache_creation_tokens INTEGER NOT NULL,
  cache_read_tokens     INTEGER NOT NULL,
  n_tool_calls          INTEGER NOT NULL,
  tool_calls_by_type    TEXT NOT NULL,
  n_turns               INTEGER NOT NULL,
  started_at            TEXT,
  ended_at              TEXT,
  outcome               TEXT,
  -- Enforce the natural key in SQL, not just the writer's delete-then-insert:
  -- a future write path that skips the delete turns a duplicate into a loud
  -- failure here rather than a silent double row that runsFor would return.
  UNIQUE(work_id, session_uuid)
);
CREATE INDEX idx_runs_work    ON trace_runs(work_id);
CREATE INDEX idx_runs_session ON trace_runs(session_uuid);
CREATE INDEX idx_runs_model   ON trace_runs(model);

-- Append-only distilled lessons (Decision 9). Deliberately NO foreign key to
-- work_records: a lesson must survive its record's delete/re-ingest, and may
-- even be extracted before the record lands. The citation (work_id +
-- commit_sha) is snapshotted at append time, never joined live — a live join
-- would let a re-ingested outcome silently rewrite an existing citation,
-- violating the append-only contract.
CREATE TABLE lessons (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id      TEXT NOT NULL,
  extracted_at TEXT NOT NULL,
  commit_sha   TEXT,
  payload      TEXT NOT NULL
);
CREATE INDEX idx_lessons_work ON lessons(work_id);

-- Append-only provenance event log (mem-side prototype of the proposed beads
-- provenance_events primitive; see docs/mem-bead-provenance-upstream-
-- contribution.md and schemas/provenance-event.ts). One immutable row per
-- causal fact bound to a work_id: cut|claim|...|land|used. Deliberately NOT a
-- projection of work_records.record (unlike record_agents/links above) and,
-- like lessons, carries NO foreign key to work_records: a producer (a future
-- git hook / orchestrator) may record a cut before the record is ingested,
-- and the events must survive a record delete/re-ingest. id is the dedup key
-- (deterministic for backfilled events → idempotent re-ingest; a ulid for real
-- producer events), so the recorder is INSERT OR IGNORE and append-only: there
-- is no update/delete path, corrections are new rows. occurred_at (event-time)
-- is separate from created_at (ingest-time) because hooks may record after the
-- fact; it is the ordering key. actor/ref are opaque; only kind/ref_kind are
-- structurally validated (ZFC).
CREATE TABLE provenance_events (
  id          TEXT PRIMARY KEY,
  work_id     TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN
                ('cut','claim','suspend','resume','handoff','commit','land','used')),
  actor       TEXT,
  ref         TEXT,
  ref_kind    TEXT CHECK (ref_kind IS NULL OR ref_kind IN
                ('git-sha','pr','work-id','transcript','branch')),
  payload     TEXT,
  source      TEXT NOT NULL,
  occurred_at TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX idx_prov_events_work ON provenance_events(work_id, occurred_at);
CREATE INDEX idx_prov_events_ref  ON provenance_events(ref);
CREATE INDEX idx_prov_events_kind ON provenance_events(kind);
`;
