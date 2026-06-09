import { describe, expect, it } from 'vitest';

import {
  type GitRunner,
  attachProvenance,
  deriveProvenance,
  provenanceInput,
  toGitUtc,
} from '../src/ingest/provenance.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

/** A full 40-hex commit SHA, the shape `git rev-list -1` emits and the form
 * ProvenanceSchema.base_commit enforces. */
const SHA = '0123456789abcdef0123456789abcdef01234567';

/** A spine record with arbitrary metadata + lifecycle, validated like ingest. */
const record = (
  workId: string,
  metadata: Record<string, unknown>,
  lifecycle: { created: string; started?: string }
): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig: 'mem',
    title: `work ${workId}`,
    metadata,
    lifecycle: { ...lifecycle, status: 'closed' },
  });

describe('toGitUtc', () => {
  it('appends an explicit UTC offset to a TZ-less dolt timestamp', () => {
    expect(toGitUtc('2026-06-07 02:19:05')).toBe('2026-06-07 02:19:05 +0000');
  });

  it('rewrites a trailing Z to an explicit offset (git approxidate is local-biased)', () => {
    expect(toGitUtc('2026-06-05T03:24:03Z')).toBe('2026-06-05T03:24:03 +0000');
  });

  it('leaves an already-explicit offset untouched', () => {
    expect(toGitUtc('2026-06-07 02:19:05 +0000')).toBe('2026-06-07 02:19:05 +0000');
    expect(toGitUtc('2026-06-07T02:19:05-04:00')).toBe('2026-06-07T02:19:05-04:00');
  });

  it('throws on a value that is not a recognizable datetime (upstream corruption)', () => {
    expect(() => toGitUtc('--output=/tmp/pwned')).toThrow(/not a recognizable datetime/);
    expect(() => toGitUtc('')).toThrow(/not a recognizable datetime/);
  });
});

describe('provenanceInput', () => {
  it('reads gc.work_dir (dotted keys are LITERAL flat keys) and gc.var.base_branch', () => {
    const rec = record(
      'w-1',
      { 'gc.work_dir': '/home/ds/projects/mem', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' }
    );
    expect(provenanceInput(rec)).toEqual({
      work_dir: '/home/ds/projects/mem',
      repo: 'mem',
      base_branch: 'main',
      started_at: '2026-06-05 03:24:03',
    });
  });

  it('falls back to the legacy flat work_dir key and to created when started is absent', () => {
    const rec = record(
      'w-2',
      { work_dir: '/home/ds/gas-city' },
      { created: '2026-06-01T00:00:00Z' }
    );
    expect(provenanceInput(rec)).toEqual({
      work_dir: '/home/ds/gas-city',
      repo: 'gas-city',
      started_at: '2026-06-01T00:00:00Z',
    });
  });

  it('returns null when no work_dir is present (the common case)', () => {
    const rec = record('w-3', { workflow_id: 'mem-nokh' }, { created: '2026-06-01T00:00:00Z' });
    expect(provenanceInput(rec)).toBeNull();
  });

  it('ignores an empty-string work_dir (boundary validation)', () => {
    const rec = record('w-4', { 'gc.work_dir': '' }, { created: '2026-06-01T00:00:00Z' });
    expect(provenanceInput(rec)).toBeNull();
  });

  it('rejects a non-absolute work_dir (gc.work_dir is by contract absolute)', () => {
    const rec = record(
      'w-5',
      { 'gc.work_dir': 'relative/path' },
      { created: '2026-06-01T00:00:00Z' }
    );
    expect(provenanceInput(rec)).toBeNull();
  });
});

describe('deriveProvenance', () => {
  const input = {
    work_dir: '/repo',
    repo: 'repo',
    base_branch: 'main',
    started_at: '2026-06-05 03:24:03',
  };

  it('resolves the commit by date, normalizes the timestamp, and guards the ref with --end-of-options', () => {
    const calls: string[][] = [];
    const run: GitRunner = (workDir, args) => {
      calls.push([workDir, ...args]);
      return `${SHA}\n`;
    };
    const prov = deriveProvenance(input, run);
    expect(prov).toEqual({
      work_dir: '/repo',
      repo: 'repo',
      base_branch: 'main',
      base_commit: SHA,
      history_state: 'commit-by-date',
    });
    // --end-of-options before the DB-sourced ref blocks git argument injection.
    expect(calls).toEqual([
      ['/repo', 'rev-list', '-1', '--before=2026-06-05 03:24:03 +0000', '--end-of-options', 'main'],
    ]);
  });

  it('fails the schema parse when git output is not a 40-hex SHA (corruption guard)', () => {
    expect(() => deriveProvenance(input, () => 'not-a-sha\n')).toThrow();
  });

  it('marks unresolved when no base_branch was recorded (never falls back to HEAD)', () => {
    const run: GitRunner = () => {
      throw new Error('git must not be called without a base branch');
    };
    const prov = deriveProvenance(
      { work_dir: '/repo', repo: 'repo', started_at: '2026-06-05 03:24:03' },
      run
    );
    expect(prov).toEqual({ work_dir: '/repo', repo: 'repo', history_state: 'unresolved' });
  });

  it('marks unresolved on a zero-exit empty stdout (valid branch, no commit before start)', () => {
    const run: GitRunner = () => '   \n';
    const prov = deriveProvenance(input, run);
    expect(prov).toEqual({
      work_dir: '/repo',
      repo: 'repo',
      base_branch: 'main',
      history_state: 'unresolved',
    });
  });

  it('marks unresolved on a non-zero git exit (work_dir gone / unknown branch)', () => {
    const run: GitRunner = () => {
      throw Object.assign(new Error('fatal: not a git repository'), { status: 128 });
    };
    expect(deriveProvenance(input, run).history_state).toBe('unresolved');
  });

  it('propagates a missing-git-binary failure (a misconfiguration, not unresolved)', () => {
    const run: GitRunner = () => {
      throw Object.assign(new Error('spawn git ENOENT'), { code: 'ENOENT' });
    };
    expect(() => deriveProvenance(input, run)).toThrow(/ENOENT/);
  });
});

describe('attachProvenance', () => {
  it('attaches provenance to work_dir-bearing records and leaves the rest untouched', () => {
    const withDir = record(
      'w-dir',
      { 'gc.work_dir': '/repo', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' }
    );
    const noDir = record('w-none', {}, { created: '2026-06-01T00:00:00Z' });

    const run: GitRunner = () => `${SHA}\n`;
    const [a, b] = attachProvenance([withDir, noDir], { run });

    expect(a.provenance).toEqual({
      work_dir: '/repo',
      repo: 'repo',
      base_branch: 'main',
      base_commit: SHA,
      history_state: 'commit-by-date',
    });
    expect(b.provenance).toBeUndefined();
  });

  it('copies records — never mutates the input', () => {
    const rec = record(
      'w-imm',
      { 'gc.work_dir': '/repo', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' }
    );
    attachProvenance([rec], { run: () => `${SHA}\n` });
    expect(rec.provenance).toBeUndefined();
  });
});
