import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { attachAndParse, buildStoreFromRecords } from '../src/cli/commands/build-store.js';
import { openStore, queryRecords, workIdsBySignature } from '../src/store/index.js';
import { failureSignature } from '../src/parse/recurrence.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const record = (workId: string, rig: string): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig,
    title: `work ${workId}`,
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      closed: '2026-06-05T00:00:00Z',
      status: 'closed',
    },
  });

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-build-store-'));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('buildStoreFromRecords', () => {
  it('persists records into a fresh store readable by queryRecords', () => {
    const path = join(dir, 'nested', 'store.db'); // parent dir must be created
    const count = buildStoreFromRecords(path, [record('b-1', 'rigA'), record('b-2', 'rigB')]);

    expect(count).toBe(2);
    expect(existsSync(path)).toBe(true);

    const db = openStore(path);
    try {
      const ids = queryRecords(db).map(r => r.work_id);
      expect(ids).toEqual(['b-1', 'b-2']); // reader's deterministic ORDER BY work_id
    } finally {
      db.close();
    }
  });

  it('writes an empty store for an empty corpus (no throw)', () => {
    const path = join(dir, 'store.db');
    expect(buildStoreFromRecords(path, [])).toBe(0);
    const db = openStore(path);
    try {
      expect(queryRecords(db)).toEqual([]);
    } finally {
      db.close();
    }
  });
});

/** Assistant entry issuing one Bash tool_use. */
const bashCall = (id: string, command: string): string =>
  JSON.stringify({
    type: 'assistant',
    message: { content: [{ type: 'tool_use', id, name: 'Bash', input: { command } }] },
  });

/** Matching user entry carrying the tool_result + captured output. */
const bashResult = (id: string, stdout: string): string =>
  JSON.stringify({
    type: 'user',
    message: { content: [{ type: 'tool_result', tool_use_id: id, is_error: true }] },
    toolUseResult: { stdout, stderr: '' },
  });

describe('attachAndParse (P1.3 resolve → P1.6 parse)', () => {
  it('lands deterministic trace errors as queryable D8 signatures', () => {
    const rec = WorkRecordSchema.parse({
      work_id: 'w-trace',
      rig: 'mem',
      title: 'work that failed a build',
      lifecycle: {
        created: '2026-06-01T00:00:00Z',
        closed: '2026-06-05T00:00:00Z',
        status: 'closed',
      },
      agents: [{ agent_id: 'gc-999' }],
    });
    const transcript = [
      bashCall('c1', 'tsc --noEmit'),
      bashResult('c1', 'src/a.ts(12,5): error TS2345: bad arg.'),
    ].join('\n');

    // Injected resolver + reader: no `gc` binary, no transcript on disk.
    const parsed = attachAndParse([rec], {
      resolve: sessionId => (sessionId === 'gc-999' ? '/fake/t.jsonl' : null),
      read: path => (path === '/fake/t.jsonl' ? transcript : ''),
    });

    expect(parsed[0].trace?.errors).toHaveLength(1);
    const sig = failureSignature(parsed[0].trace!.errors![0]);

    const path = join(dir, 'store.db');
    buildStoreFromRecords(path, parsed);
    const db = openStore(path);
    try {
      // The signature the `ours` arm keys on is recoverable from the store.
      expect(workIdsBySignature(db, sig)).toEqual(['w-trace']);
    } finally {
      db.close();
    }
  });

  it('leaves records without a resolvable session untouched', () => {
    const rec = WorkRecordSchema.parse({
      work_id: 'w-human',
      rig: 'mem',
      title: 'human-owned',
      lifecycle: { created: '2026-06-01T00:00:00Z', status: 'open' },
      agents: [{ agent_id: 'sjarmak@users.noreply.github.com' }],
    });
    const parsed = attachAndParse([rec], { resolve: () => null, read: () => '' });
    expect(parsed[0].trace).toBeUndefined();
  });
});
