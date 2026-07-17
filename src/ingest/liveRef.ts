/**
 * ingest/liveRef (mem-wanz.6, PRD §5.2, key #3, risks R2/R3) — re-measure the
 * live-ref join that the corpus framing claimed at 27%.
 *
 * The "266/983 sessions ≈ 27% carry a live branch ref" headline was NOT
 * reproducible from any substrate-derivable join (it came from transcript-side
 * parsing with no validated path to a work_id). The ONLY join the store + git can
 * actually derive is: a `gc-<id>` work record ↔ a live `refs/heads/bd-gc-<id>`
 * session branch. This module builds that validated resolver and the fail-closed
 * classifier for the merge-base write-gate, so the real percentage can be
 * measured against the Day-0 frozen refs (the IO runner does the git; this stays
 * pure and unit-tested).
 *
 * Two precision choices make the join high-confidence (key #3, P≈0.95):
 *  - EXACT token match `bd-<work_id>` — a suffixed branch (`bd-<id>-rebase`,
 *    `-fixup`) is a different slug and is NOT the canonical landing, so it is
 *    excluded rather than collapsed onto the work id.
 *  - FAIL-CLOSED merge-base gate: a base with no merge-base on the AUTHORITATIVE
 *    remote's integration branch is DROPPED as decay, never written. The R3
 *    silent-corruption guard for the 14-remote, worktree-aliased gascity checkout
 *    lives UPSTREAM of this classifier — in the runner's slug-based remote pick +
 *    authRef-resolvability gate, and git-common-dir Step-0 (mem-wanz.2). Here the
 *    ancestry check is only an invariant assertion: `base_sha` is a merge-base of
 *    authRef, so it is an ancestor by construction, and a non-true answer is a git
 *    fault (undecided), not a measurable corruption rate (mem-zzzl4).
 *
 * ZFC: mechanical parsing + set membership + ancestor arithmetic. No semantic
 * judgment; git reports the merge-base and ancestry, we classify the result.
 */

import type { AncestryFault, MergeBaseFault } from './provenance.js';

/** One ref from a frozen `git for-each-ref --format='%(objectname) %(refname)
 * %(committerdate:iso)'` dump (the Day-0 snapshot, mem-wanz.1). */
export interface RefEntry {
  sha: string;
  refname: string;
  date: string;
}

/** Parse a frozen for-each-ref dump. Lines are `<sha> <refname> <iso-date>`;
 * the date carries no spaces, so a 3-way split on the first two spaces is exact.
 * Blank lines (trailing newline) are skipped. */
export function parseForEachRef(text: string): RefEntry[] {
  const out: RefEntry[] = [];
  for (const line of text.split('\n')) {
    if (line.trim() === '') continue;
    const first = line.indexOf(' ');
    const second = line.indexOf(' ', first + 1);
    if (first === -1 || second === -1) continue;
    out.push({
      sha: line.slice(0, first),
      refname: line.slice(first + 1, second),
      date: line.slice(second + 1),
    });
  }
  return out;
}

const BD_HEAD_RE = /^refs\/heads\/bd-(.+)$/;

/** The work-id slug of a `refs/heads/bd-<slug>` session branch, else null (a
 * non-bd head, a tag, or a non-head ref). */
export function branchSlug(refname: string): string | null {
  const m = BD_HEAD_RE.exec(refname);
  return m === null ? null : m[1];
}

/** A work_id joined to its live session branch by the exact-token rule. */
export interface ResolvedRef {
  work_id: string;
  refname: string;
  sha: string;
}

/**
 * Join each work id to its live `refs/heads/bd-<work_id>` head, exact-token. The
 * branch slug must equal the work id (case-insensitive) — a suffixed branch is a
 * distinct slug and is not matched, which keeps the join high-precision. Work ids
 * with no live head are absent from the result (unlinked, not an error). The
 * result preserves `workIds` order for a reproducible report.
 */
