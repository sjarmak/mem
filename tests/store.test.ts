import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import {
  SCHEMA_VERSION,
  allLessons,
  appendLesson,
  getRecord,
  importLessons,
  lastKLessons,
  lessonsFor,
  lessonsForRig,
  linksFor,
  maxLessonId,
  openStore,
  queryRecords,
  runsFor,
  searchErrorMessages,
  siblingColumnsByWorkIds,
  supersedesClosure,
  workIdsBySignature,
  writeRecords,
} from '../src/store/index.js';
import { failureSignature } from '../src/parse/index.js';
import { linkProjection } from '../src/store/writer.js';
import type { TraceError } from '../src/schemas/trace.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const tsError = (overrides: Partial<TraceError> = {}): TraceError => ({
  tool: 'tsc',
  severity: 'error',
  message: 'TS2345: bad argument',
  file: 'src/a.ts',
  line: 12,
  column: 5,
  ...overrides,
});

/** A maximal record: every nested field populated, so round-trip tests cover
 * the JSON column's payload, not just the promoted scalar columns. */
const fullRecord = (overrides: Partial<WorkRecord> = {}): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: 'demo-1a2b',
    rig: 'demo',
    title: 'Fix the build',
    labels: ['phase1', 'bug'],
    metadata: { 'gc.kind': 'task', nested: { depth: 2, list: [1, 2] } },
    priority: 1,
    external_ref: 'polecat/demo-1a2b',
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      started: '2026-06-01T01:00:00Z',
      closed: '2026-06-02T00:00:00Z',
      status: 'closed',
      status_history: [{ status: 'open', at: '2026-06-01T00:00:00Z' }],
    },
    agents: [
      { agent_id: 'gc-1001', role: 'polecat', account: 'a1', trace_ref: '/traces/x.jsonl' },
      { agent_id: 'gc-1002', role: 'refinery' },
    ],
    trace: {
      jsonl_path: '/traces/x.jsonl',
      n_turns: 42,
      tool_outcomes: [
        { runner: 'tsc', command: 'npm run typecheck', status: 'fail', errors: [tsError()] },
      ],
      errors: [tsError(), tsError({ tool: 'eslint', message: 'Unexpected any (no-explicit-any)' })],
    },
    outcome: { pr: '#63', pr_state: 'merged', commit_sha: 'abc123', ci: 'pass' },
    signal: { deterministic: { recurrences: 2 }, semantic: { root_cause: 'missing flag' } },
    links: { deps: ['demo-0f0f'], convoy_id: 'convoy-7', supersedes: ['demo-dead'] },
    ...overrides,
  });

describe('openStore', () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it('initializes a fresh database and reopens it', () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-store-'));
    const path = join(dir, 'mem.db');

    const db = openStore(path);
    writeRecords(db, [fullRecord()]);
    db.close();

    const reopened = openStore(path);
    expect(getRecord(reopened, 'demo-1a2b')).not.toBeNull();
    reopened.close();
  });

  it('fails loudly on a schema version mismatch, inventorying the non-regenerable tables', () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-store-'));
    const path = join(dir, 'mem.db');

    const db = openStore(path);
    appendLesson(db, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-03T00:00:00Z',
      payload: { root_cause: 'missing flag' },
    });
    db.pragma('user_version = 99');
    db.close();

    expect(() => openStore(path)).toThrow(/schema version/i);
    // The refusal names what a blind delete would strand, and the way out.
    expect(() => openStore(path)).toThrow(/lessons: 1 row/);
    expect(() => openStore(path)).toThrow(/memory_events: 0 row/);
    expect(() => openStore(path)).toThrow(/provenance_events: 0 row/);
    expect(() => openStore(path)).toThrow(/mem rebuild/);
  });
});

describe('mem-wanz.3 — PROV-O links schema', () => {
  it('projects link_tier + link_source onto work_records', () => {
    const db = openStore(':memory:');
    const cols = db.prepare('PRAGMA table_info(work_records)').all() as { name: string }[];
    const names = new Set(cols.map(c => c.name));
    expect(names.has('link_tier')).toBe(true);
    expect(names.has('link_source')).toBe(true);
    db.close();
  });

  it('creates a links table with the PRD §4 columns, separate from record_links', () => {
    const db = openStore(':memory:');
    const cols = db.prepare('PRAGMA table_info(links)').all() as { name: string }[];
    expect(cols.map(c => c.name)).toEqual([
      'id',
      'work_id',
      'session_uuid',
      'relation',
      'entity_ref',
      'entity_kind',
      'key_type',
      'tier',
      'confidence',
      'provenance',
      'suspect',
      'created_at',
    ]);
    // record_links stays the SRP-separate intra-corpus dep|supersedes table.
    const rl = db.prepare('PRAGMA table_info(record_links)').all() as { name: string }[];
    expect(rl.map(c => c.name)).toEqual(['work_id', 'kind', 'target_id']);
    db.close();
  });

  it('accepts a valid wasInformedBy edge and enforces relation/tier CHECKs + the unique key', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]); // the FK parent
    const insert = (cols: Record<string, unknown>) => {
      const keys = Object.keys(cols);
      db.prepare(
        `INSERT INTO links (${keys.join(',')}) VALUES (${keys.map(k => '@' + k).join(',')})`
      ).run(cols);
    };
    const edge = {
      work_id: 'demo-1a2b',
      session_uuid: 's-1',
      relation: 'wasInformedBy', // the memory edge the eval measures
      entity_ref: 'mem-other',
      entity_kind: 'work_record',
      key_type: 'pr-link',
      tier: 'T1',
      confidence: 0.9,
      provenance: 'events+content-scan',
      suspect: 0,
      created_at: '2026-06-18T00:00:00Z',
    };
    expect(() => insert(edge)).not.toThrow();
    // A relation outside the PROV-O vocabulary is rejected.
    expect(() => insert({ ...edge, relation: 'causedBy', entity_ref: 'x' })).toThrow(/CHECK/i);
    // A tier outside T1|T2|T3 is rejected.
    expect(() => insert({ ...edge, tier: 'T4', entity_ref: 'y' })).toThrow(/CHECK/i);
    // The unique key collapses a re-derivation of the same edge.
    expect(() => insert(edge)).toThrow(/UNIQUE/i);
    db.close();
  });
});

