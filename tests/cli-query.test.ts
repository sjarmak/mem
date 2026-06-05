import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { appendLesson, openStore, writeRecords } from '../src/store/index.js';
import { failureSignature } from '../src/parse/index.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';
import type { TraceError } from '../src/schemas/trace.js';
import { CliOptions, CommandContext } from '../src/cli/index.js';
import { queryCommand } from '../src/cli/commands/query.js';
import { lessonsCommand } from '../src/cli/commands/lessons.js';
import { signatureCommand } from '../src/cli/commands/signature.js';
import { searchErrorsCommand } from '../src/cli/commands/search-errors.js';

const tsError = (overrides: Partial<TraceError> = {}): TraceError => ({
  tool: 'tsc',
  severity: 'error',
  message: 'TS2345: bad argument',
  file: 'src/a.ts',
  line: 12,
  column: 5,
  ...overrides,
});

const fullRecord = (overrides: Partial<WorkRecord> = {}): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: 'demo-1a2b',
    rig: 'demo',
    title: 'Fix the build',
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      closed: '2026-06-02T00:00:00Z',
      status: 'closed',
      status_history: [],
    },
    agents: [{ agent_id: 'gc-1001' }],
    trace: { jsonl_path: '/t/x.jsonl', errors: [tsError()] },
    outcome: { pr: '#63', pr_state: 'merged', ci: 'pass' },
    ...overrides,
  });

/** A CommandContext with the given positional args + a temp store path. */
function ctx(storePath: string, args: string[], options: Partial<CliOptions> = {}): CommandContext {
  return { args, options: { json: true, verbose: false, store: storePath, ...options } };
}

let dir: string;
let storePath: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-cli-'));
  storePath = join(dir, 'store.db');
  const db = openStore(storePath);
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
      trace: { jsonl_path: '/t/y.jsonl', errors: [] },
      outcome: { pr: '#64', ci: 'fail' },
    }),
    fullRecord({
      work_id: 'other-9z9z',
      rig: 'other',
      trace: { jsonl_path: '/t/z.jsonl', errors: [] },
    }),
  ]);
  appendLesson(db, {
    work_id: 'demo-1a2b',
    extracted_at: '2026-06-03T00:00:00Z',
    payload: { root_cause: 'x' },
  });
  db.close();
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('query command', () => {
  it('looks up a single record by positional work_id', () => {
    const result = queryCommand(ctx(storePath, ['demo-1a2b']));
    expect(result.count).toBe(1);
    expect(result.records[0]?.work_id).toBe('demo-1a2b');
  });

  it('returns an empty set for an unknown work_id', () => {
    expect(queryCommand(ctx(storePath, ['nope-0000'])).records).toEqual([]);
  });

  it('filters by rig', () => {
    const result = queryCommand(ctx(storePath, [], { rig: 'demo' }));
    expect(result.records.map(r => r.work_id)).toEqual(['demo-1a2b', 'demo-2b3c']);
  });

  it('filters by agent', () => {
    expect(
      queryCommand(ctx(storePath, [], { agent: 'gc-2002' })).records.map(r => r.work_id)
    ).toEqual(['demo-2b3c']);
  });

  it('filters by outcome (ci, pr-state)', () => {
    expect(queryCommand(ctx(storePath, [], { ci: 'fail' })).records.map(r => r.work_id)).toEqual([
      'demo-2b3c',
    ]);
    expect(
      queryCommand(ctx(storePath, [], { 'pr-state': 'merged' })).records.map(r => r.work_id)
    ).toEqual(['demo-1a2b', 'other-9z9z']);
  });

  it('returns all records with no filter', () => {
    expect(queryCommand(ctx(storePath, [])).records.map(r => r.work_id)).toEqual([
      'demo-1a2b',
      'demo-2b3c',
      'other-9z9z',
    ]);
  });

  it('rejects mixing a work_id with filters', () => {
    expect(() => queryCommand(ctx(storePath, ['demo-1a2b'], { rig: 'demo' }))).toThrow(
      /either a work_id or filters/
    );
  });

  it('rejects an invalid enum filter value', () => {
    expect(() => queryCommand(ctx(storePath, [], { ci: 'green' }))).toThrow(/--ci must be one of/);
    expect(() => queryCommand(ctx(storePath, [], { 'pr-state': 'open' }))).toThrow(
      /--pr-state must be one of/
    );
  });

  it('refuses a missing store instead of creating an empty one', () => {
    expect(() => queryCommand(ctx(join(dir, 'absent.db'), []))).toThrow(/No store at/);
  });
});

describe('lessons command', () => {
  it('returns appended lessons for a bead', () => {
    const result = lessonsCommand(ctx(storePath, ['demo-1a2b']));
    expect(result.count).toBe(1);
    expect(result.lessons[0]?.payload).toEqual({ root_cause: 'x' });
  });

  it('returns an empty set for a bead with no lessons', () => {
    expect(lessonsCommand(ctx(storePath, ['demo-2b3c'])).lessons).toEqual([]);
  });

  it('requires a work_id', () => {
    expect(() => lessonsCommand(ctx(storePath, []))).toThrow(/requires a work_id/);
  });
});

describe('signature command', () => {
  it('returns work ids carrying a failure signature', () => {
    const result = signatureCommand(ctx(storePath, [failureSignature(tsError())]));
    expect(result.work_ids).toEqual(['demo-1a2b']);
  });

  it('requires a signature', () => {
    expect(() => signatureCommand(ctx(storePath, []))).toThrow(/requires a value/);
  });
});

describe('search-errors command', () => {
  it('full-text searches trace-error messages', () => {
    const result = searchErrorsCommand(ctx(storePath, ['argument']));
    expect(result.count).toBe(1);
    expect(result.hits[0]?.work_id).toBe('demo-1a2b');
    expect(result.limit).toBe(20);
  });

  it('honors --limit', () => {
    const result = searchErrorsCommand(ctx(storePath, ['argument'], { limit: '5' }));
    expect(result.limit).toBe(5);
  });

  it('rejects a non-positive-integer limit', () => {
    expect(() => searchErrorsCommand(ctx(storePath, ['argument'], { limit: '0' }))).toThrow(
      /positive integer/
    );
    expect(() => searchErrorsCommand(ctx(storePath, ['argument'], { limit: 'x' }))).toThrow(
      /positive integer/
    );
  });

  it('requires a query', () => {
    expect(() => searchErrorsCommand(ctx(storePath, []))).toThrow(/requires a query/);
  });
});
