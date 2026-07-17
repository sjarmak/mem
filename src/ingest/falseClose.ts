import { newIdTokenRe } from './commitLinkage.js';
import type {
  LandedContentResult,
  LandedContentVerdict,
  UndecidableCause,
} from './landedContent.js';
import { isLanded } from './landedContent.js';

/**
 * ingest/falseClose (mem-z14cd) — the pure half of the false-close measurement:
 * join closed beads to their surviving branches, then aggregate the per-branch
 * content verdicts (ingest/landedContent) into a per-rig rate with a confidence
 * interval. The IO half — reading the store, shelling git — is
 * scripts/measure-false-close.mjs.
 *
 * The question: mem treats `bead closed` as a VERIFIED outcome label, which is
 * the premise that makes the city's exhaust a benchmark rather than a log. This
 * measures whether that holds, by asking of every closed bead that still has a
 * branch: is its change actually on the integration branch?
 *
 * What this module does NOT do is redefine the label. It produces a rate and an
 * explicit accounting of what the rate could not see; the decision about the
 * label's definition is an eval-design call made elsewhere, on this evidence.
 *
 * ZFC: set intersection and arithmetic. The one judgment-shaped choice — which
 * verdicts count as "landed" — lives in landedContent's `isLanded` and is a
 * definition, not an inference.
 */

/**
 * Every work-id candidate in a branch name: each contiguous run of segments
 * within each compound.
 *
 * commitLinkage takes each compound WHOLE, and is right to: it reads commit
 * messages, where whitespace and punctuation delimit an id (`… (mem-zfeys)`),
 * so the compound IS the id. Branch names have no such delimiter — the id is
 * built into a longer name — and reusing the whole-compound rule here silently
 * loses the majority of the corpus. Measured against the real checkouts: gascity
 * names its branches `bd-gc-<id>`, which as one compound matches no bead, so
 * whole-compound matching joined 4 of gascity's 112 branches, 2 of gpk's 71, and
 * 53 of mem's 103. gascity is the rig that answers whether mem's rate
 * generalizes at all; measuring it at 4 branches would have read as "no power"
 * when the frame is 112.
 *
 * Enumerating sub-spans restores those without loosening the match: precision
 * still comes entirely from intersecting candidates against the rig's known
 * closed-id set, and every span is still segment-aligned, so matching stays
 * boundary-exact. A parent id cannot match inside a child (`mem-75t.12` keeps
 * its dotted suffix attached to its last segment, so it never yields
 * `mem-75t`), and a mid-word substring is never a candidate. The permissiveness
 * is bounded and checked: across all five rigs with a checkout, no branch name
 * matches more than one closed bead, so this adds reach without adding a single
 * ambiguous join.
 *
 * Spans are cut with their ORIGINAL separators intact, never re-joined on a
 * canonical one: the underscore rigs (`scix_experiments-0c73`,
 * `migration_evals-0qd2`) carry `_` inside the id itself, so normalising
 * separators would rewrite every one of their ids into a string no bead is
 * keyed by.
 */
function idCandidates(name: string): Set<string> {
  const candidates = new Set<string>();
  for (const m of name.matchAll(newIdTokenRe())) {
    // Capturing the separator keeps it in the split output, so `parts` alternates
    // segment, separator, segment, … Stepping by 2 walks the segments, and each
    // slice is therefore a verbatim run of the original compound.
    const parts = m[0].toLowerCase().split(/([_-])/);
    for (let start = 0; start < parts.length; start += 2) {
      for (let end = start; end < parts.length; end += 2) {
        candidates.add(parts.slice(start, end + 1).join(''));
      }
    }
  }
  return candidates;
}

/** A ref split into scope and short name. `remote` is set only when scoped to a
 * remote-tracking branch. */
export interface ParsedRef {
  scope: 'local' | 'remote';
  /** The remote's name, for a remote-tracking ref. */
  remote?: string;
  /** The branch name without its scope prefix (e.g. `work/mem-cv06b`). */
  name: string;
  /** The full refname it was parsed from. */
  refname: string;
}