describe('writeRecords / getRecord round-trip', () => {
  it('round-trips a maximal record exactly (nested metadata, signal, links)', () => {
    const db = openStore(':memory:');
    const record = fullRecord();

    writeRecords(db, [record]);

    expect(getRecord(db, record.work_id)).toEqual(record);
  });

  it('round-trips a minimal spine record (absent optionals stay absent)', () => {
    const db = openStore(':memory:');
    const record = WorkRecordSchema.parse({
      work_id: 'demo-min1',
      rig: 'demo',
      title: 'Bare spine',
      lifecycle: { created: '2026-06-01T00:00:00Z', status: 'open' },
    });

    writeRecords(db, [record]);
    const read = getRecord(db, 'demo-min1');

    expect(read).toEqual(record);
    expect(read?.trace).toBeUndefined();
    expect(read?.outcome).toBeUndefined();
  });

  it('returns null for an unknown work_id', () => {
    const db = openStore(':memory:');
    expect(getRecord(db, 'nope-0000')).toBeNull();
  });

  it('re-ingest replaces the record and its children without duplication', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]);
    const updated = fullRecord({
      labels: ['phase1'],
      trace: {
        jsonl_path: '/traces/x.jsonl',
        errors: [tsError({ message: 'TS2551: renamed symbol', line: 99 })],
      },
    });

    writeRecords(db, [updated]);

    expect(getRecord(db, updated.work_id)).toEqual(updated);
    // Old child rows are gone: the original error signature no longer resolves.
    expect(workIdsBySignature(db, failureSignature(tsError()))).toEqual([]);
    expect(
      workIdsBySignature(
        db,
        failureSignature(tsError({ message: 'TS2551: renamed symbol', line: 99 }))
      )
    ).toEqual([updated.work_id]);
  });

  it('bulk re-ingest stays duplicate-free (batched child-row clear)', () => {
    const records = Array.from({ length: 1201 }, (_, i) =>
      WorkRecordSchema.parse({
        work_id: `bulk-${i}`,
        rig: 'demo',
        title: `bulk ${i}`,
        labels: ['bulk'],
        lifecycle: { created: '2026-06-01T00:00:00Z', status: 'closed' },
      })
    );
    const db = openStore(':memory:');
    writeRecords(db, records);
    writeRecords(db, records);

    expect(queryRecords(db, { rig: 'demo' })).toHaveLength(1201);
    const labelRows = db
      .prepare("SELECT COUNT(*) AS n FROM record_labels WHERE label = 'bulk'")
      .get() as { n: number };
    expect(labelRows.n).toBe(1201);
  });
});

