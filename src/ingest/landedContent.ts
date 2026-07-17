import {
  type GitPipeRunner,
  type GitRunner,
  defaultGitPipeRunner,
  defaultGitRunner,
  isAncestor,
  isNonZeroExit,
  shaOrNull,
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

/** Resolve a ref to a full commit sha, or null when it does not resolve. The ref
 * is DB/branch-listing-sourced, so `--end-of-options` precedes it: git then reads
 * it strictly as a revision and a value like `--output=<path>` is an unknown rev
 * (non-zero exit → null) rather than a flag git acts on. `^{commit}` peels an
 * annotated tag and rejects a ref that names a tree or blob. Every later command
 * takes the resolved 40-hex sha, so this is the only injection surface.
 *
 * Exported: scripts/measure-false-close.mjs resolves integration-ref tips with
 * this same rev-parse shape (mem-j1r2w) — no reason for the runner to keep its
 * own copy. */
export function resolveCommit(run: GitRunner, work_dir: string, ref: string): string | null {
  return shaOrNull(run, work_dir, ['rev-parse', '--verify', '--end-of-options', `${ref}^{commit}`]);
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

/** Pipe a diff listing into `git patch-id` and parse the ids back. A non-zero
 * exit — a pruned object, an unknown revision — propagates unchanged;
 * {@link classifyLandedContent}'s catch tests `isNonZeroExit` directly and maps
 * it to `range-unreadable`, so one unreadable range degrades that branch
 * instead of failing the whole cross-rig sweep. Any OTHER failure (a missing
 * `git` or `bash` binary) is a misconfiguration, which is why nothing here
 * intercepts it either: a swallowed misconfiguration would silently mark every
 * branch undecidable, an empty measurement dressed up as a coverage gap.
 *
 * The pipe is what keeps the patch text out of this process: only patch-id's
 * output crosses back. That is also why no "is the diff empty?" guard precedes
 * it — there is no diff here to inspect, and none is needed, since `patch-id`
 * emits nothing for an empty patch and an empty map is already the answer.
 *
 * It is also why there is no "output too large" cause any more. What crosses
 * back is ~82 bytes per commit, so the runner's buffer now bounds a range's
 * COMMIT COUNT (~200k against 16MB), not its diff size — four orders of
 * magnitude off the 1196-commit range that used to overflow. If one ever did
 * exceed it, Node kills the child, leaving `status` null rather than a number:
 * `isNonZeroExit` is false, so it propagates and fails the sweep loudly instead
 * of degrading that branch to a quiet `range-unreadable`. That is the right end
 * of the trade — a buffer that large being hit is a bug to see, not a coverage
 * hole to record. */
function pipedPatchIds(
  runPipe: GitPipeRunner,
  work_dir: string,
  args: string[]
): Map<string, string> {
  return parsePatchIds(runPipe(work_dir, args));
}

/** A caller-owned memo of {@link rangePatchIds} results, keyed by the full
 * argument tuple `${work_dir}\x00${base}\x00${tip}`. Caller-owned, never a module
 * global: the tests inject a fresh fake runner per case, so a shared cache would
 * serve one case's fake output to the next; a real sweep wants one cache per rig
 * so each rig's maps free at the loop boundary. The value is a `ReadonlyMap` so a
 * cached walk cannot be mutated in place by a later caller — the type enforces
 * what a docstring could only ask for. */
export type LandedContentCache = Map<string, ReadonlyMap<string, string>>;

/** Patch-ids of every content-bearing commit in `base..tip`, keyed to the commit
 * they came from. `log -p` emits a `commit <sha>` header per patch, which is what
 * lets `patch-id` attribute each id — the pipeline the git docs name for exactly
 * this. Merges are excluded: a merge's diff is a combination of its parents' and
 * has no patch-id of its own to match.
 *
 * The walk is a pure function of `(work_dir, base, tip)` over an immutable,
 * content-addressed DAG, so `cache` (when present) memoizes it verbatim. Both
 * `base` and `tip` are the full 40-hex shas already resolved by the caller, so
 * the key is injective without escaping; the `\x00` separator is belt-and-braces
 * (no POSIX path holds a NUL) and is written as an escape, not a raw byte, so the
 * file stays greppable (a raw NUL reads as binary — see mem-y2x7n). `base` is in
 * the key deliberately: widening the range to a shared superset would dissolve
 * the fork-point scoping that {@link classifyLandedContent} documents, which is
 * the rejected alternative from this bead's history — do not collapse it to `tip`.
 *
 * An empty result is a legitimate cached value: `set -o pipefail` in the pipe
 * script (provenance.ts) means an empty map is "git walked, no content-bearing
 * commit", never a swallowed failure — so caching it cannot promote a one-off
 * fault into a sweep-wide false `no-branch-content`. A failed walk throws before
 * `set`, so it is never cached. */
function rangePatchIds(
  runPipe: GitPipeRunner,
  work_dir: string,
  base: string,
  tip: string,
  cache?: LandedContentCache
): ReadonlyMap<string, string> {
  const key = `${work_dir}\x00${base}\x00${tip}`;
  const hit = cache?.get(key);
  if (hit !== undefined) return hit;
  const ids = pipedPatchIds(runPipe, work_dir, [
    'log',
    '-p',
    '--no-merges',
    '--no-color',
    `${base}..${tip}`,
  ]);
  cache?.set(key, ids);
  return ids;
}

/** The patch-id of the branch's COMBINED diff (`base` → `tip` as one patch) — the
 * fingerprint a squash-merge commit carries on integration. Null when the
 * combined diff is empty (the branch's commits cancel out): `patch-id` emits no
 * line, so there is no first key. */
function combinedPatchId(
  runPipe: GitPipeRunner,
  work_dir: string,
  base: string,
  tip: string
): string | null {
  const ids = pipedPatchIds(runPipe, work_dir, ['diff', '--no-color', base, tip]);
  const [first] = ids.keys();
  return first ?? null;
}

/** Options for {@link classifyLandedContent}. */
export interface LandedContentOptions {
  /** work_dir + args → stdout runner, for the single-command rungs of the ladder
   * (`rev-parse`, `merge-base`). Defaults to {@link defaultGitRunner}. */
  run?: GitRunner;
  /** The `<diff listing> | patch-id` runner. Defaults to
   * {@link defaultGitPipeRunner}. Separate from `run` because a pipeline is a
   * different shape, and keeping the patch text in the kernel is the point. */
  runPipe?: GitPipeRunner;
  /** A caller-owned {@link LandedContentCache} memoizing the patch-id range
   * walks. Optional: absent means every call re-walks (the prior behavior). Pass
   * one across a sweep so branches sharing a merge base — a convoy cut from one
   * integration tip, or a branch decided against two refs with a common fork
   * point — reuse a single walk instead of re-listing the same history. */
  cache?: LandedContentCache;
}

/**
 * Decide whether `input.branch`'s work is present on `input.integration`, walking
 * the {@link LandedContentVerdict} ladder in the order that type declares.
 *
 * The one ordering that is not self-evident: `landed-squashed` is checked BEFORE
 * `partial`, because a squash-merge generally leaves no individual commit's
 * patch-id intact — so a squashed branch that happens to share one trivial commit
 * with integration would otherwise be misread as a partial landing.
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
  const runPipe = opts.runPipe ?? defaultGitPipeRunner;
  const { cache } = opts;
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
    const branchIds = rangePatchIds(runPipe, work_dir, base, tip, cache);
    if (branchIds.size === 0) {
      return { verdict: 'undecidable', cause: 'no-branch-content', ...located, n_commits: 0 };
    }

    const headIds = rangePatchIds(runPipe, work_dir, base, head, cache);
    const matched = [...branchIds.keys()].filter(id => headIds.has(id));
    const counts = { n_commits: branchIds.size, n_matched: matched.length };

    if (matched.length === branchIds.size) {
      return { verdict: 'landed-equivalent', ...located, ...counts };
    }

    const combined = combinedPatchId(runPipe, work_dir, base, tip);
    if (combined !== null && headIds.has(combined)) {
      return { verdict: 'landed-squashed', ...located, ...counts };
    }

    return { verdict: matched.length > 0 ? 'partial' : 'absent', ...located, ...counts };
  } catch (err) {
    if (isNonZeroExit(err)) {
      return { verdict: 'undecidable', cause: 'range-unreadable', ...located };
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
 * verdict to {@link LandedContentVerdict} fails to compile here (the declared
 * `boolean` return has no case to satisfy it). A set would accept a new
 * `landed-*` verdict silently and classify it as NOT landed — quietly inflating
 * the false-close rate this module exists to measure.
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
  }
}