/** Parse a full refname into scope + branch name, or null when it is neither a
 * local head nor a remote-tracking branch (a tag, a note, `refs/stash`). Full
 * refnames are required rather than `%(refname:short)` output because the short
 * form cannot distinguish a local `origin/x` head from remote-tracking `origin/x`. */
export function parseRef(refname: string): ParsedRef | null {
  const head = /^refs\/heads\/(.+)$/.exec(refname);
  if (head !== null) return { scope: 'local', name: head[1], refname };

  const remote = /^refs\/remotes\/([^/]+)\/(.+)$/.exec(refname);
  if (remote === null) return null;
  // `refs/remotes/<remote>/HEAD` is a symbolic alias for the remote's default
  // branch, not a branch of its own; counting it would double-count that branch.
  if (remote[2] === 'HEAD') return null;
  return { scope: 'remote', remote: remote[1], name: remote[2], refname };
}

/** The ref to hand to git for a parsed ref: the short name for a local head,
 * `<remote>/<name>` for a remote-tracking one. Deliberately NOT the refname —
 * this is the argument git wants, while {@link ParsedRef.refname} is the raw
 * string git gave. Keeping the two apart is what stops a reported refname from
 * silently becoming a git-convenient short form. */
function gitRef(parsed: ParsedRef): string {
  return parsed.scope === 'local' ? parsed.name : `${parsed.remote}/${parsed.name}`;
}

/** A closed bead joined to the one surviving branch that names it. */
export interface JoinedBranch {
  work_id: string;
  /** The ref to hand to git — the short name for a local head, `<remote>/<name>`
   * for a remote-tracking one. */
  ref: string;
  scope: 'local' | 'remote';
}

/** Why a ref did not yield a usable bead↔branch join. Reported alongside the
 * rate so the sampling frame is auditable rather than implicit. */
export type JoinSkipReason =
  /** The branch name contains no token matching a closed bead in this rig. */
  | 'no-work-id'
  /** The branch names more than one closed bead — which one it carries is not a
   * mechanical fact, so it is dropped rather than attributed to a guess. */
  | 'ambiguous-multi-id'
  /** The ref IS the integration branch, or tracks it. */
  | 'integration-branch'
  /** A remote-tracking ref on a remote that is not the rig's authoritative one.
   * A fork's copy of a branch says nothing about whether work reached upstream. */
  | 'non-authoritative-remote'
  /** Both a local head and an authoritative remote ref name this bead; the local
   * head wins and the remote one is recorded here, never counted twice. */
  | 'duplicate-of-local';

/** One skipped ref and the reason. */
export interface JoinSkip {
  /** The raw full refname, as `git for-each-ref` gave it — never the short or
   * remote-prefixed form, so a reason's refnames mean the same thing whichever
   * ref discovery order produced them. */
  refname: string;
  reason: JoinSkipReason;
}

/** The join's output: what resolved, and everything that did not. */
export interface JoinResult {
  joined: JoinedBranch[];
  skipped: JoinSkip[];
}

/** Inputs for {@link joinBranches}. */
export interface JoinOptions {
  /** Full refnames from `git for-each-ref --format=%(refname)`. */
  refnames: readonly string[];
  /** The rig's closed bead ids. */
  closedIds: ReadonlySet<string>;
  /** The rig's integration branch (e.g. `main`). */
  integration: string;
  /** The remote whose copy of the integration branch is authoritative — resolved
   * by slug, never assumed to be `origin` (gascity's checkout carries fourteen
   * remotes, most of them contributor forks).
   *
   * `null` means no remote matched the rig's slug, in which case NO remote ref is
   * authoritative and the join falls back to local heads alone. It is a real
   * state, so it is spelled as one: an earlier version passed an unmatchable
   * sentinel string instead, which is the kind of hack that silently rots (it
   * acquired a NUL byte and turned the runner into a "binary" file to git). */
  authoritativeRemote: string | null;
}

/**
 * Join each closed bead to a surviving branch that names it, exact-token.
 *
 * Selection is per BEAD, not per ref: the unit under measurement is a closed
 * bead, and the same bead routinely has both a local head and its remote-tracking
 * copy. A local head wins — it is the working state that a stranded branch is
 * stranded IN, and it is what the hand-verified cases were found on — with the
 * authoritative remote's copy as the fallback, so a bead whose local branch was
 * pruned but whose pushed copy survives is still measurable. Results are sorted
 * by work_id so a run is reproducible independent of git's ref ordering.
 */
