import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { distillLessonsCommand } from '../src/cli/commands/distill-lessons.js';
import {
  buildDistillPrompt,
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
