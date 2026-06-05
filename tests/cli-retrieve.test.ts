import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { openStore, writeRecords } from '../src/store/index.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';
import type { TraceError } from '../src/schemas/trace.js';
import { CliOptions, CommandContext } from '../src/cli/index.js';
import { retrieveCommand } from '../src/cli/commands/retrieve.js';

const tsError = (overrides: Partial<TraceError> = {}): TraceError => ({
  tool: 'tsc',
  severity: 'error',
  message: 'TS2345: bad argument',
  file: 'src/a.ts',
  line: 12,
  ...overrides,
});

const record = (workId: string, rig: string, overrides: Partial<WorkRecord> = {}): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig,
    title: `Work ${workId}`,
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      started: '2026-06-01T01:00:00Z',
      closed: '2026-06-05T00:00:00Z',
      status: 'closed',
      status_history: [],
    },
    trace: { jsonl_path: `/t/${workId}.jsonl`, errors: [tsError()] },
    ...overrides,
  });

function ctx(storePath: string, args: string[], options: Partial<CliOptions> = {}): CommandContext {
  return { args, options: { json: true, verbose: false, store: storePath, ...options } };
}

let dir: string;
let storePath: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-cli-retrieve-'));
  storePath = join(dir, 'store.db');
  const db = openStore(storePath);
  writeRecords(db, [
    record('rigA-prior', 'rigA'),
    record('rigB-prior', 'rigB'),
    record('rigA-query', 'rigA', {
      lifecycle: {
        created: '2026-06-09T00:00:00Z',
        started: '2026-06-10T00:00:00Z',
        closed: '2026-06-12T00:00:00Z',
        status: 'closed',
        status_history: [],
      },
    }),
  ]);
  db.close();
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('retrieveCommand — replay mode', () => {
  it('retrieves same-rig priors for a stored query work', () => {
    const result = retrieveCommand(ctx(storePath, ['rigA-query'], { scope: 'same-rig' }));

    expect(result.scope).toBe('same_rig_temporal');
    expect(result.items.map(i => i.work_id)).toEqual(['rigA-prior']);
  });

  it('retrieves cross-rig priors under --scope cross-rig', () => {
    const result = retrieveCommand(ctx(storePath, ['rigA-query'], { scope: 'cross-rig' }));

    expect(result.scope).toBe('cross_rig');
    expect(result.items.map(i => i.work_id)).toEqual(['rigB-prior']);
  });

  it('honors --limit', () => {
    const result = retrieveCommand(
      ctx(storePath, ['rigA-query'], { scope: 'same-rig', limit: '0' })
    );

    expect(result.items).toEqual([]);
    expect(result.total_matched).toBe(1);
  });

  it('throws on an unknown work_id', () => {
    expect(() => retrieveCommand(ctx(storePath, ['nope'], { scope: 'same-rig' }))).toThrow(/nope/);
  });
});

describe('retrieveCommand — query-file mode', () => {
  it('retrieves for an externally supplied query context', () => {
    const queryPath = join(dir, 'query.json');
    writeFileSync(
      queryPath,
      JSON.stringify({
        work_id: 'live-1',
        rig: 'rigA',
        started: '2026-06-10T00:00:00Z',
        errors: [tsError()],
      })
    );

    const result = retrieveCommand(ctx(storePath, [], { scope: 'cross-rig', query: queryPath }));

    expect(result.items.map(i => i.work_id)).toEqual(['rigB-prior']);
  });

  it('rejects a query file that fails schema validation', () => {
    const queryPath = join(dir, 'bad.json');
    writeFileSync(queryPath, JSON.stringify({ work_id: 'live-1' }));

    expect(() =>
      retrieveCommand(ctx(storePath, [], { scope: 'cross-rig', query: queryPath }))
    ).toThrow();
  });
});

describe('retrieveCommand — argument validation', () => {
  it('requires an explicit --scope', () => {
    expect(() => retrieveCommand(ctx(storePath, ['rigA-query']))).toThrow(/--scope/);
  });

  it('rejects an unknown scope value', () => {
    expect(() => retrieveCommand(ctx(storePath, ['rigA-query'], { scope: 'both' }))).toThrow(
      /--scope/
    );
  });

  it('rejects a non-numeric --limit', () => {
    expect(() =>
      retrieveCommand(ctx(storePath, ['rigA-query'], { scope: 'same-rig', limit: 'many' }))
    ).toThrow(/--limit/);
  });

  it('requires exactly one of work_id or --query', () => {
    expect(() => retrieveCommand(ctx(storePath, [], { scope: 'same-rig' }))).toThrow();
    expect(() =>
      retrieveCommand(ctx(storePath, ['rigA-query'], { scope: 'same-rig', query: 'q.json' }))
    ).toThrow();
  });
});
