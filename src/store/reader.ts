import type { TraceRun } from '../schemas/trace.js';
import { WorkRecordSchema, type WorkRecord } from '../schemas/workrecord.js';
import type { StoreDatabase } from './sqlite.js';
import { toIsoUtc } from './timestamp.js';
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
  /** The work→landed-commit verdict (ingest/landed); filters the
   * outcome-grounded subset of the direct-to-main corpus. */
  landed_state?:
    | 'landed'
    | 'reverted'
    | 'abandoned'
    | 'empty-window'
    | 'ambiguous-window'
    | 'unresolved';
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
    ['landed_state', filter.landed_state],
  ];
  for (const [column, value] of equals) {
    if (value !== undefined) {
      where.push(`${column} = ?`);
      params.push(value);
    }
  }
  if (filter.closedBefore !== undefined) {
    where.push('closed_at IS NOT NULL AND closed_at < ?');
    // Same canonical shape as the writer projects (mem-0rrf.15) — a TEXT
    // comparison is only chronological when both sides share one format.
    params.push(toIsoUtc(filter.closedBefore));
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

interface LessonRow {
  id: number;
  work_id: string;
  extracted_at: string;
  commit_sha: string | null;
  payload: string;
}

function toStoredLesson(row: LessonRow): StoredLesson {
  return {
    id: row.id,
    work_id: row.work_id,
    extracted_at: row.extracted_at,
    ...(row.commit_sha !== null && { commit_sha: row.commit_sha }),
    payload: JSON.parse(row.payload) as Record<string, unknown>,
  };
}

/** All lessons for a bead, in append order. */
export function lessonsFor(db: StoreDatabase, workId: string): StoredLesson[] {
  const rows = db
    .prepare(
      'SELECT id, work_id, extracted_at, commit_sha, payload FROM lessons WHERE work_id = ? ORDER BY id'
    )
    .all(workId) as LessonRow[];
  return rows.map(toStoredLesson);
}

/** Every lesson in the store, in append (id) order — the export side of the
 * schema-bump migration path. Lessons are the one table a store rebuild cannot
 * regenerate (append-only, extracted once per Decision 9), so they must be
 * carriable across rebuilds. */
export function allLessons(db: StoreDatabase): StoredLesson[] {
  const rows = db
    .prepare('SELECT id, work_id, extracted_at, commit_sha, payload FROM lessons ORDER BY id')
    .all() as LessonRow[];

  return rows.map(toStoredLesson);
}

/** The highest `lessons.id` in the store, or `null` when the table is empty.
 * Snapshot it BEFORE appending new lessons and pass it back as
 * {@link lastKLessons}' `asOfLessonId` to pin that window to the lessons that
 * existed at snapshot time (mem-ljp8b). */
export function maxLessonId(db: StoreDatabase): number | null {
  const row = db.prepare('SELECT MAX(id) AS id FROM lessons').get() as { id: number | null };
  return row.id;
}

/** The `k` most-recently-appended lessons (optionally rig-scoped, optionally
 * as-of a {@link maxLessonId} snapshot), in append (id) order — the K-past-fix
 * regression check's window (mem-0r7l). Pushed into SQL (`ORDER BY id DESC
 * LIMIT k`, then reversed back to ascending) so the check never scans and
 * JSON-parses the full, unboundedly-growing `lessons` table just to keep the
 * last k rows. `k <= 0` returns `[]` directly rather than passing a
 * non-positive value to SQLite's `LIMIT`, where a negative limit means "no
 * limit" — the opposite of this function's contract. `asOfLessonId` is
 * tri-state: omitted means the live table, a number excludes lessons appended
 * after it, and `null` (what `maxLessonId` returns for an empty table) means
 * the window was empty at snapshot time and stays empty here. */
export function lastKLessons(
  db: StoreDatabase,
  k: number,
  rig?: string,
  asOfLessonId?: number | null
): StoredLesson[] {
  if (k <= 0) return [];

  // The as-of clause qualifies its column as `l.id`: a bare `id` resolves only
  // while the joined `work_records` has no `id` of its own, and would go
  // "ambiguous column name" at runtime the moment it gained one — which
  // `computeRegressions`' caller swallows into a `regressionError` string
  // rather than failing the build (mem-6hvha).
  const where: string[] = [];
  const params: (string | number | null)[] = [];
  if (rig !== undefined) {
    where.push('wr.rig = ?');
    params.push(rig);
  }
  if (asOfLessonId !== undefined) {
    where.push('l.id <= ?');
    params.push(asOfLessonId);
  }
  params.push(k);
  const whereSql = where.length > 0 ? ` WHERE ${where.join(' AND ')}` : '';
  const joinSql = rig === undefined ? '' : ' JOIN work_records wr ON wr.work_id = l.work_id';

  const rows = db
    .prepare(
      `SELECT l.id, l.work_id, l.extracted_at, l.commit_sha, l.payload
         FROM lessons l${joinSql}${whereSql}
        ORDER BY l.id DESC LIMIT ?`
    )
    .all(...params) as LessonRow[];

  return rows.reverse().map(toStoredLesson);
}

/** The `k` most-recently-appended ORPHAN lessons — those whose `work_id` has no
 * `work_records` row — in append (id) order, plus `total`: the untruncated
 * orphan count, so a caller reporting a bounded slice can say "2 of 3" rather
 * than present `k` of them as the whole population.
 *
 * An orphan is an expected state, not corruption (Decision 9): lessons carry no
 * FK to `work_records` precisely so a lesson survives its record's
 * delete/re-ingest and may be extracted before the record lands. Orphans are
 * also unattributable — `rig` lives only on `work_records`, so this query
 * *cannot* be rig-scoped and deliberately is not. {@link lastKLessons}' own
 * rig-scoped window drops orphans pre-LIMIT (its JOIN is the rig predicate);
 * this is how `computeRegressions` surfaces them anyway, alongside that window
 * rather than inside it (mem-c7mf3).
 *
 * `NOT EXISTS` rather than a `LEFT JOIN ... IS NULL` anti-join: it leaves
 * `lessons l` the only table in the outer FROM, so the ambiguous-column failure
 * {@link lastKLessons} must actively defend against (mem-6hvha) cannot arise
 * here at all, and it probes `work_records`' `work_id` primary key directly.
 * `k <= 0` and `asOfLessonId` follow {@link lastKLessons}' contracts exactly —
 * including a negative `k` returning nothing rather than SQLite's "no limit".
 * `total` honors the same as-of bound as the window: counted live against a
 * pinned window it would report "1 of 2" for a snapshot that held every orphan
 * there was. */
export function orphanLessons(
  db: StoreDatabase,
  k: number,
  asOfLessonId?: number | null
): { lessons: StoredLesson[]; total: number } {
  if (k <= 0) return { lessons: [], total: 0 };

  const isOrphan = 'NOT EXISTS (SELECT 1 FROM work_records wr WHERE wr.work_id = l.work_id)';
  // Binding an explicit null yields `l.id <= NULL` → NULL → false for every
  // row, which IS the tri-state's "empty at snapshot time" answer; a falsy
  // check here would instead drop the clause and reproduce the live query.
  const whereSql = asOfLessonId === undefined ? isOrphan : `${isOrphan} AND l.id <= ?`;
  const asOfParams: (number | null)[] = asOfLessonId === undefined ? [] : [asOfLessonId];

  const rows = db
    .prepare(
      `SELECT l.id, l.work_id, l.extracted_at, l.commit_sha, l.payload
         FROM lessons l
        WHERE ${whereSql}
        ORDER BY l.id DESC LIMIT ?`
    )
    .all(...asOfParams, k) as LessonRow[];

  const { total } = db
    .prepare(`SELECT COUNT(*) AS total FROM lessons l WHERE ${whereSql}`)
    .get(...asOfParams) as { total: number };

  return { lessons: rows.reverse().map(toStoredLesson), total };
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

/** Like {@link workIdsBySignature}, restricted to one rig and closed strictly
 * after `closedAfter` — pushed into SQL (joining the promoted, indexed
 * `work_records.rig`/`closed_at` columns) so the K-past-fix regression check
 * never fetches and parses a candidate record only to discard it on rig or
 * temporal mismatch. */
export function workIdsBySignatureSince(
  db: StoreDatabase,
  signature: string,
  filter: { rig: string; closedAfter: string }
): string[] {
  const rows = db
    .prepare(
      `SELECT DISTINCT te.work_id
         FROM trace_errors te
         JOIN work_records wr ON wr.work_id = te.work_id
        WHERE te.signature = ?
          AND wr.rig = ?
          AND wr.closed_at IS NOT NULL AND wr.closed_at > ?
        ORDER BY te.work_id`
    )
    .all(signature, filter.rig, toIsoUtc(filter.closedAfter)) as { work_id: string }[];
  return rows.map(row => row.work_id);
}

/** The scalar fields the Decision-6 sibling test ({@link isSibling} in
 * `retrieve/exclusions.ts`) needs per candidate: `convoy_id`/`pr`/
 * `external_ref` straight off their promoted `work_records` columns, `parent`
 * off the `record_links` `'parent'` edge (mem-qgdz). */
export interface SiblingColumns {
  work_id: string;
  convoy_id: string | null;
  pr: string | null;
  external_ref: string | null;
  parent: string | null;
}

/** Batched {@link SiblingColumns} for a set of work_ids — one query (`json_each`,
 * no bound-variable chunking) rather than a `getRecord`-and-Zod-parse per
 * candidate, keyed by work_id for a caller with duplicates to dedup itself
 * (mem-0xz9b). Candidates absent from the store are simply missing from the
 * result map. */
export function siblingColumnsByWorkIds(
  db: StoreDatabase,
  workIds: readonly string[]
): Map<string, SiblingColumns> {
  if (workIds.length === 0) return new Map();
  const rows = db
    .prepare(
      `SELECT wr.work_id AS work_id, wr.convoy_id AS convoy_id, wr.pr AS pr,
              wr.external_ref AS external_ref, rl.target_id AS parent
         FROM work_records wr
         LEFT JOIN record_links rl ON rl.work_id = wr.work_id AND rl.kind = 'parent'
        WHERE wr.work_id IN (SELECT value FROM json_each(?))`
    )
    .all(JSON.stringify(workIds)) as SiblingColumns[];
  return new Map(rows.map(row => [row.work_id, row]));
}

/** One FTS hit on a trace error's message. */
export interface ErrorSearchHit {
  work_id: string;
  signature: string;
  message: string;
}

/** Default row cap for {@link searchErrorMessages}, exported so the CLI can
 * report the same value it falls back to instead of restating the literal. */
export const SEARCH_ERROR_DEFAULT_LIMIT = 20;

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
  limit = SEARCH_ERROR_DEFAULT_LIMIT
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

/** One PROV-O provenance edge (schema v8, the `links` table) — a row of the
 * TASK→AGENT→OUTCOME audit graph. `suspect` surfaces as a boolean; the rest map
 * the columns directly. */
export interface StoredProvLink {
  work_id: string;
  session_uuid: string | null;
  relation: string;
  entity_ref: string;
  entity_kind: string;
  key_type: string;
  tier: string;
  confidence: number | null;
  provenance: string | null;
  suspect: boolean;
  created_at: string;
}

interface ProvLinkRow extends Omit<StoredProvLink, 'suspect'> {
  suspect: number;
}

/** Provenance links for a bead, ordered by the unique-key components so the set
 * is reproducible run-to-run (the Decision-10 precision guard). The T3 floor —
 * the lowest soundness tier (mem-wanz.4) — gives every run a `wasAssociatedWith`
 * edge here; higher tiers accrue as their stages run. */
export function linksFor(db: StoreDatabase, workId: string): StoredProvLink[] {
  const rows = db
    .prepare(
      'SELECT work_id, session_uuid, relation, entity_ref, entity_kind, key_type, ' +
        'tier, confidence, provenance, suspect, created_at FROM links ' +
        'WHERE work_id = ? ORDER BY relation, entity_ref, key_type'
    )
    .all(workId) as ProvLinkRow[];

  return rows.map(row => ({ ...row, suspect: row.suspect === 1 }));
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