export function joinBranches(opts: JoinOptions): JoinResult {
  const { closedIds, integration, authoritativeRemote } = opts;
  const skipped: JoinSkip[] = [];

  // Candidates are lowercased (branch names are not reliably cased), so the id
  // set must be keyed the same way or a mixed-case rig silently joins nothing.
  // Measured: EnterpriseBench keys its beads `EnterpriseBench-<id>` and names
  // branches `EnterpriseBench-<id>` too, yet a case-sensitive lookup joined 0 of
  // its 1509 closed beads — the rig read as "no surviving branches" when it had
  // them. The map preserves each id's canonical casing so the join reports the
  // id as the store spells it, not as the branch happened to spell it.
  const canonical = new Map<string, string>();
  for (const id of closedIds) canonical.set(id.toLowerCase(), id);
  // work_id → the best ref seen so far. A local head, once seen, is never
  // displaced; the earlier remote candidate it displaces is recorded as a skip.
  // The ref is held as the ParsedRef it arrived as, not as the JoinedBranch it
  // becomes: ParsedRef keeps `refname` (the raw string git gave us) with the
  // candidate, so a displaced one is skipped under the same shape as every skip
  // that reports the ref in hand. JoinedBranch keeps only `ref` — the short form
  // built for git — which is what let the two displacement paths disagree.
  const best = new Map<string, ParsedRef>();

  for (const refname of opts.refnames) {
    const parsed = parseRef(refname);
    if (parsed === null) continue;

    if (parsed.name === integration) {
      skipped.push({ refname, reason: 'integration-branch' });
      continue;
    }
    // With no authoritative remote (null), every remote ref is non-authoritative.
    if (
      parsed.scope === 'remote' &&
      (authoritativeRemote === null || parsed.remote !== authoritativeRemote)
    ) {
      skipped.push({ refname, reason: 'non-authoritative-remote' });
      continue;
    }

    const ids = new Set<string>();
    for (const candidate of idCandidates(parsed.name)) {
      const hit = canonical.get(candidate);
      if (hit !== undefined) ids.add(hit);
    }
    if (ids.size === 0) {
      skipped.push({ refname, reason: 'no-work-id' });
      continue;
    }
    if (ids.size > 1) {
      skipped.push({ refname, reason: 'ambiguous-multi-id' });
      continue;
    }

    const work_id = [...ids][0];
    const held = best.get(work_id);
    if (held === undefined) {
      best.set(work_id, parsed);
    } else if (held.scope === 'remote' && parsed.scope === 'local') {
      best.set(work_id, parsed);
      skipped.push({ refname: held.refname, reason: 'duplicate-of-local' });
    } else {
      skipped.push({ refname, reason: 'duplicate-of-local' });
    }
  }

  return {
    joined: [...best.entries()]
      .map(([work_id, p]) => ({ work_id, ref: gitRef(p), scope: p.scope }))
      .sort((a, b) => a.work_id.localeCompare(b.work_id)),
    skipped,
  };
}

/** One integration ref's verdict on a branch. */
export interface RefVerdict {
  /** The integration ref decided against (e.g. `main`, `upstream/main`). */
  ref: string;
  result: LandedContentResult;
}

/** Verdict strength, strongest first. The order is the evidence ladder
 * landedContent already documents, with `undecidable` weakest: a ref that could
 * not be asked must never outrank one that answered. */
const VERDICT_RANK: Record<LandedContentVerdict, number> = {
  'landed-direct': 5,
  'landed-equivalent': 4,
  'landed-squashed': 3,
  partial: 2,
  absent: 1,
  undecidable: 0,
};

