import { describe, expect, it } from 'vitest';

import {
  classifyLandedContent,
  isLanded,
  resolveCommitOrNull,
  type LandedContentCache,
  type LandedContentInput,
  type LandedContentVerdict,
} from '../src/ingest/landedContent.js';
import type { GitPipeRunner, GitRunner } from '../src/ingest/provenance.js';

const TIP = 'a'.repeat(40);
const HEAD = 'b'.repeat(40);
const BASE = 'c'.repeat(40);

/** Distinct patch-ids. `p(1)` is a 40-hex id that is stable and readable. */
const p = (n: number): string => String(n).padStart(40, '0');
/** Distinct commit-ids for patch-id output's second field. */
const c = (n: number): string => `${'d'.repeat(39)}${n}`;

/** A `git patch-id` output line. */
const idLine = (patch: string, commit: string): string => `${patch} ${commit}\n`;

const input: LandedContentInput = {
  work_dir: '/repo',
  branch: 'work/mem-cv06b',
  integration: 'main',
};

/** git exited non-zero — the shape `isNonZeroExit` recognizes. */
const exits = (status: number) => (): never => {
  throw Object.assign(new Error(`git exited ${status}`), { status });
};

/** {@link Handlers.revParse} default: the branch → TIP, `main` → HEAD. */
const defaultRevParse = (r: string): string => (r.startsWith('main') ? `${HEAD}\n` : `${TIP}\n`);

/** The combined diff's patch-id — the one a squash-merge would carry. */
const COMBINED = p(12);

interface Handlers {
  /** ref → sha, or a thrower. Defaults: the branch → TIP, `main` → HEAD. */
  revParse?: (ref: string) => string;
  /** Throw to signal "not an ancestor" (exit 1). Default: not an ancestor. */
  isAncestor?: () => string;
  /** Default: BASE. */
  mergeBase?: () => string;
  /** `log -p <base>..<rev> | patch-id` → patch-id output. Keys on the range's tip. */
  log?: (range: string) => string;
  /** `diff <base> <tip> | patch-id` → the combined diff's patch-id output. */
  diff?: () => string;
}

/** The two seams {@link classifyLandedContent} runs git through. */
interface Fake {
  run: GitRunner;
  runPipe: GitPipeRunner;
}

/** A git fake backed by per-subcommand handlers, mirroring the handler-table
 * fake in tests/ingest.landed.test.ts. The pipe seam's handlers return `patch-id`
 * OUTPUT directly: the patch text never leaves the kernel, so there is no diff
 * stream for a fake to model — only what patch-id hands back. */
const git = (h: Handlers): Fake => ({
  run: (_workDir, args) => {
    if (args[0] === 'rev-parse') {
      // args: rev-parse --verify --end-of-options <ref>^{commit}
      const ref = args[3].replace(/\^\{commit\}$/, '');
      return (h.revParse ?? defaultRevParse)(ref);
    }
    if (args[0] === 'merge-base' && args[1] === '--is-ancestor') {
      return (h.isAncestor ?? exits(1))();
    }
    if (args[0] === 'merge-base') return (h.mergeBase ?? (() => `${BASE}\n`))();
    throw new Error(`unexpected git ${args.join(' ')}`);
  },
  runPipe: (_workDir, args) => {
    if (args[0] === 'log') {
      return (h.log ?? (() => idLine(p(1), c(1))))(args[args.length - 1]);
    }
    if (args[0] === 'diff') return (h.diff ?? (() => idLine(COMBINED, c(8))))();
    throw new Error(`unexpected git ${args.join(' ')} | patch-id`);
  },
});

/** Records every pipe arg vector while delegating to the real fake, so a test
 * can assert which `log`/`diff` listings actually ran. */
const counting = (fake: Fake): { runPipe: GitPipeRunner; calls: string[][] } => {
  const calls: string[][] = [];
  return {
    calls,
    runPipe: (workDir, args) => {
      calls.push(args);
      return fake.runPipe(workDir, args);
    },
  };
};

