import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { importRecordsCommand, parseRecordLines } from '../src/cli/commands/import-records.js';
import { openStore, queryRecords, workIdsBySignature } from '../src/store/index.js';
import { failureSignature } from '../src/parse/recurrence.js';

const recordJson = (workId: string, rig: string): string =>
  JSON.stringify({
    work_id: workId,
    rig,
    title: `work ${workId}`,
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      closed: '2026-06-05T00:00:00Z',
      status: 'closed',
    },
    trace: {
      jsonl_path: `/traces/${workId}.jsonl`,
      errors: [
        {
          tool: 'pytest',
          severity: 'error',
          message: 'AssertionError: assert 1 == 2',
          file: 'tests/test_x.py',
          line: 0,
        },
      ],
    },
  });

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-import-records-'));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('parseRecordLines', () => {
  it('parses NDJSON, one record per line', () => {
    const input = `${recordJson('r-1', 'rigA')}\n${recordJson('r-2', 'rigA')}`;
    const records = parseRecordLines(input);
    expect(records.map(r => r.work_id)).toEqual(['r-1', 'r-2']);
  });

  it('parses a JSON array', () => {
    const input = `[${recordJson('r-1', 'rigA')},${recordJson('r-2', 'rigA')}]`;
    expect(parseRecordLines(input).map(r => r.work_id)).toEqual(['r-1', 'r-2']);
  });

  it('returns [] for empty/whitespace input', () => {
    expect(parseRecordLines('   \n ')).toEqual([]);
  });

  it('aborts on a malformed record with its line number', () => {
    const input = `${recordJson('r-1', 'rigA')}\n{"work_id":"r-2"}`;
    expect(() => parseRecordLines(input)).toThrow(/line 2/);
  });
});

describe('importRecordsCommand', () => {
  it('creates a store from a records file and projects trace_errors for retrieval', async () => {
    const file = join(dir, 'records.ndjson');
    writeFileSync(file, `${recordJson('r-1', 'rigA')}\n${recordJson('r-2', 'rigA')}`);
    const store = join(dir, 'nested', 'store.db');

    const result = await importRecordsCommand({
      args: [],
      options: { json: true, verbose: false, file, store },
    });

    expect(result.imported).toBe(2);
    expect(result.store).toBe(store);

    const db = openStore(store);
    try {
      expect(queryRecords(db).map(r => r.work_id)).toEqual(['r-1', 'r-2']);
      // The projected failure signature is searchable — the substrate the `ours`
      // arm retrieves on.
      const sig = failureSignature({
        tool: 'pytest',
        severity: 'error',
        message: 'AssertionError: assert 1 == 2',
        file: 'tests/test_x.py',
        line: 0,
      });
      expect(workIdsBySignature(db, sig).sort()).toEqual(['r-1', 'r-2']);
    } finally {
      db.close();
    }
  });
});
