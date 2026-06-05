import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { appendLesson, openStore, writeRecords, type StoreDatabase } from '../src/store/index.js';
import {
  RetrievalQuerySchema,
  queryFromRecord,
  retrieve,
  type RetrievalQuery,
} from '../src/retrieve/index.js';
import type { TraceError } from '../src/schemas/trace.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

/** The canonical query failure: signature `tsc:src/a.ts:12:TS2345`. */
const tsError = (overrides: Partial<TraceError> = {}): TraceError => ({
  tool: 'tsc',
  severity: 'error',
  message: 'TS2345: bad argument',
  file: 'src/a.ts',
  line: 12,
  column: 5,
  ...overrides,
});

/** A closed prior record carrying one parseable error — the retrievable shape. */
const priorRecord = (
  workId: string,
  rig: string,
  overrides: Partial<WorkRecord> = {}
): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig,
    title: `Prior work ${workId}`,
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      started: '2026-06-01T01:00:00Z',
      closed: '2026-06-05T00:00:00Z',
      status: 'closed',
      status_history: [],
    },
    trace: { jsonl_path: `/t/${workId}.jsonl`, errors: [tsError()] },
    outcome: { pr: `#${workId}`, pr_state: 'merged', commit_sha: `sha-${workId}`, ci: 'pass' },
    ...overrides,
  });

/** The query work context: rigA, started 06-10, hit the canonical failure. */
const baseQuery = (overrides: Partial<RetrievalQuery> = {}): RetrievalQuery => ({
  work_id: 'rigA-b',
  rig: 'rigA',
  started: '2026-06-10T00:00:00Z',
  errors: [tsError()],
  ...overrides,
});

let dir: string;
let db: StoreDatabase;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-retrieve-'));
  db = openStore(join(dir, 'store.db'));
});

afterEach(() => {
  db.close();
  rmSync(dir, { recursive: true, force: true });
});

describe('retrieve — D6 temporal leave-one-out', () => {
  it('returns records closed strictly before the query boundary', () => {
    writeRecords(db, [priorRecord('rigA-old', 'rigA')]);

    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    expect(result.items.map(i => i.work_id)).toEqual(['rigA-old']);
  });

  it('excludes records closed at or after the boundary, and never-closed records', () => {
    writeRecords(db, [
      priorRecord('rigA-at', 'rigA', {
        lifecycle: {
          created: '2026-06-01T00:00:00Z',
          closed: '2026-06-10T00:00:00Z', // exactly at boundary — strict <
          status: 'closed',
          status_history: [],
        },
      }),
      priorRecord('rigA-late', 'rigA', {
        lifecycle: {
          created: '2026-06-01T00:00:00Z',
          closed: '2026-06-11T00:00:00Z',
          status: 'closed',
          status_history: [],
        },
      }),
      priorRecord('rigA-open', 'rigA', {
        lifecycle: { created: '2026-06-01T00:00:00Z', status: 'open', status_history: [] },
      }),
    ]);

    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    expect(result.items).toEqual([]);
    expect(result.total_matched).toBe(0);
  });

  it('never retrieves the query work itself', () => {
    writeRecords(db, [priorRecord('rigA-old', 'rigA')]);

    // A live re-run of old work: the stored record satisfies the temporal
    // boundary but is the query work — self-exclusion must catch it.
    const result = retrieve(db, baseQuery({ work_id: 'rigA-old' }), {
      scope: 'same_rig_temporal',
    });

    expect(result.items).toEqual([]);
  });

  it('excludes convoy siblings of the query work', () => {
    writeRecords(db, [
      priorRecord('rigA-convoy', 'rigA', {
        links: { deps: [], convoy_id: 'c1', supersedes: [] },
      }),
      priorRecord('rigA-other', 'rigA'),
    ]);

    const result = retrieve(db, baseQuery({ convoy_id: 'c1' }), {
      scope: 'same_rig_temporal',
    });

    expect(result.items.map(i => i.work_id)).toEqual(['rigA-other']);
  });

  it('excludes records sharing the query work PR or branch (external_ref)', () => {
    writeRecords(db, [
      priorRecord('rigA-prsib', 'rigA', {
        outcome: { pr: '#77', pr_state: 'merged', commit_sha: 'x', ci: 'pass' },
      }),
      priorRecord('rigA-branchsib', 'rigA', { external_ref: 'feat/x' }),
      priorRecord('rigA-clean', 'rigA'),
    ]);

    const result = retrieve(db, baseQuery({ pr: '#77', external_ref: 'feat/x' }), {
      scope: 'same_rig_temporal',
    });

    expect(result.items.map(i => i.work_id)).toEqual(['rigA-clean']);
  });

  it('does not treat absent pr/external_ref as shared (NULL-safe sibling match)', () => {
    // Neither the query nor the record names a PR/branch — absence must not match.
    writeRecords(db, [priorRecord('rigA-nopr', 'rigA', { outcome: undefined })]);

    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    expect(result.items.map(i => i.work_id)).toEqual(['rigA-nopr']);
  });

  it('excludes the full supersedes chain in both directions, multi-hop', () => {
    writeRecords(db, [
      // ancestors: query work -> old1 -> old0
      WorkRecordSchema.parse({
        ...priorRecord('rigA-b', 'rigA'),
        links: { deps: [], supersedes: ['rigA-old1'] },
      }),
      priorRecord('rigA-old1', 'rigA', {
        links: { deps: [], supersedes: ['rigA-old0'] },
      }),
      priorRecord('rigA-old0', 'rigA'),
      // descendant: new supersedes the query work
      priorRecord('rigA-new', 'rigA', {
        links: { deps: [], supersedes: ['rigA-b'] },
      }),
      // unrelated chain stays retrievable
      priorRecord('rigA-free', 'rigA'),
    ]);

    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    expect(result.items.map(i => i.work_id)).toEqual(['rigA-free']);
  });
});