describe('lessons (append-only, D9)', () => {
  it('appends lessons with a snapshotted citation and lists them in insertion order', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]);

    appendLesson(db, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-03T00:00:00Z',
      commit_sha: 'abc123',
      payload: { root_cause: 'missing flag', resolution: 'add --no-tls' },
    });
    appendLesson(db, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-04T00:00:00Z',
      payload: { root_cause: 'second pass' },
    });

    const lessons = lessonsFor(db, 'demo-1a2b');
    expect(lessons).toHaveLength(2);
    expect(lessons[0].commit_sha).toBe('abc123');
    expect(lessons[0].payload).toEqual({ root_cause: 'missing flag', resolution: 'add --no-tls' });
    expect(lessons[1].commit_sha).toBeUndefined();
    expect(lessons[0].id).toBeLessThan(lessons[1].id);
  });

  it('validates the disclosure convention: a malformed concept tag is rejected', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]);

    expect(() =>
      appendLesson(db, {
        work_id: 'demo-1a2b',
        extracted_at: '2026-06-03T00:00:00Z',
        payload: { subtitle: 'x', concepts: ['not-a-real-tag'] },
      })
    ).toThrow();

    const ok = appendLesson(db, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-03T00:00:00Z',
      payload: { subtitle: 'x', concepts: ['gotcha', 'trade-off'], extra: { kept: true } },
    });
    expect(lessonsFor(db, 'demo-1a2b')[0].id).toBe(ok);
    // Freeform keys outside the convention pass through untouched.
    expect(lessonsFor(db, 'demo-1a2b')[0].payload).toMatchObject({ extra: { kept: true } });
  });

  it('importLessons carries pre-convention payloads the append gate would reject', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]);
    // A historical payload that happens to use a reserved key with another
    // shape — the migration path must not brick on it.
    const legacy = {
      work_id: 'demo-1a2b',
      extracted_at: '2026-01-01T00:00:00Z',
      payload: { facts: 'a single string, not a list' },
    };

    expect(() => appendLesson(db, legacy)).toThrow();
    expect(importLessons(db, [legacy])).toEqual({ appended: 1, skipped: 0 });
    expect(importLessons(db, [legacy])).toEqual({ appended: 0, skipped: 1 });
    expect(lessonsFor(db, 'demo-1a2b')[0].payload).toEqual(legacy.payload);
  });

  it('lessons survive a re-ingest of their record', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]);
    appendLesson(db, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-03T00:00:00Z',
      payload: { root_cause: 'x' },
    });

    writeRecords(db, [fullRecord({ title: 'Fix the build (retry)' })]);

    expect(lessonsFor(db, 'demo-1a2b')).toHaveLength(1);
  });

  it('allLessons lists every lesson across beads in append order', () => {
    const db = openStore(':memory:');
    appendLesson(db, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-03T00:00:00Z',
      payload: { root_cause: 'a' },
    });
    appendLesson(db, {
      work_id: 'demo-2b3c',
      extracted_at: '2026-06-04T00:00:00Z',
      commit_sha: 'def456',
      payload: { root_cause: 'b' },
    });

    const lessons = allLessons(db);
    expect(lessons.map(l => l.work_id)).toEqual(['demo-1a2b', 'demo-2b3c']);
    expect(lessons[1].commit_sha).toBe('def456');
  });

  it('lessonsForRig lists only lessons whose source record is in that rig, in append order', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord({ work_id: 'w-a', rig: 'rigA' }),
      fullRecord({ work_id: 'w-b', rig: 'rigB' }),
    ]);
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-03T00:00:00Z', payload: {} });
    appendLesson(db, { work_id: 'w-b', extracted_at: '2026-06-04T00:00:00Z', payload: {} });
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-05T00:00:00Z', payload: {} });

    expect(lessonsForRig(db, 'rigA').map(l => l.work_id)).toEqual(['w-a', 'w-a']);
    expect(lessonsForRig(db, 'rigB').map(l => l.work_id)).toEqual(['w-b']);
    expect(lessonsForRig(db, 'rigC')).toEqual([]);
  });

  it("lessonsForRig excludes a lesson whose source record no longer exists (can't attribute it to any rig)", () => {
    const db = openStore(':memory:');
    appendLesson(db, { work_id: 'w-orphan', extracted_at: '2026-06-03T00:00:00Z', payload: {} });

    expect(lessonsForRig(db, 'rigA')).toEqual([]);
    expect(allLessons(db)).toHaveLength(1);
  });

  it('lastKLessons returns the k most-recently-appended lessons, in append order', () => {
    const db = openStore(':memory:');
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-03T00:00:00Z', payload: {} });
    appendLesson(db, { work_id: 'w-b', extracted_at: '2026-06-04T00:00:00Z', payload: {} });
    appendLesson(db, { work_id: 'w-c', extracted_at: '2026-06-05T00:00:00Z', payload: {} });

    expect(lastKLessons(db, 2).map(l => l.work_id)).toEqual(['w-b', 'w-c']);
    expect(lastKLessons(db, 100).map(l => l.work_id)).toEqual(['w-a', 'w-b', 'w-c']);
  });

  it('lastKLessons returns [] for k <= 0, never SQLite LIMIT-with-negative-value "no limit"', () => {
    const db = openStore(':memory:');
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-03T00:00:00Z', payload: {} });

    expect(lastKLessons(db, 0)).toEqual([]);
    expect(lastKLessons(db, -1)).toEqual([]);
  });

  it('lastKLessons scopes the window by rig when one is given', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord({ work_id: 'w-a', rig: 'rigA' }),
      fullRecord({ work_id: 'w-b', rig: 'rigB' }),
    ]);
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-03T00:00:00Z', payload: {} });
    appendLesson(db, { work_id: 'w-b', extracted_at: '2026-06-04T00:00:00Z', payload: {} });

    expect(lastKLessons(db, 1, 'rigA').map(l => l.work_id)).toEqual(['w-a']);
    expect(lastKLessons(db, 5, 'rigA').map(l => l.work_id)).toEqual(['w-a']);
    expect(lastKLessons(db, 5, 'rigC')).toEqual([]);
  });

  it('maxLessonId returns null for an empty table and the highest id otherwise', () => {
    const db = openStore(':memory:');
    expect(maxLessonId(db)).toBeNull();

    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-03T00:00:00Z', payload: {} });
    const idA = maxLessonId(db);
    expect(idA).not.toBeNull();

    appendLesson(db, { work_id: 'w-b', extracted_at: '2026-06-04T00:00:00Z', payload: {} });
    expect(maxLessonId(db)).toBe((idA as number) + 1);
  });

  it('lastKLessons excludes lessons appended after an asOfLessonId snapshot (unscoped)', () => {
    const db = openStore(':memory:');
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-03T00:00:00Z', payload: {} });
    appendLesson(db, { work_id: 'w-b', extracted_at: '2026-06-04T00:00:00Z', payload: {} });
    const snapshot = maxLessonId(db) as number;
    appendLesson(db, { work_id: 'w-c', extracted_at: '2026-06-05T00:00:00Z', payload: {} });

    // Without the snapshot, w-c (appended after) is in the window.
    expect(lastKLessons(db, 2).map(l => l.work_id)).toEqual(['w-b', 'w-c']);
    // Pinned to the snapshot, w-c is excluded even though it now exists.
    expect(lastKLessons(db, 2, undefined, snapshot).map(l => l.work_id)).toEqual(['w-a', 'w-b']);
  });

  it('lastKLessons excludes lessons appended after an asOfLessonId snapshot (rig-scoped)', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord({ work_id: 'w-a', rig: 'rigA' }),
      fullRecord({ work_id: 'w-b', rig: 'rigA' }),
    ]);
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-03T00:00:00Z', payload: {} });
    const snapshot = maxLessonId(db) as number;
    appendLesson(db, { work_id: 'w-b', extracted_at: '2026-06-04T00:00:00Z', payload: {} });

    expect(lastKLessons(db, 5, 'rigA').map(l => l.work_id)).toEqual(['w-a', 'w-b']);
    expect(lastKLessons(db, 5, 'rigA', snapshot).map(l => l.work_id)).toEqual(['w-a']);
  });

  it('lastKLessons returns [] for an explicit null asOfLessonId (snapshot taken when the table was empty) — never the unbounded live query', () => {
    const db = openStore(':memory:');
    // A snapshot of an empty table (maxLessonId(db) === null here) means no
    // lessons existed yet — the correct window is empty, not "everything
    // currently in the table," which is what an omitted (undefined) boundary
    // means. Collapsing null into undefined would wrongly reproduce the
    // unbounded live-query behavior for a caller that snapshotted at zero.
    expect(maxLessonId(db)).toBeNull();
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-03T00:00:00Z', payload: {} });

    expect(lastKLessons(db, 5, undefined, null)).toEqual([]);
    expect(lastKLessons(db, 5, 'rigA', null)).toEqual([]);
    // Contrast: an omitted boundary sees the lesson that now exists.
    expect(lastKLessons(db, 5).map(l => l.work_id)).toEqual(['w-a']);
  });

  it('importLessons appends exported lessons and skips full-content duplicates', () => {
    const source = openStore(':memory:');
    appendLesson(source, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-03T00:00:00Z',
      commit_sha: 'abc123',
      payload: { root_cause: 'a' },
    });
    appendLesson(source, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-04T00:00:00Z',
      payload: { root_cause: 'b' },
    });
    const exported = allLessons(source);

    const dest = openStore(':memory:');
    expect(importLessons(dest, exported)).toEqual({ appended: 2, skipped: 0 });
    // Re-import is idempotent: identical content is skipped, never doubled.
    expect(importLessons(dest, exported)).toEqual({ appended: 0, skipped: 2 });

    const imported = allLessons(dest);
    expect(imported).toHaveLength(2);
    expect(imported[0].payload).toEqual({ root_cause: 'a' });
    expect(imported[0].commit_sha).toBe('abc123');
    expect(imported[1].commit_sha).toBeUndefined();
  });
});

