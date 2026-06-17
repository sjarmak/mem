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

/** A spine record with arbitrary metadata + lifecycle, validated like ingest.
 * `rig` defaults to `dec`, an UNMAPPED rig, so a test exercises pure metadata
 * resolution unless it opts into a mapped rig (e.g. `mem`) for backfill. */
const record = (
  workId: string,
  metadata: Record<string, unknown>,
  lifecycle: { created: string; started?: string },
  rig = 'dec'
): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig,
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

describe('provenanceInput — recorded metadata', () => {
  it('reads gc.work_dir (dotted keys are LITERAL flat keys) and gc.var.base_branch', () => {
    const rec = record(
      'w-1',
      { 'gc.work_dir': '/home/ds/projects/mem', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' },
      'mem'
    );
    expect(provenanceInput(rec)).toEqual({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'metadata',
      repo: 'mem',
      base_branch: 'main',
      base_branch_source: 'metadata',
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
      work_dir_source: 'metadata',
      repo: 'gas-city',
      started_at: '2026-06-01T00:00:00Z',
    });
  });

  it('defaults the branch to the rig integration branch when the dir is recorded but the branch is not', () => {
    // mem is a mapped rig: a recorded work_dir but no recorded base_branch still
    // gets the rig's known integration branch (data-backed default).
    const rec = record(
      'w-2b',
      { 'gc.work_dir': '/home/ds/projects/mem' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' },
      'mem'
    );
    expect(provenanceInput(rec)).toMatchObject({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'metadata',
      base_branch: 'main',
      base_branch_source: 'default',
    });
  });
});

describe('provenanceInput — rig-map backfill', () => {
  it('backfills work_dir + branch from the rig when no metadata work_dir was recorded', () => {
    const rec = record(
      'w-bf',
      { workflow_id: 'mem-nokh' },
      { created: '2026-06-01T00:00:00Z' },
      'mem'
    );
    expect(provenanceInput(rec)).toEqual({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'rig-map',
      repo: 'mem',
      base_branch: 'main',
      base_branch_source: 'default',
      started_at: '2026-06-01T00:00:00Z',
    });
  });

  it("honors a rig's non-main integration branch", () => {
    const rec = record('w-zelda', {}, { created: '2026-06-01T00:00:00Z' }, 'zeldascension');
    expect(provenanceInput(rec)).toMatchObject({
      work_dir: '/home/ds/projects/zeldascension',
      work_dir_source: 'rig-map',
      base_branch: 'master',
      base_branch_source: 'default',
    });
  });

  it('backfills from the rig when the metadata work_dir is empty or non-absolute', () => {
    const empty = record('w-e', { 'gc.work_dir': '' }, { created: '2026-06-01T00:00:00Z' }, 'mem');
    const rel = record(
      'w-r',
      { 'gc.work_dir': 'rel/path' },
      { created: '2026-06-01T00:00:00Z' },
      'mem'
    );
    expect(provenanceInput(empty)).toMatchObject({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'rig-map',
    });
    expect(provenanceInput(rel)).toMatchObject({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'rig-map',
    });
  });
});

describe('provenanceInput — no usable source', () => {
  it('returns null for an unmapped rig with no work_dir', () => {
    const rec = record('w-3', { workflow_id: 'mem-nokh' }, { created: '2026-06-01T00:00:00Z' });
    expect(provenanceInput(rec)).toBeNull();
  });

  it('returns null for an unmapped rig with an empty-string work_dir', () => {
    const rec = record('w-4', { 'gc.work_dir': '' }, { created: '2026-06-01T00:00:00Z' });
    expect(provenanceInput(rec)).toBeNull();
  });

  it('returns null for an unmapped rig with a non-absolute work_dir', () => {
    const rec = record(
      'w-5',
      { 'gc.work_dir': 'relative/path' },
      { created: '2026-06-01T00:00:00Z' }
    );
    expect(provenanceInput(rec)).toBeNull();
  });

  it('returns null for a multi-repo rig (gc) — never backfills a single dir', () => {
    const rec = record('w-gc', {}, { created: '2026-06-01T00:00:00Z' }, 'gc');
    expect(provenanceInput(rec)).toBeNull();
  });
});

describe('deriveProvenance', () => {
  const input = {
    work_dir: '/repo',
    work_dir_source: 'metadata' as const,
    repo: 'repo',
    base_branch: 'main',
    base_branch_source: 'metadata' as const,
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
      work_dir_source: 'metadata',
      repo: 'repo',
      base_branch: 'main',
      base_branch_source: 'metadata',
      base_commit: SHA,
      history_state: 'commit-by-date',
    });
    // --end-of-options before the DB-sourced ref blocks git argument injection.
    expect(calls).toEqual([
      ['/repo', 'rev-list', '-1', '--before=2026-06-05 03:24:03 +0000', '--end-of-options', 'main'],
    ]);
  });

  it('carries a defaulted branch source through to the Provenance', () => {
    const prov = deriveProvenance({ ...input, base_branch_source: 'default' }, () => `${SHA}\n`);
    expect(prov).toMatchObject({
      base_branch: 'main',
      base_branch_source: 'default',
      base_commit: SHA,
    });
  });

  it('fails the schema parse when git output is not a 40-hex SHA (corruption guard)', () => {
    expect(() => deriveProvenance(input, () => 'not-a-sha\n')).toThrow();
  });

  it('marks unresolved when no base_branch is known (never falls back to HEAD)', () => {
    const run: GitRunner = () => {
      throw new Error('git must not be called without a base branch');
    };
    const prov = deriveProvenance(
      {
        work_dir: '/repo',
        work_dir_source: 'metadata',
        repo: 'repo',
        started_at: '2026-06-05 03:24:03',
      },
      run
    );
    expect(prov).toEqual({
      work_dir: '/repo',
      work_dir_source: 'metadata',
      repo: 'repo',
      history_state: 'unresolved',
    });
  });

  it('marks unresolved on a zero-exit empty stdout (valid branch, no commit before start)', () => {
    const run: GitRunner = () => '   \n';
    const prov = deriveProvenance(input, run);
    expect(prov).toEqual({
      work_dir: '/repo',
      work_dir_source: 'metadata',
      repo: 'repo',
      base_branch: 'main',
      base_branch_source: 'metadata',
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
  it('attaches provenance to work_dir-bearing records and leaves unresolvable rigs untouched', () => {
    const withDir = record(
      'w-dir',
      { 'gc.work_dir': '/repo', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' },
      'mem'
    );
    // Unmapped rig with no work_dir → no provenance source at all.
    const noDir = record('w-none', {}, { created: '2026-06-01T00:00:00Z' });

    const run: GitRunner = () => `${SHA}\n`;
    const [a, b] = attachProvenance([withDir, noDir], { run });

    expect(a.provenance).toEqual({
      work_dir: '/repo',
      work_dir_source: 'metadata',
      repo: 'repo',
      base_branch: 'main',
      base_branch_source: 'metadata',
      base_commit: SHA,
      history_state: 'commit-by-date',
    });
    expect(b.provenance).toBeUndefined();
  });

  it('attaches a rig-map baseline to a mapped record that recorded no work_dir', () => {
    const rec = record('w-rigonly', {}, { created: '2026-06-01T00:00:00Z' }, 'mem');
    const [out] = attachProvenance([rec], { run: () => `${SHA}\n` });
    expect(out.provenance).toMatchObject({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'rig-map',
      base_branch: 'main',
      base_branch_source: 'default',
      base_commit: SHA,
      history_state: 'commit-by-date',
    });
  });

  it('copies records — never mutates the input', () => {
    const rec = record(
      'w-imm',
      { 'gc.work_dir': '/repo', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' },
      'mem'
    );
    attachProvenance([rec], { run: () => `${SHA}\n` });
    expect(rec.provenance).toBeUndefined();
  });
});
