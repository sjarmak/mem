import { describe, expect, it } from 'vitest';

import {
  EXPECTED_FLOORS,
  RIG_FLOOR_KEYS,
  aggregateConclusion,
  bundleParity,
  classifyCiRow,
  dedupeStores,
  detachedRecovery,
  floorCheck,
  isSessionStore,
  parseDetachedHeads,
  parseRefIndex,
  summarizeCi,
} from '../scripts/freeze/lib.mjs';

const SHA = (c: string): string => c.repeat(40);

describe('aggregateConclusion (fail-closed roll-up)', () => {
  it('all-success → success', () => {
    expect(aggregateConclusion(['success', 'success', 'skipped'])).toEqual({
      conclusion: 'success',
      reason: 'all-checks-passed',
    });
  });
  it('any recognized failure → failure', () => {
    expect(aggregateConclusion(['success', 'timed_out']).conclusion).toBe('failure');
    expect(aggregateConclusion(['cancelled']).conclusion).toBe('failure');
  });
  it('an incomplete (null) run never coerces to pass/fail', () => {
    const r = aggregateConclusion(['success', null]);
    expect(r.conclusion).toBe('UNKNOWN');
    expect(r.reason).toBe('incomplete-run');
  });
  it('an unrecognized conclusion stays UNKNOWN and surfaces the value', () => {
    const r = aggregateConclusion(['success', 'wat']);
    expect(r.conclusion).toBe('UNKNOWN');
    expect(r.reason).toBe('unrecognized-conclusion:wat');
  });
  it('empty / non-array → UNKNOWN no-check-runs', () => {
    expect(aggregateConclusion([]).reason).toBe('no-check-runs');
    // @ts-expect-error exercising the defensive non-array guard
    expect(aggregateConclusion(undefined).conclusion).toBe('UNKNOWN');
  });
});

describe('classifyCiRow', () => {
  it('green merge with all-passing checks → success', () => {
    const r = classifyCiRow({
      number: 137,
      mergeCommit: { oid: SHA('a') },
      headRefName: 'fix/x',
      checkRuns: [{ conclusion: 'success' }],
    });
    expect(r).toMatchObject({ pr: 137, ci_conclusion: 'success', merge_oid: SHA('a') });
  });
  it('no merge commit → UNKNOWN no-merge-commit', () => {
    const r = classifyCiRow({ number: 9, mergeCommit: null });
    expect(r).toMatchObject({
      ci_conclusion: 'UNKNOWN',
      reason: 'no-merge-commit',
      merge_oid: null,
    });
  });
  it('merged but check-runs never fetched → UNKNOWN check-runs-not-fetched (not silently green)', () => {
    const r = classifyCiRow({ number: 9, mergeCommit: { oid: SHA('b') }, checkRuns: null });
    expect(r.ci_conclusion).toBe('UNKNOWN');
    expect(r.reason).toBe('check-runs-not-fetched');
  });
  it('records head_ref_deleted', () => {
    const r = classifyCiRow({
      number: 1,
      mergeCommit: { oid: SHA('c') },
      headRefName: 'fix/y',
      headRefDeleted: true,
      checkRuns: [],
    });
    expect(r.head_ref_deleted).toBe(true);
    expect(r.ci_conclusion).toBe('UNKNOWN'); // empty check-runs
  });
});

describe('summarizeCi', () => {
  it('tallies by conclusion', () => {
    const rows = [
      { ci_conclusion: 'success' },
      { ci_conclusion: 'success' },
      { ci_conclusion: 'failure' },
      { ci_conclusion: 'UNKNOWN' },
    ];
    expect(summarizeCi(rows)).toEqual({ total: 4, success: 2, failure: 1, UNKNOWN: 1 });
  });
});

describe('bundleParity (named refs only)', () => {
  it('passes when list-heads equals heads+tags', () => {
    expect(bundleParity({ listHeads: 397, heads: 345, tags: 52 }).ok).toBe(true);
  });
  it('fails on a short/empty bundle', () => {
    const p = bundleParity({ listHeads: 0, heads: 345, tags: 52 });
    expect(p.ok).toBe(false);
    expect(p.expected).toBe(397);
  });
  it('accounts for ambiguous branch/tag name collisions (git drops both copies)', () => {
    // gascity: a branch AND tag both named candidate-a-2978 → 2 refs dropped.
    const p = bundleParity({ listHeads: 395, heads: 345, tags: 52, collisions: 1 });
    expect(p.ok).toBe(true);
    expect(p.expected).toBe(395);
  });
});

describe('parseRefIndex collisions', () => {
  it('counts names present as both a head and a tag', () => {
    const stdout = [
      `${SHA('a')} refs/heads/dup 2026-06-01T00:00:00Z`,
      `${SHA('b')} refs/tags/dup 2026-06-01T00:00:00Z`,
      `${SHA('c')} refs/heads/main 2026-06-01T00:00:00Z`,
    ].join('\n');
    expect(parseRefIndex(stdout).collisions).toBe(1);
  });
});