describe('queryRecords', () => {
  const seed = (db: ReturnType<typeof openStore>) => {
    writeRecords(db, [
      fullRecord(),
      fullRecord({
        work_id: 'demo-2b3c',
        lifecycle: {
          created: '2026-06-02T00:00:00Z',
          started: '2026-06-02T01:00:00Z',
          status: 'in_progress',
          status_history: [],
        },
        agents: [{ agent_id: 'gc-2002' }],
        outcome: { pr: '#64', ci: 'fail' },
      }),
      fullRecord({
        work_id: 'other-9z9z',
        rig: 'other',
        lifecycle: {
          created: '2026-06-01T00:00:00Z',
          started: '2026-06-01T02:00:00Z',
          closed: '2026-06-03T00:00:00Z',
          status: 'closed',
          status_history: [],
        },
      }),
    ]);
  };

  it('filters by rig and status', () => {
    const db = openStore(':memory:');
    seed(db);

    expect(queryRecords(db, { rig: 'demo' }).map(r => r.work_id)).toEqual([
      'demo-1a2b',
      'demo-2b3c',
    ]);
    expect(queryRecords(db, { status: 'closed' }).map(r => r.work_id)).toEqual([
      'demo-1a2b',
      'other-9z9z',
    ]);
  });

  it('filters by outcome fields', () => {
    const db = openStore(':memory:');
    seed(db);

    expect(queryRecords(db, { ci: 'fail' }).map(r => r.work_id)).toEqual(['demo-2b3c']);
    expect(queryRecords(db, { pr_state: 'merged' }).map(r => r.work_id)).toEqual([
      'demo-1a2b',
      'other-9z9z',
    ]);
  });

  it('filters by agent', () => {
    const db = openStore(':memory:');
    seed(db);

    expect(queryRecords(db, { agent: 'gc-2002' }).map(r => r.work_id)).toEqual(['demo-2b3c']);
  });

  it('filters by landed_state (the work→landed-commit verdict)', () => {
    const db = openStore(':memory:');
    const sha = '0'.repeat(40);
    writeRecords(db, [
      fullRecord({
        work_id: 'land-aaaa',
        landed: {
          base_commit: sha,
          landed_commit: '1'.repeat(40),
          n_commits: 3,
          landed_state: 'landed',
        },
      }),
      fullRecord({
        work_id: 'land-bbbb',
        landed: { base_commit: sha, landed_state: 'ambiguous-window' },
      }),
      // A record with no landed projection must not match any landed filter.
      fullRecord({ work_id: 'land-cccc' }),
    ]);

    expect(queryRecords(db, { landed_state: 'landed' }).map(r => r.work_id)).toEqual(['land-aaaa']);
    expect(queryRecords(db, { landed_state: 'ambiguous-window' }).map(r => r.work_id)).toEqual([
      'land-bbbb',
    ]);
    expect(queryRecords(db, { landed_state: 'unresolved' })).toEqual([]);
  });

  it('closedBefore is strict — the temporal leave-one-out boundary (D6)', () => {
    const db = openStore(':memory:');
    seed(db);

    // demo-1a2b closed exactly at the boundary: excluded (strictly before).
    expect(queryRecords(db, { closedBefore: '2026-06-02T00:00:00Z' })).toEqual([]);
    expect(queryRecords(db, { closedBefore: '2026-06-02T00:00:01Z' }).map(r => r.work_id)).toEqual([
      'demo-1a2b',
    ]);
    // Never-closed records are never retrievable.
    expect(queryRecords(db, { closedBefore: '2099-01-01T00:00:00Z' }).map(r => r.work_id)).toEqual([
      'demo-1a2b',
      'other-9z9z',
    ]);
  });
});

describe('record_links projection (mem-qgdz)', () => {
  it('projects deps, supersedes and the epic parent from the record JSON', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord({
        links: {
          deps: ['demo-0f0f'],
          supersedes: ['demo-dead'],
          convoy_id: 'convoy-7',
          parent: 'demo-1a2b-epic',
        },
      }),
    ]);

    const rows = db
      .prepare('SELECT kind, target_id FROM record_links WHERE work_id = ? ORDER BY kind')
      .all('demo-1a2b') as { kind: string; target_id: string }[];
    expect(rows).toEqual([
      { kind: 'dep', target_id: 'demo-0f0f' },
      { kind: 'parent', target_id: 'demo-1a2b-epic' },
      { kind: 'supersedes', target_id: 'demo-dead' },
    ]);
    db.close();
  });
});

