import { describe, expect, it } from 'vitest';

import {
  combineRefVerdicts,
  hasPower,
  joinBranches,
  MIN_DECIDED_FOR_RATE,
  parseRef,
  tallyPooled,
  tallyRig,
  wilsonInterval,
  type DecidedBranch,
  type JoinOptions,
} from '../src/ingest/falseClose.js';
import type { LandedContentResult, LandedContentVerdict } from '../src/ingest/landedContent.js';

describe('parseRef', () => {
  it('parses a local head', () => {
    expect(parseRef('refs/heads/work/mem-cv06b')).toEqual({
      scope: 'local',
      name: 'work/mem-cv06b',
      refname: 'refs/heads/work/mem-cv06b',
    });
  });

  it('parses a remote-tracking branch, splitting remote from name', () => {
    expect(parseRef('refs/remotes/upstream/bd-gc-0a6')).toEqual({
      scope: 'remote',
      remote: 'upstream',
      name: 'bd-gc-0a6',
      refname: 'refs/remotes/upstream/bd-gc-0a6',
    });
  });

  it('keeps a slashed branch name intact under a remote', () => {
    expect(parseRef('refs/remotes/origin/work/mem-x1')).toMatchObject({
      remote: 'origin',
      name: 'work/mem-x1',
    });
  });

  it('rejects refs/remotes/<remote>/HEAD — an alias, not a branch', () => {
    // Counting it would double-count whichever branch it points at.
    expect(parseRef('refs/remotes/origin/HEAD')).toBeNull();
  });

  it('rejects refs that are neither heads nor remote-tracking branches', () => {
    expect(parseRef('refs/tags/v1.0')).toBeNull();
    expect(parseRef('refs/stash')).toBeNull();
    expect(parseRef('refs/notes/commits')).toBeNull();
  });
});