describe('retrieve — D7 dual-track scope', () => {
  beforeEach(() => {
    writeRecords(db, [priorRecord('rigA-old', 'rigA'), priorRecord('rigB-old', 'rigB')]);
  });

  it('cross_rig returns only other-rig records', () => {
    const result = retrieve(db, baseQuery(), { scope: 'cross_rig' });

    expect(result.scope).toBe('cross_rig');
    expect(result.items.map(i => i.work_id)).toEqual(['rigB-old']);
  });

  it('same_rig_temporal returns only same-rig records', () => {
    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    expect(result.scope).toBe('same_rig_temporal');
    expect(result.items.map(i => i.work_id)).toEqual(['rigA-old']);
  });

  it('cross_rig still honors the temporal boundary', () => {
    writeRecords(db, [
      priorRecord('rigB-late', 'rigB', {
        lifecycle: {
          created: '2026-06-01T00:00:00Z',
          closed: '2026-06-11T00:00:00Z',
          status: 'closed',
          status_history: [],
        },
      }),
    ]);

    const result = retrieve(db, baseQuery(), { scope: 'cross_rig' });

    expect(result.items.map(i => i.work_id)).toEqual(['rigB-old']);
  });
});

describe('retrieve — D8 failure-triggered matching and ranking', () => {
  it('returns nothing when the query carries no errors (no trigger)', () => {
    writeRecords(db, [priorRecord('rigB-old', 'rigB')]);

    const result = retrieve(db, baseQuery({ errors: [] }), { scope: 'cross_rig' });

    expect(result.trigger_count).toBe(0);
    expect(result.items).toEqual([]);
  });

  it('ranks exact signature above error-class above message-only matches', () => {
    writeRecords(db, [
      // message-only: different tool/class, shares "bad argument" tokens
      priorRecord('rigB-msg', 'rigB', {
        trace: {
          jsonl_path: '/t/m.jsonl',
          errors: [
            {
              tool: 'pytest',
              severity: 'error',
              message: 'assertion failed: bad argument shape',
              file: 'tests/test_x.py',
              line: 3,
            },
          ],
        },
      }),
      // class match: same tool + TS2345, different file/line
      priorRecord('rigB-class', 'rigB', {
        trace: {
          jsonl_path: '/t/c.jsonl',
          errors: [tsError({ file: 'lib/z.ts', line: 99, message: 'TS2345: other text' })],
        },
      }),
      // exact signature match
      priorRecord('rigB-exact', 'rigB'),
    ]);

    const result = retrieve(db, baseQuery(), { scope: 'cross_rig' });

    expect(result.items.map(i => [i.work_id, i.match])).toEqual([
      ['rigB-exact', 'signature'],
      ['rigB-class', 'error_class'],
      ['rigB-msg', 'message'],
    ]);
  });

  it('reports which query signatures and classes each item matched', () => {
    writeRecords(db, [
      priorRecord('rigB-exact', 'rigB'),
      priorRecord('rigB-class', 'rigB', {
        trace: {
          jsonl_path: '/t/c.jsonl',
          errors: [tsError({ file: 'lib/z.ts', line: 99 })],
        },
      }),
    ]);

    const result = retrieve(db, baseQuery(), { scope: 'cross_rig' });

    const exact = result.items.find(i => i.work_id === 'rigB-exact');
    expect(exact?.matched_signatures).toEqual(['tsc:src/a.ts:12:TS2345']);
    const cls = result.items.find(i => i.work_id === 'rigB-class');
    expect(cls?.matched_signatures).toEqual([]);
    expect(cls?.matched_classes).toEqual(['tsc:TS2345']);
  });

  it('breaks ties by work_id when no message tokens exist to rank on', () => {
    // Empty messages: no FTS query is possible, so the final explicit
    // tiebreaker (work_id ascending) decides the order.
    const bare = (id: string): WorkRecord =>
      priorRecord(id, 'rigB', {
        trace: {
          jsonl_path: `/t/${id}.jsonl`,
          errors: [{ tool: 'make', severity: 'error', message: '', file: 'Makefile', line: 1 }],
        },
      });
    writeRecords(db, [bare('rigB-tie2'), bare('rigB-tie1')]);

    const result = retrieve(
      db,
      baseQuery({
        errors: [{ tool: 'make', severity: 'error', message: '', file: 'Makefile', line: 1 }],
      }),
      { scope: 'cross_rig' }
    );

    expect(result.items.map(i => i.work_id)).toEqual(['rigB-tie1', 'rigB-tie2']);
  });

  it('is deterministic run-to-run', () => {
    writeRecords(db, [
      priorRecord('rigB-1', 'rigB'),
      priorRecord('rigB-2', 'rigB', {
        trace: { jsonl_path: '/t/2.jsonl', errors: [tsError({ line: 99 })] },
      }),
    ]);

    const a = retrieve(db, baseQuery(), { scope: 'cross_rig' });
    const b = retrieve(db, baseQuery(), { scope: 'cross_rig' });

    expect(a).toEqual(b);
    expect(a.fts_truncated).toBe(false);
  });

  it('flags FTS truncation instead of silently capping the message tier', () => {
    const records = Array.from({ length: 300 }, (_, i) =>
      priorRecord(`rigB-${String(i).padStart(3, '0')}`, 'rigB')
    );
    writeRecords(db, records);

    const result = retrieve(db, baseQuery(), { scope: 'cross_rig' });

    expect(result.fts_truncated).toBe(true);
    expect(result.total_matched).toBe(300);
  });

  it('caps items at the limit but reports the full match count', () => {
    const records = Array.from({ length: 15 }, (_, i) =>
      priorRecord(`rigB-${String(i).padStart(2, '0')}`, 'rigB')
    );
    writeRecords(db, records);

    const capped = retrieve(db, baseQuery(), { scope: 'cross_rig' });
    expect(capped.items).toHaveLength(10); // documented default
    expect(capped.total_matched).toBe(15);

    const wide = retrieve(db, baseQuery(), { scope: 'cross_rig', limit: 15 });
    expect(wide.items).toHaveLength(15);
  });
});

