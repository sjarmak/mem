import { describe, expect, it } from 'vitest';

import {
  classifyLandedContent,
  isLanded,
  type LandedContentInput,
  type LandedContentVerdict,
} from '../src/ingest/landedContent.js';
import type { GitRunner } from '../src/ingest/provenance.js';

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

interface Handlers {
  /** ref → sha, or a thrower. Defaults: the branch → TIP, `main` → HEAD. */
  revParse?: (ref: string) => string;
  /** Throw to signal "not an ancestor" (exit 1). Default: not an ancestor. */
  isAncestor?: () => string;
  /** Default: BASE. */
  mergeBase?: () => string;
  /** `log -p <base>..<rev>` → a diff stream. The fake keys on the range's tip. */
  log?: (range: string) => string;
  /** `diff <base> <tip>` → the combined diff stream. */
  diff?: () => string;
  /** patch-id output for a given stdin diff stream. */
  patchId?: (stdin: string) => string;
}

/** A git runner backed by per-subcommand handlers, mirroring the handler-table
 * fake in tests/ingest.landed.test.ts. Diff streams are opaque marker strings —
 * the fake `patch-id` maps a marker to ids — since the module never parses a
 * diff itself, it only pipes one from `log`/`diff` into `patch-id`. */
const git = (h: Handlers): GitRunner => {
  const defaultRevParse = (ref: string): string =>
    ref.startsWith('main') ? `${HEAD}\n` : `${TIP}\n`;
  return (_workDir, args, stdin) => {
    if (args[0] === 'rev-parse') {
      // args: rev-parse --verify --end-of-options <ref>^{commit}
      const ref = args[3].replace(/\^\{commit\}$/, '');
      return (h.revParse ?? defaultRevParse)(ref);
    }
    if (args[0] === 'merge-base' && args[1] === '--is-ancestor') {
      return (h.isAncestor ?? exits(1))();
    }
    if (args[0] === 'merge-base') return (h.mergeBase ?? (() => `${BASE}\n`))();
    if (args[0] === 'log') {
      const range = args[args.length - 1];
      return (h.log ?? (() => 'diff:branch'))(range);
    }
    if (args[0] === 'diff') return (h.diff ?? (() => 'diff:combined'))();
    if (args[0] === 'patch-id') return (h.patchId ?? (() => ''))(stdin ?? '');
    throw new Error(`unexpected git ${args.join(' ')}`);
  };
};

/** The common shape: the branch carries commits 1 and 2; integration carries
 * whichever of them `landedIds` names, plus its own unrelated commit 9. */
const twoCommitBranch = (landedIds: readonly string[], combinedLands = false): Handlers => ({
  log: range => (range.endsWith(TIP) ? 'diff:branch' : 'diff:head'),
  patchId: stdin => {
    if (stdin === 'diff:branch') return idLine(p(1), c(1)) + idLine(p(2), c(2));
    if (stdin === 'diff:head') {
      const own = idLine(p(9), c(9));
      const landed = landedIds.map((id, i) => idLine(id, c(i + 5))).join('');
      return own + landed + (combinedLands ? idLine(p(12), c(8)) : '');
    }
    if (stdin === 'diff:combined') return idLine(p(12), '0'.repeat(40));
    throw new Error(`unexpected patch-id stdin ${JSON.stringify(stdin)}`);
  },
});

describe('classifyLandedContent — landed ladder', () => {
  it('reports landed-direct when the branch tip is an ancestor of integration', () => {
    const out = classifyLandedContent(input, { run: git({ isAncestor: () => '' }) });
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
    expect(() => classifyLandedContent(input, { run: git({ isAncestor: exits(128) }) })).toThrow(
      /git exited 128/
    );
  });

  it('reports landed-equivalent when every branch commit has a patch-id twin', () => {
    const out = classifyLandedContent(input, { run: git(twoCommitBranch([p(1), p(2)])) });
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
    const out = classifyLandedContent(input, { run: git(twoCommitBranch([], true)) });
    expect(out).toMatchObject({ verdict: 'landed-squashed', n_commits: 2, n_matched: 0 });
  });

  it('prefers landed-squashed over partial when one commit ALSO matches', () => {
    // A squash-merge leaves no individual patch-id intact, so a lone matching
    // commit is incidental (a trivial hunk that also landed on its own). Reading
    // that as a partial landing would understate a branch that fully landed.
    const out = classifyLandedContent(input, { run: git(twoCommitBranch([p(1)], true)) });
    expect(out).toMatchObject({ verdict: 'landed-squashed', n_commits: 2, n_matched: 1 });
  });

  it('reports partial when some but not all commits landed', () => {
    const out = classifyLandedContent(input, { run: git(twoCommitBranch([p(1)])) });
    expect(out).toMatchObject({ verdict: 'partial', n_commits: 2, n_matched: 1 });
  });

  it('reports absent when no commit and no combined diff has a twin', () => {
    const out = classifyLandedContent(input, { run: git(twoCommitBranch([])) });
    expect(out).toMatchObject({ verdict: 'absent', n_commits: 2, n_matched: 0 });
  });

  it('reports absent, not landed-squashed, when the combined diff is empty', () => {
    // A branch whose commits cancel out has no combined patch to match.
    const out = classifyLandedContent(input, {
      run: git({ ...twoCommitBranch([]), diff: () => '' }),
    });
    expect(out).toMatchObject({ verdict: 'absent' });
  });
});

