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
export const SCHEMA_VERSION = 1;

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
  record       TEXT NOT NULL
);
CREATE INDEX idx_records_rig          ON work_records(rig);
CREATE INDEX idx_records_status       ON work_records(status);
CREATE INDEX idx_records_started      ON work_records(started_at);
CREATE INDEX idx_records_closed       ON work_records(closed_at);
CREATE INDEX idx_records_pr           ON work_records(pr);
CREATE INDEX idx_records_external_ref ON work_records(external_ref);

CREATE TABLE record_agents (
  work_id   TEXT NOT NULL REFERENCES work_records(work_id),
  agent_id  TEXT NOT NULL,
  role      TEXT,
  account   TEXT,
  trace_ref TEXT
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
`;
