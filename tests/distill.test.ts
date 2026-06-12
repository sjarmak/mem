import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { distillLessonsCommand } from '../src/cli/commands/distill-lessons.js';
import {
  buildDistillPrompt,
  distillLessons,
  parseDistilledPayload,
  selectCandidates,
} from '../src/distill/distiller.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';
import {
  appendLesson,
  lessonsFor,
  openStore,
  writeRecords,
  type StoreDatabase,
} from '../src/store/index.js';

const tsError = (file = 'src/a.ts') => ({
  tool: 'tsc',
  severity: 'error' as const,
  message: "TS2741: Property 'coverage' is missing in type 'X'",
  file,
  line: 13,
});

const closedRecord = (
  workId: string,
  rig: string,
  overrides: Partial<WorkRecord> = {}
): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig,
    title: `Fix attention contributor ${workId}`,
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      started: '2026-06-01T01:00:00Z',
      closed: '2026-06-05T00:00:00Z',
      status: 'closed',
      status_history: [],
    },
    trace: {
      jsonl_path: `/t/${workId}.jsonl`,
      errors: [tsError()],
      tool_outcomes: [{ runner: 'tsc', command: 'npm run typecheck', status: 'pass', errors: [] }],
    },
    ...overrides,
  });

const validLessonJson = JSON.stringify({
  subtitle: 'AttentionContributor requires coverage',
  facts: ['Adding a contributor requires the coverage field on every test fixture.'],
  narrative: 'TS2741 fired on test fixtures missing the new required field.',
  concepts: ['gotcha'],
});

let dir: string;
let db: StoreDatabase;
let storeFile: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-distill-'));
  storeFile = join(dir, 'store.db');
  db = openStore(storeFile);
});

afterEach(() => {
  db.close();
  rmSync(dir, { recursive: true, force: true });
});

describe('selectCandidates', () => {
  it('picks closed records with trace errors, skipping already-lessoned ones', () => {
    writeRecords(db, [
      closedRecord('w-err', 'rigA'),
      closedRecord('w-clean', 'rigA', {
        trace: { jsonl_path: '/t/w-clean.jsonl', errors: [] },
      }),
      closedRecord('w-open', 'rigA', {
        lifecycle: {
          created: '2026-06-01T00:00:00Z',
          started: '2026-06-01T01:00:00Z',
          status: 'open',
          status_history: [],
        },
      }),
      closedRecord('w-lessoned', 'rigA'),
    ]);
    appendLesson(db, {
      work_id: 'w-lessoned',
      extracted_at: '2026-06-10T00:00:00Z',
      payload: { subtitle: 'already there' },
    });

    const picked = selectCandidates(db, {}).map(r => r.work_id);
    expect(picked).toEqual(['w-err']);
  });

  it('narrows by rig, explicit work_ids, and limit; --force re-admits lessoned records', () => {
    writeRecords(db, [
      closedRecord('a-1', 'rigA'),
      closedRecord('a-2', 'rigA'),
      closedRecord('b-1', 'rigB'),
    ]);
    appendLesson(db, {
      work_id: 'a-1',
      extracted_at: '2026-06-10T00:00:00Z',
      payload: { subtitle: 's' },
    });

    expect(selectCandidates(db, { rig: 'rigB' }).map(r => r.work_id)).toEqual(['b-1']);
    expect(selectCandidates(db, { workIds: ['a-2', 'b-1'], limit: 1 }).map(r => r.work_id)).toEqual(
      ['a-2']
    );
    expect(selectCandidates(db, { rig: 'rigA', force: true }).map(r => r.work_id)).toEqual([
      'a-1',
      'a-2',
    ]);
  });
});

