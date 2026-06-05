import { WorkRecordSchema, type WorkRecord } from '../schemas/workrecord.js';
import type { StoreDatabase } from './sqlite.js';
import type { LessonInput } from './writer.js';

/**
 * Store readers (P1.5). Every query has an explicit, deterministic ORDER BY —
 * the Decision-10 precision guard measures retrieved sets, so result identity
 * and order must be reproducible run-to-run.
 *
 * These are the audit-query and retrieval *primitives*. Composing them into a
 * ranked retrieval (structured filters first, FTS message match as the weak
 * tiebreaker — Decision 8) is Phase-2 retrieve/ policy and deliberately does
 * not live in the store.
 */

/** Re-validate stored JSON on the way out: the row was schema-conformant when
 * written, so a parse failure here means store corruption — fail loudly. */
function parseStored(json: string): WorkRecord {
  return WorkRecordSchema.parse(JSON.parse(json));
}

/** Read one WorkRecord by bead id, or null when absent. */
export function getRecord(db: StoreDatabase, workId: string): WorkRecord | null {
  const row = db.prepare('SELECT record FROM work_records WHERE work_id = ?').get(workId) as
    | { record: string }
    | undefined;
  return row ? parseStored(row.record) : null;
}

/** Equality/temporal filters over the promoted columns. All optional; absent
 * filters match everything. `closedBefore` is strict (`closed_at < t`) — the
 * Decision-6 temporal leave-one-out boundary ("closed strictly before
 * B.started"); never-closed records never match it. */
export interface RecordFilter {
  rig?: string;
  status?: string;
  agent?: string;
  ci?: 'pass' | 'fail';
  pr_state?: 'merged' | 'closed';
  closedBefore?: string;
}

/** Query WorkRecords by filter, ordered by work_id. */
export function queryRecords(db: StoreDatabase, filter: RecordFilter = {}): WorkRecord[] {
  const where: string[] = [];
  const params: string[] = [];

  const equals: [column: string, value: string | undefined][] = [
    ['rig', filter.rig],
    ['status', filter.status],
    ['ci', filter.ci],
    ['pr_state', filter.pr_state],
  ];
  for (const [column, value] of equals) {
    if (value !== undefined) {
      where.push(`${column} = ?`);
      params.push(value);
    }
  }
  if (filter.closedBefore !== undefined) {
    where.push('closed_at IS NOT NULL AND closed_at < ?');
    params.push(filter.closedBefore);
  }
  if (filter.agent !== undefined) {
    where.push(
      'EXISTS (SELECT 1 FROM record_agents a WHERE a.work_id = work_records.work_id AND a.agent_id = ?)'
    );
    params.push(filter.agent);
  }

  const sql =
    'SELECT record FROM work_records' +
    (where.length > 0 ? ` WHERE ${where.join(' AND ')}` : '') +
    ' ORDER BY work_id';
  const rows = db.prepare(sql).all(...params) as { record: string }[];
  return rows.map(row => parseStored(row.record));
}

/** A stored lesson (Decision 9): the appended input plus its insertion id. */
export interface StoredLesson extends LessonInput {
  id: number;
}

/** All lessons for a bead, in append order. */
export function lessonsFor(db: StoreDatabase, workId: string): StoredLesson[] {
  const rows = db
    .prepare(
      'SELECT id, work_id, extracted_at, commit_sha, payload FROM lessons WHERE work_id = ? ORDER BY id'
    )
    .all(workId) as {
    id: number;
    work_id: string;
    extracted_at: string;
    commit_sha: string | null;
    payload: string;
  }[];

  return rows.map(row => ({
    id: row.id,
    work_id: row.work_id,
    extracted_at: row.extracted_at,
    ...(row.commit_sha !== null && { commit_sha: row.commit_sha }),
    payload: JSON.parse(row.payload) as Record<string, unknown>,
  }));
}

/** Distinct bead ids whose traces exhibit a failure signature (the Decision-8
 * retrieval key, from `failureSignature` in parse/recurrence), sorted. */
export function workIdsBySignature(db: StoreDatabase, signature: string): string[] {
  const rows = db
    .prepare('SELECT DISTINCT work_id FROM trace_errors WHERE signature = ? ORDER BY work_id')
    .all(signature) as { work_id: string }[];
  return rows.map(row => row.work_id);
}

/** One FTS hit on a trace error's message. */
export interface ErrorSearchHit {
  work_id: string;
  signature: string;
  message: string;
}

/**
 * Full-text search over trace-error messages — the Decision-8 "weak
 * tiebreaker". `query` is raw FTS5 MATCH syntax and the trust boundary is the
 * caller's: a malformed query throws, and operators like `*` widen the match
 * — never pass untrusted input through unescaped (the Phase-2 retrieve layer
 * owns query construction). Best match first (bm25), with stable id tiebreak.
 */
export function searchErrorMessages(
  db: StoreDatabase,
  query: string,
  limit = 20
): ErrorSearchHit[] {
  return db
    .prepare(
      `SELECT te.work_id, te.signature, te.message
       FROM trace_errors_fts f JOIN trace_errors te ON te.id = f.rowid
       WHERE trace_errors_fts MATCH ?
       ORDER BY f.rank, te.id
       LIMIT ?`
    )
    .all(query, limit) as ErrorSearchHit[];
}
