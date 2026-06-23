/**
 * ingest/beads — the P1.2 dolt reader. Reads the bead store across ALL rigs
 * and emits the WorkRecord *spine* (id, rig, assignee→agent, status, lifecycle,
 * external_ref, labels, metadata). Trace, outcome and signal are attached by
 * later stages (P1.3/P1.4/P1.6).
 *
 * Substrate: a single Gas City dolt sql-server hosts one database per rig
 * (`gascity`, `gc`, `codeprobe`, …). A "rig" is therefore any database on that
 * server that has an `issues` table. This is pure IO — no judgment lives here.
 */
import { execFile } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { promisify } from 'node:util';
import { z } from 'zod';
import { AgentRef, WorkRecord, WorkRecordSchema } from '../schemas/workrecord.js';

const execFileAsync = promisify(execFile);

/** dolt JSON output can be large for a busy rig; allow up to 256 MiB. */
const MAX_BUFFER = 256 * 1024 * 1024;

/** Connection to the shared city dolt server. */
export interface DoltConnection {
  host: string;
  port: number;
  user: string;
  password: string;
}

/**
 * Default connection to the local city dolt server. The port is read from
 * `.beads/dolt-server.port` when present (each rig records the shared port
 * there), falling back to the conventional 29620.
 */
export function defaultConnection(portFile = '.beads/dolt-server.port'): DoltConnection {
  let port = 29620;
  try {
    const parsed = Number(readFileSync(portFile, 'utf8').trim());
    if (Number.isInteger(parsed) && parsed > 0) {
      port = parsed;
    }
  } catch {
    // Port file absent (e.g. running outside a beads workspace) — the
    // conventional city port is the documented fallback, not an error.
  }
  return { host: '127.0.0.1', port, user: 'root', password: '' };
}

/**
 * A SQL runner returns the decoded `rows` for a query against one database.
 * Injectable so the mapping/orchestration can be tested without a live server.
 */
export type SqlRunner = (database: string, sql: string) => Promise<DoltRow[]>;

/** A raw dolt result row. dolt renders every column value as a string and omits
 * NULL columns entirely, so optional columns are absent rather than null. */
export type DoltRow = Record<string, string | undefined>;

const DoltResultSchema = z.object({
  rows: z.array(z.record(z.string(), z.string())).optional(),
});

/** Parse `dolt sql -r json` stdout into rows. Empty results render as `{}`. */
export function parseDoltRows(stdout: string): DoltRow[] {
  const trimmed = stdout.trim();
  if (trimmed === '') {
    return [];
  }
  return DoltResultSchema.parse(JSON.parse(trimmed) as unknown).rows ?? [];
}

// Rig db names are backtick-quoted in SQL, so the only character that could
// break out is a backtick (and backslash). Allow the full set dolt permits in
// an unquoted-friendly name — alphanumerics, underscore, hyphen — which covers
// every real rig (`gascity_dashboard`, `code-intel-digest`, …).
const IDENTIFIER_RE = /^[A-Za-z0-9_-]+$/;

/** Database names are interpolated into SQL, so they must be safe identifiers. */
export function assertIdentifier(name: string): void {
  if (!IDENTIFIER_RE.test(name)) {
    throw new Error(`Unsafe SQL identifier: ${JSON.stringify(name)}`);
  }
}

/** Real runner: shells out to the `dolt` CLI in JSON mode against the server. */
export function doltRunner(conn: DoltConnection): SqlRunner {
  return async (database, sql) => {
    assertIdentifier(database);
    const { stdout } = await execFileAsync(
      'dolt',
      [
        '--host',
        conn.host,
        '--port',
        String(conn.port),
        '--user',
        conn.user,
        '--password',
        conn.password,
        '--no-tls',
        'sql',
        '-r',
        'json',
        '-q',
        `use \`${database}\`; ${sql}`,
      ],
      { maxBuffer: MAX_BUFFER }
    );
    return parseDoltRows(stdout);
  };
}

// --- pure mapping ----------------------------------------------------------

/** A session id embedded in an assignee, e.g. `gc-335825`, with an optional
 * role prefix, e.g. `polecat-gc-335825` or `mem-worker-gc-340057`. */
const ASSIGNEE_RE = /^(?:(.+)-)?([a-z][a-z0-9]*-\d+)$/;

/**
 * Decompose a bead `assignee` into an {@link AgentRef}. When the assignee
 * embeds a session id (`<role>-<session>`), `agent_id` is the session and
 * `role` the prefix — matching the EPIC's "agent_id = the session". Otherwise
 * the whole assignee becomes `agent_id` (e.g. `control-dispatcher`). Full
 * session→trace resolution is P1.3; this is structural string parsing only.
 */
export function parseAssignee(raw: string): AgentRef | null {
  const assignee = raw.trim();
  if (assignee === '') {
    return null;
  }
  const match = ASSIGNEE_RE.exec(assignee);
  if (match) {
    const [, role, sessionId] = match;
    return role ? { agent_id: sessionId, role } : { agent_id: sessionId };
  }
  return { agent_id: assignee };
}

/** Sink for non-fatal ingest anomalies. Defaults to stderr so a degraded read
 * is always visible; tests inject a collector. */
export type WarnFn = (message: string) => void;

const stderrWarn: WarnFn = message => console.error(message);

/** The bead `metadata` column is a JSON-encoded object; decode it (empty → {}).
 * Malformed JSON or a non-object payload throws — both are real producer bugs,
 * surfaced here with an actionable message rather than as a deep Zod path.
 * (The mapping layer catches this per bead — see {@link beadToWorkRecord} — so
 * one bad producer row degrades that record to `{}` instead of aborting the
 * whole rig read.) */
