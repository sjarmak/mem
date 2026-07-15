import {
  type GitRunner,
  defaultGitRunner,
  exitStatus,
  isNonZeroExit,
  isOutputTooLarge,
} from './provenance.js';

/**
 * ingest/landedContent — decides, for one bead-named branch, whether its work is
 * present on the rig's integration branch by CONTENT rather than by ref.
 *
 * ingest/landed answers "what landed on main while this session held it" from a
 * time window. That is the right oracle for direct-to-main work, but it cannot
 * answer the question mem-z14cd asks: a bead is closed and a branch bearing its
 * id still carries commits — did the change actually land? Ref-level tests all
 * fail here. `branch merged` is false for a squash-merge, `git cherry` reports
 * rebased-but-landed commits as unlanded, and a stranded branch and a
 * squash-merged one are indistinguishable by ancestry alone.
 *
 * So the test is patch-id equivalence: the fingerprint git computes over a
 * diff's content, invariant to sha, parent, author, date, and commit message.
 * A commit that was rebased, cherry-picked, or replayed onto the integration
 * branch keeps its patch-id; a squash-merge's single commit carries the patch-id
 * of the branch's COMBINED diff. Both are checked, on a ladder from strongest to
 * weakest evidence.
 *
 * ZFC: git computes the ancestry and the patch-ids; this maps them onto a
 * verdict with no semantic judgment. What it CANNOT see is a change
 * re-implemented by hand in a different shape — that reads `absent`. Patch-id is
 * evidence of landing, not proof of its absence, and the measurement built on
 * this module (scripts/measure-false-close.mjs) reports it as such.
 */

/** Where a branch's content stands relative to the integration branch, ordered
 * strongest evidence first. Every verdict except `undecidable` is a git fact
 * about content; `undecidable` means git could not be asked (see
 * {@link UndecidableCause}) and is reported separately from `absent`, never
 * folded into it — an unanswerable case is not a negative one. */
export type LandedContentVerdict =
  /** The branch tip is an ancestor of integration: it landed, unrewritten. */
  | 'landed-direct'
  /** Every content-bearing branch commit has a patch-id twin on integration —
   * the branch was rebased or cherry-picked in, so no sha survives. */
  | 'landed-equivalent'
  /** The branch's combined diff has a patch-id twin in a single integration
   * commit — a squash-merge, where no individual commit's patch-id survives. */
  | 'landed-squashed'
  /** Some, but not all, branch commits have patch-id twins on integration. */
  | 'partial'
  /** No branch commit and no combined diff has a twin on integration. */
  | 'absent'
  /** git could not decide — the case is excluded from the rate, not counted. */
  | 'undecidable';

/** Why a verdict is `undecidable`. Each is a distinct coverage hole, reported
 * per-cause so the measurement's denominator is auditable rather than a single
 * opaque "skipped" bucket. */
export type UndecidableCause =
  /** The branch ref does not resolve to a commit — deleted, or never existed. */
  | 'branch-unresolvable'
  /** The integration branch does not resolve — checkout gone, or wrong branch. */
  | 'integration-unresolvable'
  /** The two refs share no common ancestor (unrelated histories, grafted repo). */
  | 'no-merge-base'
  /** A diff listing exited non-zero — typically an object pruned by gc. */
  | 'range-unreadable'
  /** A diff listing's output exceeded the runner's buffer. Kept distinct from
   * `range-unreadable` because the two are different coverage holes with
   * different fixes: a pruned object is gone for good, whereas this range is
   * merely too large to read back and a bigger buffer would decide it. */
  | 'range-too-large'
  /** The branch carries no content-bearing commit past the merge base: nothing
   * to look for, so its absence from integration proves nothing. */
  | 'no-branch-content';

/** One branch to decide, against one integration branch, in one checkout. */
export interface LandedContentInput {
  /** The rig's local checkout root. */
  work_dir: string;
  /** The bead-named branch under test (e.g. `work/mem-cv06b`). */
  branch: string;
  /** The rig's integration branch (e.g. `main`). */
  integration: string;
}

/** The verdict plus the git facts it was derived from. The anchors are recorded
 * because the corpus moves under measurement — a branch's verdict is only
 * reproducible against the exact integration commit it was decided against — and
 * the counts let a reviewer re-derive `partial` vs `landed-equivalent` by hand. */
export interface LandedContentResult {
  verdict: LandedContentVerdict;
  /** Present exactly when `verdict` is `undecidable`. */
  cause?: UndecidableCause;
  /** The branch tip, once resolved. */
  branch_commit?: string;
  /** The integration tip the verdict was decided against. */
  integration_commit?: string;
  /** The fork point the branch's commits were listed from. */
  merge_base?: string;
  /** Content-bearing branch commits past the merge base (empty diffs excluded). */
  n_commits?: number;
  /** How many of those have a patch-id twin on integration. */
  n_matched?: number;
}

/** Run a git command whose answer is a sha, returning null when git exits
 * non-zero — for these callers that exit means "no such commit", the question's
 * legitimate negative answer, not a fault. Any other failure (a missing binary)
 * propagates. Empty output is null too: git answered with no sha. */