describe('joinBranches', () => {
  const base: JoinOptions = {
    refnames: [],
    closedIds: new Set(['mem-cv06b', 'mem-zfeys', 'gc-0a6']),
    integration: 'main',
    authoritativeRemote: 'origin',
  };

  const join = (refnames: string[], over: Partial<JoinOptions> = {}) =>
    joinBranches({ ...base, refnames, ...over });

  it('joins a closed bead to its local branch by exact token', () => {
    const out = join(['refs/heads/work/mem-cv06b']);
    expect(out.joined).toEqual([{ work_id: 'mem-cv06b', ref: 'work/mem-cv06b', scope: 'local' }]);
    expect(out.skipped).toEqual([]);
  });

  it('joins across prefix conventions — no prefix rule is used', () => {
    // gascity beads are `gc-*` but its branches are `bd-gc-*`. A prefix rule
    // would miss this join entirely; whole-token intersection catches it.
    const out = join(['refs/heads/bd-gc-0a6']);
    expect(out.joined).toEqual([{ work_id: 'gc-0a6', ref: 'bd-gc-0a6', scope: 'local' }]);
  });

  it('skips a branch naming no closed bead', () => {
    const out = join(['refs/heads/feat/some-thing']);
    expect(out.joined).toEqual([]);
    expect(out.skipped).toEqual([{ refname: 'refs/heads/feat/some-thing', reason: 'no-work-id' }]);
  });

  it('skips a branch naming two closed beads rather than guessing', () => {
    const out = join(['refs/heads/work/mem-cv06b-mem-zfeys']);
    expect(out.joined).toEqual([]);
    expect(out.skipped).toEqual([
      { refname: 'refs/heads/work/mem-cv06b-mem-zfeys', reason: 'ambiguous-multi-id' },
    ]);
  });

  it('skips the integration branch itself', () => {
    const out = join(['refs/heads/main']);
    expect(out.skipped).toEqual([{ refname: 'refs/heads/main', reason: 'integration-branch' }]);
  });

  it('skips a remote-tracking copy of the integration branch', () => {
    const out = join(['refs/remotes/origin/main']);
    expect(out.skipped).toEqual([
      { refname: 'refs/remotes/origin/main', reason: 'integration-branch' },
    ]);
  });

  it("skips a fork's copy — only the authoritative remote can attest landing", () => {
    // gascity's checkout carries fourteen remotes, most of them contributor
    // forks. A branch surviving on a fork says nothing about upstream.
    const out = join(['refs/remotes/someforks/work/mem-cv06b']);
    expect(out.joined).toEqual([]);
    expect(out.skipped).toEqual([
      { refname: 'refs/remotes/someforks/work/mem-cv06b', reason: 'non-authoritative-remote' },
    ]);
  });

  it('treats every remote ref as non-authoritative when no remote matched the slug', () => {
    // null is a real state (no remote matches the rig's slug), not a sentinel:
    // the join falls back to local heads alone rather than trusting a fork.
    const out = join(['refs/remotes/origin/work/mem-cv06b', 'refs/heads/work/mem-zfeys'], {
      authoritativeRemote: null,
    });
    expect(out.joined).toEqual([{ work_id: 'mem-zfeys', ref: 'work/mem-zfeys', scope: 'local' }]);
    expect(out.skipped).toEqual([
      { refname: 'refs/remotes/origin/work/mem-cv06b', reason: 'non-authoritative-remote' },
    ]);
  });

  it('prefixes an authoritative remote ref with its remote name for git', () => {
    const out = join(['refs/remotes/origin/work/mem-cv06b']);
    expect(out.joined).toEqual([
      { work_id: 'mem-cv06b', ref: 'origin/work/mem-cv06b', scope: 'remote' },
    ]);
  });

  it('prefers the local head when a bead has both, counting the bead once', () => {
    const out = join(['refs/remotes/origin/work/mem-cv06b', 'refs/heads/work/mem-cv06b']);
    expect(out.joined).toEqual([{ work_id: 'mem-cv06b', ref: 'work/mem-cv06b', scope: 'local' }]);
    // The displaced remote is skipped under its raw refname, identical to the
    // opposite-ordering case below: a skip's refname must not change shape with
    // ref discovery order.
    expect(out.skipped).toEqual([
      { refname: 'refs/remotes/origin/work/mem-cv06b', reason: 'duplicate-of-local' },
    ]);
  });

  it('prefers the local head regardless of ref ordering', () => {
    const out = join(['refs/heads/work/mem-cv06b', 'refs/remotes/origin/work/mem-cv06b']);
    expect(out.joined).toEqual([{ work_id: 'mem-cv06b', ref: 'work/mem-cv06b', scope: 'local' }]);
    expect(out.skipped).toEqual([
      { refname: 'refs/remotes/origin/work/mem-cv06b', reason: 'duplicate-of-local' },
    ]);
  });

  it('falls back to the authoritative remote when no local head survives', () => {
    const out = join(['refs/remotes/origin/work/mem-cv06b']);
    expect(out.joined[0].scope).toBe('remote');
  });

  it('does not match an open bead — closedIds is the whole population', () => {
    const out = join(['refs/heads/work/mem-open1'], { closedIds: new Set(['mem-cv06b']) });
    expect(out.joined).toEqual([]);
  });

  it('matches a dotted child id without matching its parent', () => {
    // Boundary-exactness: `mem-75t` must not match inside `mem-75t.12`.
    const out = join(['refs/heads/mem-75t.12-sha'], {
      closedIds: new Set(['mem-75t', 'mem-75t.12']),
    });
    expect(out.joined).toEqual([{ work_id: 'mem-75t.12', ref: 'mem-75t.12-sha', scope: 'local' }]);
  });

  it('joins a mixed-case rig, reporting the id as the STORE spells it', () => {
    // EnterpriseBench keys beads `EnterpriseBench-<id>` and names branches the
    // same way, yet a case-sensitive lookup joined 0 of its 1509 closed beads:
    // candidates are lowercased, so the id set must be keyed that way too. The
    // reported work_id keeps the store's canonical casing.
    const out = join(['refs/heads/fix/EnterpriseBench-keaq'], {
      closedIds: new Set(['EnterpriseBench-keaq']),
    });
    expect(out.joined).toEqual([
      { work_id: 'EnterpriseBench-keaq', ref: 'fix/EnterpriseBench-keaq', scope: 'local' },
    ]);
  });

  it('joins when the BRANCH is cased differently from the bead id', () => {
    const out = join(['refs/heads/enterprisebench-s7oe'], {
      closedIds: new Set(['EnterpriseBench-s7oe']),
    });
    expect(out.joined[0].work_id).toBe('EnterpriseBench-s7oe');
  });

  it('preserves an underscore inside an id — separators are never normalised', () => {
    // The underscore rigs key their beads with `_` in the id itself. Cutting
    // spans on a canonical `-` would rewrite every one into a string no bead
    // is keyed by, silently zeroing those rigs.
    const out = join(['refs/heads/work/scix_experiments-0c73-retry'], {
      closedIds: new Set(['scix_experiments-0c73']),
    });
    expect(out.joined).toEqual([
      { work_id: 'scix_experiments-0c73', ref: 'work/scix_experiments-0c73-retry', scope: 'local' },
    ]);
  });

  it('matches an id embedded mid-name, not only at the start', () => {
    const out = join(['refs/heads/bd-gc-13qn0-3862-hook-upsert'], {
      closedIds: new Set(['gc-13qn0']),
    });
    expect(out.joined).toEqual([
      { work_id: 'gc-13qn0', ref: 'bd-gc-13qn0-3862-hook-upsert', scope: 'local' },
    ]);
  });

  it('never matches a mid-segment substring — spans stay segment-aligned', () => {
    // `mem-cv0` is not a span of `work/mem-cv06b`; a substring rule would
    // attribute this branch to the wrong bead.
    const out = join(['refs/heads/work/mem-cv06b'], { closedIds: new Set(['mem-cv0']) });
    expect(out.joined).toEqual([]);
    expect(out.skipped).toEqual([{ refname: 'refs/heads/work/mem-cv06b', reason: 'no-work-id' }]);
  });

  it('sorts by work_id so a run is reproducible independent of git ref order', () => {
    const out = join(['refs/heads/work/mem-zfeys', 'refs/heads/bd-gc-0a6']);
    expect(out.joined.map(j => j.work_id)).toEqual(['gc-0a6', 'mem-zfeys']);
  });

  it('ignores tags and stash entries entirely', () => {
    const out = join(['refs/tags/work/mem-cv06b', 'refs/stash']);
    expect(out.joined).toEqual([]);
    expect(out.skipped).toEqual([]);
  });
});