export function resolveLiveRefs(
  workIds: readonly string[],
  refs: readonly RefEntry[]
): ResolvedRef[] {
  const bySlug = new Map<string, RefEntry>();
  for (const ref of refs) {
    const slug = branchSlug(ref.refname);
    if (slug !== null) bySlug.set(slug.toLowerCase(), ref);
  }

  const out: ResolvedRef[] = [];
  for (const work_id of workIds) {
    const ref = bySlug.get(work_id.toLowerCase());
    if (ref !== undefined) out.push({ work_id, refname: ref.refname, sha: ref.sha });
  }
  return out;
}

/** Drop reason: git ANSWERED that the branch and the authoritative branch share no
 * common ancestor (`merge-base` exit 1) — the branch's history is disjoint from the
 * integration branch. This is the live-ref DECAY signal — the case the Day-0 bundle
 * exists to backstop — and the ONLY measurable drop this gate reports. A merge-base
 * git could not COMPUTE (a pruned/unreadable object, a signal, a fault) is NOT this:
 * it routes to {@link UNDECIDED_MERGE_BASE_OBJECT_UNREADABLE} /
 * {@link UNDECIDED_MERGE_BASE_GIT_UNAVAILABLE}, so a fault is never counted as decay
 * (mem-f0n07). */
export const DROP_NO_MERGE_BASE = 'no_merge_base_in_authoritative_checkout';

/** Undecided cause: git could not read the objects for this base in THIS checkout
 * (canonically exit 128 on a pruned/GC'd object). Checkout-local and recoverable
 * — the Day-0 frozen bundle backstops it — which is what the `_in_checkout` suffix
 * asserts. Maps from {@link AncestryFault} `object-unreadable`. */
export const UNDECIDED_OBJECT_UNREADABLE = 'ancestry_object_unreadable_in_checkout';

/** Undecided cause: git could not run the probe at all — a missing binary, a
 * signal kill, a maxBuffer overrun. Environment-wide, NOT a property of this
 * rig's checkout (so deliberately no `_in_checkout` suffix): the remedy is to
 * rerun once the toolchain is healthy, not to reach for the Day-0 bundle. Maps
 * from {@link AncestryFault} `git-unavailable`, and is the fail-safe bucket for
 * the impossible `false` — an answer we cannot attribute is reported as
 * needs-investigation, never silently kept.
 *
 * Splitting this from {@link UNDECIDED_OBJECT_UNREADABLE} keeps
 * `undecided_by_cause` auditable per-remedy rather than an opaque single-key
 * bucket (mem-y2x7n, mem-zwmuq). */
export const UNDECIDED_GIT_UNAVAILABLE = 'ancestry_git_unavailable';

/** Undecided cause: git could not read the objects to COMPUTE the merge-base in
 * THIS checkout (canonically exit 128 on a pruned/GC'd object). The merge-base
 * twin of {@link UNDECIDED_OBJECT_UNREADABLE}: checkout-local and recoverable via
 * the Day-0 frozen bundle, which the `_in_checkout` suffix asserts. Distinct from
 * {@link DROP_NO_MERGE_BASE} — a git exit-1 no-merge-base is the decay signal, but
 * a fault computing the base has no verdict in it, and folding the two would
 * fabricate a decay data point out of a measurement fault (mem-f0n07 — the
 * merge-base twin of mem-y2x7n/mem-zwmuq's ancestry fix). Maps from a {@link
 * MergeBaseFault} whose `fault` is `object-unreadable`. */
export const UNDECIDED_MERGE_BASE_OBJECT_UNREADABLE = 'merge_base_object_unreadable_in_checkout';

/** Undecided cause: git could not run the merge-base computation at all — a
 * missing binary, a signal kill, a maxBuffer overrun. The merge-base twin of
 * {@link UNDECIDED_GIT_UNAVAILABLE}: environment-wide, NOT a property of this rig's
 * checkout (so no `_in_checkout` suffix), remedied by a rerun once the toolchain is
 * healthy, not the Day-0 bundle. Reported per-cause beside the object-unreadable
 * bucket for the same reason the ancestry causes are split — `undecided_by_cause`
 * stays auditable per-remedy. Maps from a {@link MergeBaseFault} whose `fault` is
 * `git-unavailable`. */