export function parseMetadata(raw: string | undefined): Record<string, unknown> {
  if (raw === undefined || raw === '') {
    return {};
  }
  const parsed = JSON.parse(raw) as unknown;
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error(`bead metadata is not a JSON object: ${raw}`);
  }
  return parsed as Record<string, unknown>;
}

/** Group `(issue_id, label)` rows into a label list per issue. */
export function groupLabels(rows: DoltRow[]): Map<string, string[]> {
  const byIssue = new Map<string, string[]>();
  for (const { issue_id, label } of rows) {
    if (issue_id === undefined || label === undefined) {
      continue;
    }
    const existing = byIssue.get(issue_id);
    if (existing) {
      existing.push(label);
    } else {
      byIssue.set(issue_id, [label]);
    }
  }
  return byIssue;
}

/** Map one issues row + its labels to a validated WorkRecord spine.
 * Malformed metadata is external producer data we don't control, so it degrades
 * to `{}` with a warning rather than aborting the rig read; every other shape
 * violation still throws (a broken spine is not ingestible). */
export function beadToWorkRecord(
  row: DoltRow,
  rig: string,
  labels: string[],
  warn: WarnFn = stderrWarn
): WorkRecord {
  const agent = row.assignee ? parseAssignee(row.assignee) : null;
  let metadata: Record<string, unknown>;
  try {
    metadata = parseMetadata(row.metadata);
  } catch (error: unknown) {
    const detail = error instanceof Error ? error.message : String(error);
    warn(`warning: ${rig}/${row.id ?? '<no id>'}: malformed bead metadata ignored (${detail})`);
    metadata = {};
  }
  const candidate = {
    work_id: row.id,
    rig,
    title: row.title ?? '',
    labels,
    metadata,
    priority: row.priority === undefined ? undefined : Number(row.priority),
    external_ref: row.external_ref,
    lifecycle: {
      created: row.created_at,
      started: row.started_at,
      closed: row.closed_at,
      status: row.status,
      // status_history is built from the `events` table in a later stage.
    },
    agents: agent ? [agent] : [],
  };
  return WorkRecordSchema.parse(candidate);
}

// --- orchestration ---------------------------------------------------------

const ISSUES_SQL =
  'select id, title, status, assignee, external_ref, priority, ' +
  'created_at, started_at, closed_at, metadata from issues';

const LABELS_SQL = 'select issue_id, label from labels';

const RIGS_SQL =
  "select table_schema as rig from information_schema.tables where table_name = 'issues'";

/** dolt/MySQL system schemas. They have no `issues` table so RIGS_SQL already
 * skips them, but they are listed for defence-in-depth (and clarity). */
const SYSTEM_SCHEMAS = new Set(['information_schema', 'mysql', 'sys', 'performance_schema']);

/** Exact non-rig databases on the shared server: a dolt probe and an empty
 * shared-package store. Both can carry an `issues` table but hold no work. */
const NON_RIG_DATABASES = new Set(['__gc_probe', 'dolt_pkg_shared']);

/** Name prefixes of the ephemeral databases gascity's test suites create on the
 * shared dolt server and routinely leak (e.g. `testdb_8212308f205f_shared`,
 * `test_cloud_auth_route_0_32819`, `fixdepkeys_489f1e277a97`). Many carry a
 * schema-conformant `issues` table, so schema alone cannot tell them apart from
 * a real rig — they are excluded by their stable naming convention. */
const TEST_DATABASE_PREFIXES = [
  'testdb_',
  'test_cloud_auth_',
  'test_federation_',
  'test_guard_',
  'fixdepkeys_',
];

/** True for a real work rig: not a system schema, not a known non-rig, and not
 * an ephemeral test-suite database. The dolt server is an external boundary, so
 * its database list is untrusted input — a test fixture's beads must never be
 * projected into the canonical store. Hence an explicit exclusion rather than
 * "any database with an issues table". */
function isWorkRig(name: string): boolean {
  if (SYSTEM_SCHEMAS.has(name) || NON_RIG_DATABASES.has(name)) return false;
  return !TEST_DATABASE_PREFIXES.some(prefix => name.startsWith(prefix));
}

/** List every work rig on the server: a database with an `issues` table that is
 * neither a system schema nor an ephemeral gascity test-suite database. */
export async function listRigs(run: SqlRunner): Promise<string[]> {
  const rows = await run('information_schema', RIGS_SQL);
  return rows
    .map(row => row.rig)
    .filter((rig): rig is string => rig !== undefined)
    .filter(isWorkRig)
    .sort();
}

/** Read all WorkRecord spines for a single rig. */
export async function readRig(
  run: SqlRunner,
  rig: string,
  warn: WarnFn = stderrWarn
): Promise<WorkRecord[]> {
  const [issues, labels] = await Promise.all([run(rig, ISSUES_SQL), run(rig, LABELS_SQL)]);
  const labelsByIssue = groupLabels(labels);
  return issues
    .filter((row): row is DoltRow & { id: string } => row.id !== undefined)
    .map(row => beadToWorkRecord(row, rig, labelsByIssue.get(row.id) ?? [], warn));
}

/** Read WorkRecord spines across every rig on the server. */
export async function readAllRigs(
  run: SqlRunner,
  warn: WarnFn = stderrWarn
): Promise<WorkRecord[]> {
  const rigs = await listRigs(run);
  const all: WorkRecord[] = [];
  for (const rig of rigs) {
    all.push(...(await readRig(run, rig, warn)));
  }
  return all;
}