describe('wilsonInterval', () => {
  it('returns the full [0,1] interval for n=0 — unmeasured, not zero', () => {
    expect(wilsonInterval(0, 0)).toEqual({ low: 0, high: 1 });
  });

  it('stays inside [0,1] at k=0 where the normal approximation goes negative', () => {
    const ci = wilsonInterval(0, 10);
    expect(ci.low).toBe(0);
    expect(ci.high).toBeGreaterThan(0);
    expect(ci.high).toBeLessThan(1);
  });

  it('stays inside [0,1] at k=n', () => {
    const ci = wilsonInterval(10, 10);
    expect(ci.low).toBeGreaterThan(0);
    expect(ci.high).toBe(1);
  });

  it('brackets the point estimate', () => {
    const ci = wilsonInterval(5, 20);
    expect(ci.low).toBeLessThan(0.25);
    expect(ci.high).toBeGreaterThan(0.25);
  });

  it('matches the published Wilson value for 2/20 at 95%', () => {
    // Textbook closed form: centre .16445, half-width .13657.
    const ci = wilsonInterval(2, 20);
    expect(ci.low).toBeCloseTo(0.0279, 3);
    expect(ci.high).toBeCloseTo(0.301, 3);
  });

  it('narrows as n grows at a fixed rate', () => {
    const small = wilsonInterval(5, 20);
    const large = wilsonInterval(50, 200);
    expect(large.high - large.low).toBeLessThan(small.high - small.low);
  });
});

