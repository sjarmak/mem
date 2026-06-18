import { describe, expect, it } from 'vitest';

import {
  aggregateConclusion,
  classifyCiEntry,
  indexSnapshot,
  parsePrUrl,
  planCiElevations,
  type DashboardCiEntry,
  type PrLinkRow,
} from '../src/ingest/dashboardCi.js';

const SHA = (c: string): string => c.repeat(40);

const entry = (over: Partial<DashboardCiEntry> = {}): DashboardCiEntry => ({
  number: 137,
  mergeCommit: { oid: SHA('a') },
  headRefName: 'fix/x',
  headRefDeleted: false,
  checkRuns: [{ conclusion: 'success', name: 'tests', status: 'completed' }],
  ...over,
});

describe('aggregateConclusion', () => {
  it('is UNKNOWN for an empty check set (fail-closed)', () => {
    expect(aggregateConclusion([])).toEqual({ conclusion: 'UNKNOWN', reason: 'no-check-runs' });
  });
  it('is UNKNOWN when any run is incomplete (null conclusion)', () => {
    expect(aggregateConclusion(['success', null])).toEqual({
      conclusion: 'UNKNOWN',
      reason: 'incomplete-run',
    });
  });
  it('is UNKNOWN on an unrecognized conclusion string', () => {
    expect(aggregateConclusion(['success', 'flubbed'])).toEqual({
      conclusion: 'UNKNOWN',
      reason: 'unrecognized-conclusion:flubbed',
    });
  });
  it('is a failure when any recognized failure is present', () => {
    expect(aggregateConclusion(['success', 'failure'])).toEqual({
      conclusion: 'failure',
      reason: 'check-run-failure',
    });
  });
  it('counts neutral/skipped as success', () => {
    expect(aggregateConclusion(['success', 'neutral', 'skipped'])).toEqual({
      conclusion: 'success',
      reason: 'all-checks-passed',
    });
  });
});

describe('classifyCiEntry', () => {
  it('classifies a green merged PR', () => {
    expect(classifyCiEntry(entry())).toEqual({
      pr_number: 137,
      merge_oid: SHA('a'),
      ci: 'success',
      reason: 'all-checks-passed',
    });
  });
  it('is UNKNOWN with no merge commit (never merged)', () => {
    const c = classifyCiEntry(entry({ mergeCommit: null }));
    expect(c.ci).toBe('UNKNOWN');
    expect(c.reason).toBe('no-merge-commit');
    expect(c.merge_oid).toBeNull();
  });
  it('distinguishes never-fetched checks from genuinely empty', () => {
    expect(classifyCiEntry(entry({ checkRuns: null })).reason).toBe('check-runs-not-fetched');
    expect(classifyCiEntry(entry({ checkRuns: [] })).reason).toBe('no-check-runs');
  });
  it('classifies a failed merged PR', () => {
    const c = classifyCiEntry(
      entry({ checkRuns: [{ conclusion: 'failure', name: 't', status: 'completed' }] })
    );
    expect(c.ci).toBe('failure');
  });
});

describe('indexSnapshot', () => {
  it('validates and indexes a raw snapshot by PR number', () => {
    const idx = indexSnapshot([entry({ number: 1 }), entry({ number: 2, mergeCommit: null })]);
    expect(idx.size).toBe(2);
    expect(idx.get(1)?.ci).toBe('success');
    expect(idx.get(2)?.ci).toBe('UNKNOWN');
  });
  it('rejects a malformed snapshot at the boundary', () => {
    expect(() => indexSnapshot([{ number: 'oops' }])).toThrow();
    expect(() => indexSnapshot({ not: 'an array' })).toThrow();
  });
});

describe('parsePrUrl', () => {
  it('extracts owner/repo and number from a canonical PR url', () => {
    expect(parsePrUrl('https://github.com/gastownhall/gascity-dashboard/pull/137')).toEqual({
      repo: 'gastownhall/gascity-dashboard',
      pr_number: 137,
    });
  });
  it('tolerates a trailing slash or fragment', () => {
    expect(parsePrUrl('https://github.com/o/r/pull/9/files')?.pr_number).toBe(9);
  });
  it('returns null for a non-PR url', () => {
    expect(parsePrUrl('https://github.com/o/r/issues/3')).toBeNull();
    expect(parsePrUrl('not a url')).toBeNull();
  });
});

describe('planCiElevations', () => {
  const repo = 'gastownhall/gascity-dashboard';
  const url = (n: number): string => `https://github.com/${repo}/pull/${n}`;
  const link = (over: Partial<PrLinkRow> = {}): PrLinkRow => ({
    work_id: 'gascity-dashboard-aaa.1',
    entity_ref: url(137),
    ...over,
  });

  it('elevates a T2 pr-link whose PR is green', () => {
    const idx = indexSnapshot([entry({ number: 137 })]);
    const out = planCiElevations(idx, repo, [link()]);
    expect(out).toHaveLength(1);
    expect(out[0].work_id).toBe('gascity-dashboard-aaa.1');
    expect(out[0].entity_ref).toBe(url(137));
    expect(out[0].pr_number).toBe(137);
    expect(out[0].outcome).toEqual({
      pr: '#137',
      repo,
      pr_state: 'merged',
      commit_sha: SHA('a'),
      ci: 'pass',
    });
  });

  it('does NOT elevate a failed-CI PR (stays T2, fail-closed)', () => {
    const idx = indexSnapshot([
      entry({ number: 64, checkRuns: [{ conclusion: 'failure', name: 't', status: 'completed' }] }),
    ]);
    expect(planCiElevations(idx, repo, [link({ entity_ref: url(64) })])).toEqual([]);
  });

  it('does NOT elevate an UNKNOWN-CI PR', () => {
    const idx = indexSnapshot([entry({ number: 74, checkRuns: [] })]);
    expect(planCiElevations(idx, repo, [link({ entity_ref: url(74) })])).toEqual([]);
  });

  it('does NOT match a same-numbered PR in a different repo', () => {
    const idx = indexSnapshot([entry({ number: 137 })]);
    const foreign = link({ entity_ref: 'https://github.com/other/repo/pull/137' });
    expect(planCiElevations(idx, repo, [foreign])).toEqual([]);
  });

  it('skips a pr-link with no matching snapshot entry', () => {
    const idx = indexSnapshot([entry({ number: 1 })]);
    expect(planCiElevations(idx, repo, [link({ entity_ref: url(999) })])).toEqual([]);
  });

  it('is idempotent — re-planning the same link yields an identical elevation', () => {
    const idx = indexSnapshot([entry({ number: 137 })]);
    const first = planCiElevations(idx, repo, [link()]);
    const second = planCiElevations(idx, repo, [link()]);
    expect(second).toEqual(first);
    expect(second).toHaveLength(1);
  });
});
