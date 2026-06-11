import { errorClass, failureSignature, normalizePath } from '../parse/recurrence.js';
import { LessonPayloadSchema } from '../schemas/lesson.js';
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
  pr, pr_state, commit_sha, ci, trace_path, n_turns,
  repo, base_commit, commit_state, record
) VALUES (
  @work_id, @rig, @title, @status, @priority, @external_ref,
  @created_at, @started_at, @closed_at, @convoy_id,
  @pr, @pr_state, @commit_sha, @ci, @trace_path, @n_turns,
  @repo, @base_commit, @commit_state, @record
)
ON CONFLICT(work_id) DO UPDATE SET
  rig = excluded.rig, title = excluded.title, status = excluded.status,
  priority = excluded.priority, external_ref = excluded.external_ref,
  created_at = excluded.created_at, started_at = excluded.started_at,
  closed_at = excluded.closed_at, convoy_id = excluded.convoy_id,
  pr = excluded.pr, pr_state = excluded.pr_state,
  commit_sha = excluded.commit_sha, ci = excluded.ci,
  trace_path = excluded.trace_path, n_turns = excluded.n_turns,
  repo = excluded.repo, base_commit = excluded.base_commit,
  commit_state = excluded.commit_state, record = excluded.record
`;

const CHILD_TABLES = [
  'record_agents',
  'record_labels',
  'record_links',
  'trace_errors',
  'trace_runs',
] as const;

/** Clear every child row for the given work_ids — one DELETE per table for the
 * whole batch, replacing a DELETE per record per table. The ids are bound as a
 * single JSON array (`json_each`), so there is no bound-variable limit to
 * chunk around. */
function clearChildRows(db: StoreDatabase, workIds: string[]): void {
  const ids = JSON.stringify(workIds);
  for (const table of CHILD_TABLES) {
    db.prepare(`DELETE FROM ${table} WHERE work_id IN (SELECT value FROM json_each(?))`).run(ids);
  }
}

/** The agent this transcript is attributed to: the one whose `trace_ref` is the
 * parsed transcript, else the record's first agent, else null (a trace can be
 * resolved without a matching agent row). */
function runAgentId(record: WorkRecord): string | null {
  const path = record.trace?.jsonl_path;
  const owner = record.agents.find(a => a.trace_ref === path);
  return (owner ?? record.agents[0])?.agent_id ?? null;
}

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
    repo: record.provenance?.repo ?? null,
    base_commit: record.provenance?.base_commit ?? null,
    commit_state: record.provenance?.history_state ?? null,
    record: JSON.stringify(record),
  };
}

/**
 * Upsert WorkRecords in a single transaction. Idempotent: re-ingesting the
 * same records leaves the store byte-identical; child rows (agents, labels,
 * links, trace errors + their FTS index, run metadata) are deleted (batched
 * per table) and rebuilt, never accumulated. Lessons are untouched — they live outside the
 * re-ingest cycle by design (see schema.ts).
 */
export function writeRecords(db: StoreDatabase, records: WorkRecord[]): void {
  const upsert = db.prepare(UPSERT_RECORD);
  const insertAgent = db.prepare(
    'INSERT INTO record_agents (work_id, agent_id, role, account, trace_ref, ' +
      'sequence, started_at, ended_at, sources, suspect) ' +
      'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
  );
  const insertLabel = db.prepare('INSERT INTO record_labels (work_id, label) VALUES (?, ?)');
  const insertLink = db.prepare(
    'INSERT INTO record_links (work_id, kind, target_id) VALUES (?, ?, ?)'
  );
  const insertError = db.prepare(
    'INSERT INTO trace_errors (work_id, signature, tool, severity, file, line, col, error_class, message) ' +
      'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
  );
  const insertRun = db.prepare(
    'INSERT INTO trace_runs (work_id, agent_id, session_uuid, model, harness_version, ' +
      'input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, ' +
      'n_tool_calls, tool_calls_by_type, n_turns, started_at, ended_at, outcome) ' +
      'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
  );

  db.transaction(() => {
    // Old child rows are cleared for the whole batch up front, then rebuilt
    // per record below. A validation failure mid-loop rolls the clear back
    // with everything else.
    clearChildRows(
      db,
      records.map(record => record.work_id)
    );
    for (const candidate of records) {
      // Validate at the boundary: the store only ever holds schema-conformant
      // JSON, so readers can parse it back without defensive handling.
      const record = WorkRecordSchema.parse(candidate);

      upsert.run(toRow(record));

      for (const agent of record.agents) {
        insertAgent.run(
          record.work_id,
          agent.agent_id,
          agent.role ?? null,
          agent.account ?? null,
          agent.trace_ref ?? null,
          agent.sequence ?? null,
          agent.started_at ?? null,
          agent.ended_at ?? null,
          agent.sources && agent.sources.length > 0 ? agent.sources.join('+') : null,
          agent.suspect === true ? 1 : 0
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
      const run = record.trace?.run;
      if (run) {
        insertRun.run(
          record.work_id,
          runAgentId(record),
          run.session_uuid,
          run.model ?? null,
          run.harness_version ?? null,
          run.input_tokens,
          run.output_tokens,
          run.cache_creation_tokens,
          run.cache_read_tokens,
          run.n_tool_calls,
          JSON.stringify(run.tool_calls_by_type),
          run.n_turns,
          run.started_at ?? null,
          run.ended_at ?? null,
          run.outcome ?? null
        );
      }
    }
  })();
}

/** A distilled lesson to append (Decision 9). `commit_sha` is the citation
 * snapshot taken at extraction time — pass the outcome's sha as it existed
 * when the lesson was distilled, not a value to be resolved later. The
 * payload is freeform, but its well-known progressive-disclosure fields
 * (subtitle/facts/narrative/concepts — see schemas/lesson) must be
 * well-formed when present. */
export interface LessonInput {
  work_id: string;
  extracted_at: string;
  commit_sha?: string;
  payload: Record<string, unknown>;
}

/** The raw INSERT, shared by the validated append path and the migration
 * import path (which must carry pre-convention payloads byte-identically). */
function insertLesson(db: StoreDatabase, lesson: LessonInput): number {
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

/** Append one newly distilled lesson and return its id. INSERT only — see
 * module docs. The payload's disclosure convention is validated, but the
 * *original* object is stored: zod re-serialization could reorder keys, and
 * {@link importLessons} identity is byte equality of the stored JSON. */
export function appendLesson(db: StoreDatabase, lesson: LessonInput): number {
  LessonPayloadSchema.parse(lesson.payload);
  return insertLesson(db, lesson);
}

/** Outcome of {@link importLessons}: how many rows were appended vs already
 * present (matched on full content identity). */
export interface ImportLessonsResult {
  appended: number;
  skipped: number;
}

/**
 * Append exported lessons into this store — the import side of the schema-bump
 * migration path (export from the old store, rebuild, import here). Still
 * INSERT-only: a lesson whose full content (work_id, extracted_at, commit_sha,
 * payload) already exists is skipped, which makes the import idempotent without
 * ever updating a row. Identity is byte equality of the stored fields — a
 * mechanical comparison, not a semantic merge. Deliberately NOT gated on the
 * disclosure convention ({@link appendLesson} is): historical lessons predate
 * it, and a migration that hard-fails on one legacy payload would brick the
 * one store table a rebuild cannot regenerate.
 */
export function importLessons(db: StoreDatabase, lessons: LessonInput[]): ImportLessonsResult {
  const exists = db.prepare(
    'SELECT 1 FROM lessons WHERE work_id = ? AND extracted_at = ? AND commit_sha IS ? AND payload = ? LIMIT 1'
  );
  let appended = 0;
  let skipped = 0;
  db.transaction(() => {
    for (const lesson of lessons) {
      const payload = JSON.stringify(lesson.payload);
      const present = exists.get(
        lesson.work_id,
        lesson.extracted_at,
        lesson.commit_sha ?? null,
        payload
      );
      if (present !== undefined) {
        skipped += 1;
        continue;
      }
      insertLesson(db, lesson);
      appended += 1;
    }
  })();
  return { appended, skipped };
}
