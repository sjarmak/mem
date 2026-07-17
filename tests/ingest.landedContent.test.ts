import { describe, expect, it } from 'vitest';

import {
  classifyLandedContent,
  isLanded,
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
      const defaultRevParse = (r: string): string =>
        r.startsWith('main') ? `${HEAD}\n` : `${TIP}\n`;
      return (h.revParse ?? defaultRevParse)(ref);
    }
    if (args[0] === 'merge-base' && args[1] === '--is-ancestor') {
      return (h.isAncestor ?? exits(1))();
    }
    if (args[0] === 'merge-base') return (h.mergeBase ?? (() => `${BASE}\n`))();
    throw new Error(`unexpected git ${args.join(' ')}`);
  },
  runPipe: (_workDir, argsA) => {
    if (argsA[0] === 'log') {
      return (h.log ?? (() => idLine(p(1), c(1))))(argsA[argsA.length - 1]);
    }
    if (argsA[0] === 'diff') return (h.diff ?? (() => idLine(COMBINED, c(8))))();
    throw new Error(`unexpected git ${argsA.join(' ')} | patch-id`);
  },
});

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
    const runPipe: GitPipeRunner = (workDir, argsA, argsB) => {
      if (argsA[0] === 'log') ranges.push(argsA[argsA.length - 1]);
      return fake.runPipe(workDir, argsA, argsB);
    };
    classifyLandedContent(input, { ...fake, runPipe });
    expect(ranges).toEqual([`${BASE}..${TIP}`, `${BASE}..${HEAD}`]);
  });

  it('pipes every diff listing into patch-id --stable, excluding merges and colour', () => {
    // The second stage is what keeps the patch text in the kernel, and --stable
    // is what makes ids computed on different hosts comparable. Both sides of
    // every pipe are pinned here because neither is visible in a verdict.
    // A `partial` fixture, so the combined-diff pipe runs too: `landed-equivalent`
    // returns before it and would leave that third pipe unpinned.
    const fake = git(twoCommitBranch([p(1)]));
    const piped: { a: string[]; b: string[] }[] = [];
    const runPipe: GitPipeRunner = (workDir, argsA, argsB) => {
      piped.push({ a: argsA, b: argsB });
      return fake.runPipe(workDir, argsA, argsB);
    };
    classifyLandedContent(input, { ...fake, runPipe });
    expect(piped.map(x => x.a[0])).toEqual(['log', 'log', 'diff']);
    expect(piped.every(x => x.a.includes('--no-color'))).toBe(true);
    expect(piped.find(x => x.a[0] === 'log')!.a).toContain('--no-merges');
    for (const x of piped) expect(x.b).toEqual(['patch-id', '--stable']);
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