describe('classifyLandedContent — undecidable causes', () => {
  it('reports branch-unresolvable when the branch ref is gone (exit 128)', () => {
    const run = git({
      revParse: ref => (ref.startsWith('main') ? `${HEAD}\n` : exits(128)()),
    });
    expect(classifyLandedContent(input, { run })).toEqual({
      verdict: 'undecidable',
      cause: 'branch-unresolvable',
    });
  });

  it('reports integration-unresolvable when the integration branch is gone', () => {
    const run = git({
      revParse: ref => (ref.startsWith('main') ? exits(128)() : `${TIP}\n`),
    });
    expect(classifyLandedContent(input, { run })).toEqual({
      verdict: 'undecidable',
      cause: 'integration-unresolvable',
      branch_commit: TIP,
    });
  });

  it('reports no-merge-base for unrelated histories (merge-base exits 1)', () => {
    const out = classifyLandedContent(input, { run: git({ mergeBase: exits(1) }) });
    expect(out).toEqual({
      verdict: 'undecidable',
      cause: 'no-merge-base',
      branch_commit: TIP,
      integration_commit: HEAD,
    });
  });

  it('reports range-unreadable when a pruned object breaks the diff listing', () => {
    const out = classifyLandedContent(input, { run: git({ log: exits(128) }) });
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
    const out = classifyLandedContent(input, { run: git({ log: () => '', patchId: () => '' }) });
    expect(out).toMatchObject({
      verdict: 'undecidable',
      cause: 'no-branch-content',
      n_commits: 0,
    });
  });

  it('reports range-too-large when the diff outruns the runner buffer', () => {
    // Node kills the child and reports ENOBUFS with no exit status. git was
    // asked a valid question whose answer was too big to read back — a
    // coverage hole, not a crash. Real case: CodeScaleBench's local main
    // trails upstream by 1196 commits, so the range diff runs past 64MB.
    const enobufs = (): never => {
      throw Object.assign(new Error('spawnSync git ENOBUFS'), { code: 'ENOBUFS', status: null });
    };
    expect(classifyLandedContent(input, { run: git({ log: enobufs }) })).toEqual({
      verdict: 'undecidable',
      cause: 'range-too-large',
      branch_commit: TIP,
      integration_commit: HEAD,
      merge_base: BASE,
    });
  });

  it('reports range-too-large when the diff outruns V8 max string length', () => {
    // Raising maxBuffer past the diff size only converts ENOBUFS into this:
    // decoding the output overruns V8's ~512MB string cap. Same coverage hole,
    // so it must not be allowed to kill the sweep either.
    const tooLong = (): never => {
      throw Object.assign(new Error('Cannot create a string longer than 0x1fffffe8 characters'), {
        code: 'ERR_STRING_TOO_LONG',
      });
    };
    expect(classifyLandedContent(input, { run: git({ log: tooLong }) })).toMatchObject({
      verdict: 'undecidable',
      cause: 'range-too-large',
    });
  });

  it('reports range-unreadable when the INTEGRATION side listing fails', () => {
    const run = git({
      log: range => (range.endsWith(TIP) ? 'diff:branch' : exits(128)()),
      patchId: () => idLine(p(1), c(1)),
    });
    expect(classifyLandedContent(input, { run })).toMatchObject({
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
    expect(() => classifyLandedContent(input, { run: git({ revParse: noGit }) })).toThrow(/ENOENT/);
  });

  it('rethrows when merge-base --is-ancestor cannot run', () => {
    expect(() => classifyLandedContent(input, { run: git({ isAncestor: noGit }) })).toThrow(
      /ENOENT/
    );
  });

  it('rethrows when the diff listing cannot run', () => {
    expect(() => classifyLandedContent(input, { run: git({ log: noGit }) })).toThrow(/ENOENT/);
  });
});

describe('classifyLandedContent — git invocation contract', () => {
  it('pins DB-sourced refs behind --end-of-options and peels to a commit', () => {
    const seen: string[][] = [];
    const run: GitRunner = (workDir, args, stdin) => {
      seen.push(args);
      return git(twoCommitBranch([p(1), p(2)]))(workDir, args, stdin);
    };
    classifyLandedContent({ ...input, branch: '--output=/tmp/pwn' }, { run });
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
    const ranges: string[] = [];
    const run: GitRunner = (workDir, args, stdin) => {
      if (args[0] === 'log') ranges.push(args[args.length - 1]);
      return git(twoCommitBranch([p(1)]))(workDir, args, stdin);
    };
    classifyLandedContent(input, { run });
    expect(ranges).toEqual([`${BASE}..${TIP}`, `${BASE}..${HEAD}`]);
  });

  it('excludes merges and colour from the diff stream and stabilises patch-id', () => {
    const args: string[][] = [];
    const run: GitRunner = (workDir, a, stdin) => {
      args.push(a);
      return git(twoCommitBranch([p(1), p(2)]))(workDir, a, stdin);
    };
    classifyLandedContent(input, { run });
    const log = args.find(a => a[0] === 'log')!;
    expect(log).toContain('--no-merges');
    expect(log).toContain('--no-color');
    expect(args.find(a => a[0] === 'patch-id')).toEqual(['patch-id', '--stable']);
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