export const UNDECIDED_MERGE_BASE_GIT_UNAVAILABLE = 'merge_base_git_unavailable';

/** The result of the IO layer's merge-base computation for one resolved ref. */
export interface MergeBaseInput {
  work_id: string;
  refname: string;
  branch_sha: string;
  /** `git merge-base <branch_sha> <authoritative>/main`, as a tri-state that keeps
   * a git fault apart from a genuine no-merge-base — the merge-base twin of {@link
   * is_ancestor}'s tri-state:
   *  - a sha                    → the fork point on the authoritative branch; keep candidate.
   *  - null                     → git ANSWERED there is none (exit 1): the branch
   *    shares no history with the authoritative branch — the decay signal.
   *  - a {@link MergeBaseFault} → git could NOT compute it, ATTRIBUTED to its cause
   *    (`object-unreadable` = exit 128 on a pruned/GC'd object, checkout-local;
   *    `git-unavailable` = a missing binary/signal/maxBuffer overrun,
   *    environment-wide). **A fault is not null.** It routes to undecided, never to
   *    decay (mem-f0n07). The cause reuses the ancestry fault vocabulary because
   *    {@link AncestryFault} names HOW git failed, not what was asked; it is WRAPPED
   *    in an object because a fork-point sha is a string and the fault literals are
   *    too, so a bare union would let the compiler confuse them ({@link MergeBaseFault}).
   *
   * {@link classifyMergeBase} discriminates the fault by `typeof === 'object'`, the
   * sha and null falling out as the non-object states. Producers should use
   * ingest/provenance.ts's `mergeBaseOrFault`, which returns exactly these states. */
  base_sha: string | null | MergeBaseFault;
  /** The ancestry of `base_sha` against the authoritative branch, from
   * `merge-base --is-ancestor` by exit code. `base_sha` is itself a merge-base of
   * authRef, so this is an INVARIANT PROBE, not a corruption measurement — its
   * substantive outcomes are:
   *  - `true`              — git answered YES (exit 0). The invariant holds; keep.
   *  - an {@link AncestryFault} — git could NOT answer, ATTRIBUTED to its cause:
   *    `object-unreadable` (exit 128 on a pruned/GC'd object, checkout-local) or
   *    `git-unavailable` (a missing binary, a signal, a maxBuffer overrun,
   *    environment-wide). Each routes to a distinct undecided cause, so an
   *    operator can tell "fall back to the Day-0 bundle" from "rerun the sweep".
   * A `false` (exit 1) is mathematically impossible for a merge-base of authRef
   * (mem-zzzl4); {@link classifyMergeBase} folds it into `git-unavailable`'s
   * undecided bucket as a fail-safe, so an impossible answer is never silently
   * kept.
   *
   * Producers should use ingest/provenance.ts's `ancestryOrFault`, which returns
   * exactly `true | false | AncestryFault`. */
  is_ancestor: boolean | AncestryFault;
}

/** A kept live-ref base: a replayable {base, branch tip} anchored on the
 * authoritative branch. */
export interface LiveRefBase {
  work_id: string;
  refname: string;
  branch_sha: string;
  base_sha: string;
}

/** A dropped resolution, with the reason it failed the write-gate. The only drop
 * reason is {@link DROP_NO_MERGE_BASE} — decay, not corruption. */
export interface LiveRefDrop {
  work_id: string;
  refname: string;
  reason: string;
}

/** A resolution the gate could not confirm, with the cause. Distinct from
 * {@link LiveRefDrop} on purpose: a drop is a decayed base (no merge-base at all),
 * an undecided is a base that resolved but whose ancestry invariant went
 * unconfirmed (git faulted, or the impossible false). Folding the two would let a
 * git fault masquerade as decay. */