describe('combineRefVerdicts', () => {
  const rv = (ref: string, verdict: LandedContentVerdict) => ({
    ref,
    result: { verdict } as LandedContentResult,
  });

  it('takes a landed verdict from ANY ref — landed on one ref is landed', () => {
    // mem's local main is 53 commits ahead of origin/main; work that landed
    // locally must not read `absent` because the remote has not seen it.
    const out = combineRefVerdicts([rv('origin/main', 'absent'), rv('main', 'landed-direct')]);
    expect(out).toMatchObject({ ref: 'main' });
    expect(out.result.verdict).toBe('landed-direct');
  });

  it('takes a landed verdict from the REMOTE when local is behind', () => {
    // gpk's local main is 14 commits behind upstream/main — the mirror case.
    const out = combineRefVerdicts([rv('main', 'absent'), rv('upstream/main', 'landed-squashed')]);
    expect(out).toMatchObject({ ref: 'upstream/main' });
  });

  it('never lets an undecidable ref outrank one that answered', () => {
    const out = combineRefVerdicts([rv('main', 'undecidable'), rv('origin/main', 'absent')]);
    expect(out.result.verdict).toBe('absent');
  });

  it('prefers partial over absent — a half-landing is evidence, not nothing', () => {
    const out = combineRefVerdicts([rv('main', 'absent'), rv('origin/main', 'partial')]);
    expect(out.result.verdict).toBe('partial');
  });

  it('prefers any landed-* over partial', () => {
    const out = combineRefVerdicts([rv('main', 'partial'), rv('origin/main', 'landed-equivalent')]);
    expect(out.result.verdict).toBe('landed-equivalent');
  });

  it('reports undecidable only when every ref was undecidable', () => {
    const out = combineRefVerdicts([rv('main', 'undecidable'), rv('origin/main', 'undecidable')]);
    expect(out.result.verdict).toBe('undecidable');
  });

  it('passes a single ref through unchanged', () => {
    expect(combineRefVerdicts([rv('main', 'absent')])).toMatchObject({ ref: 'main' });
  });

  it('keeps the first ref when two tie, for a deterministic run', () => {
    const out = combineRefVerdicts([rv('main', 'absent'), rv('origin/main', 'absent')]);
    expect(out.ref).toBe('main');
  });

  it('throws rather than inventing a verdict when no ref was consulted', () => {
    // Silently returning `absent` here would count an unmeasured branch as a
    // false close — the exact contamination this module exists to detect.
    expect(() => combineRefVerdicts([])).toThrow(/no refs/);
  });
});

/** A decided branch carrying `verdict`, with the anchors the tally ignores. */
const decided = (work_id: string, verdict: LandedContentVerdict, cause?: string): DecidedBranch => {
  const result = { verdict, ...(cause === undefined ? {} : { cause }) } as LandedContentResult;
  return { work_id, ref: `work/${work_id}`, scope: 'local', result };
};

describe('tallyRig', () => {
  it('counts absent and partial as not-landed, landed-* as landed', () => {
    const t = tallyRig('mem', 100, [
      decided('a', 'landed-direct'),
      decided('b', 'landed-equivalent'),
      decided('c', 'landed-squashed'),
      decided('d', 'partial'),
      decided('e', 'absent'),
    ]);
    expect(t.n_decided).toBe(5);
    expect(t.n_not_landed).toBe(2);
    expect(t.rate).toBeCloseTo(0.4, 10);
  });

  it('excludes undecidable from the denominator rather than counting it absent', () => {
    // An unanswerable case is not a negative one. Folding it into `absent`
    // would manufacture false closes out of pruned objects.
    const t = tallyRig('mem', 100, [
      decided('a', 'landed-direct'),
      decided('b', 'absent'),
      decided('c', 'undecidable', 'range-unreadable'),
      decided('d', 'undecidable', 'no-branch-content'),
    ]);
    expect(t.n_joined).toBe(4);
    expect(t.n_decided).toBe(2);
    expect(t.n_not_landed).toBe(1);
    expect(t.rate).toBeCloseTo(0.5, 10);
  });

  it('reports undecidable causes per cause, not as one opaque bucket', () => {
    const t = tallyRig('mem', 10, [
      decided('a', 'undecidable', 'range-unreadable'),
      decided('b', 'undecidable', 'range-unreadable'),
      decided('c', 'undecidable', 'no-merge-base'),
    ]);
    expect(t.undecidable_causes).toEqual({ 'range-unreadable': 2, 'no-merge-base': 1 });
  });

  it('buckets a causeless undecidable as unspecified rather than dropping it', () => {
    const t = tallyRig('mem', 10, [decided('a', 'undecidable')]);
    expect(t.undecidable_causes).toEqual({ unspecified: 1 });
  });

  it('yields a null rate and null CI when nothing was decided', () => {
    // A rate over an empty denominator is unmeasured, not zero.
    const t = tallyRig('mem', 100, [decided('a', 'undecidable', 'no-branch-content')]);
    expect(t.rate).toBeNull();
    expect(t.ci).toBeNull();
  });

  it('computes coverage against the closed population, not the joined set', () => {
    const t = tallyRig('mem', 1649, [decided('a', 'absent'), decided('b', 'landed-direct')]);
    expect(t.coverage).toBeCloseTo(2 / 1649, 10);
  });

  it('reports zero coverage for an empty rig rather than dividing by zero', () => {
    const t = tallyRig('empty', 0, []);
    expect(t.coverage).toBe(0);
    expect(t.rate).toBeNull();
  });

  it('tallies every verdict, including ones with no occurrences', () => {
    const t = tallyRig('mem', 10, [decided('a', 'absent')]);
    expect(t.verdicts).toEqual({
      'landed-direct': 0,
      'landed-equivalent': 0,
      'landed-squashed': 0,
      partial: 0,
      absent: 1,
      undecidable: 0,
    });
  });
});

