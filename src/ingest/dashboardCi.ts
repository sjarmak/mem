import { z } from 'zod';
import { OutcomeSchema, type Outcome } from '../schemas/workrecord.js';

/**
 * ingest/dashboardCi (mem-wanz.5, PRD §5.4, keys #2/#8) — the dashboard
 * merged-PR/CI oracle: the only path to a true CI-verified T1 tier.
 *
 * mem-wanz.7 already writes a `wasGeneratedBy` link per transcript `pr-link`
 * entry, but at tier T2 — a verifiable PR *reference*, not yet a CI/merge oracle.
 * This stage closes that gap. It reads the Day-0 frozen snapshot of the
 * dashboard's merged PRs (`freeze/<date>/dashboard-ci.raw.json`, harvested by
 * mem-wanz.1 — never a live `gh` call), rolls each PR's check-runs up into one
 * fail-closed CI verdict, and for every green merged PR ELEVATES the matching T2
 * pr-link link to T1, accreting `ci-rollup` onto its provenance and deriving the
 * replayable {@link Outcome} (merge SHA + ci=pass) the headline's sound core
 * stands on.
 *
 * Fail-closed by design: a PR whose CI is `failure` or `UNKNOWN` (no merge
 * commit, checks never fetched, an incomplete or unrecognized run) is NOT
 * elevated — its link stays T2. T1 means CI-green-verified, nothing weaker.
 *
 * ZFC: this is mechanical translation. The freeze reports the check conclusions;
 * we bucket them with deterministic rules and map merge SHA → Outcome schema with
 * no semantic guessing. The classifier mirrors `scripts/freeze/lib.mjs`
 * (`aggregateConclusion`/`classifyCiRow`) so the post-pass agrees byte-for-byte
 * with the freeze's own summary.
 */

/** GitHub check-run conclusions that mean a check failed. A required check left
 * cancelled/timed-out/stale did not finish green, so it counts as a failure. */
const FAILURE_CONCLUSIONS = new Set([
  'failure',
  'timed_out',
  'cancelled',
  'action_required',
  'startup_failure',
  'stale',
]);

/** Conclusions that count as success — a neutral/skipped check does not by itself
 * make a PR red. */
const SUCCESS_CONCLUSIONS = new Set(['success', 'neutral', 'skipped']);

/** The rolled-up CI verdict for one PR. `UNKNOWN` is the fail-closed verdict for
 * anything we cannot assert green or red. */
export type CiVerdict = 'success' | 'failure' | 'UNKNOWN';

/** One check-run of a frozen PR. Only `conclusion` drives the verdict; `name`
 * and `status` are kept for traceability (`passthrough` tolerates extra gh
 * fields without coupling to them). */
const CheckRunSchema = z
  .object({
    conclusion: z.string().nullish(),
    name: z.string().optional(),
    status: z.string().optional(),
  })
  .passthrough();

/** One entry of the Day-0 dashboard merged-PR snapshot. Drives off
 * `mergeCommit.oid → checkRuns` (NOT `statusCheckRollup`, which GitHub empties
 * once a squash-merged head ref is deleted). */
export const DashboardCiEntrySchema = z
  .object({
    number: z.number().int().positive(),
    mergeCommit: z.object({ oid: z.string().min(1) }).nullish(),
    headRefName: z.string().nullish(),
    headRefDeleted: z.boolean().optional(),
    checkRuns: z.array(CheckRunSchema).nullish(),
  })
  .passthrough();

export type DashboardCiEntry = z.infer<typeof DashboardCiEntrySchema>;

/** The whole frozen snapshot: an array of merged-PR entries. */
export const DashboardCiSnapshotSchema = z.array(DashboardCiEntrySchema);

/** A PR's rolled-up CI classification. `merge_oid` is the replayable landing
 * SHA, null only when the PR never merged. */
export interface CiClassification {
  pr_number: number;
  merge_oid: string | null;
  ci: CiVerdict;
  reason: string;
}

/**
 * Roll a list of check-run conclusions into one verdict, fail-closed. An
 * incomplete run (null conclusion) or an unrecognized conclusion string makes
 * the whole set UNKNOWN — we cannot assert green if any signal is missing or
 * unparseable. A single recognized failure makes it a failure. Only an
 * all-recognized-success set yields success.
 */
export function aggregateConclusion(conclusions: ReadonlyArray<string | null | undefined>): {
  conclusion: CiVerdict;
  reason: string;
} {
  if (conclusions.length === 0) {
    return { conclusion: 'UNKNOWN', reason: 'no-check-runs' };
  }
  if (conclusions.some(c => c === null || c === undefined)) {
    return { conclusion: 'UNKNOWN', reason: 'incomplete-run' };
  }
  const unrecognized = conclusions.find(
    c => !FAILURE_CONCLUSIONS.has(c!) && !SUCCESS_CONCLUSIONS.has(c!)
  );
  if (unrecognized !== undefined) {
    return { conclusion: 'UNKNOWN', reason: `unrecognized-conclusion:${unrecognized}` };
  }
  if (conclusions.some(c => FAILURE_CONCLUSIONS.has(c!))) {
    return { conclusion: 'failure', reason: 'check-run-failure' };
  }
  return { conclusion: 'success', reason: 'all-checks-passed' };
}

