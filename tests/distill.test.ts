import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { distillLessonsCommand } from '../src/cli/commands/distill-lessons.js';
import * as distiller from '../src/distill/distiller.js';
import {
  buildDistillPrompt,
  computeRegressions,
  distillLessons,
  parseDistilledPayload,
  resolveResolutionEvidence,
  selectCandidates,
  type ResolutionEvidence,
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

/** The `computeRegressions` tests below all need one closed record that
 * postdates `closedRecord`'s default `closed: '2026-06-05'` — this fixed
 * lifecycle is that "later" fixture. */
const LATER_LIFECYCLE: WorkRecord['lifecycle'] = {
  created: '2026-06-06T00:00:00Z',
  started: '2026-06-06T01:00:00Z',
  closed: '2026-06-07T00:00:00Z',
  status: 'closed',
  status_history: [],
};

const laterClosedRecord = (
  workId: string,
  rig: string,
  overrides: Partial<WorkRecord> = {}
): WorkRecord => closedRecord(workId, rig, { ...overrides, lifecycle: LATER_LIFECYCLE });

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
    const prompt = buildDistillPrompt(closedRecord('w-1', 'rigA'), null);
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
      closedRecord('w-many', 'rigA', { trace: { jsonl_path: '/t/x.jsonl', errors } }),
      null
    );
    expect(prompt).toContain('src/f19.ts');
    expect(prompt).not.toContain('src/f20.ts');
    expect(prompt).toContain('(5 further errors omitted)');
  });

  it('embeds a landed diff, its commit, and the truncation note', () => {
    const evidence: ResolutionEvidence = {
      kind: 'landed-diff',
      commit_sha: 'abc1234',
      text: 'diff --git a/src/a.ts b/src/a.ts\n+  coverage: 1,',
      truncated: true,
      total_chars: 9_000,
    };
    const prompt = buildDistillPrompt(closedRecord('w-1', 'rigA'), evidence);
    expect(prompt).toContain('Resolution evidence — the landed diff for commit abc1234:');
    expect(prompt).toContain('+  coverage: 1,');
    expect(prompt).toContain('of 9000 characters; later hunks omitted)');
    expect(prompt).toContain('what actually resolved it');
  });

  it('embeds a transcript tail with its truncation note', () => {
    const evidence: ResolutionEvidence = {
      kind: 'transcript-tail',
      text: 'added the coverage field to every fixture',
      truncated: true,
      total_chars: 8_000,
    };
    const prompt = buildDistillPrompt(closedRecord('w-1', 'rigA'), evidence);
    expect(prompt).toContain(
      'Resolution evidence — transcript tail from the last recorded failure to session close:'
    );
    expect(prompt).toContain('added the coverage field to every fixture');
    expect(prompt).toContain('of 8000 characters)');
  });

  it('states plainly when no resolution evidence exists and forbids guessing', () => {
    const prompt = buildDistillPrompt(closedRecord('w-1', 'rigA'), null);
    expect(prompt).toContain('Resolution evidence: none available');
    expect(prompt).toContain('say so instead of guessing');
    expect(prompt).not.toContain('landed diff for commit');
  });
});