/** The common shape: the branch carries commits 1 and 2; integration carries
 * whichever of them `landedIds` names, plus its own unrelated commit 9. */
const twoCommitBranch = (landedIds: readonly string[], combinedLands = false): Handlers => ({
  log: range =>
    range.endsWith(TIP)
      ? idLine(p(1), c(1)) + idLine(p(2), c(2))
      : idLine(p(9), c(9)) +
        landedIds.map((id, i) => idLine(id, c(i + 5))).join('') +
        (combinedLands ? idLine(COMBINED, c(8)) : ''),
});

describe('classifyLandedContent — landed ladder', () => {
  it('reports landed-direct when the branch tip is an ancestor of integration', () => {
    const out = classifyLandedContent(input, git({ isAncestor: () => '' }));
    expect(out).toEqual({
      verdict: 'landed-direct',
      branch_commit: TIP,
      integration_commit: HEAD,
    });
  });

  it('propagates a bad object from is-ancestor rather than reading it as not-landed', () => {
    // `merge-base --is-ancestor` exits 1 for "not an ancestor" but 128 for a
    // pruned or corrupt object. Both args are already-resolved shas, so 128 is a
    // real fault: swallowing it would send the branch down the patch-id ladder
    // and could land it on `absent`, manufacturing a false close out of a broken
    // object. Only the exact status 1 may be read as an answer.
    expect(() => classifyLandedContent(input, git({ isAncestor: exits(128) }))).toThrow(
      /git exited 128/
    );
  });

  it('propagates a bad object from merge-base rather than reading it as no-merge-base', () => {
    // The merge-base twin of the is-ancestor case above (mem-f0n07). `merge-base`
    // exits 1 for unrelated histories but 128 for a pruned/corrupt object; both
    // operands are already-resolved shas, so a 128 is a real fault. The shared
    // `mergeBase` reads only the exact status 1 as "no common ancestor" and
    // rethrows anything else, so a fault surfaces here rather than collapsing into
    // an `undecidable/no-merge-base` verdict that would understate a broken object
    // as a clean disjoint history — the same fabrication the sibling probe guards.
    expect(() => classifyLandedContent(input, git({ mergeBase: exits(128) }))).toThrow(
      /git exited 128/
    );
  });

  it('reports landed-equivalent when every branch commit has a patch-id twin', () => {
    const out = classifyLandedContent(input, git(twoCommitBranch([p(1), p(2)])));
    expect(out).toEqual({
      verdict: 'landed-equivalent',
      branch_commit: TIP,
      integration_commit: HEAD,
      merge_base: BASE,
      n_commits: 2,
      n_matched: 2,
    });
  });

  it('reports landed-squashed when only the combined diff has a twin', () => {
    const out = classifyLandedContent(input, git(twoCommitBranch([], true)));
    expect(out).toMatchObject({ verdict: 'landed-squashed', n_commits: 2, n_matched: 0 });
  });

  it('prefers landed-squashed over partial when one commit ALSO matches', () => {
    // A squash-merge leaves no individual patch-id intact, so a lone matching
    // commit is incidental (a trivial hunk that also landed on its own). Reading
    // that as a partial landing would understate a branch that fully landed.
    const out = classifyLandedContent(input, git(twoCommitBranch([p(1)], true)));
    expect(out).toMatchObject({ verdict: 'landed-squashed', n_commits: 2, n_matched: 1 });
  });

  it('reports partial when some but not all commits landed', () => {
    const out = classifyLandedContent(input, git(twoCommitBranch([p(1)])));
    expect(out).toMatchObject({ verdict: 'partial', n_commits: 2, n_matched: 1 });
  });

  it('reports absent when no commit and no combined diff has a twin', () => {
    const out = classifyLandedContent(input, git(twoCommitBranch([])));
    expect(out).toMatchObject({ verdict: 'absent', n_commits: 2, n_matched: 0 });
  });

  it('reports absent, not landed-squashed, when the combined diff is empty', () => {
    // A branch whose commits cancel out has no combined patch to match.
    const out = classifyLandedContent(input, git({ ...twoCommitBranch([]), diff: () => '' }));
    expect(out).toMatchObject({ verdict: 'absent' });
  });
});