function shaOrNull(run: GitRunner, work_dir: string, args: string[]): string | null {
  let stdout: string;
  try {
    stdout = run(work_dir, args);
  } catch (err) {
    if (isNonZeroExit(err)) return null;
    throw err;
  }
  const sha = stdout.trim();
  return sha === '' ? null : sha;
}

/** Resolve a ref to a full commit sha, or null when it does not resolve. The ref
 * is DB/branch-listing-sourced, so `--end-of-options` precedes it: git then reads
 * it strictly as a revision and a value like `--output=<path>` is an unknown rev
 * (non-zero exit → null) rather than a flag git acts on. `^{commit}` peels an
 * annotated tag and rejects a ref that names a tree or blob. Every later command
 * takes the resolved 40-hex sha, so this is the only injection surface. */
function resolveCommit(run: GitRunner, work_dir: string, ref: string): string | null {
  return shaOrNull(run, work_dir, ['rev-parse', '--verify', '--end-of-options', `${ref}^{commit}`]);
}

/** True when `commit` is an ancestor of `of`. `merge-base --is-ancestor` exits 1
 * for "not an ancestor"; both args are resolved shas, so any other non-zero exit
 * (128 — a pruned or corrupt object) is a real fault and propagates. Guarding on
 * the exact status rather than "exited non-zero" is what keeps that fault from
 * reading as a negative answer: a 128 swallowed here would route the branch down
 * the patch-id ladder and could land it on `absent`, manufacturing a false close
 * out of a broken object. Mirrors `survives` in ingest/landed.ts. */
function isAncestor(run: GitRunner, work_dir: string, commit: string, of: string): boolean {
  try {
    run(work_dir, ['merge-base', '--is-ancestor', commit, of]);
    return true;
  } catch (err) {
    if (exitStatus(err) === 1) return false;
    throw err;
  }
}

/** The fork point of two resolved commits, or null when they share no ancestor
 * (`merge-base` exits 1 on unrelated histories). */
function mergeBase(run: GitRunner, work_dir: string, a: string, b: string): string | null {
  return shaOrNull(run, work_dir, ['merge-base', a, b]);
}

/** A `git patch-id` line: `<patch-id> <commit-id>`, both 40-hex. */
const PATCH_ID_RE = /^([0-9a-f]{40}) ([0-9a-f]{40})$/;

/** Parse `git patch-id` output into patch-id → commit-id. A commit whose diff is
 * empty produces no line at all, which is why this map's size is the count of
 * CONTENT-BEARING commits: an empty commit has nothing that can land. */
function parsePatchIds(stdout: string): Map<string, string> {
  const ids = new Map<string, string>();
  for (const line of stdout.split('\n')) {
    const m = PATCH_ID_RE.exec(line.trim());
    if (m !== null) ids.set(m[1], m[2]);
  }
  return ids;
}

/** Thrown when a diff listing cannot be read. `cause` carries which kind of
 * coverage hole it is. It is caught at the top of {@link classifyLandedContent}
 * and mapped to an `undecidable` verdict, so one unreadable range degrades that
 * branch instead of failing the whole cross-rig sweep. */
class RangeUnreadable extends Error {
  constructor(
    readonly cause_: 'range-unreadable' | 'range-too-large',
    message: string
  ) {
    super(message);
  }
}

/** Run `run`, converting the two failures that mean "this range cannot be read"
 * into {@link RangeUnreadable}: a non-zero exit (a pruned object) and a buffer
 * overflow (a range too large to read back). Any OTHER non-exit failure — a
 * missing `git` binary — is NOT converted: that is a misconfiguration which
 * would silently mark every branch undecidable, an empty measurement dressed up
 * as a coverage gap, so it propagates and fails the sweep loudly. */
function readOrFail(run: GitRunner, work_dir: string, args: string[], stdin?: string): string {
  try {
    return run(work_dir, args, stdin);
  } catch (err) {
    if (isNonZeroExit(err)) throw new RangeUnreadable('range-unreadable', `git ${args.join(' ')}`);
    if (isOutputTooLarge(err))
      throw new RangeUnreadable('range-too-large', `git ${args.join(' ')}`);
    throw err;
  }
}

/** Patch-ids of every content-bearing commit in `base..tip`, keyed to the commit
 * they came from. `log -p` emits a `commit <sha>` header per patch, which is what
 * lets `patch-id` attribute each id — the pipeline the git docs name for exactly
 * this. Merges are excluded: a merge's diff is a combination of its parents' and
 * has no patch-id of its own to match. `--stable` pins the hash to git's
 * version-independent algorithm so ids computed on different hosts compare. */
function rangePatchIds(
  run: GitRunner,
  work_dir: string,
  base: string,
  tip: string
): Map<string, string> {
  const diffs = readOrFail(run, work_dir, [
    'log',
    '-p',
    '--no-merges',
    '--no-color',
    `${base}..${tip}`,
  ]);
  if (diffs.trim() === '') return new Map();
  return parsePatchIds(readOrFail(run, work_dir, ['patch-id', '--stable'], diffs));
}