describe('mem-0rrf.15 — canonical lifecycle timestamps (format-free D6 boundary)', () => {
  const timedRecord = (workId: string, closed?: string): WorkRecord =>
    WorkRecordSchema.parse({
      work_id: workId,
      rig: 'demo',
      title: workId,
      lifecycle: {
        created: '2026-06-01 00:00:00', // dolt shape, deliberately
        ...(closed !== undefined && { closed }),
        status: closed !== undefined ? 'closed' : 'open',
        status_history: [],
      },
      links: { deps: [], supersedes: [] },
    });

  it('bumped SCHEMA_VERSION to 11 (canonical lifecycle projections force a rebuild of pre-fix stores)', () => {
    expect(SCHEMA_VERSION).toBe(11);
  });

  it('normalizes every producer shape to one canonical ISO-8601 UTC column value', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      timedRecord('dolt-aaaa', '2026-06-02 00:00:00'), // dolt space-separated, zoneless
      timedRecord('isoz-bbbb', '2026-06-02T00:00:00Z'), // synthetic ISO T/Z (Decision 19)
      timedRecord('offs-cccc', '2026-06-02T02:00:00+02:00'), // explicit offset
    ]);
    const closedAt = (id: string): string =>
      (
        db.prepare('SELECT closed_at FROM work_records WHERE work_id = ?').get(id) as {
          closed_at: string;
        }
      ).closed_at;
    // All three name the same instant — the projected column must be one shape.
    expect(closedAt('dolt-aaaa')).toBe('2026-06-02T00:00:00.000Z');
    expect(closedAt('isoz-bbbb')).toBe('2026-06-02T00:00:00.000Z');
    expect(closedAt('offs-cccc')).toBe('2026-06-02T00:00:00.000Z');
    // The stored record JSON keeps the producer's original bytes.
    expect(getRecord(db, 'dolt-aaaa')?.lifecycle.closed).toBe('2026-06-02 00:00:00');
    db.close();
  });

  it("closes the ' '<'T' leak: a dolt-shape record closing AFTER an ISO boundary is excluded", () => {
    const db = openStore(':memory:');
    // Raw lexicographic TEXT comparison: '2026-06-02 23:59:59' < '2026-06-02T00:00:00Z'
    // (' ' < 'T'), so this record — closing ~24h after the boundary — would leak.
    writeRecords(db, [timedRecord('leak-aaaa', '2026-06-02 23:59:59')]);
    expect(queryRecords(db, { closedBefore: '2026-06-02T00:00:00Z' })).toEqual([]);
    db.close();
  });

  it('does not over-exclude the reverse mix (ISO record, dolt-shape boundary)', () => {
    const db = openStore(':memory:');
    // Raw comparison would exclude ('T' > ' '), but 01:00 < 12:00 on the same day.
    writeRecords(db, [timedRecord('okay-aaaa', '2026-06-02T01:00:00Z')]);
    expect(queryRecords(db, { closedBefore: '2026-06-02 12:00:00' }).map(r => r.work_id)).toEqual([
      'okay-aaaa',
    ]);
    db.close();
  });

  it('boundary ties are excluded across formats (strict <)', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      timedRecord('tie-space', '2026-06-02 00:00:00'),
      timedRecord('tie-offset', '2026-06-02T02:00:00+02:00'),
    ]);
    expect(queryRecords(db, { closedBefore: '2026-06-02T00:00:00Z' })).toEqual([]);
    // One tick past the boundary admits both.
    expect(
      queryRecords(db, { closedBefore: '2026-06-02T00:00:00.001Z' }).map(r => r.work_id)
    ).toEqual(['tie-offset', 'tie-space']);
    db.close();
  });

  it('never-closed records never match, whatever the boundary shape', () => {
    const db = openStore(':memory:');
    writeRecords(db, [timedRecord('open-aaaa')]);
    expect(queryRecords(db, { closedBefore: '2099-01-01 00:00:00' })).toEqual([]);
    expect(queryRecords(db, { closedBefore: '2099-01-01T00:00:00Z' })).toEqual([]);
    db.close();
  });

  it('the writer fails loudly on an unparseable lifecycle timestamp', () => {
    const db = openStore(':memory:');
    expect(() => writeRecords(db, [timedRecord('bad-aaaa', 'yesterday-ish')])).toThrow(
      /timestamp/i
    );
    // Date-only is not a boundary instant either — reject, don't guess midnight.
    expect(() => writeRecords(db, [timedRecord('bad-bbbb', '2026-06-02')])).toThrow(/timestamp/i);
    db.close();
  });

  it('rejects calendar-invalid days instead of rolling them (parity with Python canonical_ts)', () => {
    const db = openStore(':memory:');
    // V8's Date.parse rolls day overflow forward (2026-02-30 → 2026-03-02);
    // the Python mirror raises ValueError. Guessing a different instant would
    // silently reorder the boundary AND diverge the parity contract.
    expect(() => writeRecords(db, [timedRecord('bad-cccc', '2026-02-30T00:00:00Z')])).toThrow(
      /timestamp/i
    );
    // Hour 24 rolls to next-day midnight in V8 too; Python rejects it.
    expect(() => writeRecords(db, [timedRecord('bad-dddd', '2026-06-02T24:00:00Z')])).toThrow(
      /timestamp/i
    );
    db.close();
  });

  it('the reader fails loudly on an unparseable closedBefore', () => {
    const db = openStore(':memory:');
    expect(() => queryRecords(db, { closedBefore: 'not-a-time' })).toThrow(/timestamp/i);
    db.close();
  });
});

describe('supersedesClosure (D6 same-work chain)', () => {
  const chainRecord = (workId: string, supersedes: string[] = []): WorkRecord =>
    WorkRecordSchema.parse({
      work_id: workId,
      rig: 'demo',
      title: workId,
      lifecycle: { created: '2026-06-01T00:00:00Z', status: 'closed', status_history: [] },
      links: { deps: [], supersedes },
    });

  it('returns the multi-hop chain in both directions, excluding the anchor', () => {
    const db = openStore(':memory:');
    // old0 <- old1 <- b -> (superseded by) new ;  free is unrelated.
    writeRecords(db, [
      chainRecord('b', ['old1']),
      chainRecord('old1', ['old0']),
      chainRecord('old0'),
      chainRecord('new', ['b']),
      chainRecord('free'),
    ]);

    expect(supersedesClosure(db, 'b')).toEqual(['new', 'old0', 'old1']);
  });

  it('returns an empty chain for a record with no supersedes links', () => {
    const db = openStore(':memory:');
    writeRecords(db, [chainRecord('solo')]);
    expect(supersedesClosure(db, 'solo')).toEqual([]);
  });
});