describe('classifyLandedContent — undecidable causes', () => {
  it('reports branch-unresolvable when the branch ref is gone (exit 128)', () => {
    const run = git({ revParse: ref => (ref.startsWith('main') ? `${HEAD}\n` : exits(128)()) });
    expect(classifyLandedContent(input, run)).toEqual({
      verdict: 'undecidable',
      cause: 'branch-unresolvable',
    });
  });

  it('reports integration-unresolvable when the integration branch is gone', () => {
    const run = git({ revParse: ref => (ref.startsWith('main') ? exits(128)() : `${TIP}\n`) });
    expect(classifyLandedContent(input, run)).toEqual({
      verdict: 'undecidable',
      cause: 'integration-unresolvable',
      branch_commit: TIP,
    });
  });

  it('reports no-merge-base for unrelated histories (merge-base exits 1)', () => {
    const out = classifyLandedContent(input, git({ mergeBase: exits(1) }));
    expect(out).toEqual({
      verdict: 'undecidable',
      cause: 'no-merge-base',
      branch_commit: TIP,
      integration_commit: HEAD,
    });
  });

  it('reports range-unreadable when a pruned object breaks the diff listing', () => {
    const out = classifyLandedContent(input, git({ log: exits(128) }));
    expect(out).toEqual({
      verdict: 'undecidable',
      cause: 'range-unreadable',
      branch_commit: TIP,
      integration_commit: HEAD,
      merge_base: BASE,
    });
  });

  it('reports no-branch-content when the branch adds no content-bearing commit', () => {
    // Empty commits produce no patch-id line: there is nothing that could land,
    // so their absence from integration is not evidence of a false close.
    const out = classifyLandedContent(input, git({ log: () => '' }));
    expect(out).toMatchObject({
      verdict: 'undecidable',
      cause: 'no-branch-content',
      n_commits: 0,
    });
  });

  it('reports range-unreadable when the INTEGRATION side listing fails', () => {
    const run = git({ log: range => (range.endsWith(TIP) ? idLine(p(1), c(1)) : exits(128)()) });
    expect(classifyLandedContent(input, run)).toMatchObject({
      verdict: 'undecidable',
      cause: 'range-unreadable',
    });
  });
});

describe('classifyLandedContent — non-exit failures propagate', () => {
  // A missing git binary must fail the sweep, not silently mark every branch
  // undecidable: that would report an empty measurement as a coverage gap.
  const noGit = (): never => {
    throw new Error('spawn git ENOENT'); // no .status → not a non-zero exit
  };

  it('rethrows when rev-parse cannot run', () => {
    expect(() => classifyLandedContent(input, git({ revParse: noGit }))).toThrow(/ENOENT/);
  });

  it('rethrows when merge-base --is-ancestor cannot run', () => {
    expect(() => classifyLandedContent(input, git({ isAncestor: noGit }))).toThrow(/ENOENT/);
  });

  it('rethrows when the diff listing cannot run', () => {
    expect(() => classifyLandedContent(input, git({ log: noGit }))).toThrow(/ENOENT/);
  });
});

