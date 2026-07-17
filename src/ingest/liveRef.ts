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

/** Drop reason: no merge-base could be computed at all (the branch objects are
 * absent from the checkout, or share no history with the authoritative branch).
 * This is the live-ref DECAY signal — the case the Day-0 bundle exists to backstop
 * — and the ONLY measurable drop this gate reports. */
export const DROP_NO_MERGE_BASE = 'no_merge_base_in_authoritative_checkout';

/** Undecided cause: the ancestry probe did not confirm the invariant. Either git
 * could not ANSWER (an unreadable object at exit 128, a missing binary, a signal)
 * or it returned a `false` that is mathematically impossible for a merge-base of
 * authRef (mem-zzzl4) — both fail safe here, so an unconfirmed base is never kept.
 * Reported per-cause, mirroring ingest/landedContent.ts's `UndecidableCause`, so
 * the headline's shortfall is auditable rather than an opaque bucket (mem-y2x7n). */
export const UNDECIDED_ANCESTRY_UNANSWERABLE = 'ancestry_unanswerable_in_checkout';

/** The result of the IO layer's merge-base computation for one resolved ref. */
export interface MergeBaseInput {
  work_id: string;
  refname: string;
  branch_sha: string;
  /** `git merge-base <branch_sha> <authoritative>/main`, or null if none / the
   * objects are absent. */
  base_sha: string | null;
  /** The ancestry of `base_sha` against the authoritative branch, from
   * `merge-base --is-ancestor` by exit code. `base_sha` is itself a merge-base of
   * authRef, so this is an INVARIANT PROBE, not a corruption measurement — its
   * only substantive outcomes are:
   *  - `true`  — git answered YES (exit 0). The invariant holds; keep.
   *  - `null`  — git could NOT be asked (a 128 on an unreadable object, a
   *    missing binary, a signal). **null is not a no.** → undecided.
   * A `false` (exit 1) is mathematically impossible for a merge-base of authRef
   * (mem-zzzl4); {@link classifyMergeBase} folds it into undecided as a fail-safe
   * so an impossible answer is never silently kept.
   *
   * Producers should use ingest/provenance.ts's `isAncestorOrNull`, which returns
   * exactly these three states. */
  is_ancestor: boolean | null;
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

/**
 * Classify a merge-base result into keep / decay-drop / undecided. The base is
 * kept only when it resolved AND git confirmed the ancestry invariant. The two
 * non-keep outcomes are reported apart, because each is a different fact:
 *  - no merge-base → drop {@link DROP_NO_MERGE_BASE}, the decay signal, and the
 *    only measurable drop here.
 *  - a resolved base whose ancestry is unconfirmed → undecided
 *    {@link UNDECIDED_ANCESTRY_UNANSWERABLE}. `base_sha` is a merge-base of
 *    authRef, so it is an ancestor BY CONSTRUCTION; the probe can only confirm
 *    (true) or fail to answer (null). There is no reachable "not an ancestor"
 *    verdict to tally, so this is an invariant assertion, not a corruption gate
 *    (mem-zzzl4). A stray false — mathematically impossible — folds into
 *    undecided too, fail-safe: an unconfirmed base is never silently kept.
 *
 * So decay cannot be confounded with an ancestry git could not confirm, and
 * nothing is written silently on any path.
 *
 * This covers the ANCESTRY half only. `base_sha` arrives already collapsed:
 * producers derive it from a plain `merge-base`, and a fault there is
 * indistinguishable from a genuine no-merge-base by the time it reaches this
 * function, so it still drops as decay (mem-1n56l).
 */
export function classifyMergeBase(input: MergeBaseInput): LiveRefResult {
  // No-merge-base first: with no base there is nothing to ask about, so
  // `is_ancestor` carries no meaning here.
  if (input.base_sha === null) {
    return { drop: { work_id: input.work_id, refname: input.refname, reason: DROP_NO_MERGE_BASE } };
  }
  // Non-true fails SAFE to undecided, both arms of it. A resolved base is a
  // merge-base of authRef, so is_ancestor can only be true (invariant holds) or
  // null (git faulted); a false is a mathematical impossibility here (mem-zzzl4).
  // Route true → keep, everything else → undecided: an impossible answer is never
  // silently kept, and there is no measurable off-authoritative rate to tally.
  if (input.is_ancestor !== true) {
    return {
      undecided: {
        work_id: input.work_id,
        refname: input.refname,
        cause: UNDECIDED_ANCESTRY_UNANSWERABLE,
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