/**
 * Decide a branch across every integration ref it could have landed on, taking
 * the strongest verdict any of them gives.
 *
 * A rig has more than one ref that legitimately counts as "landed", and no
 * single choice is correct across the corpus — measured, not assumed:
 *
 *   mem             local main is 53 commits AHEAD of origin/main (finalize
 *                   lands on local main and workers never push)
 *   gpk             local main is 14 commits BEHIND upstream/main
 *   CodeScaleBench  local main is 1196 commits BEHIND upstream/main
 *
 * Which refs are actually consulted is the CALLER's choice, and the caller can
 * only pass what it can justify: the runner consults the local integration
 * branch plus the remote matching the rig's `RIG_REPOS` slug. For mem that is
 * `origin`, so the AHEAD case above is genuinely exercised. For gpk the slug
 * names `origin`, so `upstream/main` — the ref the BEHIND case refers to — is
 * NOT consulted in the reported run; measured sensitivity is 2 of 43 branches
 * (81.1% → 77.4%). This function cannot fix a ref the caller never passes, and
 * the gpk and CodeScaleBench rows above are the argument for widening the
 * caller's ref set, not a description of what it currently does.
 *
 * So measuring mem against its remote would read 53 commits of landed work as
 * `absent`, and measuring gpk or CodeScaleBench against local would do the same
 * for work that landed upstream. Either choice manufactures false closes out of
 * a ref selection.
 *
 * Taking the strongest verdict across refs makes the result a LOWER BOUND on the
 * false-close rate, which is the correct direction for a measurement whose
 * finding is itself an alarm: a ref-selection error can now only understate the
 * problem, never inflate it. The per-ref verdicts are retained in full so a
 * reader can re-derive the combination rather than trust it.
 */
export function combineRefVerdicts(perRef: readonly RefVerdict[]): RefVerdict {
  if (perRef.length === 0) {
    throw new Error('combineRefVerdicts: no refs were consulted');
  }
  return perRef.reduce((best, cur) =>
    VERDICT_RANK[cur.result.verdict] > VERDICT_RANK[best.result.verdict] ? cur : best
  );
}

/** A two-sided interval on a proportion. */
export interface Interval {
  low: number;
  high: number;
}

/**
 * The Wilson score interval for `k` successes in `n` trials. Chosen over the
 * normal approximation because this measurement's per-rig cells are small (most
 * rigs contribute well under 100 branches) and its rates sit near zero, exactly
 * where the normal approximation produces intervals that extend below 0. Wilson
 * stays inside [0,1] at every n and k. Mirrors the closed form already used by
 * memory-bench's base-rate gate (`grading/base_rate.py`), widened to two sides.
 *
 * `n === 0` yields the full [0,1] interval: no observation constrains the rate,
 * which is the honest answer for an empty cell rather than a point at zero.
 */
export function wilsonInterval(k: number, n: number, z = 1.96): Interval {
  if (n === 0) return { low: 0, high: 1 };
  const phat = k / n;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const center = (phat + z2 / (2 * n)) / denom;
  const half = (z / denom) * Math.sqrt((phat * (1 - phat)) / n + z2 / (4 * n * n));
  return { low: Math.max(0, center - half), high: Math.min(1, center + half) };
}

/** One closed bead's branch, decided. */
export interface DecidedBranch extends JoinedBranch {
  /** The winning verdict — {@link combineRefVerdicts} over `per_ref`. */
  result: LandedContentResult;
  /** The integration ref the winning verdict came from. */
  decided_by?: string;
  /** Every ref consulted, retained so the combination is auditable. */
  per_ref?: RefVerdict[];
}

/** The measured false-close picture for one rig. */
export interface RigTally {
  rig: string;
  /** Closed beads in the store for this rig — the population the coverage line
   * is against, stated explicitly so no rate is read against an implicit base. */
  n_closed: number;
  /** Closed beads that joined to a surviving branch: the sampling frame. */
  n_joined: number;
  /** Joined beads git could decide (everything but `undecidable`) — the rate's
   * denominator. */
  n_decided: number;
  /** Decided beads whose change is NOT fully on integration (`absent` +
   * `partial`) — the rate's numerator. */
  n_not_landed: number;
  verdicts: Record<LandedContentVerdict, number>;
  undecidable_causes: Record<string, number>;
  /** `n_not_landed / n_decided`, or null when nothing was decided. A rate over
   * an empty denominator is not zero; it is unmeasured. */
  rate: number | null;
  /** The Wilson 95% interval on `rate`. Null exactly when `rate` is. */
  ci: Interval | null;
  /** `n_joined / n_closed` — what fraction of closed beads this rate can see at
   * all. The headline number of the whole measurement is this one, not the rate:
   * a rate over 2% of the corpus does not generalize to the corpus. */
  coverage: number;
}

