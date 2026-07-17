import { describe, expect, it } from 'vitest';

import {
  branchSlug,
  classifyMergeBase,
  DROP_BASE_NOT_ANCESTOR,
  DROP_NO_MERGE_BASE,
  parseForEachRef,
  resolveLiveRefs,
  summarize,
  UNDECIDED_ANCESTRY_UNANSWERABLE,
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

  it('UNDECIDES an unanswerable ancestry — it is not a drop, and not the R3 alarm', () => {
    // The bug this arm exists for (mem-y2x7n): a git fault reaching the gate as
    // `false` was indistinguishable from a genuine "this base is off the
    // authoritative branch" — i.e. it tripped the R3 CORRUPTION ALARM with a
    // fabricated data point. An unanswerable case is not a negative one.
    const r = classifyMergeBase({ ...base, is_ancestor: null });
    expect(r.undecided).toEqual({
      work_id: 'gc-0a6',
      refname: 'refs/heads/bd-gc-0a6',
      cause: UNDECIDED_ANCESTRY_UNANSWERABLE,
    });
    expect(r.drop).toBeUndefined();
    expect(r.kept).toBeUndefined();
  });

  it('checks no-merge-base BEFORE ancestry, since the question was never asked', () => {
    // Ordering is load-bearing. With no merge-base there is no base to ask
    // about, so `is_ancestor` is meaningless and must not be read — the result
    // is DECAY, not an unanswerable ancestry.
    const r = classifyMergeBase({ ...base, base_sha: null, is_ancestor: null });
    expect(r.drop?.reason).toBe(DROP_NO_MERGE_BASE);
    expect(r.undecided).toBeUndefined();
  });

  it('does not let the null ancestry fall through to the non-ancestor drop', () => {
    // The inverse ordering hazard: `!input.is_ancestor` is true for null as
    // well as false, so testing it before the null check would swallow the
    // undecided arm into DROP_BASE_NOT_ANCESTOR — reporting a git fault as the
    // R3 alarm, strictly worse than the status quo this bead fixes.
    const r = classifyMergeBase({ ...base, is_ancestor: null });
    expect(r.drop?.reason).not.toBe(DROP_BASE_NOT_ANCESTOR);
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
    expect(report.undecided).toBe(0);
    expect(report.drops_by_reason).toEqual({ [DROP_NO_MERGE_BASE]: 1 });
    expect(report.undecided_by_cause).toEqual({});
    expect(report.pct).toBeCloseTo((100 * 1) / 2799, 6);
  });

  it('counts an undecided ref in its OWN bucket, never as a drop', () => {
    // Without a bucket of its own an undecided result would hit neither branch
    // of the accumulator and vanish silently, while still sitting in
    // `resolved` — the fault would be invisible in the report AND deflate the
    // headline. Both halves are asserted here (mem-y2x7n).
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
        base_sha: SHA('2'),
        is_ancestor: null,
      }),
    ];
    const report = summarize(100, results);
    expect(report.resolved).toBe(2);
    expect(report.kept).toBe(1);
    expect(report.dropped).toBe(0);
    expect(report.drops_by_reason).toEqual({});
    expect(report.undecided).toBe(1);
    expect(report.undecided_by_cause).toEqual({ [UNDECIDED_ANCESTRY_UNANSWERABLE]: 1 });
  });

  it('keeps an undecided ref out of the R3 drop count, so the alarm stays clean', () => {
    // The R3 signal is read as "a non-zero count here is the alarm". A git
    // fault must not be able to raise it.
    const report = summarize(
      10,
      [null, null].map((_, i) =>
        classifyMergeBase({
          work_id: `u${i}`,
          refname: `refs/heads/bd-u${i}`,
          branch_sha: SHA('a'),
          base_sha: SHA('b'),
          is_ancestor: null,
        })
      )
    );
    expect(report.drops_by_reason[DROP_BASE_NOT_ANCESTOR]).toBeUndefined();
    expect(report.dropped).toBe(0);
    expect(report.undecided).toBe(2);
  });

  it('is 0% with an empty result set', () => {
    const report = summarize(100, []);
    expect(report).toMatchObject({ resolved: 0, kept: 0, dropped: 0, pct: 0 });
  });
});
