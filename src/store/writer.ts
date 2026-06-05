import { errorClass, failureSignature, normalizePath } from '../parse/recurrence.js';
import { WorkRecordSchema, type WorkRecord } from '../schemas/workrecord.js';
import type { StoreDatabase } from './sqlite.js';

/**
 * Store writer (P1.5). `writeRecords` wires the ingest/parse pipeline output
 * (P1.2 spine, P1.3 trace refs, P1.4 outcomes, P1.6 parsed errors) into the
 * sidecar: the validated WorkRecord JSON is the stored truth, and every
 * queryable projection (promoted columns, child tables, failure signatures via
 * the existing parse/recurrence functions) is rebuilt from it on each upsert.
 *
 * `appendLesson` is the Decision-9 surface: INSERT only. There is deliberately
 * no update or delete — distilled lessons are extracted once and never
 * rewritten (continuous LLM rewriting degrades consolidated memory).
 */

const UPSERT_RECORD = `
INSERT INTO work_records (
  work_id, rig, title, status, priority, external_ref,
  created_at, started_at, closed_at, convoy_id,
  pr, pr_state, commit_sha, ci, trace_path, n_turns, record
) VALUES (
  @work_id, @rig, @title, @status, @priority, @external_ref,
  @created_at, @started_at, @closed_at, @convoy_id,
  @pr, @pr_state, @commit_sha, @ci, @trace_path, @n_turns, @record
)
ON CONFLICT(work_id) DO UPDATE SET
  rig = excluded.rig, title = excluded.title, status = excluded.status,
  priority = excluded.priority, external_ref = excluded.external_ref,
  created_at = excluded.created_at, started_at = excluded.started_at,
  closed_at = excluded.closed_at, convoy_id = excluded.convoy_id,
  pr = excluded.pr, pr_state = excluded.pr_state,
  commit_sha = excluded.commit_sha, ci = excluded.ci,
  trace_path = excluded.trace_path, n_turns = excluded.n_turns,
  record = excluded.record
`;

const CHILD_TABLES = ['record_agents', 'record_labels', 'record_links', 'trace_errors'] as const;

/** Promote the queryable columns out of a validated record. */
function toRow(record: WorkRecord): Record<string, string | number | null> {
  return {
    work_id: record.work_id,
    rig: record.rig,
    title: record.title,
    status: record.lifecycle.status,
    priority: record.priority ?? null,
    external_ref: record.external_ref ?? null,
    created_at: record.lifecycle.created,
    started_at: record.lifecycle.started ?? null,
    closed_at: record.lifecycle.closed ?? null,
    convoy_id: record.links.convoy_id ?? null,
    pr: record.outcome?.pr ?? null,
    pr_state: record.outcome?.pr_state ?? null,
    commit_sha: record.outcome?.commit_sha ?? null,
    ci: record.outcome?.ci ?? null,
    trace_path: record.trace?.jsonl_path ?? null,
    n_turns: record.trace?.n_turns ?? null,
    record: JSON.stringify(record),
  };
}

/**
 * Upsert WorkRecords in a single transaction. Idempotent: re-ingesting the
 * same records leaves the store byte-identical; child rows (agents, labels,
 * links, trace errors + their FTS index) are deleted and rebuilt per record,
 * never accumulated. Lessons are untouched — they live outside the re-ingest
 * cycle by design (see schema.ts).
 */
export function writeRecords(db: StoreDatabase, records: WorkRecord[]): void {
  const upsert = db.prepare(UPSERT_RECORD);
  const clearChild = CHILD_TABLES.map(table =>
    db.prepare(`DELETE FROM ${table} WHERE work_id = ?`)
  );
  const insertAgent = db.prepare(
    'INSERT INTO record_agents (work_id, agent_id, role, account, trace_ref) VALUES (?, ?, ?, ?, ?)'
  );
  const insertLabel = db.prepare('INSERT INTO record_labels (work_id, label) VALUES (?, ?)');
  const insertLink = db.prepare(
    'INSERT INTO record_links (work_id, kind, target_id) VALUES (?, ?, ?)'
  );
  const insertError = db.prepare(
    'INSERT INTO trace_errors (work_id, signature, tool, severity, file, line, col, error_class, message) ' +
      'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
  );

  db.transaction(() => {
    for (const candidate of records) {
      // Validate at the boundary: the store only ever holds schema-conformant
      // JSON, so readers can parse it back without defensive handling.
      const record = WorkRecordSchema.parse(candidate);

      upsert.run(toRow(record));
      for (const clear of clearChild) clear.run(record.work_id);

      for (const agent of record.agents) {
        insertAgent.run(
          record.work_id,
          agent.agent_id,
          agent.role ?? null,
          agent.account ?? null,
          agent.trace_ref ?? null
        );
      }
      for (const label of record.labels) insertLabel.run(record.work_id, label);
      for (const dep of record.links.deps) insertLink.run(record.work_id, 'dep', dep);
      for (const target of record.links.supersedes) {
        insertLink.run(record.work_id, 'supersedes', target);
      }
      for (const error of record.trace?.errors ?? []) {
        insertError.run(
          record.work_id,
          failureSignature(error),
          error.tool,
          error.severity,
          normalizePath(error.file),
          error.line,
          error.column ?? null,
          errorClass(error),
          error.message
        );
      }
    }
  })();
}

/** A distilled lesson to append (Decision 9). `commit_sha` is the citation
 * snapshot taken at extraction time — pass the outcome's sha as it existed
 * when the lesson was distilled, not a value to be resolved later. */
export interface LessonInput {
  work_id: string;
  extracted_at: string;
  commit_sha?: string;
  payload: Record<string, unknown>;
}

/** Append one lesson and return its id. INSERT only — see module docs. */
export function appendLesson(db: StoreDatabase, lesson: LessonInput): number {
  const result = db
    .prepare('INSERT INTO lessons (work_id, extracted_at, commit_sha, payload) VALUES (?, ?, ?, ?)')
    .run(
      lesson.work_id,
      lesson.extracted_at,
      lesson.commit_sha ?? null,
      JSON.stringify(lesson.payload)
    );
  // lastInsertRowid is number | bigint; without .safeIntegers() it is a
  // number for every id this table can realistically reach.
  return Number(result.lastInsertRowid);
}
