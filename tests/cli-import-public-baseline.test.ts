import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import * as hegel from '@hegeldev/hegel';
import * as gs from '@hegeldev/hegel/generators';
import { afterEach, beforeEach, describe, expect, it, test } from 'vitest';

import {
  importPublicBaselineCommand,
  parsePublicBaselineLines,
  publicBaselineAliasFromWorkId,
  publicBaselineCandidateWorkId,
  publicBaselineQueryWorkId,
  publicBaselineRig,
} from '../src/cli/commands/import-public-baseline.js';
import { queryFromRecord, retrieve } from '../src/retrieve/retrieval.js';
import { lessonsFor, openStore, queryRecords } from '../src/store/index.js';

function releasedRecord(recordId: string, askedAt: string, alias: string, closed: string) {
  return {
    record_id: recordId,
    question: {
      asked_at: askedAt,
      scope_id: recordId,
      text: 'What is the approved rollback command?',
    },
    provenance: { world_id: 'world-shared' },
    candidate_pool: { ids: [alias] },
    loo: {
      boundary: askedAt,
      candidates: [
        {
          id: alias,
          closed,
          convoy_key: null,
          pr_key: null,
          external_ref_key: null,
          parent_key: null,
        },
      ],
      query: {
        id: `k-query-${recordId}`,
        convoy_key: null,
        pr_key: null,
        external_ref_key: null,
        parent_key: null,
      },
    },
    evidence: {
      gold: { [alias]: 'the approved rollback command is helm rollback' },
      distractors: {},
      superseded: {},
    },
  };
}

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-public-baseline-'));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

test('public baseline candidate ids round-trip for every valid released id', () =>
  hegel.test(tc => {
    const recordId = tc.draw(gs.fromRegex('[A-Za-z0-9][A-Za-z0-9._-]{0,30}'));
    const alias = tc.draw(gs.fromRegex('k-[0-9a-f]{1,32}'));
    const workId = publicBaselineCandidateWorkId(recordId, alias);

    expect(publicBaselineAliasFromWorkId(recordId, workId)).toBe(alias);
  }));

describe('parsePublicBaselineLines', () => {
  it('keeps repeated aliases distinct with record-local close times and rigs', () => {
    const input = [
      releasedRecord('record-a', '2020-01-01T00:10:00.000Z', 'k-ab12', '2020-01-01T00:09:00.000Z'),
      releasedRecord('record-b', '2020-01-01T00:20:00.000Z', 'k-ab12', '2020-01-01T00:02:00.000Z'),
    ]
      .map(record => JSON.stringify(record))
      .join('\n');

    const converted = parsePublicBaselineLines(input);
    const candidates = converted.records.filter(record => record.lifecycle.status === 'closed');

    expect(candidates.map(record => record.work_id)).toEqual([
      publicBaselineCandidateWorkId('record-a', 'k-ab12'),
      publicBaselineCandidateWorkId('record-b', 'k-ab12'),
    ]);
    expect(candidates.map(record => record.lifecycle.closed)).toEqual([
      '2020-01-01T00:09:00.000Z',
      '2020-01-01T00:02:00.000Z',
    ]);
    expect(candidates.map(record => record.rig)).toEqual([
      publicBaselineRig('record-a'),
      publicBaselineRig('record-b'),
    ]);
  });

  it('rejects a pool candidate without an eligible close instant', () => {
    const payload = releasedRecord(
      'record-a',
      '2020-01-01T00:10:00.000Z',
      'k-ab12',
      '2020-01-01T00:09:00.000Z'
    );
    payload.loo.candidates[0].closed = null as unknown as string;

    expect(() => parsePublicBaselineLines(JSON.stringify(payload))).toThrow(/k-ab12.*closed/);
  });
});

