import { describe, expect, it } from 'vitest';

import {
  checkPriorFixRegression,
  recordSignatures,
  verifyFixEvidence,
  type RegressionCandidate,
} from '../src/distill/verify.js';
import type { ResolutionEvidence } from '../src/distill/distiller.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const tsError = (file = 'src/a.ts', line = 13) => ({
  tool: 'tsc',
  severity: 'error' as const,
  message: "TS2741: Property 'coverage' is missing in type 'X'",
  file,
  line,
});

const record = (workId: string, overrides: Partial<WorkRecord> = {}): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig: 'rigA',
    title: `Fix ${workId}`,
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

describe('verifyFixEvidence', () => {
  it('refuses admission when there is no resolution evidence', () => {
    const check = verifyFixEvidence(null);
    if (check.admitted) throw new Error('expected refusal');
    expect(check.reason).toContain('no-resolution-evidence');
  });

  it('admits a landed-diff-backed candidate, carrying the narrowed evidence', () => {
    const evidence: ResolutionEvidence = {
      kind: 'landed-diff',
      commit_sha: 'abc1234',
      text: 'diff',
      truncated: false,
      total_chars: 4,
    };
    expect(verifyFixEvidence(evidence)).toEqual({ admitted: true, evidence });
  });

  it('admits a transcript-tail-backed candidate, carrying the narrowed evidence', () => {
    const evidence: ResolutionEvidence = {
      kind: 'transcript-tail',
      text: 'fixed it',
      truncated: false,
      total_chars: 8,
    };
    expect(verifyFixEvidence(evidence)).toEqual({ admitted: true, evidence });
  });
});

describe('recordSignatures', () => {
  it('extracts distinct Decision-8 signatures from trace errors', () => {
    const r = record('w-1', {
      trace: {
        jsonl_path: '/t/w-1.jsonl',
        errors: [tsError('src/a.ts', 13), tsError('src/a.ts', 13), tsError('src/b.ts', 7)],
      },
    });
    expect(recordSignatures(r)).toEqual(['tsc:src/a.ts:13:TS2741', 'tsc:src/b.ts:7:TS2741']);
  });

  it('returns an empty array when the record has no trace errors', () => {
    expect(
      recordSignatures(record('w-1', { trace: { jsonl_path: '/t/w-1.jsonl', errors: [] } }))
    ).toEqual([]);
  });
});

describe('checkPriorFixRegression', () => {
  const candidate = (workId: string, isSibling: boolean): RegressionCandidate => ({
    work_id: workId,
    is_sibling: isSibling,
  });

  it('flags a signature that recurred in a later, non-sibling record', () => {
    const flags = checkPriorFixRegression(
      'w-lesson',
      '2026-06-10T00:00:00Z',
      new Map([['tsc:src/a.ts:13:TS2741', [candidate('w-later', false)]]])
    );
    expect(flags).toEqual([
      {
        lesson_work_id: 'w-lesson',
        extracted_at: '2026-06-10T00:00:00Z',
        signature: 'tsc:src/a.ts:13:TS2741',
        recurred_in: ['w-later'],
      },
    ]);
  });

  it('does NOT flag a convoy/PR/parent/supersedes sibling recurrence (expected iteration, not a regression)', () => {
    const flags = checkPriorFixRegression(
      'w-lesson',
      '2026-06-10T00:00:00Z',
      new Map([['tsc:src/a.ts:13:TS2741', [candidate('w-followup', true)]]])
    );
    expect(flags).toEqual([]);
  });

  it('does not flag the lesson-source record against itself', () => {
    const flags = checkPriorFixRegression(
      'w-lesson',
      '2026-06-10T00:00:00Z',
      new Map([['tsc:src/a.ts:13:TS2741', [candidate('w-lesson', false)]]])
    );
    expect(flags).toEqual([]);
  });

  it('returns nothing when no candidates recurred for any signature', () => {
    expect(checkPriorFixRegression('w-lesson', '2026-06-10T00:00:00Z', new Map())).toEqual([]);
  });

  it('mixes flagged and unflagged signatures within one lesson independently', () => {
    const flags = checkPriorFixRegression(
      'w-lesson',
      '2026-06-10T00:00:00Z',
      new Map([
        ['sig-recurred', [candidate('w-a', false), candidate('w-b', true)]],
        ['sig-only-siblings', [candidate('w-c', true)]],
      ])
    );
    expect(flags).toEqual([
      {
        lesson_work_id: 'w-lesson',
        extracted_at: '2026-06-10T00:00:00Z',
        signature: 'sig-recurred',
        recurred_in: ['w-a'],
      },
    ]);
  });
});