describe('retrieve — D9 payload', () => {
  beforeEach(() => {
    writeRecords(db, [priorRecord('rigA-old', 'rigA')]);
    appendLesson(db, {
      work_id: 'rigA-old',
      extracted_at: '2026-06-05T01:00:00Z',
      commit_sha: 'sha-rigA-old',
      payload: { root_cause: 'missing flag', resolution: 'add --flag' },
    });
    appendLesson(db, {
      work_id: 'rigA-old',
      extracted_at: '2026-06-05T02:00:00Z',
      payload: { root_cause: 'second pass' },
    });
  });

  it('attaches stored lessons in append order with the citation', () => {
    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    const item = result.items[0];
    expect(item.lessons.map(l => l.payload['root_cause'])).toEqual(['missing flag', 'second pass']);
    expect(item.citation).toEqual({
      work_id: 'rigA-old',
      commit_sha: 'sha-rigA-old',
      pr: '#rigA-old',
    });
  });

  it('attaches literal file:line refs on the same-rig track only', () => {
    const sameRig = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });
    expect(sameRig.items[0].literal).toEqual([{ file: 'src/a.ts', line: 12 }]);

    writeRecords(db, [priorRecord('rigB-old', 'rigB')]);
    const crossRig = retrieve(db, baseQuery(), { scope: 'cross_rig' });
    expect(crossRig.items[0].literal).toBeUndefined();
  });

  it('never injects the prior trace: items carry no trace or message fields', () => {
    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    const item = result.items[0] as unknown as Record<string, unknown>;
    expect(item['trace']).toBeUndefined();
    expect(JSON.stringify(item)).not.toContain('bad argument');
  });
});

