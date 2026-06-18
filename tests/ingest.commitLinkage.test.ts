import { describe, expect, it } from 'vitest';

import {
  deriveCommitOutcome,
  extractPr,
  gitLogCommits,
  linkCommits,
  linkRigOutcomes,
  parseGitLog,
  referencedWorkIds,
  type CommitMeta,
} from '../src/ingest/commitLinkage.js';
import type { GitRunner } from '../src/ingest/provenance.js';

const SHA = (c: string): string => c.repeat(40);

/** Build the delimited block `gitLogCommits` emits, so parser tests exercise the
 * exact wire format rather than a hand-built CommitMeta. */
const FS = '\x1f';
const RS = '\x1e';
const block = (
  sha: string,
  subject: string,
  body = '',
  cn = 'GitHub',
  date = '2026-06-07T12:00:00Z'
): string => `${sha}${FS}${date}${FS}${cn}${FS}${subject}${FS}${body}${RS}`;

describe('extractPr', () => {
  it('returns the trailing PR number', () => {
    expect(extractPr('fix(nav): unify (gascity-dashboard-2j8e.7) (#104)')).toBe(104);
  });
  it('returns the last PR ref when several appear', () => {
    expect(extractPr('revert (#90)\n\nreapplies (#104)')).toBe(104);
  });
  it('returns null for a direct commit', () => {
    expect(extractPr('feat(mem): add distiller (mem-08k)')).toBeNull();
  });
});

describe('referencedWorkIds', () => {
  const ids = new Set(['gascity-dashboard-2j8e', 'gascity-dashboard-2j8e.7', 'gc-00lpsm']);
  it('matches a full dotted child id without matching the parent', () => {
    expect(referencedWorkIds('land (gascity-dashboard-2j8e.7) (#104)', ids)).toEqual([
      'gascity-dashboard-2j8e.7',
    ]);
  });
  it('matches the parent id only when it appears without a dotted suffix', () => {
    expect(referencedWorkIds('land (gascity-dashboard-2j8e) (#95)', ids)).toEqual([
      'gascity-dashboard-2j8e',
    ]);
  });
  it('is case-insensitive and dedupes repeats', () => {
    expect(referencedWorkIds('GC-00LPSM and gc-00lpsm', ids)).toEqual(['gc-00lpsm']);
  });
  it('returns empty when no known id is named', () => {
    expect(referencedWorkIds('chore: bump deps (#12)', ids)).toEqual([]);
  });
  it('matches underscore-rig ids (scix_experiments, migration_evals)', () => {
    const us = new Set(['scix_experiments-0c73', 'migration_evals-0qd2']);
    expect(referencedWorkIds('feat: add (scix_experiments-0c73)', us)).toEqual([
      'scix_experiments-0c73',
    ]);
    expect(referencedWorkIds('fix migration_evals-0qd2 flake', us)).toEqual([
      'migration_evals-0qd2',
    ]);
  });
});

describe('parseGitLog', () => {
  it('parses sha, date, committer, subject, body, and PR', () => {
    const stdout =
      block(SHA('a'), 'feat (gascity-dashboard-2j8e.7) (#104)', 'body line', 'GitHub') +
      '\n' +
      block(SHA('b'), 'fix (mem-08k)', '', 'sjarmak');
    const commits = parseGitLog(stdout);
    expect(commits).toHaveLength(2);
    expect(commits[0]).toMatchObject({ sha: SHA('a'), committer_name: 'GitHub', pr: 104 });
    expect(commits[1]).toMatchObject({ sha: SHA('b'), committer_name: 'sjarmak', pr: null });
  });
  it('ignores trailing empty blocks', () => {
    expect(parseGitLog(`${block(SHA('a'), 's')}\n`)).toHaveLength(1);
  });
});

describe('linkCommits', () => {
  it('attaches a multi-id commit to every id it names', () => {
    const ids = new Set(['mem-08k', 'mem-ztcw0']);
    const commits: CommitMeta[] = [
      {
        sha: SHA('a'),
        author_date: '2026-06-07T12:00:00Z',
        committer_name: 'sjarmak',
        subject: 'feat (mem-08k)',
        body: 'supersedes mem-ztcw0',
        pr: null,
      },
    ];
    const byId = linkCommits(commits, ids);
    expect(byId.get('mem-08k')).toHaveLength(1);
    expect(byId.get('mem-ztcw0')).toHaveLength(1);
  });
});