describe('detachedRecovery', () => {
  it('passes when every detached SHA is recovered (order-independent)', () => {
    const a = SHA('1');
    const b = SHA('2');
    expect(detachedRecovery([a, b], [b, a]).ok).toBe(true);
  });
  it('fails and names the missing SHAs', () => {
    const a = SHA('1');
    const b = SHA('2');
    const r = detachedRecovery([a, b], [a]);
    expect(r.ok).toBe(false);
    expect(r.missing).toEqual([b]);
    expect(r.recovered).toBe(1);
  });
  it('trivially passes with no detached heads', () => {
    expect(detachedRecovery([], []).ok).toBe(true);
  });
});

describe('floorCheck', () => {
  it('passes at or above the floor', () => {
    expect(floorCheck('gascity', 345).ok).toBe(true);
    expect(floorCheck('scix', 19).ok).toBe(true); // exactly at floor
  });
  it('fails below the floor and reports the gap', () => {
    const f = floorCheck('packs', 10);
    expect(f.ok).toBe(false);
    expect(f).toMatchObject({ applicable: true, floor: 50, count: 10 });
  });
  it('a store with no floor key is not gated', () => {
    const f = floorCheck(null, 0);
    expect(f.applicable).toBe(false);
    expect(f.ok).toBe(true);
  });
  it('floor table matches the architect expected-floor table', () => {
    expect(EXPECTED_FLOORS).toEqual({ gascity: 83, packs: 50, scix: 19, zelda: 1 });
  });
});

describe('isSessionStore', () => {
  it('floor-keyed stores are always session stores', () => {
    expect(isSessionStore({ floorKey: 'gascity', refnames: [] })).toBe(true);
  });
  it('a store with bd-/gc- refs is a session store', () => {
    expect(isSessionStore({ floorKey: null, refnames: ['refs/heads/bd-abc.1'] })).toBe(true);
    expect(isSessionStore({ floorKey: null, refnames: ['refs/heads/gc-xyz'] })).toBe(true);
  });
  it('a plain repo with no session refs is not bundled', () => {
    expect(isSessionStore({ floorKey: null, refnames: ['refs/heads/main', 'refs/tags/v1'] })).toBe(
      false
    );
  });
});

describe('dedupeStores (collapse shared object stores)', () => {
  it('groups checkouts that share a common-dir and keeps the floor key', () => {
    const stores = dedupeStores([
      {
        rig: 'gascity',
        dir: '/home/ds/gascity-main',
        commonDir: '/home/ds/gascity/.git',
        floorKey: 'gascity',
      },
      {
        rig: 'gascity_alt',
        dir: '/home/ds/gascity-alt',
        commonDir: '/home/ds/gascity/.git',
        floorKey: null,
      },
      {
        rig: 'mem',
        dir: '/home/ds/projects/mem',
        commonDir: '/home/ds/projects/mem/.git',
        floorKey: null,
      },
    ]);
    expect(stores).toHaveLength(2);
    const gas = stores.find(s => s.commonDir === '/home/ds/gascity/.git')!;
    expect(gas.rigs).toEqual(['gascity', 'gascity_alt']);
    expect(gas.floorKey).toBe('gascity');
    expect(gas.dir).toBe('/home/ds/gascity-main'); // anchored on the floor-owning checkout
  });
  it('a floor key seen on a later checkout still wins', () => {
    const stores = dedupeStores([
      { rig: 'plain', dir: '/d/plain', commonDir: '/shared/.git', floorKey: null },
      { rig: 'zeldascension', dir: '/d/zelda', commonDir: '/shared/.git', floorKey: 'zelda' },
    ]);
    expect(stores[0].floorKey).toBe('zelda');
    expect(stores[0].dir).toBe('/d/zelda');
  });
});

describe('parseRefIndex', () => {
  it('counts heads and tags and keeps refnames', () => {
    const stdout = [
      `${SHA('a')} refs/heads/main 2026-06-01T00:00:00Z`,
      `${SHA('b')} refs/heads/bd-x.1 2026-06-02T00:00:00Z`,
      `${SHA('c')} refs/tags/v1 2026-06-03T00:00:00Z`,
      '',
    ].join('\n');
    const idx = parseRefIndex(stdout);
    expect(idx.total).toBe(3);
    expect(idx.heads).toBe(2);
    expect(idx.tags).toBe(1);
    expect(idx.refnames).toContain('refs/heads/bd-x.1');
  });
});

describe('parseDetachedHeads', () => {
  it('extracts only the detached worktree HEAD SHAs', () => {
    const porcelain = [
      'worktree /home/ds/gascity-main',
      `HEAD ${SHA('a')}`,
      'branch refs/heads/main',
      '',
      'worktree /home/ds/gascity-worktrees/polecat-1-ghas3',
      `HEAD ${SHA('d')}`,
      'detached',
      '',
      'worktree /home/ds/gascity-worktrees/polecat-2',
      `HEAD ${SHA('e')}`,
      'branch refs/heads/fix/foo',
      '',
    ].join('\n');
    expect(parseDetachedHeads(porcelain)).toEqual([SHA('d')]);
  });
  it('returns [] when no worktree is detached', () => {
    expect(parseDetachedHeads(`worktree /x\nHEAD ${SHA('a')}\nbranch refs/heads/main\n`)).toEqual(
      []
    );
  });
});

describe('RIG_FLOOR_KEYS', () => {
  it('maps the four session rigs to floor keys', () => {
    expect(RIG_FLOOR_KEYS).toEqual({
      gascity: 'gascity',
      gpk: 'packs',
      scix_experiments: 'scix',
      zeldascension: 'zelda',
    });
  });
});
