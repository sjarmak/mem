import { describe, expect, it } from 'vitest';

import {
  AMBIGUOUS_FANOUT,
  classifySessions,
  COLLAPSED_TO_CANONICAL,
  countMisattributions,
  DISAMBIGUATED,
  SINGLE_WORK_ID,
  summarizeFanout,
  type SessionAssoc,
} from '../src/bench/session-fanout.js';

/** Build an assoc from the work's canonical identity (slug = work_id; an optional
 * branch-root / landed-commit drive the collapse), mirroring how the harness
 * derives it via loo-dedup canonicalIdentity. */
const assoc = (
  session_uuid: string,
  work_id: string,
  extra: { branchRoot?: string; landedCommit?: string } = {}
): SessionAssoc => ({
  session_uuid,
  work_id,
  canonical: { slug: work_id, ...extra },
});

describe('classifySessions', () => {
  it('scores a session with a single work_id', () => {
    const [t] = classifySessions([assoc('s1', 'gc-a')]);
    expect(t).toMatchObject({
      session_uuid: 's1',
      primary_work_id: 'gc-a',
      eligibility: 'scorable',
      fanout_degree: 1,
      canonical_degree: 1,
      reason: SINGLE_WORK_ID,
    });
  });

  it('collapses a fan-out that shares a branch-root to ONE scorable canonical', () => {
    // run-1 / run-2 of the same bead: distinct work_ids, same branch-root.
    const [t] = classifySessions([
      assoc('s1', 'gc-b2', { branchRoot: 'feat-x' }),
      assoc('s1', 'gc-b1', { branchRoot: 'feat-x' }),
    ]);
    expect(t.eligibility).toBe('scorable');
    expect(t.fanout_degree).toBe(2);
    expect(t.canonical_degree).toBe(1);
    expect(t.reason).toBe(COLLAPSED_TO_CANONICAL);
    // deterministic representative: lexicographically smallest work_id
    expect(t.primary_work_id).toBe('gc-b1');
  });

  it('collapses across a shared landed_commit too', () => {
    const [t] = classifySessions([
      assoc('s1', 'gc-c1', { landedCommit: 'deadbeef' }),
      assoc('s1', 'gc-c2', { landedCommit: 'deadbeef' }),
    ]);
    expect(t.canonical_degree).toBe(1);
    expect(t.eligibility).toBe('scorable');
  });

  it('marks a genuinely-distinct fan-out AMBIGUOUS — never scored (R1)', () => {
    const targets = classifySessions([
      assoc('s1', 'gc-d', { branchRoot: 'feat-d' }),
      assoc('s1', 'gc-e', { branchRoot: 'feat-e' }),
    ]);
    expect(targets[0]).toMatchObject({
      eligibility: 'ambiguous',
      primary_work_id: null,
      fanout_degree: 2,
      canonical_degree: 2,
      reason: AMBIGUOUS_FANOUT,
    });
  });

  it('rescues a distinct fan-out only when the 2nd-gate disambiguator picks a member', () => {
    const assocs = [
      assoc('s1', 'gc-d', { branchRoot: 'feat-d' }),
      assoc('s1', 'gc-e', { branchRoot: 'feat-e' }),
    ];
    const disamb = new Map([['s1', 'gc-e']]);
    const [t] = classifySessions(assocs, disamb);
    expect(t).toMatchObject({
      eligibility: 'scorable',
      primary_work_id: 'gc-e',
      reason: DISAMBIGUATED,
    });
  });

  it('IGNORES a disambiguator choice that is not a member of the session (fail-closed)', () => {
    const assocs = [
      assoc('s1', 'gc-d', { branchRoot: 'feat-d' }),
      assoc('s1', 'gc-e', { branchRoot: 'feat-e' }),
    ];
    const disamb = new Map([['s1', 'gc-ZZZ']]); // not on the session
    const [t] = classifySessions(assocs, disamb);
    expect(t.eligibility).toBe('ambiguous');
    expect(t.primary_work_id).toBeNull();
  });

  it('does not disambiguate a session that already collapses (no spurious downgrade)', () => {
    const [t] = classifySessions(
      [
        assoc('s1', 'gc-b1', { branchRoot: 'feat-x' }),
        assoc('s1', 'gc-b2', { branchRoot: 'feat-x' }),
      ],
      new Map([['s1', 'gc-b2']])
    );
    expect(t.reason).toBe(COLLAPSED_TO_CANONICAL); // not DISAMBIGUATED
    expect(t.primary_work_id).toBe('gc-b1');
  });

  it('dedupes repeated (session, work) rows before counting fan-out degree', () => {
    const [t] = classifySessions([assoc('s1', 'gc-a'), assoc('s1', 'gc-a')]);
    expect(t.fanout_degree).toBe(1);
    expect(t.eligibility).toBe('scorable');
  });

  it('orders output by session_uuid for a reproducible report', () => {
    const targets = classifySessions([assoc('s2', 'gc-x'), assoc('s1', 'gc-y')]);
    expect(targets.map(t => t.session_uuid)).toEqual(['s1', 's2']);
  });
});

describe('summarizeFanout', () => {
  it('tallies the eligibility breakdown', () => {
    const targets = classifySessions([
      assoc('s1', 'gc-a'), // single
      assoc('s2', 'gc-b1', { branchRoot: 'x' }), // collapsed pair
      assoc('s2', 'gc-b2', { branchRoot: 'x' }),
      assoc('s3', 'gc-d', { branchRoot: 'd' }), // ambiguous pair
      assoc('s3', 'gc-e', { branchRoot: 'e' }),
    ]);
    const r = summarizeFanout(targets);
    expect(r).toEqual({
      sessions: 3,
      fanout_sessions: 2,
      scorable: 2,
      ambiguous: 1,
      collapsed: 1,
      disambiguated: 0,
      by_reason: { [SINGLE_WORK_ID]: 1, [COLLAPSED_TO_CANONICAL]: 1, [AMBIGUOUS_FANOUT]: 1 },
    });
  });
});

describe('countMisattributions', () => {
  it('flags verdicts attributed to a non-primary work_id (R1 early-warning)', () => {
    const targets = classifySessions([
      assoc('s1', 'gc-a'),
      assoc('s2', 'gc-d', { branchRoot: 'd' }),
      assoc('s2', 'gc-e', { branchRoot: 'e' }),
    ]);
    const verdicts = [
      { session_uuid: 's1', verdict_source_work_id: 'gc-a' }, // matches primary — ok
      { session_uuid: 's2', verdict_source_work_id: 'gc-d' }, // s2 is ambiguous (primary null) — misattributed
    ];
    expect(countMisattributions(targets, verdicts)).toBe(1);
  });

  it('counts a verdict on a non-primary member of a scorable session', () => {
    const targets = classifySessions([
      assoc('s1', 'gc-b1', { branchRoot: 'x' }),
      assoc('s1', 'gc-b2', { branchRoot: 'x' }),
    ]);
    // primary is gc-b1; a verdict sourced from gc-b2 is a mis-attribution
    expect(
      countMisattributions(targets, [{ session_uuid: 's1', verdict_source_work_id: 'gc-b2' }])
    ).toBe(1);
    expect(
      countMisattributions(targets, [{ session_uuid: 's1', verdict_source_work_id: 'gc-b1' }])
    ).toBe(0);
  });
});
