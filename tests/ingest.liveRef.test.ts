import { describe, expect, it } from 'vitest';

import {
  branchSlug,
  classifyMergeBase,
  DROP_BASE_NOT_ANCESTOR,
  DROP_NO_MERGE_BASE,
  parseForEachRef,
  resolveLiveRefs,
  summarize,
  type MergeBaseInput,
} from '../src/ingest/liveRef.js';

const SHA = (c: string): string => c.repeat(40);

describe('parseForEachRef', () => {
  const dump = [
    `${SHA('a')} refs/heads/bd-gc-0a6 2026-06-08T18:25:59-04:00`,
    `${SHA('b')} refs/heads/main 2026-06-01T00:00:00-04:00`,
    '', // trailing blank line tolerated
  ].join('\n');

  it('parses sha / refname / date triples', () => {
    const refs = parseForEachRef(dump);
    expect(refs).toEqual([
      { sha: SHA('a'), refname: 'refs/heads/bd-gc-0a6', date: '2026-06-08T18:25:59-04:00' },
      { sha: SHA('b'), refname: 'refs/heads/main', date: '2026-06-01T00:00:00-04:00' },
    ]);
  });
  it('returns [] for empty input', () => {
    expect(parseForEachRef('')).toEqual([]);
    expect(parseForEachRef('\n\n')).toEqual([]);
  });
});

describe('branchSlug', () => {
  it('strips the refs/heads/bd- prefix', () => {
    expect(branchSlug('refs/heads/bd-gc-0a6')).toBe('gc-0a6');
  });
  it('returns null for non-bd heads and non-heads', () => {
    expect(branchSlug('refs/heads/main')).toBeNull();
    expect(branchSlug('refs/heads/fix/foo')).toBeNull();
    expect(branchSlug('refs/adopt-pr/pr-1216-head')).toBeNull();
    expect(branchSlug('refs/tags/bd-gc-0a6')).toBeNull();
  });
});

describe('resolveLiveRefs', () => {
  const workIds = ['gc-0a6', 'gc-3mqde', 'gc-unmatched'];
  const refs = parseForEachRef(
    [
      `${SHA('a')} refs/heads/bd-gc-0a6 2026-06-08T00:00:00-04:00`,
      `${SHA('c')} refs/heads/bd-gc-3mqde 2026-06-09T00:00:00-04:00`,
      `${SHA('d')} refs/heads/bd-gc-3mqde-rebase 2026-06-10T00:00:00-04:00`,
      `${SHA('e')} refs/heads/bd-gc-other 2026-06-11T00:00:00-04:00`,
    ].join('\n')
  );

  it('joins each work_id to its exact bd-<work_id> head', () => {
    const resolved = resolveLiveRefs(workIds, refs);
    expect(resolved).toEqual([
      { work_id: 'gc-0a6', refname: 'refs/heads/bd-gc-0a6', sha: SHA('a') },
      { work_id: 'gc-3mqde', refname: 'refs/heads/bd-gc-3mqde', sha: SHA('c') },
    ]);
  });

  it('does NOT match a suffixed branch-root (bd-gc-3mqde-rebase is a different slug)', () => {
    const resolved = resolveLiveRefs(['gc-3mqde'], refs);
    expect(resolved).toHaveLength(1);
    expect(resolved[0].refname).toBe('refs/heads/bd-gc-3mqde');
  });

  it('is case-insensitive on the work-id token', () => {
    const resolved = resolveLiveRefs(['GC-0A6'], refs);
    expect(resolved).toHaveLength(1);
    expect(resolved[0].sha).toBe(SHA('a'));
  });

  it('skips work_ids with no live ref', () => {
    expect(resolveLiveRefs(['gc-nope'], refs)).toEqual([]);
  });
});

describe('classifyMergeBase', () => {
  const base: MergeBaseInput = {
    work_id: 'gc-0a6',
    refname: 'refs/heads/bd-gc-0a6',
    branch_sha: SHA('a'),
    base_sha: SHA('b'),
    is_ancestor: true,
  };

  it('keeps a base that is an ancestor of the authoritative branch', () => {
    const r = classifyMergeBase(base);
    expect(r.kept).toEqual({
      work_id: 'gc-0a6',
      refname: 'refs/heads/bd-gc-0a6',
      branch_sha: SHA('a'),
      base_sha: SHA('b'),
    });
    expect(r.drop).toBeUndefined();
  });

  it('DROPS a resolved-but-non-ancestor base as the R3 corruption signal', () => {
    const r = classifyMergeBase({ ...base, is_ancestor: false });
    expect(r.kept).toBeUndefined();
    expect(r.drop).toEqual({
      work_id: 'gc-0a6',
      refname: 'refs/heads/bd-gc-0a6',
      reason: DROP_BASE_NOT_ANCESTOR,
    });
  });

  it('DROPS a missing merge-base as the DECAY signal, distinct from R3', () => {
    const r = classifyMergeBase({ ...base, base_sha: null, is_ancestor: false });
    expect(r.drop?.reason).toBe(DROP_NO_MERGE_BASE);
  });
});

describe('summarize', () => {
  it('reports the real live-ref percentage against the stated denominator', () => {
    const results = [
      classifyMergeBase({
        work_id: 'a',
        refname: 'refs/heads/bd-a',
        branch_sha: SHA('a'),
        base_sha: SHA('1'),
        is_ancestor: true,
      }),
      classifyMergeBase({
        work_id: 'b',
        refname: 'refs/heads/bd-b',
        branch_sha: SHA('b'),
        base_sha: null,
        is_ancestor: false,
      }),
    ];
    const report = summarize(2799, results);
    expect(report.denominator).toBe(2799);
    expect(report.resolved).toBe(2);
    expect(report.kept).toBe(1);
    expect(report.dropped).toBe(1);
    expect(report.drops_by_reason).toEqual({ [DROP_NO_MERGE_BASE]: 1 });
    expect(report.pct).toBeCloseTo((100 * 1) / 2799, 6);
  });

  it('is 0% with an empty result set', () => {
    const report = summarize(100, []);
    expect(report).toMatchObject({ resolved: 0, kept: 0, dropped: 0, pct: 0 });
  });
});
