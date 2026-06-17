import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import {
  allLessons,
  appendLesson,
  getRecord,
  importLessons,
  lessonsFor,
  openStore,
  queryRecords,
  runsFor,
  searchErrorMessages,
  supersedesClosure,
  workIdsBySignature,
  writeRecords,
} from '../src/store/index.js';
import { failureSignature } from '../src/parse/index.js';
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

  it('fails loudly on a schema version mismatch', () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-store-'));
    const path = join(dir, 'mem.db');

    const db = openStore(path);
    db.pragma('user_version = 99');
    db.close();

    expect(() => openStore(path)).toThrow(/schema version/i);
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