describe('hasPower', () => {
  const withDecided = (n: number) =>
    tallyRig(
      'r',
      1000,
      Array.from({ length: n }, (_, i) => decided(`b${i}`, 'landed-direct'))
    );

  it('rejects a cell below the minimum', () => {
    expect(hasPower(withDecided(MIN_DECIDED_FOR_RATE - 1))).toBe(false);
  });

  it('accepts a cell at the minimum', () => {
    expect(hasPower(withDecided(MIN_DECIDED_FOR_RATE))).toBe(true);
  });

  it('rejects a rig whose branches all went undecidable despite a large join', () => {
    const t = tallyRig(
      'r',
      1000,
      Array.from({ length: 50 }, (_, i) => decided(`b${i}`, 'undecidable', 'range-unreadable'))
    );
    expect(hasPower(t)).toBe(false);
  });
});

describe('tallyPooled', () => {
  const rigA = tallyRig('a', 100, [
    decided('a1', 'absent'),
    decided('a2', 'landed-direct'),
    decided('a3', 'undecidable', 'no-merge-base'),
  ]);
  const rigB = tallyRig('b', 900, [
    decided('b1', 'partial'),
    decided('b2', 'landed-squashed'),
    decided('b3', 'landed-direct'),
    decided('b4', 'undecidable', 'range-unreadable'),
  ]);

  it('pools by summing counts, not by averaging per-rig rates', () => {
    // Averaging rates would let rig a (2 decided) move the headline as much as
    // a rig with a hundred. Summed: 2 not-landed / 5 decided = .4, whereas the
    // unweighted mean of .5 and .333 would be ~.417.
    const p = tallyPooled([rigA, rigB]);
    expect(p.n_decided).toBe(5);
    expect(p.n_not_landed).toBe(2);
    expect(p.rate).toBeCloseTo(0.4, 10);
  });

  it('sums the closed population and recomputes coverage against it', () => {
    const p = tallyPooled([rigA, rigB]);
    expect(p.n_closed).toBe(1000);
    expect(p.n_joined).toBe(7);
    expect(p.coverage).toBeCloseTo(7 / 1000, 10);
  });

  it('merges verdict and cause histograms across rigs', () => {
    const p = tallyPooled([rigA, rigB]);
    expect(p.verdicts).toEqual({
      'landed-direct': 2,
      'landed-equivalent': 0,
      'landed-squashed': 1,
      partial: 1,
      absent: 1,
      undecidable: 2,
    });
    expect(p.undecidable_causes).toEqual({ 'no-merge-base': 1, 'range-unreadable': 1 });
  });

  it('carries a CI derived from the pooled counts', () => {
    const p = tallyPooled([rigA, rigB]);
    expect(p.ci).toEqual(wilsonInterval(2, 5));
  });

  it('labels the pooled row so it cannot be mistaken for a rig', () => {
    expect(tallyPooled([rigA]).rig).toBe('POOLED');
  });

  it('pools an empty set to a null rate, not zero', () => {
    const p = tallyPooled([]);
    expect(p.rate).toBeNull();
    expect(p.ci).toBeNull();
    expect(p.coverage).toBe(0);
  });
});
