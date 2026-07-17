import { describe, expect, it } from 'vitest';

import {
  branchSlug,
  classifyMergeBase,
  DROP_NO_MERGE_BASE,
  parseForEachRef,
  resolveLiveRefs,
  summarize,
  UNDECIDED_GIT_UNAVAILABLE,
  UNDECIDED_MERGE_BASE_GIT_UNAVAILABLE,
  UNDECIDED_MERGE_BASE_OBJECT_UNREADABLE,
  UNDECIDED_OBJECT_UNREADABLE,
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

  it('fails an impossible non-ancestor base SAFE to undecided — never kept, never a drop', () => {
    // base_sha is derived as merge-base(branch, authRef), so it is BY DEFINITION
    // an ancestor of authRef: is_ancestor can only be true or a fault. A `false`
    // is a mathematical impossibility at this call site (mem-zzzl4), so there is
    // no measurable "off-authoritative base" rate to tally. If one ever surfaces
    // we fail safe: route it to undecided under git-unavailable (an answer we
    // cannot attribute → needs-investigation), so it is never silently kept and
    // is never blamed on this rig's checkout (mem-zwmuq).
    const r = classifyMergeBase({ ...base, is_ancestor: false });
    expect(r.kept).toBeUndefined();
    expect(r.drop).toBeUndefined();
    expect(r.undecided).toEqual({
      work_id: 'gc-0a6',
      refname: 'refs/heads/bd-gc-0a6',
      cause: UNDECIDED_GIT_UNAVAILABLE,
    });
  });

  it('DROPS a missing merge-base as the DECAY signal — the only measurable drop', () => {
    const r = classifyMergeBase({ ...base, base_sha: null, is_ancestor: false });
    expect(r.drop?.reason).toBe(DROP_NO_MERGE_BASE);
  });

  it('attributes an object-unreadable fault to the CHECKOUT-LOCAL cause, not a drop', () => {
    // Git ran but could not read this base's objects in this checkout (exit 128
    // on a pruned/GC'd object). That is checkout-local and recoverable — the
    // Day-0 frozen bundle backstops it — so it reports under
    // object_unreadable_in_checkout, distinct from an environment-wide fault
    // (mem-zwmuq).
    const r = classifyMergeBase({ ...base, is_ancestor: 'object-unreadable' });
    expect(r.undecided).toEqual({
      work_id: 'gc-0a6',
      refname: 'refs/heads/bd-gc-0a6',
      cause: UNDECIDED_OBJECT_UNREADABLE,
    });
    expect(r.drop).toBeUndefined();
    expect(r.kept).toBeUndefined();
  });

  it('attributes a git-unavailable fault to the ENVIRONMENT-WIDE cause, not the checkout', () => {
    // Git could not run at all (missing binary, signal, maxBuffer overrun): a
    // non-exit failure with no status. The remedy is to rerun once the toolchain
    // is healthy, NOT to reach for the Day-0 bundle — so it must not carry the
    // _in_checkout suffix that would misattribute it to this rig (mem-zwmuq).
    const r = classifyMergeBase({ ...base, is_ancestor: 'git-unavailable' });
    expect(r.undecided?.cause).toBe(UNDECIDED_GIT_UNAVAILABLE);
    expect(r.undecided?.cause).not.toContain('_in_checkout');
    expect(r.drop).toBeUndefined();
    expect(r.kept).toBeUndefined();
  });

  it('checks no-merge-base BEFORE ancestry, since the question was never asked', () => {
    // Ordering is load-bearing. With no merge-base there is no base to ask
    // about, so `is_ancestor` is meaningless and must not be read — even the
    // keep-worthy `true` from `base` still drops as DECAY, not an unanswerable
    // ancestry.
    const r = classifyMergeBase({ ...base, base_sha: null });
    expect(r.drop?.reason).toBe(DROP_NO_MERGE_BASE);
    expect(r.undecided).toBeUndefined();
  });

  it('attributes a merge-base object-unreadable fault to its OWN cause, never DROP_NO_MERGE_BASE (mem-f0n07)', () => {
    // The bead's core fix: git faulting while COMPUTING the merge-base (exit 128
    // on a pruned object, wrapped as a MergeBaseFault) is undecided, not the null
    // decay signal. Folding it into DROP_NO_MERGE_BASE would fabricate a decay
    // data point out of a measurement fault — the merge-base twin of the ancestry
    // fix. The cause is the checkout-local one (use the Day-0 bundle).
    const r = classifyMergeBase({ ...base, base_sha: { fault: 'object-unreadable' } });
    expect(r.undecided).toEqual({
      work_id: 'gc-0a6',
      refname: 'refs/heads/bd-gc-0a6',
      cause: UNDECIDED_MERGE_BASE_OBJECT_UNREADABLE,
    });
    expect(r.drop).toBeUndefined();
    expect(r.kept).toBeUndefined();
  });

  it('attributes a merge-base git-unavailable fault to the environment-wide cause', () => {
    // The merge-base twin of the ancestry git-unavailable arm: git could not run
    // the computation at all, so the remedy is a rerun, not the Day-0 bundle — no
    // _in_checkout suffix.
    const r = classifyMergeBase({ ...base, base_sha: { fault: 'git-unavailable' } });
    expect(r.undecided?.cause).toBe(UNDECIDED_MERGE_BASE_GIT_UNAVAILABLE);
    expect(r.undecided?.cause).not.toContain('_in_checkout');
    expect(r.drop).toBeUndefined();
    expect(r.kept).toBeUndefined();
  });

  it('settles a merge-base fault from base_sha ALONE — is_ancestor is never consulted', () => {
    // The fault arm precedes both the null and the ancestry arms, so a fault is
    // decided from base_sha before is_ancestor is read. A keep-worthy `true`
    // cannot rescue a base git could not even compute.
    const r = classifyMergeBase({
      ...base,
      base_sha: { fault: 'object-unreadable' },
      is_ancestor: true,
    });
    expect(r.undecided?.cause).toBe(UNDECIDED_MERGE_BASE_OBJECT_UNREADABLE);
    expect(r.kept).toBeUndefined();
  });

  it('keeps the merge-base fault causes DISTINCT from the ancestry fault causes (auditable per-remedy)', () => {
    // undecided_by_cause must not collapse a merge-base-computation fault into an
    // ancestry-probe fault: they are different git calls with the same remedy
    // vocabulary but distinct buckets, so an operator can tell which probe faulted.
    expect(UNDECIDED_MERGE_BASE_OBJECT_UNREADABLE).not.toBe(UNDECIDED_OBJECT_UNREADABLE);
    expect(UNDECIDED_MERGE_BASE_GIT_UNAVAILABLE).not.toBe(UNDECIDED_GIT_UNAVAILABLE);
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
        is_ancestor: 'object-unreadable',
      }),
    ];
    const report = summarize(100, results);
    expect(report.resolved).toBe(2);
    expect(report.kept).toBe(1);
    expect(report.dropped).toBe(0);
    expect(report.drops_by_reason).toEqual({});
    expect(report.undecided).toBe(1);
    expect(report.undecided_by_cause).toEqual({ [UNDECIDED_OBJECT_UNREADABLE]: 1 });
  });

  it('tallies distinct faults into SEPARATE cause buckets — not one opaque key (mem-zwmuq)', () => {
    // The bead's core assertion: undecided_by_cause must be able to hold more
    // than one key. A checkout-local unreadable object and an environment-wide
    // git failure are different facts with different remedies, so an operator
    // reading the headline can tell "fall back to the Day-0 bundle" from "rerun".
    const results = [
      classifyMergeBase({
        work_id: 'a',
        refname: 'refs/heads/bd-a',
        branch_sha: SHA('a'),
        base_sha: SHA('1'),
        is_ancestor: 'object-unreadable',
      }),
      classifyMergeBase({
        work_id: 'b',
        refname: 'refs/heads/bd-b',
        branch_sha: SHA('b'),
        base_sha: SHA('2'),
        is_ancestor: 'git-unavailable',
      }),
    ];
    const report = summarize(100, results);
    expect(report.undecided).toBe(2);
    expect(report.undecided_by_cause).toEqual({
      [UNDECIDED_OBJECT_UNREADABLE]: 1,
      [UNDECIDED_GIT_UNAVAILABLE]: 1,
    });
  });

  it('keeps undecided refs out of every drop count, so no fault reads as decay', () => {
    // A git fault degrades to `undecided`; it must not land in `dropped` at all.
    // The only measurable drop is no-merge-base decay — a fault is not that.
    const report = summarize(
      10,
      [null, null].map((_, i) =>
        classifyMergeBase({
          work_id: `u${i}`,
          refname: `refs/heads/bd-u${i}`,
          branch_sha: SHA('a'),
          base_sha: SHA('b'),
          is_ancestor: 'git-unavailable',
        })
      )
    );
    expect(report.drops_by_reason).toEqual({});
    expect(report.dropped).toBe(0);
    expect(report.undecided).toBe(2);
  });

  it('is 0% with an empty result set', () => {
    const report = summarize(100, []);
    expect(report).toMatchObject({ resolved: 0, kept: 0, dropped: 0, pct: 0 });
  });
});