describe('retrieve — D6 duplicate audit flag', () => {
  it('flags a top exact-signature neighbor as a near-duplicate', () => {
    writeRecords(db, [priorRecord('rigA-old', 'rigA')]);

    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    expect(result.near_duplicate_top).toBe(true);
  });

  it('does not flag class- or message-tier top matches', () => {
    writeRecords(db, [
      priorRecord('rigA-class', 'rigA', {
        trace: { jsonl_path: '/t/c.jsonl', errors: [tsError({ line: 99 })] },
      }),
    ]);

    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    expect(result.items).toHaveLength(1);
    expect(result.near_duplicate_top).toBe(false);
  });

  it('does not flag an empty result', () => {
    const result = retrieve(db, baseQuery(), { scope: 'same_rig_temporal' });

    expect(result.near_duplicate_top).toBe(false);
  });
});

describe('queryFromRecord', () => {
  it('builds the query context from a stored record', () => {
    writeRecords(db, [
      priorRecord('rigA-b', 'rigA', {
        external_ref: 'feat/x',
        links: { deps: [], convoy_id: 'c1', supersedes: [] },
      }),
    ]);

    const query = queryFromRecord(db, 'rigA-b');

    expect(query).toEqual({
      work_id: 'rigA-b',
      rig: 'rigA',
      started: '2026-06-01T01:00:00Z',
      errors: [tsError()],
      convoy_id: 'c1',
      pr: '#rigA-b',
      external_ref: 'feat/x',
    });
  });

  it('falls back to created when the record never started (earlier = leak-safe)', () => {
    writeRecords(db, [
      priorRecord('rigA-b', 'rigA', {
        lifecycle: {
          created: '2026-06-01T00:00:00Z',
          closed: '2026-06-05T00:00:00Z',
          status: 'closed',
          status_history: [],
        },
      }),
    ]);

    expect(queryFromRecord(db, 'rigA-b').started).toBe('2026-06-01T00:00:00Z');
  });

  it('throws on an unknown work_id', () => {
    expect(() => queryFromRecord(db, 'nope')).toThrow(/nope/);
  });
});

describe('RetrievalQuerySchema', () => {
  it('accepts a minimal query and defaults errors to empty', () => {
    const parsed = RetrievalQuerySchema.parse({
      work_id: 'w1',
      rig: 'r1',
      started: '2026-06-10T00:00:00Z',
    });

    expect(parsed.errors).toEqual([]);
  });

  it('rejects a query without a temporal boundary', () => {
    expect(() => RetrievalQuerySchema.parse({ work_id: 'w1', rig: 'r1' })).toThrow();
  });
});