/**
 * Classify one frozen PR entry into a CI verdict. A PR with no merge commit
 * never landed; one merged without fetched check-runs is UNKNOWN with a distinct
 * reason — separable from a merge that genuinely had no checks.
 */
export function classifyCiEntry(entry: DashboardCiEntry): CiClassification {
  const merge_oid = entry.mergeCommit?.oid ?? null;
  if (merge_oid === null) {
    return { pr_number: entry.number, merge_oid: null, ci: 'UNKNOWN', reason: 'no-merge-commit' };
  }
  if (entry.checkRuns === undefined || entry.checkRuns === null) {
    return { pr_number: entry.number, merge_oid, ci: 'UNKNOWN', reason: 'check-runs-not-fetched' };
  }
  const agg = aggregateConclusion(entry.checkRuns.map(r => r.conclusion ?? null));
  return { pr_number: entry.number, merge_oid, ci: agg.conclusion, reason: agg.reason };
}

/**
 * Validate a raw frozen snapshot at the boundary and index it by PR number.
 * Throws on a malformed snapshot — the store only ever sees schema-conformant
 * input. A later duplicate PR number wins (the snapshot is harvested unique, so
 * this is defensive only).
 */
export function indexSnapshot(raw: unknown): Map<number, CiClassification> {
  const entries = DashboardCiSnapshotSchema.parse(raw);
  const index = new Map<number, CiClassification>();
  for (const entry of entries) index.set(entry.number, classifyCiEntry(entry));
  return index;
}

/** `owner/repo` + PR number parsed from a canonical GitHub PR url. */
export interface ParsedPrUrl {
  repo: string;
  pr_number: number;
}

const PR_URL_RE = /github\.com\/([^/\s]+\/[^/\s]+)\/pull\/(\d+)/i;

/** Parse `https://github.com/<owner>/<repo>/pull/<n>` into its repo and number,
 * or null when the url is not a PR reference. Tolerates trailing path/fragment. */
export function parsePrUrl(url: string): ParsedPrUrl | null {
  const m = PR_URL_RE.exec(url);
  if (m === null) return null;
  return { repo: m[1], pr_number: Number(m[2]) };
}

/** The subset of a stored pr-link `links` row this stage reads. The join is
 * value-driven (the CI verdict decides the elevation), so the link's current
 * `tier`/`provenance` are not read here — the UPDATE is idempotent. */
export interface PrLinkRow {
  work_id: string;
  /** The link's `entity_ref` — the canonical PR url. */
  entity_ref: string;
}

/** A planned elevation of one T2 pr-link edge to a T1 CI-verified oracle. */
export interface CiElevation {
  work_id: string;
  /** The unique-key `entity_ref` (pr_url) identifying the link row to elevate. */
  entity_ref: string;
  pr_number: number;
  /** The replayable, CI-attested outcome to merge into the WorkRecord. */
  outcome: Outcome;
}

/** Provenance string after a CI rollup corroborates a pr-link (source accretion,
 * per the unique-key convention — one logical edge, '+'-joined sources). */
export const CI_ROLLUP_PROVENANCE = 'pr-link+ci-rollup';

/**
 * Plan which pr-link links elevate to T1. For each link whose `entity_ref`
 * resolves to a PR in `repo` that the snapshot reports green-merged, emit the
 * elevation plus the derived {@link Outcome} (merge SHA + ci=pass). Links for
 * other repos, unmatched PRs, or non-green PRs are skipped — fail-closed, never
 * silently downgraded. Idempotent: an already-T1 link still plans the same
 * elevation, so re-running writes byte-identical rows.
 */
export function planCiElevations(
  index: ReadonlyMap<number, CiClassification>,
  repo: string,
  prLinks: readonly PrLinkRow[]
): CiElevation[] {
  const out: CiElevation[] = [];
  for (const link of prLinks) {
    const parsed = parsePrUrl(link.entity_ref);
    if (parsed === null || parsed.repo !== repo) continue;

    const classification = index.get(parsed.pr_number);
    if (classification === undefined || classification.ci !== 'success') continue;

    const outcome = OutcomeSchema.parse({
      pr: `#${parsed.pr_number}`,
      repo,
      pr_state: 'merged' as const,
      commit_sha: classification.merge_oid!,
      ci: 'pass' as const,
    });
    out.push({
      work_id: link.work_id,
      entity_ref: link.entity_ref,
      pr_number: parsed.pr_number,
      outcome,
    });
  }
  return out;
}