const EMPTY_VERDICTS = (): Record<LandedContentVerdict, number> => ({
  'landed-direct': 0,
  'landed-equivalent': 0,
  'landed-squashed': 0,
  partial: 0,
  absent: 0,
  undecidable: 0,
});

/** The counts a tally is built from — every RigTally field that is observed
 * rather than derived. */
type TallyCounts = Omit<RigTally, 'rig' | 'rate' | 'ci' | 'coverage'>;

/** Derive the rate, its interval, and coverage from a set of counts. Both the
 * per-rig row and the pooled row are built through here so the two cannot drift
 * on the cases that carry this measurement's meaning: an empty denominator is a
 * null rate (unmeasured, never zero), and an empty population is zero coverage
 * rather than a division by zero. */
function makeTally(rig: string, counts: TallyCounts): RigTally {
  const { n_closed, n_joined, n_decided, n_not_landed } = counts;
  const rate = n_decided === 0 ? null : n_not_landed / n_decided;
  return {
    rig,
    ...counts,
    rate,
    ci: rate === null ? null : wilsonInterval(n_not_landed, n_decided),
    coverage: n_closed === 0 ? 0 : n_joined / n_closed,
  };
}

/** Aggregate one rig's decided branches into its tally. Pure arithmetic over the
 * verdicts; `undecidable` branches are excluded from the rate's denominator and
 * reported per cause, never folded into `absent`. */
export function tallyRig(
  rig: string,
  nClosed: number,
  decided: readonly DecidedBranch[]
): RigTally {
  const verdicts = EMPTY_VERDICTS();
  const undecidable_causes: Record<string, number> = {};
  let n_decided = 0;
  let n_not_landed = 0;

  for (const d of decided) {
    const { verdict } = d.result;
    verdicts[verdict] += 1;
    if (verdict === 'undecidable') {
      const cause: UndecidableCause | 'unspecified' = d.result.cause ?? 'unspecified';
      undecidable_causes[cause] = (undecidable_causes[cause] ?? 0) + 1;
      continue;
    }
    n_decided += 1;
    if (!isLanded(verdict)) n_not_landed += 1;
  }

  return makeTally(rig, {
    n_closed: nClosed,
    n_joined: decided.length,
    n_decided,
    n_not_landed,
    verdicts,
    undecidable_causes,
  });
}

/** Below this many decided branches, a per-rig rate's Wilson interval is too
 * wide to say anything — the cell is reported but must not be read as a rate.
 * Set at 20: at n=20 the interval spans roughly ±20 points, already barely
 * informative, and every rig below it in this corpus contributes single digits. */
export const MIN_DECIDED_FOR_RATE = 20;

/** True when a rig's cell has enough decided branches to read its rate. */
export function hasPower(tally: RigTally): boolean {
  return tally.n_decided >= MIN_DECIDED_FOR_RATE;
}

/** The pooled cross-rig tally. Rigs are pooled by summing counts, NOT by
 * averaging per-rig rates: an unweighted mean of rates would let a rig with three
 * decided branches move the headline as much as one with a hundred. */
export function tallyPooled(rigs: readonly RigTally[]): RigTally {
  const verdicts = EMPTY_VERDICTS();
  const undecidable_causes: Record<string, number> = {};
  let n_closed = 0;
  let n_joined = 0;
  let n_decided = 0;
  let n_not_landed = 0;

  for (const r of rigs) {
    n_closed += r.n_closed;
    n_joined += r.n_joined;
    n_decided += r.n_decided;
    n_not_landed += r.n_not_landed;
    for (const [v, n] of Object.entries(r.verdicts)) {
      verdicts[v as LandedContentVerdict] += n;
    }
    for (const [c, n] of Object.entries(r.undecidable_causes)) {
      undecidable_causes[c] = (undecidable_causes[c] ?? 0) + n;
    }
  }

  return makeTally('POOLED', {
    n_closed,
    n_joined,
    n_decided,
    n_not_landed,
    verdicts,
    undecidable_causes,
  });
}