export interface LiveRefUndecided {
  work_id: string;
  refname: string;
  cause: string;
}

/** Exactly one of `kept` / `drop` / `undecided` is set. */
export interface LiveRefResult {
  kept?: LiveRefBase;
  drop?: LiveRefDrop;
  undecided?: LiveRefUndecided;
}

/** Route an unconfirmed ancestry to its undecided cause. Written as an exhaustive
 * switch, mirroring {@link isLanded} in ingest/landedContent.ts: a new {@link
 * AncestryFault} variant then fails to COMPILE here (the declared `string` return
 * has no case to satisfy it) rather than silently mis-bucketing into
 * git-unavailable and defeating the per-cause auditability this split exists to
 * add (mem-zwmuq). `object-unreadable` is checkout-local; `git-unavailable` and
 * the impossible `false` share the environment-wide/unattributable fail-safe
 * bucket — see the {@link UNDECIDED_OBJECT_UNREADABLE} / {@link
 * UNDECIDED_GIT_UNAVAILABLE} constant docs for the per-cause remedy. */
function undecidedCause(is_ancestor: false | AncestryFault): string {
  switch (is_ancestor) {
    case 'object-unreadable':
      return UNDECIDED_OBJECT_UNREADABLE;
    case 'git-unavailable':
    case false:
      return UNDECIDED_GIT_UNAVAILABLE;
  }
}

/** Route a merge-base git could not COMPUTE to its undecided cause — the merge-base
 * twin of {@link undecidedCause}, and an exhaustive switch for the same reason: a
 * new {@link AncestryFault} variant fails to COMPILE here (the declared `string`
 * return has no case to satisfy it) rather than silently mis-bucketing, keeping
 * `undecided_by_cause` auditable per-remedy. `object-unreadable` is checkout-local
 * (use the Day-0 bundle); `git-unavailable` is environment-wide (rerun) — see the
 * {@link UNDECIDED_MERGE_BASE_OBJECT_UNREADABLE} / {@link
 * UNDECIDED_MERGE_BASE_GIT_UNAVAILABLE} constant docs. There is no `false` case to
 * fold in as there is for the ancestry probe: a merge-base has no exit-1 fault
 * state, only the exit-1 no-common-ancestor ANSWER, which is `null` decay and
 * settled before this is ever reached. */
function mergeBaseUndecidedCause(fault: AncestryFault): string {
  switch (fault) {
    case 'object-unreadable':
      return UNDECIDED_MERGE_BASE_OBJECT_UNREADABLE;
    case 'git-unavailable':
      return UNDECIDED_MERGE_BASE_GIT_UNAVAILABLE;
  }
}

/**
 * Classify a merge-base result into keep / decay-drop / undecided. The base is
 * kept only when it resolved AND git confirmed the ancestry invariant. The
 * non-keep outcomes are reported apart, because each is a different fact:
 *  - no merge-base → drop {@link DROP_NO_MERGE_BASE}, the decay signal, and the
 *    only measurable drop here.
 *  - a merge-base git could not COMPUTE (a `base_sha` {@link MergeBaseFault}) →
 *    undecided, ATTRIBUTED to its cause by {@link mergeBaseUndecidedCause}
 *    (`object-unreadable` → the Day-0 bundle, `git-unavailable` → rerun). Checked
 *    BEFORE the `null` decay arm so a fault is never miscounted as decay
 *    (mem-f0n07) — the merge-base half.
 *  - a resolved base whose ancestry is unconfirmed → undecided, with the cause
 *    ATTRIBUTED from `is_ancestor` by {@link undecidedCause} (the impossible
 *    `false` folds fail-safe into git-unavailable). `base_sha` is a merge-base of
 *    authRef, so it is an ancestor BY CONSTRUCTION; the probe can only confirm
 *    (true) or fail to answer. There is no reachable "not an ancestor" verdict to
 *    tally, so this is an invariant assertion, not a corruption gate (mem-zzzl4) —
 *    which is why a stray false folds into undecided fail-safe rather than a
 *    corruption count.
 *
 * So decay cannot be confounded with a merge-base git could not compute, nor with
 * an ancestry git could not confirm, an environment-wide git fault cannot be
 * misattributed to this rig's checkout, and nothing is written silently on any
 * path (mem-y2x7n, mem-zwmuq, mem-f0n07).
 */