describe('resolveResolutionEvidence', () => {
  const sha = 'abc1234def';
  const withCommit = (workId: string) =>
    closedRecord(workId, 'rigA', {
      outcome: { commit_sha: sha },
      provenance: { work_dir: '/repo/checkout', repo: 'checkout', history_state: 'unresolved' },
    });

  it('prefers the landed diff, shown from the provenance work_dir', () => {
    const calls: { workDir: string; args: string[] }[] = [];
    const evidence = resolveResolutionEvidence(withCommit('w-1'), {
      run: (workDir, args) => {
        calls.push({ workDir, args });
        return 'diff --git a/src/a.ts b/src/a.ts\n+  coverage: 1,\n';
      },
    });
    expect(evidence).toMatchObject({ kind: 'landed-diff', commit_sha: sha, truncated: false });
    expect(evidence?.text).toContain('+  coverage: 1,');
    expect(calls).toHaveLength(1);
    expect(calls[0].workDir).toBe('/repo/checkout');
    expect(calls[0].args).toContain('show');
    // The DB-sourced sha is pinned as a revision, never parseable as a flag.
    expect(calls[0].args.slice(-2)).toEqual(['--end-of-options', sha]);
  });

  it('head-truncates an oversized diff at a line boundary and reports totals', () => {
    const header = 'diff --git a/src/a.ts b/src/a.ts';
    const diff = `${header}\n${'+ a line of added code padding out\n'.repeat(400)}`;
    const evidence = resolveResolutionEvidence(withCommit('w-1'), { run: () => diff });
    expect(evidence).toMatchObject({ kind: 'landed-diff', truncated: true });
    expect(evidence?.text.startsWith(header)).toBe(true);
    expect(evidence?.text.length).toBeLessThanOrEqual(6_000);
    expect(evidence?.text.endsWith('padding out')).toBe(true); // cut on a line boundary
    if (evidence?.kind === 'landed-diff') expect(evidence.total_chars).toBe(diff.length);
  });

  it('falls back to the transcript tail when git cannot show the sha', () => {
    const evidence = resolveResolutionEvidence(withCommit('w-1'), {
      run: () => {
        throw Object.assign(new Error('bad object'), { status: 128 });
      },
      readTranscript: () => 'early noise\nran tsc, saw the failure\nfixed it by adding coverage\n',
    });
    expect(evidence?.kind).toBe('transcript-tail');
    expect(evidence?.text).toContain('fixed it by adding coverage');
  });

  it('starts the transcript tail at the LAST recorded failure, matching escaped JSONL', () => {
    const msg = tsError().message; // contains single quotes; also test a double-quoted one
    const record = closedRecord('w-1', 'rigA', {
      trace: {
        jsonl_path: '/t/w-1.jsonl',
        errors: [tsError(), { ...tsError(), message: 'cannot find "coverage" export' }],
      },
    });
    const content =
      `{"text":"early attempt: ${msg}"}\n` +
      `{"text":"retry: cannot find \\"coverage\\" export"}\n` +
      `{"text":"added the field, tsc clean"}\n`;
    const evidence = resolveResolutionEvidence(record, { readTranscript: () => content });
    expect(evidence?.kind).toBe('transcript-tail');
    expect(evidence?.text).not.toContain('early attempt');
    // Starts AT the escaped occurrence of the newest error, not the older raw one.
    expect(evidence?.text.startsWith('cannot find \\"coverage\\" export')).toBe(true);
    expect(evidence?.text).toContain('added the field, tsc clean');
  });

  it('tail-truncates an oversized transcript slice, keeping the close', () => {
    const content = `${'a filler transcript line\n'.repeat(500)}the final fix line\n`;
    const evidence = resolveResolutionEvidence(closedRecord('w-1', 'rigA'), {
      readTranscript: () => content,
    });
    expect(evidence).toMatchObject({ kind: 'transcript-tail', truncated: true });
    expect(evidence?.text.length).toBeLessThanOrEqual(6_000);
    expect(evidence?.text).toContain('the final fix line');
  });

  it('returns null when there is no commit and the transcript is unreadable', () => {
    const evidence = resolveResolutionEvidence(closedRecord('w-1', 'rigA'), {
      readTranscript: () => {
        throw Object.assign(new Error('ENOENT: no such file'), { code: 'ENOENT' });
      },
    });
    expect(evidence).toBeNull();
  });

  it('returns null for a commit_sha with no resolvable work_dir and no trace', () => {
    const record = closedRecord('w-1', 'unmapped-rig', { outcome: { commit_sha: sha } });
    const evidence = resolveResolutionEvidence(
      { ...record, trace: undefined },
      {
        run: () => {
          throw new Error('git must not be called without a work_dir');
        },
      }
    );
    expect(evidence).toBeNull();
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

    // Both records need resolution evidence to clear the mem-0r7l gate before
    // the JSON-validity failure (isolated to w-bad) can be observed.
    const outcome = distillLessons(records, runner, '2026-06-12T00:00:00Z', {
      readTranscript: () => 'fixed by adding the coverage field',
    });
    expect(outcome.lessons).toHaveLength(1);
    expect(outcome.lessons[0]).toMatchObject({
      work_id: 'w-good',
      extracted_at: '2026-06-12T00:00:00Z',
      commit_sha: 'abc123',
    });
    expect(outcome.lessons[0].payload.evidence_kind).toBe('transcript-tail');
    expect(outcome.failures).toHaveLength(1);
    expect(outcome.failures[0].work_id).toBe('w-bad');
    expect(outcome.failures[0].error).toContain('JSON');
  });

  it('refuses admission (and never calls the runner) for a candidate with no resolution evidence', () => {
    let runnerCalls = 0;
    const outcome = distillLessons(
      [closedRecord('w-unverifiable', 'rigA')],
      () => {
        runnerCalls++;
        return validLessonJson;
      },
      '2026-06-12T00:00:00Z',
      {
        readTranscript: () => {
          throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' });
        },
      }
    );
    expect(outcome.lessons).toHaveLength(0);
    expect(outcome.failures).toHaveLength(1);
    expect(outcome.failures[0].work_id).toBe('w-unverifiable');
    expect(outcome.failures[0].error).toContain('no-resolution-evidence');
    expect(runnerCalls).toBe(0);
  });

  it('stamps evidence_kind: landed-diff for a git-verified candidate', () => {
    const record = closedRecord('w-1', 'rigA', {
      outcome: { commit_sha: 'abc123' },
      provenance: { work_dir: '/repo/checkout', repo: 'checkout', history_state: 'unresolved' },
    });
    const outcome = distillLessons([record], () => validLessonJson, '2026-06-12T00:00:00Z', {
      run: () => 'diff --git a/src/a.ts b/src/a.ts\n+  coverage: 1,\n',
    });
    expect(outcome.lessons[0].payload.evidence_kind).toBe('landed-diff');
  });

  it('does not reject an otherwise-valid lesson when the model emits its own evidence_kind (mechanically overwritten, never validated)', () => {
    const modelJson = JSON.stringify({
      subtitle: 'AttentionContributor requires coverage',
      facts: ['Adding a contributor requires the coverage field on every test fixture.'],
      narrative: 'TS2741 fired on test fixtures missing the new required field.',
      concepts: ['gotcha'],
      // Not one of the two real evidence_kind values — must not fail parsing,
      // since the code always overwrites this field after parsing anyway.
      evidence_kind: 'diff',
    });
    const outcome = distillLessons(
      [closedRecord('w-1', 'rigA')],
      () => modelJson,
      '2026-06-12T00:00:00Z',
      { readTranscript: () => 'fixed by adding the coverage field' }
    );
    expect(outcome.failures).toEqual([]);
    expect(outcome.lessons).toHaveLength(1);
    expect(outcome.lessons[0].payload.evidence_kind).toBe('transcript-tail');
  });

  it('threads resolution evidence into the prompt the runner sees', () => {
    const prompts: string[] = [];
    const outcome = distillLessons(
      [closedRecord('w-1', 'rigA')],
      prompt => {
        prompts.push(prompt);
        return validLessonJson;
      },
      '2026-06-12T00:00:00Z',
      { readTranscript: () => 'session close: added the coverage field\n' }
    );
    expect(outcome.lessons).toHaveLength(1);
    expect(prompts[0]).toContain('session close: added the coverage field');
  });
});