describe('failure-signature retrieval keys (D8)', () => {
  it('finds work ids by exact failure signature', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord(),
      fullRecord({
        work_id: 'demo-2b3c',
        trace: { jsonl_path: '/t/y.jsonl', errors: [tsError()] },
      }),
    ]);

    expect(workIdsBySignature(db, failureSignature(tsError()))).toEqual(['demo-1a2b', 'demo-2b3c']);
    expect(workIdsBySignature(db, 'tsc:src/zzz.ts:1:TS9999')).toEqual([]);
  });

  it('searches error messages via FTS as the weak tiebreaker', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]);

    const hits = searchErrorMessages(db, 'argument');
    expect(hits).toHaveLength(1);
    expect(hits[0]).toMatchObject({
      work_id: 'demo-1a2b',
      message: 'TS2345: bad argument',
      signature: failureSignature(tsError()),
    });
    expect(searchErrorMessages(db, 'nonexistentword')).toEqual([]);
  });

  it('FTS index stays in sync across re-ingest (stale messages unfindable)', () => {
    const db = openStore(':memory:');
    // A second record keeps its own error rows across the re-ingest, so a
    // stale index entry could not hide behind a dangling JOIN — this covers
    // the rowid-not-reused path as well as the simple one.
    writeRecords(db, [
      fullRecord(),
      fullRecord({
        work_id: 'demo-2b3c',
        trace: { jsonl_path: '/t/y.jsonl', errors: [tsError()] },
      }),
    ]);
    writeRecords(db, [
      fullRecord({
        trace: {
          jsonl_path: '/traces/x.jsonl',
          errors: [tsError({ message: 'TS2551: fresh wording' })],
        },
      }),
      fullRecord({ work_id: 'demo-2b3c', trace: { jsonl_path: '/t/y.jsonl', errors: [] } }),
    ]);

    expect(searchErrorMessages(db, 'argument')).toEqual([]);
    expect(searchErrorMessages(db, 'fresh')).toHaveLength(1);
    // Verify the index itself, not just the JOIN-masked view: the rank=1
    // form of FTS5's integrity-check compares the index against the external
    // content table and throws if they disagree (i.e. if a sync trigger ever
    // stopped firing). The plain form only checks internal index structure.
    expect(() =>
      db.exec("INSERT INTO trace_errors_fts(trace_errors_fts, rank) VALUES ('integrity-check', 1)")
    ).not.toThrow();
  });

  it('throws on malformed FTS5 query syntax (caller owns query construction)', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]);

    expect(() => searchErrorMessages(db, '"unclosed quote')).toThrow();
  });

  it('respects the result limit', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord(),
      fullRecord({
        work_id: 'demo-2b3c',
        trace: { jsonl_path: '/t/y.jsonl', errors: [tsError()] },
      }),
    ]);

    expect(searchErrorMessages(db, 'argument', 1)).toHaveLength(1);
  });
});

describe('siblingColumnsByWorkIds (mem-0xz9b)', () => {
  it('batches convoy_id/pr/external_ref/parent for a set of work_ids in one query', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord(), // demo-1a2b: convoy_id 'convoy-7', pr '#63', external_ref set, no parent
      fullRecord({
        work_id: 'demo-2b3c',
        trace: { jsonl_path: '/t/y.jsonl', errors: [] },
        outcome: undefined,
        external_ref: undefined,
        links: { deps: [], supersedes: [], parent: 'demo-1a2b-epic' },
      }),
    ]);

    const columns = siblingColumnsByWorkIds(db, ['demo-1a2b', 'demo-2b3c']);
    expect(columns.get('demo-1a2b')).toEqual({
      work_id: 'demo-1a2b',
      convoy_id: 'convoy-7',
      pr: '#63',
      external_ref: 'polecat/demo-1a2b',
      parent: null,
    });
    expect(columns.get('demo-2b3c')).toEqual({
      work_id: 'demo-2b3c',
      convoy_id: null,
      pr: null,
      external_ref: null,
      parent: 'demo-1a2b-epic',
    });
    db.close();
  });

  it('omits work_ids that do not exist in the store, rather than a null/undefined entry', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]);

    const columns = siblingColumnsByWorkIds(db, ['demo-1a2b', 'demo-vanished']);
    expect(columns.has('demo-vanished')).toBe(false);
    expect(columns.get('demo-1a2b')?.work_id).toBe('demo-1a2b');
    db.close();
  });

  it('returns an empty map for an empty work_id list, without querying', () => {
    const db = openStore(':memory:');
    expect(siblingColumnsByWorkIds(db, [])).toEqual(new Map());
    db.close();
  });
});