describe('buildDistillPrompt', () => {
  it('carries the evidence and the payload contract', () => {
    const prompt = buildDistillPrompt(closedRecord('w-1', 'rigA'));
    expect(prompt).toContain('w-1');
    expect(prompt).toContain('Fix attention contributor w-1');
    expect(prompt).toContain('[tsc] src/a.ts:13');
    expect(prompt).toContain('tsc:pass');
    expect(prompt).toContain('"subtitle"');
    expect(prompt).toContain('gotcha');
  });

  it('caps the error list and says how many were omitted', () => {
    const errors = Array.from({ length: 25 }, (_, i) => tsError(`src/f${i}.ts`));
    const prompt = buildDistillPrompt(
      closedRecord('w-many', 'rigA', { trace: { jsonl_path: '/t/x.jsonl', errors } })
    );
    expect(prompt).toContain('src/f19.ts');
    expect(prompt).not.toContain('src/f20.ts');
    expect(prompt).toContain('(5 further errors omitted)');
  });
});

describe('parseDistilledPayload', () => {
  it('accepts plain JSON and fenced JSON', () => {
    expect(parseDistilledPayload(validLessonJson).subtitle).toContain('AttentionContributor');
    expect(parseDistilledPayload('```json\n' + validLessonJson + '\n```').facts).toHaveLength(1);
  });

  it('rejects missing required fields, unknown concept tags, and prose', () => {
    expect(() => parseDistilledPayload('{"subtitle":"x"}')).toThrow();
    expect(() =>
      parseDistilledPayload(
        JSON.stringify({ subtitle: 'x', facts: ['f'], narrative: 'n', concepts: ['vibes'] })
      )
    ).toThrow();
    expect(() => parseDistilledPayload('Here is the lesson: ...')).toThrow();
  });
});

describe('distillLessons', () => {
  it('produces import-ready lessons and records per-record failures without aborting', () => {
    const records = [
      closedRecord('w-good', 'rigA', {
        outcome: { commit_sha: 'abc123' },
      }),
      closedRecord('w-bad', 'rigA'),
    ];
    const runner = (prompt: string): string =>
      prompt.includes('w-bad') ? 'not json at all' : validLessonJson;

    const outcome = distillLessons(records, runner, '2026-06-12T00:00:00Z');
    expect(outcome.lessons).toHaveLength(1);
    expect(outcome.lessons[0]).toMatchObject({
      work_id: 'w-good',
      extracted_at: '2026-06-12T00:00:00Z',
      commit_sha: 'abc123',
    });
    expect(outcome.failures).toHaveLength(1);
    expect(outcome.failures[0].work_id).toBe('w-bad');
    expect(outcome.failures[0].error).toContain('JSON');
  });
});

describe('distillLessonsCommand', () => {
  const ctx = (options: Record<string, unknown>) => ({
    args: [],
    options: { json: true, verbose: false, store: storeFile, ...options } as never,
  });

  it('distills, writes NDJSON, and imports into the store', () => {
    writeRecords(db, [closedRecord('w-1', 'rigA')]);
    const outFile = join(dir, 'lessons.jsonl');

    const result = distillLessonsCommand(
      ctx({ out: outFile, import: true }),
      () => validLessonJson
    );

    expect(result).toMatchObject({
      candidates: 1,
      distilled: 1,
      failures: [],
      out: outFile,
      imported: { appended: 1, skipped: 0 },
    });
    const line = JSON.parse(readFileSync(outFile, 'utf8').trim()) as { work_id: string };
    expect(line.work_id).toBe('w-1');
    expect(lessonsFor(db, 'w-1')).toHaveLength(1);
  });

  it('is idempotent across reruns: lessoned records are no longer candidates', () => {
    writeRecords(db, [closedRecord('w-1', 'rigA')]);
    distillLessonsCommand(ctx({ import: true }), () => validLessonJson);

    const rerun = distillLessonsCommand(ctx({ import: true }), () => validLessonJson);
    expect(rerun.candidates).toBe(0);
    expect(lessonsFor(db, 'w-1')).toHaveLength(1);
  });

  it('refuses to run with neither --out nor --import', () => {
    expect(() => distillLessonsCommand(ctx({}), () => validLessonJson)).toThrow(/nothing to do/);
  });

  it('rejects a non-numeric --limit', () => {
    expect(() =>
      distillLessonsCommand(ctx({ import: true, limit: 'lots' }), () => validLessonJson)
    ).toThrow(/--limit/);
  });
});