describe('classifyLandedContent — git invocation contract', () => {
  it('pins DB-sourced refs behind --end-of-options and peels to a commit', () => {
    const fake = git(twoCommitBranch([p(1), p(2)]));
    const seen: string[][] = [];
    const run: GitRunner = (workDir, args) => {
      seen.push(args);
      return fake.run(workDir, args);
    };
    classifyLandedContent({ ...input, branch: '--output=/tmp/pwn' }, { ...fake, run });
    expect(seen[0]).toEqual([
      'rev-parse',
      '--verify',
      '--end-of-options',
      '--output=/tmp/pwn^{commit}',
    ]);
  });

  it('scopes both patch-id ranges to the merge base, not to full history', () => {
    // An unscoped integration-side search would match the branch's own pre-fork
    // ancestry and report every branch as landed.
    const fake = git(twoCommitBranch([p(1)]));
    const ranges: string[] = [];
    const runPipe: GitPipeRunner = (workDir, args) => {
      if (args[0] === 'log') ranges.push(args[args.length - 1]);
      return fake.runPipe(workDir, args);
    };
    classifyLandedContent(input, { ...fake, runPipe });
    expect(ranges).toEqual([`${BASE}..${TIP}`, `${BASE}..${HEAD}`]);
  });

  it('pipes every diff listing through the pipe seam, excluding merges and colour', () => {
    // Every pipe is pinned here because none is visible in a verdict. A
    // `partial` fixture, so the combined-diff pipe runs too: `landed-equivalent`
    // returns before it and would leave that third pipe unpinned. The
    // `patch-id --stable` second stage is baked into provenance.ts's pipe
    // script rather than passed by this caller, and is covered by its own
    // tests (tests/ingest.provenance.test.ts).
    const fake = git(twoCommitBranch([p(1)]));
    const { runPipe, calls } = counting(fake);
    classifyLandedContent(input, { ...fake, runPipe });
    expect(calls.map(a => a[0])).toEqual(['log', 'log', 'diff']);
    expect(calls.every(a => a.includes('--no-color'))).toBe(true);
    expect(calls.find(a => a[0] === 'log')).toContain('--no-merges');
  });
});

describe('classifyLandedContent — range cache', () => {
  it('reuses both range walks across calls, leaving only the uncached combined diff', () => {
    // A `partial` fixture, so the combined-diff pipe runs and its absence from
    // the cache is observable. The first call's ['log','log','diff'] is also the
    // proof that base..tip and base..head do NOT collide: same base, different
    // tips, and BOTH walked — a shared key would have shown ['log','diff'].
    const fake = git(twoCommitBranch([p(1)]));
    const { runPipe, calls } = counting(fake);
    const cache: LandedContentCache = new Map();

    const first = classifyLandedContent(input, { ...fake, runPipe, cache });
    expect(calls.map(a => a[0])).toEqual(['log', 'log', 'diff']);

    calls.length = 0;
    const second = classifyLandedContent(input, { ...fake, runPipe, cache });
    expect(calls.map(a => a[0])).toEqual(['diff']);
    expect(second).toEqual(first);
  });

  it('re-walks every range when no cache is passed', () => {
    const fake = git(twoCommitBranch([p(1)]));
    const { runPipe, calls } = counting(fake);

    classifyLandedContent(input, { ...fake, runPipe });
    calls.length = 0;
    classifyLandedContent(input, { ...fake, runPipe });
    expect(calls.map(a => a[0])).toEqual(['log', 'log', 'diff']);
  });

  it('does not cache a failed walk — range-unreadable stays per-call', () => {
    // The failed range must leave nothing behind: a cached negative would make a
    // transient object fault sticky for the rest of the sweep.
    const cache: LandedContentCache = new Map();
    const out = classifyLandedContent(input, { ...git({ log: exits(128) }), cache });
    expect(out).toMatchObject({ verdict: 'undecidable', cause: 'range-unreadable' });
    expect(cache.size).toBe(0);
  });

  it('caches an empty range — an empty map is a hit, not a re-walk', () => {
    // no-branch-content returns off the branch side before the integration side
    // runs, so exactly one `log` fires on the first call and none on the second.
    const fake = git({ log: () => '' });
    const { runPipe, calls } = counting(fake);
    const cache: LandedContentCache = new Map();

    const a = classifyLandedContent(input, { ...fake, runPipe, cache });
    const b = classifyLandedContent(input, { ...fake, runPipe, cache });
    expect(calls.filter(x => x[0] === 'log')).toHaveLength(1);
    expect(a).toMatchObject({ verdict: 'undecidable', cause: 'no-branch-content' });
    expect(b).toEqual(a);
  });

  it('keys the cache on work_dir — a different checkout does not collide', () => {
    // Same resolved base/tip, different work_dir: a key that dropped work_dir
    // would reuse /repo-a's walk for /repo-b. Assert both checkouts walk.
    const fake = git(twoCommitBranch([p(1)]));
    const { runPipe, calls } = counting(fake);
    const cache: LandedContentCache = new Map();

    classifyLandedContent({ ...input, work_dir: '/repo-a' }, { ...fake, runPipe, cache });
    classifyLandedContent({ ...input, work_dir: '/repo-b' }, { ...fake, runPipe, cache });
    expect(calls.filter(x => x[0] === 'log')).toHaveLength(4);
  });

  it('returns identical verdicts across the ladder with and without a cache', () => {
    const cases: Handlers[] = [
      twoCommitBranch([p(1), p(2)]), // landed-equivalent
      twoCommitBranch([], true), // landed-squashed
      twoCommitBranch([p(1)]), // partial
      twoCommitBranch([]), // absent
    ];
    for (const h of cases) {
      const uncached = classifyLandedContent(input, git(h));
      const cache: LandedContentCache = new Map();
      const cached = classifyLandedContent(input, { ...git(h), cache });
      expect(cached).toEqual(uncached);
    }
  });
});