describe('trace_runs projection (run-level metadata)', () => {
  const run = {
    session_uuid: 'sess-aaaa',
    model: 'claude-opus-4-8',
    harness_version: '2.1.138',
    input_tokens: 100,
    output_tokens: 200,
    cache_creation_tokens: 300,
    cache_read_tokens: 400,
    n_tool_calls: 5,
    tool_calls_by_type: { Bash: 3, Read: 2 },
    n_turns: 12,
    started_at: '2026-06-01T00:00:00Z',
    ended_at: '2026-06-01T01:00:00Z',
    outcome: 'end_turn',
  };

  const withRun = (overrides: Partial<WorkRecord> = {}): WorkRecord =>
    fullRecord({
      trace: { jsonl_path: '/traces/x.jsonl', run },
      ...overrides,
    });

  it('projects the run row keyed by (work_id, agent_id, session_uuid)', () => {
    const db = openStore(':memory:');
    writeRecords(db, [withRun()]);

    const rows = runsFor(db, 'demo-1a2b');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      work_id: 'demo-1a2b',
      // gc-1001 is the agent whose trace_ref matches the trace's jsonl_path.
      agent_id: 'gc-1001',
      ...run,
    });
  });

  it('omits the run row entirely when the trace has no parsed run', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]); // fullRecord's trace carries no `run`
    expect(runsFor(db, 'demo-1a2b')).toEqual([]);
  });

  it('attributes to the first agent when no agent owns the transcript', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      withRun({
        agents: [
          { agent_id: 'gc-2001', role: 'polecat', trace_ref: '/traces/other.jsonl' },
          { agent_id: 'gc-2002', role: 'refinery' },
        ],
      }),
    ]);
    expect(runsFor(db, 'demo-1a2b')[0].agent_id).toBe('gc-2001');
  });

  it('attributes to null when the record carries no agents', () => {
    const db = openStore(':memory:');
    writeRecords(db, [withRun({ agents: [] })]);
    expect(runsFor(db, 'demo-1a2b')[0].agent_id).toBeNull();
  });

  it('rebuilds the run row on re-ingest — never drifts, never duplicates', () => {
    const db = openStore(':memory:');
    writeRecords(db, [withRun()]);
    writeRecords(db, [
      withRun({
        trace: {
          jsonl_path: '/traces/x.jsonl',
          run: { ...run, input_tokens: 999, n_tool_calls: 1, tool_calls_by_type: { Bash: 1 } },
        },
      }),
    ]);

    const rows = runsFor(db, 'demo-1a2b');
    expect(rows).toHaveLength(1);
    expect(rows[0].input_tokens).toBe(999);
    expect(rows[0].n_tool_calls).toBe(1);
    expect(rows[0].tool_calls_by_type).toEqual({ Bash: 1 });
  });
});

describe('mem-wanz.4 — T3 session-association floor (links)', () => {
  const run = {
    session_uuid: 'sess-t3',
    input_tokens: 1,
    output_tokens: 1,
    cache_creation_tokens: 0,
    cache_read_tokens: 0,
    n_tool_calls: 0,
    tool_calls_by_type: {},
    n_turns: 1,
    started_at: '2026-06-01T03:00:00Z',
  };
  const withRun = (overrides: Partial<WorkRecord> = {}): WorkRecord =>
    fullRecord({ trace: { jsonl_path: '/traces/x.jsonl', run }, ...overrides });

  it('asserts a wasAssociatedWith T3 edge for a run, keyed on session_uuid', () => {
    const db = openStore(':memory:');
    writeRecords(db, [withRun()]);
    // The PROV-O floor: the run (Activity) wasAssociatedWith its session (Agent).
    expect(linksFor(db, 'demo-1a2b')).toEqual([
      {
        work_id: 'demo-1a2b',
        session_uuid: 'sess-t3',
        relation: 'wasAssociatedWith',
        entity_ref: 'sess-t3', // the agent the run ran as
        entity_kind: 'session',
        key_type: 'session_uuid',
        tier: 'T3',
        confidence: 1, // exact spine join
        provenance: 'session_uuid',
        suspect: false,
        created_at: '2026-06-01T03:00:00Z', // the run start — deterministic, not wall-clock
      },
    ]);
  });

  it('derives created_at from the record creation when the run has no start', () => {
    const db = openStore(':memory:');
    const { started_at: _omit, ...noStart } = run;
    writeRecords(db, [withRun({ trace: { jsonl_path: '/traces/x.jsonl', run: noStart } })]);
    // fullRecord's lifecycle.created — keeps the edge byte-identical on re-ingest.
    expect(linksFor(db, 'demo-1a2b')[0].created_at).toBe('2026-06-01T00:00:00Z');
  });

  it('writes no floor edge when the record has no run (the floor is over trace_runs)', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord()]); // fullRecord's trace carries no run
    expect(linksFor(db, 'demo-1a2b')).toEqual([]);
  });

  it('covers 100% of trace_runs — exactly one association edge per run across a batch', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      withRun(),
      withRun({
        work_id: 'demo-2b3c',
        trace: { jsonl_path: '/traces/y.jsonl', run: { ...run, session_uuid: 'sess-2' } },
      }),
      fullRecord({ work_id: 'demo-3c4d' }), // no run → no edge
    ]);
    const runs = (db.prepare('SELECT count(*) AS n FROM trace_runs').get() as { n: number }).n;
    const floor = (
      db
        .prepare(
          "SELECT count(*) AS n FROM links WHERE relation = 'wasAssociatedWith' AND tier = 'T3'"
        )
        .get() as { n: number }
    ).n;
    expect(runs).toBe(2);
    expect(floor).toBe(runs); // 100% of runs carry the floor edge — the T3 spine
  });

  it('rebuilds the floor edge on re-ingest — never drifts, never duplicates', () => {
    const db = openStore(':memory:');
    writeRecords(db, [withRun()]);
    writeRecords(db, [withRun()]); // re-ingest the same record
    expect(linksFor(db, 'demo-1a2b')).toHaveLength(1);
  });
});