describe('distillLessonsCommand', () => {
  const ctx = (options: Record<string, unknown>) => ({
    args: [],
    options: { json: true, verbose: false, store: storeFile, ...options } as never,
  });

  const evidenceOpts = { readTranscript: () => 'fixed by adding the coverage field' };

  it('distills, writes NDJSON, and imports into the store', () => {
    writeRecords(db, [closedRecord('w-1', 'rigA')]);
    const outFile = join(dir, 'lessons.jsonl');

    const result = distillLessonsCommand(
      ctx({ out: outFile, import: true }),
      () => validLessonJson,
      evidenceOpts
    );

    expect(result).toMatchObject({
      candidates: 1,
      distilled: 1,
      failures: [],
      out: outFile,
      imported: { appended: 1, skipped: 0 },
      regressions: [],
    });
    const line = JSON.parse(readFileSync(outFile, 'utf8').trim()) as { work_id: string };
    expect(line.work_id).toBe('w-1');
    expect(lessonsFor(db, 'w-1')).toHaveLength(1);
  });

  it('is idempotent across reruns: lessoned records are no longer candidates', () => {
    writeRecords(db, [closedRecord('w-1', 'rigA')]);
    distillLessonsCommand(ctx({ import: true }), () => validLessonJson, evidenceOpts);

    const rerun = distillLessonsCommand(ctx({ import: true }), () => validLessonJson, evidenceOpts);
    expect(rerun.candidates).toBe(0);
    expect(lessonsFor(db, 'w-1')).toHaveLength(1);
  });

  it('refuses admission for a candidate with no resolution evidence, via the CLI', () => {
    writeRecords(db, [closedRecord('w-unverifiable', 'rigA')]);

    const result = distillLessonsCommand(ctx({ import: true }), () => validLessonJson, {
      readTranscript: () => {
        throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' });
      },
    });

    expect(result.distilled).toBe(0);
    expect(result.failures).toHaveLength(1);
    expect(result.failures[0].work_id).toBe('w-unverifiable');
    expect(result.failures[0].error).toContain('no-resolution-evidence');
    expect(lessonsFor(db, 'w-unverifiable')).toHaveLength(0);
  });

  it('rejects a non-numeric --regression-window', () => {
    expect(() =>
      distillLessonsCommand(
        ctx({ import: true, 'regression-window': 'lots' }),
        () => validLessonJson,
        evidenceOpts
      )
    ).toThrow(/--regression-window/);
  });

  it('rejects a bare --regression-window (parsed as boolean true) instead of silently using 1', () => {
    expect(() =>
      distillLessonsCommand(
        ctx({ import: true, 'regression-window': true }),
        () => validLessonJson,
        evidenceOpts
      )
    ).toThrow(/--regression-window/);
  });

  it('refuses to run with neither --out nor --import', () => {
    expect(() => distillLessonsCommand(ctx({}), () => validLessonJson)).toThrow(/nothing to do/);
  });

  it('rejects a non-numeric --limit', () => {
    expect(() =>
      distillLessonsCommand(ctx({ import: true, limit: 'lots' }), () => validLessonJson)
    ).toThrow(/--limit/);
  });

  it('rejects a bare --limit (parsed as boolean true) instead of silently using 1', () => {
    expect(() =>
      distillLessonsCommand(ctx({ import: true, limit: true }), () => validLessonJson)
    ).toThrow(/--limit/);
  });

  it('degrades to an empty, report-only regression list when computeRegressions throws, without losing the already-committed import, and surfaces regressionError even in --json mode', () => {
    writeRecords(db, [closedRecord('w-1', 'rigA')]);
    const regressionSpy = vi.spyOn(distiller, 'computeRegressions').mockImplementation(() => {
      throw new Error('boom: regression check exploded');
    });

    try {
      const result = distillLessonsCommand(
        ctx({ import: true }),
        () => validLessonJson,
        evidenceOpts
      );
      expect(result.imported).toEqual({ appended: 1, skipped: 0 });
      expect(result.regressions).toEqual([]);
      expect(result.regressionError).toContain('boom: regression check exploded');
      expect(lessonsFor(db, 'w-1')).toHaveLength(1);
    } finally {
      regressionSpy.mockRestore();
    }
  });

  it("checks pre-existing lessons before this run's own batch is imported, so a large import cannot evict them from the K-window first", () => {
    writeRecords(db, [closedRecord('w-old', 'rigA'), laterClosedRecord('w-old-recur', 'rigA')]);
    appendLesson(db, {
      work_id: 'w-old',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 'pre-existing' },
    });
    // w-old-recur already has a lesson too, so it's not itself a candidate
    // for this run's batch — only the signature-recurrence check matters here.
    appendLesson(db, {
      work_id: 'w-old-recur',
      extracted_at: '2026-06-07T00:00:00Z',
      payload: { subtitle: 'unrelated' },
    });
    // Default --regression-window is 5; 6 new candidates in this run's own
    // batch would evict w-old's lesson from the K-window before it could be
    // checked, if the regression check ran after (not before) the import.
    writeRecords(
      db,
      Array.from({ length: 6 }, (_, i) =>
        closedRecord(`w-new-${i}`, 'rigA', {
          trace: { jsonl_path: `/t/w-new-${i}.jsonl`, errors: [tsError(`src/new-${i}.ts`)] },
        })
      )
    );

    const result = distillLessonsCommand(
      ctx({ import: true }),
      () => validLessonJson,
      evidenceOpts
    );

    expect(result.imported).toEqual({ appended: 6, skipped: 0 });
    expect(result.regressions).toEqual([
      expect.objectContaining({ lesson_work_id: 'w-old', recurred_in: ['w-old-recur'] }),
    ]);
  });

  it('surfaces regressionSkipped for lessons the K-window could not evaluate', () => {
    appendLesson(db, { work_id: 'w-deleted', extracted_at: '2026-06-05T12:00:00Z', payload: {} });

    const result = distillLessonsCommand(
      ctx({ import: true }),
      () => validLessonJson,
      evidenceOpts
    );

    expect(result.regressionSkipped).toEqual([
      { work_id: 'w-deleted', reason: 'source-record-missing' },
    ]);
  });
});

