import { describe, it, expect } from 'vitest';
import {
  mapCiRollup,
  mapPullRequestToOutcome,
  resolveBranchOutcome,
  GH_OUTCOME_FIELDS,
  type GhRunner,
} from '../src/ingest/outcomes.js';
import { OutcomeSchema } from '../src/schemas/workrecord.js';

describe('mapCiRollup', () => {
  it('has no verdict for an empty rollup', () => {
    expect(mapCiRollup([])).toBeUndefined();
  });

  it('passes when every check succeeded', () => {
    expect(
      mapCiRollup([
        { status: 'COMPLETED', conclusion: 'SUCCESS' },
        { status: 'COMPLETED', conclusion: 'SUCCESS' },
      ])
    ).toBe('pass');
  });

  it('fails as soon as one check failed, even amid successes', () => {
    expect(
      mapCiRollup([
        { status: 'COMPLETED', conclusion: 'SUCCESS' },
        { status: 'COMPLETED', conclusion: 'FAILURE' },
      ])
    ).toBe('fail');
  });

  it('treats a cancelled or timed-out check as a failure', () => {
    expect(mapCiRollup([{ status: 'COMPLETED', conclusion: 'CANCELLED' }])).toBe('fail');
    expect(mapCiRollup([{ status: 'COMPLETED', conclusion: 'TIMED_OUT' }])).toBe('fail');
  });

  it('leaves the verdict open while a check is still running', () => {
    expect(
      mapCiRollup([{ status: 'COMPLETED', conclusion: 'SUCCESS' }, { status: 'IN_PROGRESS' }])
    ).toBeUndefined();
  });

  it('ignores skipped/neutral checks but still passes on a real success', () => {
    expect(
      mapCiRollup([
        { status: 'COMPLETED', conclusion: 'SKIPPED' },
        { status: 'COMPLETED', conclusion: 'SUCCESS' },
      ])
    ).toBe('pass');
  });

  it('has no verdict when every check is skipped/neutral', () => {
    expect(mapCiRollup([{ status: 'COMPLETED', conclusion: 'SKIPPED' }])).toBeUndefined();
  });

  it('reads StatusContext entries via their state field', () => {
    expect(mapCiRollup([{ state: 'SUCCESS' }])).toBe('pass');
    expect(mapCiRollup([{ state: 'FAILURE' }])).toBe('fail');
    expect(mapCiRollup([{ state: 'PENDING' }])).toBeUndefined();
  });
});

describe('mapPullRequestToOutcome', () => {
  it('maps a merged PR to its merge commit', () => {
    const outcome = mapPullRequestToOutcome({
      number: 63,
      state: 'MERGED',
      mergeCommit: { oid: 'merge000sha' },
      headRefOid: 'branchtipsha',
      statusCheckRollup: [{ status: 'COMPLETED', conclusion: 'SUCCESS' }],
    });
    expect(outcome).toEqual({
      pr: '#63',
      pr_state: 'merged',
      commit_sha: 'merge000sha',
      ci: 'pass',
    });
  });

  it('falls back to the branch tip when a merged PR lacks a merge commit', () => {
    const outcome = mapPullRequestToOutcome({
      number: 7,
      state: 'MERGED',
      mergeCommit: null,
      headRefOid: 'branchtipsha',
      statusCheckRollup: [],
    });
    expect(outcome.commit_sha).toBe('branchtipsha');
    expect(outcome.pr_state).toBe('merged');
  });

  it('maps a closed (unmerged) PR to the branch tip with no CI when unknown', () => {
    const outcome = mapPullRequestToOutcome({
      number: 12,
      state: 'CLOSED',
      headRefOid: 'closedtipsha',
      statusCheckRollup: [{ status: 'COMPLETED', conclusion: 'FAILURE' }],
    });
    expect(outcome).toEqual({
      pr: '#12',
      pr_state: 'closed',
      commit_sha: 'closedtipsha',
      ci: 'fail',
    });
  });

  it('omits pr_state for an open PR but keeps commit and CI signal', () => {
    const outcome = mapPullRequestToOutcome({
      number: 99,
      state: 'OPEN',
      headRefOid: 'opentipsha',
      statusCheckRollup: [{ status: 'IN_PROGRESS' }],
    });
    expect(outcome).toEqual({ pr: '#99', commit_sha: 'opentipsha' });
    expect(outcome.pr_state).toBeUndefined();
    expect(outcome.ci).toBeUndefined();
  });

  it('produces a schema-valid Outcome', () => {
    const outcome = mapPullRequestToOutcome({
      number: 1,
      state: 'MERGED',
      mergeCommit: { oid: 'sha' },
      headRefOid: 'tip',
      statusCheckRollup: [],
    });
    expect(() => OutcomeSchema.parse(outcome)).not.toThrow();
  });
});

describe('resolveBranchOutcome', () => {
  it('returns null when no PR exists for the branch', async () => {
    const runner: GhRunner = () => Promise.resolve('[]');
    expect(await resolveBranchOutcome('owner/repo', 'feature-x', runner)).toBeNull();
  });

  it('queries gh with the expected repo, head, state, and fields', async () => {
    let captured: string[] = [];
    const runner: GhRunner = args => {
      captured = args;
      return Promise.resolve('[]');
    };
    await resolveBranchOutcome('gastownhall/gascity', 'mem-i42', runner);
    expect(captured).toEqual([
      'pr',
      'list',
      '--repo',
      'gastownhall/gascity',
      '--head',
      'mem-i42',
      '--state',
      'all',
      '--json',
      GH_OUTCOME_FIELDS,
      '--limit',
      '1',
    ]);
  });

  it('resolves the first PR returned by gh', async () => {
    const runner: GhRunner = () =>
      Promise.resolve(
        JSON.stringify([
          {
            number: 63,
            state: 'MERGED',
            mergeCommit: { oid: 'abc1234' },
            headRefOid: 'def5678',
            statusCheckRollup: [{ status: 'COMPLETED', conclusion: 'SUCCESS' }],
          },
        ])
      );
    expect(await resolveBranchOutcome('owner/repo', 'branch', runner)).toEqual({
      pr: '#63',
      pr_state: 'merged',
      commit_sha: 'abc1234',
      ci: 'pass',
    });
  });

  it('wraps a gh failure with repo/branch context instead of swallowing it', async () => {
    const runner: GhRunner = () => Promise.reject(new Error('gh: not authenticated'));
    await expect(resolveBranchOutcome('owner/repo', 'branch', runner)).rejects.toThrow(
      /gh pr list failed for owner\/repo branch "branch": gh: not authenticated/
    );
  });
});
