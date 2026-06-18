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
 *  - FAIL-CLOSED merge-base gate (R3): a base that is not an ancestor of the
 *    AUTHORITATIVE remote's integration branch is DROPPED with a reason, never
 *    written — the silent-corruption guard for the 14-remote, worktree-aliased
 *    gascity checkout.
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

/** Drop reason: a merge-base RESOLVED but is not an ancestor of the authoritative
 * integration branch — the R3 silent-corruption signal (wrong checkout/remote, or
 * a base genuinely off the authoritative branch). A non-zero count here is the
 * alarm; zero means no off-authoritative bases slipped through. */
export const DROP_BASE_NOT_ANCESTOR = 'base_not_on_authoritative_integration_branch';

/** Drop reason: no merge-base could be computed at all (the branch objects are
 * absent from the checkout, or share no history with the authoritative branch).
 * This is the live-ref DECAY signal — the case the Day-0 bundle exists to backstop
 * — kept SEPARATE from {@link DROP_BASE_NOT_ANCESTOR} so a zero R3 count is not
 * confounded with decay. */
export const DROP_NO_MERGE_BASE = 'no_merge_base_in_authoritative_checkout';

/** The result of the IO layer's merge-base computation for one resolved ref. */
export interface MergeBaseInput {
  work_id: string;
  refname: string;
  branch_sha: string;
  /** `git merge-base <branch_sha> <authoritative>/main`, or null if none / the
   * objects are absent. */
  base_sha: string | null;
  /** `git merge-base --is-ancestor <base_sha> <authoritative>/main` succeeded. */
  is_ancestor: boolean;
}

/** A kept live-ref base: a replayable {base, branch tip} anchored on the
 * authoritative branch. */
export interface LiveRefBase {
  work_id: string;
  refname: string;
  branch_sha: string;
  base_sha: string;
}

/** A dropped resolution, with the reason it failed the write-gate. */
export interface LiveRefDrop {
  work_id: string;
  refname: string;
  reason: string;
}

/** Exactly one of `kept` / `drop` is set. */
export interface LiveRefResult {
  kept?: LiveRefBase;
  drop?: LiveRefDrop;
}

/**
 * Apply the fail-closed write-gate (R3) to a merge-base result. The base is kept
 * only when it resolved AND is an ancestor of the authoritative integration
 * branch. A result with no merge-base drops as {@link DROP_NO_MERGE_BASE} (the
 * decay signal); a resolved-but-non-ancestor base drops as
 * {@link DROP_BASE_NOT_ANCESTOR} (the R3 corruption signal) — the two are kept
 * distinct so a zero R3 count cannot be confounded with decay. Nothing is written
 * silently either way.
 */
export function classifyMergeBase(input: MergeBaseInput): LiveRefResult {
  if (input.base_sha === null) {
    return { drop: { work_id: input.work_id, refname: input.refname, reason: DROP_NO_MERGE_BASE } };
  }
  if (!input.is_ancestor) {
    return {
      drop: { work_id: input.work_id, refname: input.refname, reason: DROP_BASE_NOT_ANCESTOR },
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
  /** Resolved refs dropped by the gate. */
  dropped: number;
  drops_by_reason: Record<string, number>;
  /** The REAL live-ref percentage: `100 * kept / denominator`. */
  pct: number;
}

/** Aggregate per-ref results into the reportable headline. Pure arithmetic. */
export function summarize(denominator: number, results: readonly LiveRefResult[]): LiveRefReport {
  const drops_by_reason: Record<string, number> = {};
  let kept = 0;
  let dropped = 0;
  for (const r of results) {
    if (r.kept !== undefined) {
      kept += 1;
    } else if (r.drop !== undefined) {
      dropped += 1;
      drops_by_reason[r.drop.reason] = (drops_by_reason[r.drop.reason] ?? 0) + 1;
    }
  }
  return {
    denominator,
    resolved: results.length,
    kept,
    dropped,
    drops_by_reason,
    pct: denominator === 0 ? 0 : (100 * kept) / denominator,
  };
}