describe('computeRegressions', () => {
  it('flags a lesson signature that recurred in later, unrelated closed work', () => {
    writeRecords(db, [
      closedRecord('w-src', 'rigA'), // closed 2026-06-05
      laterClosedRecord('w-later', 'rigA'),
    ]);
    appendLesson(db, {
      work_id: 'w-src',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 's' },
    });

    const { flags } = computeRegressions(db);
    expect(flags).toEqual([
      {
        lesson_work_id: 'w-src',
        // Normalized (mem-0r7l bug4): the SQL boundary uses the canonicalized
        // extracted_at, so the flag's own extracted_at must match rather than
        // echo the raw, non-canonical input.
        extracted_at: '2026-06-05T12:00:00.000Z',
        signature: 'tsc:src/a.ts:13:TS2741',
        recurred_in: ['w-later'],
      },
    ]);
  });

  it('does NOT flag a convoy-sibling recurrence (expected iteration, not a regression)', () => {
    writeRecords(db, [
      closedRecord('w-src', 'rigA', { links: { deps: [], supersedes: [], convoy_id: 'c-1' } }),
      laterClosedRecord('w-followup', 'rigA', {
        links: { deps: [], supersedes: [], convoy_id: 'c-1' },
      }),
    ]);
    appendLesson(db, {
      work_id: 'w-src',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 's' },
    });

    expect(computeRegressions(db).flags).toEqual([]);
  });

  it('does NOT flag a supersedes-chain recurrence', () => {
    writeRecords(db, [
      closedRecord('w-src', 'rigA'),
      laterClosedRecord('w-superseder', 'rigA', { links: { deps: [], supersedes: ['w-src'] } }),
    ]);
    appendLesson(db, {
      work_id: 'w-src',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 's' },
    });

    expect(computeRegressions(db).flags).toEqual([]);
  });

  it('does not flag when the signature never recurs', () => {
    writeRecords(db, [closedRecord('w-src', 'rigA')]);
    appendLesson(db, {
      work_id: 'w-src',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 's' },
    });

    expect(computeRegressions(db).flags).toEqual([]);
  });

  it('does not flag a recurrence that closed BEFORE the lesson was extracted', () => {
    writeRecords(db, [
      closedRecord('w-src', 'rigA'), // closed 2026-06-05T00:00:00Z
      closedRecord('w-earlier', 'rigA', {
        lifecycle: {
          created: '2026-06-01T00:00:00Z',
          started: '2026-06-01T01:00:00Z',
          closed: '2026-06-02T00:00:00Z',
          status: 'closed',
          status_history: [],
        },
      }),
    ]);
    // extracted_at AFTER w-earlier closed, so w-earlier is not "later" work.
    appendLesson(db, {
      work_id: 'w-src',
      extracted_at: '2026-06-10T00:00:00Z',
      payload: { subtitle: 's' },
    });

    expect(computeRegressions(db).flags).toEqual([]);
  });

  it('respects the k window: only the most-recently-appended lessons are checked', () => {
    writeRecords(db, [
      closedRecord('w-old', 'rigA', {
        trace: { jsonl_path: '/t/w-old.jsonl', errors: [tsError('src/old.ts')] },
      }),
      closedRecord('w-recent', 'rigA'),
      laterClosedRecord('w-later', 'rigA'),
    ]);
    appendLesson(db, { work_id: 'w-old', extracted_at: '2026-06-05T10:00:00Z', payload: {} });
    appendLesson(db, { work_id: 'w-recent', extracted_at: '2026-06-05T12:00:00Z', payload: {} });

    // w-later recurs w-recent's signature, not w-old's — k=1 only checks w-recent.
    const { flags } = computeRegressions(db, 1);
    expect(flags).toHaveLength(1);
    expect(flags[0].lesson_work_id).toBe('w-recent');
  });

  it('checks nothing for k <= 0, guarding against the slice(-0) === slice(0) footgun', () => {
    writeRecords(db, [closedRecord('w-src', 'rigA'), laterClosedRecord('w-later', 'rigA')]);
    appendLesson(db, {
      work_id: 'w-src',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 's' },
    });

    expect(computeRegressions(db, 0).flags).toEqual([]);
  });

  it('skips a lesson gracefully when its source record no longer exists', () => {
    appendLesson(db, {
      work_id: 'w-deleted',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 's' },
    });

    const { flags, skipped } = computeRegressions(db);
    expect(flags).toEqual([]);
    expect(skipped).toEqual([{ work_id: 'w-deleted', reason: 'source-record-missing' }]);
  });

  it('does NOT flag a same-signature recurrence in a different rig (no cross-rig confusion)', () => {
    writeRecords(db, [
      closedRecord('w-src', 'rigA'),
      laterClosedRecord('w-later-other-rig', 'rigB'),
    ]);
    appendLesson(db, {
      work_id: 'w-src',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 's' },
    });

    expect(computeRegressions(db).flags).toEqual([]);
  });

  it('skips a lesson gracefully when its extracted_at is unparseable, and still checks the rest', () => {
    writeRecords(db, [
      closedRecord('w-bad', 'rigA'),
      closedRecord('w-good', 'rigA', {
        trace: { jsonl_path: '/t/w-good.jsonl', errors: [tsError('src/good.ts')] },
      }),
      laterClosedRecord('w-later', 'rigA', {
        trace: { jsonl_path: '/t/w-later.jsonl', errors: [tsError('src/good.ts')] },
      }),
    ]);
    // Simulates a lesson admitted via `import-lessons`, whose LessonLineSchema
    // does not enforce timestamp format — this must not crash the whole check.
    appendLesson(db, { work_id: 'w-bad', extracted_at: 'not-a-timestamp', payload: {} });
    appendLesson(db, { work_id: 'w-good', extracted_at: '2026-06-05T12:00:00Z', payload: {} });

    const { flags, skipped } = computeRegressions(db);
    expect(flags).toHaveLength(1);
    expect(flags[0].lesson_work_id).toBe('w-good');
    expect(skipped).toEqual([{ work_id: 'w-bad', reason: 'unparseable-extracted-at' }]);
  });

  it('skips a lesson (with a reason) whose source record currently carries no trace-error signatures', () => {
    writeRecords(db, [
      closedRecord('w-clean', 'rigA', { trace: { jsonl_path: '/t/w-clean.jsonl', errors: [] } }),
    ]);
    appendLesson(db, {
      work_id: 'w-clean',
      extracted_at: '2026-06-05T12:00:00Z',
      payload: { subtitle: 's' },
    });

    const { flags, skipped } = computeRegressions(db);
    expect(flags).toEqual([]);
    expect(skipped).toEqual([{ work_id: 'w-clean', reason: 'no-signatures' }]);
  });

  it('isolates one lesson whose source record fails schema validation, instead of aborting the whole check', () => {
    writeRecords(db, [
      closedRecord('w-corrupt', 'rigA'),
      closedRecord('w-good', 'rigA', {
        trace: { jsonl_path: '/t/w-good.jsonl', errors: [tsError('src/good.ts')] },
      }),
      laterClosedRecord('w-later', 'rigA', {
        trace: { jsonl_path: '/t/w-later.jsonl', errors: [tsError('src/good.ts')] },
      }),
    ]);
    appendLesson(db, { work_id: 'w-corrupt', extracted_at: '2026-06-05T10:00:00Z', payload: {} });
    appendLesson(db, { work_id: 'w-good', extracted_at: '2026-06-05T12:00:00Z', payload: {} });
    // Corrupt the stored record directly (bypassing writeRecords' own
    // validation) so `getRecord`'s WorkRecordSchema.parse throws — simulating
    // store corruption / a stale-schema row, not a merely-missing one.
    db.prepare('UPDATE work_records SET record = ? WHERE work_id = ?').run(
      JSON.stringify({ not: 'a valid WorkRecord' }),
      'w-corrupt'
    );

    const { flags, skipped } = computeRegressions(db);
    expect(flags).toHaveLength(1);
    expect(flags[0].lesson_work_id).toBe('w-good');
    expect(skipped).toHaveLength(1);
    expect(skipped[0].work_id).toBe('w-corrupt');
    expect(skipped[0].reason).toContain('source-record-invalid');
  });

  it("scopes the K-window by rig when one is given, so another rig's more-recent lessons don't evict this rig's own", () => {
    writeRecords(db, [closedRecord('w-a', 'rigA'), laterClosedRecord('w-a-recur', 'rigA')]);
    appendLesson(db, { work_id: 'w-a', extracted_at: '2026-06-05T12:00:00Z', payload: {} });

    // A different rig's lesson, appended later, fills an unscoped k=1 window.
    writeRecords(db, [closedRecord('w-b', 'rigB')]);
    appendLesson(db, { work_id: 'w-b', extracted_at: '2026-06-06T00:00:00Z', payload: {} });

    expect(computeRegressions(db, 1).flags).toEqual([]);

    const { flags } = computeRegressions(db, 1, 'rigA');
    expect(flags).toHaveLength(1);
    expect(flags[0].lesson_work_id).toBe('w-a');
  });
});