describe('importPublicBaselineCommand', () => {
  it('imports query records, isolated candidates, and idempotent lesson payloads', async () => {
    const records = [
      releasedRecord('record-a', '2020-01-01T00:10:00.000Z', 'k-ab12', '2020-01-01T00:09:00.000Z'),
      releasedRecord('record-b', '2020-01-01T00:20:00.000Z', 'k-ab12', '2020-01-01T00:02:00.000Z'),
    ];
    const file = join(dir, 'released.jsonl');
    const store = join(dir, 'store.db');
    writeFileSync(file, records.map(record => JSON.stringify(record)).join('\n'));

    const first = await importPublicBaselineCommand({
      args: [],
      options: { json: true, verbose: false, file, store },
    });
    const second = await importPublicBaselineCommand({
      args: [],
      options: { json: true, verbose: false, file, store },
    });

    expect(first).toMatchObject({ queries: 2, candidates: 2, lessons_appended: 2 });
    expect(second).toMatchObject({ queries: 2, candidates: 2, lessons_appended: 0 });

    const db = openStore(store);
    try {
      expect(queryRecords(db)).toHaveLength(4);
      const queryId = publicBaselineQueryWorkId('record-a');
      const result = retrieve(db, queryFromRecord(db, queryId), {
        scope: 'same_rig_temporal',
        limit: 6,
      });
      expect(result.items.map(item => item.work_id)).toEqual([
        publicBaselineCandidateWorkId('record-a', 'k-ab12'),
      ]);
      expect(lessonsFor(db, result.items[0].work_id)[0].payload).toEqual({
        narrative: 'the approved rollback command is helm rollback',
      });
    } finally {
      db.close();
    }
  });

  it('retrieves a record after the global FTS scan exceeds its cap', async () => {
    const records = Array.from({ length: 300 }, (_, index) => {
      const suffix = index.toString().padStart(3, '0');
      return releasedRecord(
        `record-${suffix}`,
        '2020-01-01T00:10:00.000Z',
        `k-${suffix}a`,
        '2020-01-01T00:09:00.000Z'
      );
    });
    const file = join(dir, 'wide-release.jsonl');
    const store = join(dir, 'wide-store.db');
    writeFileSync(file, records.map(record => JSON.stringify(record)).join('\n'));
    await importPublicBaselineCommand({
      args: [],
      options: { json: true, verbose: false, file, store },
    });

    const db = openStore(store);
    try {
      const queryId = publicBaselineQueryWorkId('record-299');
      const result = retrieve(db, queryFromRecord(db, queryId), {
        scope: 'same_rig_temporal',
        limit: 6,
      });
      expect(result.items.map(item => item.work_id)).toEqual([
        publicBaselineCandidateWorkId('record-299', 'k-299a'),
      ]);
    } finally {
      db.close();
    }
  });

  it('refuses a changed reimport before mixing append-only lessons', async () => {
    const original = releasedRecord(
      'record-a',
      '2020-01-01T00:10:00.000Z',
      'k-ab12',
      '2020-01-01T00:09:00.000Z'
    );
    const file = join(dir, 'changed-release.jsonl');
    const store = join(dir, 'changed-store.db');
    writeFileSync(file, JSON.stringify(original));
    await importPublicBaselineCommand({
      args: [],
      options: { json: true, verbose: false, file, store },
    });
    const changed = releasedRecord(
      'record-a',
      '2020-01-01T00:10:00.000Z',
      'k-ab12',
      '2020-01-01T00:09:00.000Z'
    );
    changed.evidence.gold['k-ab12'] = 'the approved rollback command is git revert';
    writeFileSync(file, JSON.stringify(changed));

    await expect(
      importPublicBaselineCommand({
        args: [],
        options: { json: true, verbose: false, file, store },
      })
    ).rejects.toThrow(/changed public baseline namespace/);

    const db = openStore(store);
    try {
      const workId = publicBaselineCandidateWorkId('record-a', 'k-ab12');
      expect(lessonsFor(db, workId).map(lesson => lesson.payload)).toEqual([
        { narrative: 'the approved rollback command is helm rollback' },
      ]);
    } finally {
      db.close();
    }
  });
});
