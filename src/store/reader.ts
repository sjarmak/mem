import type { TraceRun } from '../schemas/trace.js';
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

/** Every lesson in the store, in append (id) order — the export side of the
 * schema-bump migration path. Lessons are the one table a store rebuild cannot
 * regenerate (append-only, extracted once per Decision 9), so they must be
 * carriable across rebuilds. */
export function allLessons(db: StoreDatabase): StoredLesson[] {
  const rows = db
    .prepare('SELECT id, work_id, extracted_at, commit_sha, payload FROM lessons ORDER BY id')
    .all() as {
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

/** Every work id reachable from `workId` over `supersedes` links, traversed as
 * undirected edges (ancestors AND descendants — both are "the same work" for the
 * Decision-6 leave-one-out exclusion), sorted; `workId` itself is excluded
 * (self-exclusion is the caller's own rule). Multi-hop via a recursive CTE over
 * `record_links` — a read over the existing spine, no new substrate. */
export function supersedesClosure(db: StoreDatabase, workId: string): string[] {
  const rows = db
    .prepare(
      `WITH RECURSIVE closure(id) AS (
         SELECT ?
         UNION
         SELECT CASE WHEN l.work_id = c.id THEN l.target_id ELSE l.work_id END
         FROM record_links l JOIN closure c ON c.id IN (l.work_id, l.target_id)
         WHERE l.kind = 'supersedes'
       )
       SELECT id FROM closure WHERE id <> ? ORDER BY id`
    )
    .all(workId, workId) as { id: string }[];
  return rows.map(row => row.id);
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

/** A projected run row: the parsed {@link TraceRun} plus the natural-key prefix
 * (`work_id`, `agent_id`) it is attributed to in the store. */
export interface StoredRun extends TraceRun {
  work_id: string;
  agent_id: string | null;
}

interface RunRow {
  work_id: string;
  agent_id: string | null;
  session_uuid: string;
  model: string | null;
  harness_version: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  n_tool_calls: number;
  tool_calls_by_type: string;
  n_turns: number;
  started_at: string | null;
  ended_at: string | null;
  outcome: string | null;
}

/** Run-level metadata rows for a bead, ordered by session uuid. Each row is the
 * stored projection of `record.trace.run`; `tool_calls_by_type` is parsed back
 * from its JSON column, and absent optionals stay absent (mirroring the schema's
 * "parsed, found nothing" contract) rather than surfacing as null. */
export function runsFor(db: StoreDatabase, workId: string): StoredRun[] {
  const rows = db
    .prepare('SELECT * FROM trace_runs WHERE work_id = ? ORDER BY session_uuid')
    .all(workId) as RunRow[];

  return rows.map(row => ({
    work_id: row.work_id,
    agent_id: row.agent_id,
    session_uuid: row.session_uuid,
    ...(row.model !== null && { model: row.model }),
    ...(row.harness_version !== null && { harness_version: row.harness_version }),
    input_tokens: row.input_tokens,
    output_tokens: row.output_tokens,
    cache_creation_tokens: row.cache_creation_tokens,
    cache_read_tokens: row.cache_read_tokens,
    n_tool_calls: row.n_tool_calls,
    tool_calls_by_type: JSON.parse(row.tool_calls_by_type) as Record<string, number>,
    n_turns: row.n_turns,
    ...(row.started_at !== null && { started_at: row.started_at }),
    ...(row.ended_at !== null && { ended_at: row.ended_at }),
    ...(row.outcome !== null && { outcome: row.outcome }),
  }));
}

/**
 * Store-wide coverage of the trace substrate (mem-75t). Each field is a count
 * the ingest is meant to lift off zero: the epic's headline diagnostic was
 * `trace_path`/`trace_errors`/`base_commit`/`commit_sha` all empty across the
 * spine-only store. Reading these back is how `mem coverage` and the nightly
 * `ingest-traces` delta know whether a run actually populated the projection.
 */
export interface CoverageReport {
  /** Total work_records — the spine the other counts are coverage *of*. */
  records: number;
  /** Records whose transcript resolved to a JSONL path (P1.3 trace-resolve). */
  with_trace: number;
  /** Deterministic build/test/lint failure signatures parsed (P1.6). */
  trace_errors: number;
  /** Run-metadata rows: tokens/model/harness/tool-calls/turns (P1.2). */
  trace_runs: number;
  /** Records with a git base_commit anchor (P1.3 provenance). */
  with_base_commit: number;
  /** Records with a verifiable GitHub outcome SHA (spine `outcome.commit_sha`). */
  with_commit_sha: number;
  /** Records with ≥2 non-suspect session iterations (mem-75t.4 merged join). */
  multi_session: number;
  /** Records with a task_type (formula/structural/model — mem-75t.11). */
  with_task_type: number;
}

/** Count the populated rows behind each coverage axis — one small aggregate
 * query per axis (the axes hit different tables/predicates, so they don't
 * collapse into one scan). Read-only; safe to call on a store mid-build or
 * empty. */
export function coverageReport(db: StoreDatabase): CoverageReport {
  const count = (sql: string): number => (db.prepare(sql).get() as { n: number }).n;
  return {
    records: count('SELECT COUNT(*) AS n FROM work_records'),
    with_trace: count('SELECT COUNT(*) AS n FROM work_records WHERE trace_path IS NOT NULL'),
    trace_errors: count('SELECT COUNT(*) AS n FROM trace_errors'),
    trace_runs: count('SELECT COUNT(*) AS n FROM trace_runs'),
    with_base_commit: count('SELECT COUNT(*) AS n FROM work_records WHERE base_commit IS NOT NULL'),
    with_commit_sha: count('SELECT COUNT(*) AS n FROM work_records WHERE commit_sha IS NOT NULL'),
    multi_session: count(
      'SELECT COUNT(*) AS n FROM (SELECT work_id FROM record_agents WHERE suspect = 0 ' +
        'GROUP BY work_id HAVING COUNT(*) >= 2)'
    ),
    with_task_type: count('SELECT COUNT(*) AS n FROM work_records WHERE task_type IS NOT NULL'),
  };
}