describe('deriveCommitOutcome', () => {
  const mk = (
    sha: string,
    subject: string,
    pr: number | null,
    date = '2026-06-07T12:00:00Z'
  ): CommitMeta => ({
    sha,
    author_date: date,
    committer_name: pr !== null ? 'GitHub' : 'sjarmak',
    subject,
    body: '',
    pr,
  });

  it('returns null when nothing references the id', () => {
    expect(deriveCommitOutcome('mem-08k', [])).toBeNull();
  });

  it('derives a merged PR outcome from a canonical squash-merge', () => {
    const out = deriveCommitOutcome('gascity-dashboard-2j8e.7', [
      mk(SHA('a'), 'fix(nav): unify (gascity-dashboard-2j8e.7) (#104)', 104),
    ]);
    expect(out).toEqual({
      outcome: { pr: '104', pr_state: 'merged', commit_sha: SHA('a') },
      linkage: 'canonical',
    });
  });

  it('derives a bare commit_sha for a direct landing (no PR)', () => {
    const out = deriveCommitOutcome('mem-08k', [
      mk(SHA('b'), 'feat(mem): distiller (mem-08k)', null),
    ]);
    expect(out).toEqual({ outcome: { commit_sha: SHA('b') }, linkage: 'canonical' });
  });

  it('marks a sole non-canonical reference as unique', () => {
    const out = deriveCommitOutcome('mem-08k', [
      mk(SHA('c'), 'wip: touches mem-08k somewhere', null),
    ]);
    expect(out?.linkage).toBe('unique');
    expect(out?.outcome.commit_sha).toBe(SHA('c'));
  });

  it('prefers the canonical landing over an earlier mention', () => {
    const out = deriveCommitOutcome('mem-08k', [
      mk(SHA('d'), 'wip referencing mem-08k', null, '2026-06-01T00:00:00Z'),
      mk(SHA('e'), 'feat(mem): done (mem-08k)', null, '2026-06-02T00:00:00Z'),
    ]);
    expect(out?.linkage).toBe('canonical');
    expect(out?.outcome.commit_sha).toBe(SHA('e'));
  });

  it('falls back to the newest commit and reports multiple when none is canonical', () => {
    const out = deriveCommitOutcome('mem-08k', [
      mk(SHA('f'), 'mentions mem-08k', null, '2026-06-01T00:00:00Z'),
      mk(SHA('g'), 'also mentions mem-08k', null, '2026-06-03T00:00:00Z'),
    ]);
    expect(out?.linkage).toBe('multiple');
    expect(out?.outcome.commit_sha).toBe(SHA('g'));
  });

  it('breaks an equal-date multiple tie deterministically by sha', () => {
    const SAME = '2026-06-02T00:00:00Z';
    const out = deriveCommitOutcome('mem-08k', [
      mk(SHA('f'), 'mentions mem-08k', null, SAME),
      mk(SHA('h'), 'also mentions mem-08k', null, SAME),
    ]);
    expect(out?.linkage).toBe('multiple');
    expect(out?.outcome.commit_sha).toBe(SHA('h')); // larger sha wins on a date tie
  });
});

describe('gitLogCommits + linkRigOutcomes', () => {
  const fakeRun =
    (stdout: string): GitRunner =>
    (_dir, args) => {
      if (args[0] === 'log') return stdout;
      throw new Error(`unexpected git ${args.join(' ')}`);
    };

  it('returns [] when the branch is gone (non-zero exit)', () => {
    const run: GitRunner = () => {
      const err = new Error('fatal: bad revision') as Error & { status: number };
      err.status = 128;
      throw err;
    };
    expect(gitLogCommits(run, '/repo', 'main')).toEqual([]);
  });

  it('links a rig end-to-end through the runner', () => {
    const stdout =
      block(SHA('a'), 'fix (gascity-dashboard-2j8e.7) (#104)', '', 'GitHub') +
      '\n' +
      block(SHA('b'), 'feat (mem-08k)', '', 'sjarmak');
    const out = linkRigOutcomes(
      ['gascity-dashboard-2j8e.7', 'mem-08k', 'mem-unlinked'],
      '/repo',
      'main',
      { run: fakeRun(stdout) }
    );
    expect(out.get('gascity-dashboard-2j8e.7')?.outcome).toEqual({
      pr: '104',
      pr_state: 'merged',
      commit_sha: SHA('a'),
    });
    expect(out.get('mem-08k')?.outcome).toEqual({ commit_sha: SHA('b') });
    expect(out.has('mem-unlinked')).toBe(false);
  });
});