export function classifyMergeBase(input: MergeBaseInput): LiveRefResult {
  // Merge-base fault first: git could not COMPUTE the fork point (a wrapped fault,
  // NOT a sha and NOT null). A fault has no verdict in it, so it is undecided —
  // never decay — attributed to its cause. The wrapper makes the fault the only
  // object state (a sha is a string, no-merge-base is null), so `typeof` discriminates.
  if (typeof input.base_sha === 'object' && input.base_sha !== null) {
    return {
      undecided: {
        work_id: input.work_id,
        refname: input.refname,
        cause: mergeBaseUndecidedCause(input.base_sha.fault),
      },
    };
  }
  // No-merge-base next: git ANSWERED there is none (null). With no base there is
  // nothing to ask about, so `is_ancestor` carries no meaning here.
  if (input.base_sha === null) {
    return { drop: { work_id: input.work_id, refname: input.refname, reason: DROP_NO_MERGE_BASE } };
  }
  // `!== true`, NOT `=== null`, so the impossible `false` is caught here as a
  // fail-safe rather than falling through to keep. Cause attribution — the why
  // behind each bucket — is in the header docstring.
  if (input.is_ancestor !== true) {
    return {
      undecided: {
        work_id: input.work_id,
        refname: input.refname,
        cause: undecidedCause(input.is_ancestor),
      },
    };
  }
  return {
    kept: {
      work_id: input.work_id,
      refname: input.refname,
      branch_sha: input.branch_sha,
      base_sha: input.base_sha,
    },
  };
}

/** The measured live-ref headline for one rig (or the whole corpus). */
export interface LiveRefReport {
  /** The population the percentage is against (e.g. the rig's work-record count),
   * stated explicitly per R2 — never an implicit denominator. */
  denominator: number;
  /** Work ids that resolved to a live branch head. */
  resolved: number;
  /** Resolved refs that passed the merge-base gate (the replayable base count). */
  kept: number;
  /** Resolved refs dropped by the gate — no merge-base at all (decay). */
  dropped: number;
  drops_by_reason: Record<string, number>;
  /** Resolved refs the gate could not decide. Sibling to {@link dropped}, never
   * part of it — see {@link LiveRefUndecided}. */
  undecided: number;
  undecided_by_cause: Record<string, number>;
  /** The REAL live-ref percentage: `100 * kept / denominator`. A LOWER BOUND
   * whenever {@link undecided} > 0, since some undecided refs would have been
   * kept — report the two together or the headline understates by an unseen
   * amount. */
  pct: number;
}

/** Aggregate per-ref results into the reportable headline. Pure arithmetic. */
export function summarize(denominator: number, results: readonly LiveRefResult[]): LiveRefReport {
  const drops_by_reason: Record<string, number> = {};
  const undecided_by_cause: Record<string, number> = {};
  let kept = 0;
  let dropped = 0;
  let undecided = 0;
  for (const r of results) {
    if (r.kept !== undefined) {
      kept += 1;
    } else if (r.drop !== undefined) {
      dropped += 1;
      drops_by_reason[r.drop.reason] = (drops_by_reason[r.drop.reason] ?? 0) + 1;
    } else if (r.undecided !== undefined) {
      undecided += 1;
      undecided_by_cause[r.undecided.cause] = (undecided_by_cause[r.undecided.cause] ?? 0) + 1;
    }
  }
  return {
    denominator,
    resolved: results.length,
    kept,
    dropped,
    drops_by_reason,
    undecided,
    undecided_by_cause,
    pct: denominator === 0 ? 0 : (100 * kept) / denominator,
  };
}