describe('isLanded', () => {
  it('counts every landed-* verdict as present on integration', () => {
    const landed: LandedContentVerdict[] = [
      'landed-direct',
      'landed-equivalent',
      'landed-squashed',
    ];
    expect(landed.every(isLanded)).toBe(true);
  });

  it('excludes partial — a half-landed change is the failure under measurement', () => {
    const notLanded: LandedContentVerdict[] = ['partial', 'absent', 'undecidable'];
    expect(notLanded.some(isLanded)).toBe(false);
  });
});

describe('resolveCommitOrNull — a git fault skips, a bug surfaces', () => {
  const SHA = 'a'.repeat(40);
  const REF = 'gascity/main';

  it('resolves a real ref to its sha', () => {
    const run: GitRunner = () => `${SHA}\n`;
    expect(resolveCommitOrNull(run, '/repo', REF)).toBe(SHA);
  });

  it('returns null on a non-zero exit — the ref does not resolve', () => {
    // resolveCommit maps a non-zero exit (unknown ref) to null; the wrapper
    // passes that through untouched — no throw ever reaches its catch.
    expect(resolveCommitOrNull(exits(128), '/repo', REF)).toBeNull();
  });

  it('degrades a spawn-errno fault to null so one broken rig skips, not aborts', () => {
    // uv_spawn failed before git ran: status + signal both null, code the errno.
    // A transient EMFILE persists across a batch, so a throw here would take every
    // other rig down with it — exactly the mem-egxu2 regression isGitFault avoids.
    const run: GitRunner = () => {
      throw Object.assign(new Error('spawn git EMFILE'), {
        status: null,
        signal: null,
        code: 'EMFILE',
      });
    };
    expect(resolveCommitOrNull(run, '/repo', REF)).toBeNull();
  });

  it('degrades an external signal kill to null — git was asked but could not answer', () => {
    const run: GitRunner = () => {
      throw Object.assign(new Error('git killed'), { status: null, signal: 'SIGKILL' });
    };
    expect(resolveCommitOrNull(run, '/repo', REF)).toBeNull();
  });

  it('rethrows a mis-wired-runner TypeError instead of masking it as unresolvable', () => {
    // A programming error carries no git verdict; swallowing it to null would
    // report a real bug as "authoritative ref does not resolve — skipped".
    const run: GitRunner = () => {
      throw new TypeError('run is not a function');
    };
    expect(() => resolveCommitOrNull(run, '/repo', REF)).toThrow(TypeError);
  });

  it('rethrows our own ENOBUFS maxBuffer overrun — a config bug, not a git fault', () => {
    // Node aborting the child over OUR maxBuffer is a bug to see, not a
    // git-could-not-answer fault; isGitFault carves ENOBUFS out so it propagates.
    const run: GitRunner = () => {
      throw Object.assign(new Error('stdout maxBuffer length exceeded'), {
        code: 'ENOBUFS',
        signal: 'SIGTERM',
        status: null,
      });
    };
    expect(() => resolveCommitOrNull(run, '/repo', REF)).toThrow(/maxBuffer/);
  });
});