describe('mem-wanz.7 — pr-link outcome edges (links)', () => {
  const prLink = {
    session_uuid: 'sess-pr',
    pr_number: 66,
    pr_url: 'https://github.com/sjarmak/gascity-dashboard/pull/66',
    pr_repository: 'sjarmak/gascity-dashboard',
    timestamp: '2026-06-17T13:14:43.142Z',
  };
  const withPrLinks = (links: unknown[]): WorkRecord =>
    fullRecord({
      trace: { jsonl_path: '/traces/x.jsonl', pr_links: links } as WorkRecord['trace'],
    });

  it('writes a wasGeneratedBy T2 edge to the PR, keyed by pr-link', () => {
    const db = openStore(':memory:');
    writeRecords(db, [withPrLinks([prLink])]);
    // PROV-O: the PR (Entity, the outcome) wasGeneratedBy this work (Activity).
    expect(linksFor(db, 'demo-1a2b')).toEqual([
      {
        work_id: 'demo-1a2b',
        session_uuid: 'sess-pr',
        relation: 'wasGeneratedBy',
        entity_ref: 'https://github.com/sjarmak/gascity-dashboard/pull/66',
        entity_kind: 'pull_request',
        key_type: 'pr-link',
        tier: 'T2', // a PR reference, not yet a CI/merge-verified oracle
        confidence: 0.98,
        provenance: 'pr-link',
        suspect: false,
        created_at: '2026-06-17T13:14:43.142Z',
      },
    ]);
  });

  it('writes one edge per distinct PR a session generated', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      withPrLinks([
        prLink,
        {
          ...prLink,
          pr_number: 70,
          pr_url: 'https://github.com/sjarmak/gascity-dashboard/pull/70',
        },
      ]),
    ]);
    const refs = linksFor(db, 'demo-1a2b').map(l => l.entity_ref);
    expect(refs).toEqual([
      'https://github.com/sjarmak/gascity-dashboard/pull/66',
      'https://github.com/sjarmak/gascity-dashboard/pull/70',
    ]);
  });

  it('coexists with the T3 floor edge on a record that has both a run and a PR', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord({
        trace: {
          jsonl_path: '/traces/x.jsonl',
          run: {
            session_uuid: 'sess-pr',
            input_tokens: 1,
            output_tokens: 1,
            cache_creation_tokens: 0,
            cache_read_tokens: 0,
            n_tool_calls: 0,
            tool_calls_by_type: {},
            n_turns: 1,
          },
          pr_links: [prLink],
        },
      }),
    ]);
    const byRelation = linksFor(db, 'demo-1a2b')
      .map(l => `${l.relation}:${l.tier}`)
      .sort();
    expect(byRelation).toEqual(['wasAssociatedWith:T3', 'wasGeneratedBy:T2']);
  });

  it('derives created_at from the record when the pr-link has no timestamp', () => {
    const db = openStore(':memory:');
    const { timestamp: _omit, ...noTs } = prLink;
    writeRecords(db, [withPrLinks([noTs])]);
    expect(linksFor(db, 'demo-1a2b')[0].created_at).toBe('2026-06-01T00:00:00Z'); // lifecycle.created
  });

  it('rebuilds the edge on re-ingest — never drifts, never duplicates', () => {
    const db = openStore(':memory:');
    writeRecords(db, [withPrLinks([prLink])]);
    writeRecords(db, [withPrLinks([prLink])]);
    expect(linksFor(db, 'demo-1a2b')).toHaveLength(1);
  });
});

describe('mem-0rrf.3 — link_tier/link_source projection (writer link stage)', () => {
  const run = {
    session_uuid: 'sess-proj',
    input_tokens: 1,
    output_tokens: 1,
    cache_creation_tokens: 0,
    cache_read_tokens: 0,
    n_tool_calls: 0,
    tool_calls_by_type: {},
    n_turns: 1,
    started_at: '2026-06-01T03:00:00Z',
  };
  const prLink = {
    session_uuid: 'sess-proj',
    pr_number: 66,
    pr_url: 'https://github.com/sjarmak/gascity-dashboard/pull/66',
    pr_repository: 'sjarmak/gascity-dashboard',
    timestamp: '2026-06-17T13:14:43.142Z',
  };
  const projectionOf = (db: ReturnType<typeof openStore>, workId: string) =>
    db.prepare('SELECT link_tier, link_source FROM work_records WHERE work_id = ?').get(workId) as {
      link_tier: string | null;
      link_source: string | null;
    };

  describe('linkProjection (pure)', () => {
    it('returns null/null for a record with no links', () => {
      expect(linkProjection([])).toEqual({ link_tier: null, link_source: null });
    });

    it('takes the best (lowest) tier and its source', () => {
      expect(
        linkProjection([
          { tier: 'T3', provenance: 'session_uuid' },
          { tier: 'T2', provenance: 'pr-link' },
        ])
      ).toEqual({ link_tier: 'T2', link_source: 'pr-link' });
    });

    it("'+'-joins distinct best-tier sources, sorted and deduped", () => {
      expect(
        linkProjection([
          { tier: 'T1', provenance: 'ci' },
          { tier: 'T1', provenance: 'pr-link' },
          { tier: 'T1', provenance: 'ci' },
          { tier: 'T2', provenance: 'pr-link' }, // a lower tier never feeds the source
        ])
      ).toEqual({ link_tier: 'T1', link_source: 'ci+pr-link' });
    });

    it('yields a tier with a null source when best-tier links carry no provenance', () => {
      expect(linkProjection([{ tier: 'T3', provenance: null }])).toEqual({
        link_tier: 'T3',
        link_source: null,
      });
    });
  });

  it('projects T3 onto a record whose only edge is the session floor', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord({ trace: { jsonl_path: '/traces/x.jsonl', run } })]);
    expect(projectionOf(db, 'demo-1a2b')).toEqual({
      link_tier: 'T3',
      link_source: 'session_uuid',
    });
    db.close();
  });

  it('projects the best T2 tier when a record has both a run and a PR', () => {
    const db = openStore(':memory:');
    writeRecords(db, [
      fullRecord({ trace: { jsonl_path: '/traces/x.jsonl', run, pr_links: [prLink] } }),
    ]);
    expect(projectionOf(db, 'demo-1a2b')).toEqual({
      link_tier: 'T2',
      link_source: 'pr-link',
    });
    db.close();
  });

  it('clears a stale projection on re-ingest when the links disappear', () => {
    const db = openStore(':memory:');
    writeRecords(db, [fullRecord({ trace: { jsonl_path: '/traces/x.jsonl', run } })]);
    expect(projectionOf(db, 'demo-1a2b').link_tier).toBe('T3');
    // Re-ingest the same id with no run → no links → the projection must reset.
    writeRecords(db, [fullRecord()]);
    expect(projectionOf(db, 'demo-1a2b')).toEqual({ link_tier: null, link_source: null });
    db.close();
  });
});