/** The patch-id of the branch's COMBINED diff (`base` → `tip` as one patch) — the
 * fingerprint a squash-merge commit carries on integration. Null when the
 * combined diff is empty (the branch's commits cancel out). */
function combinedPatchId(
  run: GitRunner,
  work_dir: string,
  base: string,
  tip: string
): string | null {
  const diff = readOrFail(run, work_dir, ['diff', '--no-color', base, tip]);
  if (diff.trim() === '') return null;
  const ids = parsePatchIds(readOrFail(run, work_dir, ['patch-id', '--stable'], diff));
  const [first] = ids.keys();
  return first ?? null;
}

/** Options for {@link classifyLandedContent}. */
export interface LandedContentOptions {
  /** work_dir + args + stdin → stdout runner. Defaults to provenance's
   * {@link defaultGitRunner}; `patch-id` is the one primitive needing stdin. */
  run?: GitRunner;
}

/**
 * Decide whether `input.branch`'s work is present on `input.integration`.
 *
 * The ladder, strongest evidence first:
 *
 * 1. `landed-direct` — the tip is an ancestor of integration. No patch needed.
 * 2. `landed-equivalent` — every content-bearing commit has a patch-id twin.
 *    Covers rebase and cherry-pick, where the shas differ but the content does not.
 * 3. `landed-squashed` — the combined diff's patch-id appears as a single
 *    integration commit. Checked BEFORE `partial` because a squash-merge
 *    generally leaves no individual commit's patch-id intact, so a squashed
 *    branch that happens to share one trivial commit with integration would
 *    otherwise be misread as a partial landing.
 * 4. `partial` — some commits landed, some did not. A real state in this corpus,
 *    not an error: it is what a hand-cherry-picked subset looks like.
 * 5. `absent` — no twin at any granularity.
 *
 * Patch-ids are compared only within `merge_base..tip` vs `merge_base..integration`.
 * Scoping the integration side to the fork point is what keeps the comparison
 * meaningful: an unscoped search over all of history would match any commit that
 * ever carried the same diff, including the branch's own pre-fork ancestry.
 */
export function classifyLandedContent(
  input: LandedContentInput,
  opts: LandedContentOptions = {}
): LandedContentResult {
  const run = opts.run ?? defaultGitRunner;
  const { work_dir } = input;

  const tip = resolveCommit(run, work_dir, input.branch);
  if (tip === null) return { verdict: 'undecidable', cause: 'branch-unresolvable' };

  const head = resolveCommit(run, work_dir, input.integration);
  if (head === null) {
    return { verdict: 'undecidable', cause: 'integration-unresolvable', branch_commit: tip };
  }

  const anchors = { branch_commit: tip, integration_commit: head };

  if (isAncestor(run, work_dir, tip, head)) return { verdict: 'landed-direct', ...anchors };

  const base = mergeBase(run, work_dir, tip, head);
  if (base === null) return { verdict: 'undecidable', cause: 'no-merge-base', ...anchors };

  const located = { ...anchors, merge_base: base };

  try {
    const branchIds = rangePatchIds(run, work_dir, base, tip);
    if (branchIds.size === 0) {
      return { verdict: 'undecidable', cause: 'no-branch-content', ...located, n_commits: 0 };
    }

    const headIds = rangePatchIds(run, work_dir, base, head);
    const matched = [...branchIds.keys()].filter(id => headIds.has(id));
    const counts = { n_commits: branchIds.size, n_matched: matched.length };

    if (matched.length === branchIds.size) {
      return { verdict: 'landed-equivalent', ...located, ...counts };
    }

    const combined = combinedPatchId(run, work_dir, base, tip);
    if (combined !== null && headIds.has(combined)) {
      return { verdict: 'landed-squashed', ...located, ...counts };
    }

    return { verdict: matched.length > 0 ? 'partial' : 'absent', ...located, ...counts };
  } catch (err) {
    if (err instanceof RangeUnreadable) {
      return { verdict: 'undecidable', cause: err.cause_, ...located };
    }
    throw err;
  }
}

/**
 * True when the verdict says the work is present on integration.
 *
 * `partial` is deliberately NOT landed: a bead closed on a claim of completed
 * work whose change only half-landed is the failure mode under measurement, not
 * a success. `undecidable` is not landed either, but callers must exclude it
 * from the denominator rather than count it as a failure — see `tallyRig`.
 *
 * Written as an exhaustive switch rather than a lookup set so that adding a
 * verdict to {@link LandedContentVerdict} fails to compile here. A set would
 * accept a new `landed-*` verdict silently and classify it as NOT landed —
 * quietly inflating the false-close rate this module exists to measure.
 */
export function isLanded(verdict: LandedContentVerdict): boolean {
  switch (verdict) {
    case 'landed-direct':
    case 'landed-equivalent':
    case 'landed-squashed':
      return true;
    case 'partial':
    case 'absent':
    case 'undecidable':
      return false;
    default: {
      const exhaustive: never = verdict;
      return exhaustive;
    }
  }
}
