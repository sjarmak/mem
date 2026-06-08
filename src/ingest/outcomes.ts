import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { z } from 'zod';
import { OutcomeSchema, type Outcome } from '../schemas/workrecord.js';

/**
 * ingest/outcomes (P1.4) — resolve a bead's external ref (its worktree branch)
 * to the verifiable GitHub outcome that labels the WorkRecord: merged|closed,
 * the commit sha, CI pass|fail, plus the `repo` and `base_commit` env-recon
 * anchors needed to replay the record against the right baseline.
 *
 * The gh-JSON → Outcome translation is a pure mapper (`mapPullRequestToOutcome`,
 * `mapCiRollup`) so it is testable without a network or a checked-out repo. The
 * only IO is `resolveBranchOutcome`, which shells out via an injectable runner.
 *
 * ZFC: this is mechanical translation, not judgment — gh reports the state, we
 * map it through deterministic rules onto the Outcome schema.
 */

/** The `gh pr list --json` fields we read. `baseRefOid` is the base-branch tip
 * the PR was opened against — persisted as `base_commit` so the WorkRecord can be
 * replayed at the right baseline. */
export const GH_OUTCOME_FIELDS = 'number,state,mergeCommit,headRefOid,baseRefOid,statusCheckRollup';

/** Runs `gh` with the given args and resolves to its stdout. Injected so the
 * resolver can be unit-tested with a fake. */
export type GhRunner = (args: string[]) => Promise<string>;

/** One entry of gh's `statusCheckRollup`. CheckRun entries carry
 * `status`+`conclusion`; StatusContext entries carry `state`. Other fields are
 * ignored (`passthrough`). */
const GhStatusCheckSchema = z
  .object({
    status: z.string().optional(),
    conclusion: z.string().optional(),
    state: z.string().optional(),
  })
  .passthrough();

type GhStatusCheck = z.infer<typeof GhStatusCheckSchema>;

/** The subset of `gh pr view`/`gh pr list` JSON this stage consumes. */
const GhPullRequestSchema = z.object({
  number: z.number().int(),
  state: z.enum(['OPEN', 'MERGED', 'CLOSED']),
  mergeCommit: z.object({ oid: z.string() }).nullish(),
  headRefOid: z.string().nullish(),
  baseRefOid: z.string().nullish(),
  statusCheckRollup: z.array(GhStatusCheckSchema).nullish(),
});

export type GhPullRequest = z.infer<typeof GhPullRequestSchema>;

/** Conclusions/states that mean a check failed. CANCELLED/TIMED_OUT count as
 * failures: a required check that did not finish green is not a pass. */
const FAILING = new Set([
  'FAILURE',
  'ERROR',
  'TIMED_OUT',
  'CANCELLED',
  'ACTION_REQUIRED',
  'STARTUP_FAILURE',
  'STALE',
]);

/** Conclusions that contribute neither pass nor fail (a skipped/neutral check
 * does not by itself make a PR green or red). */
const IGNORED = new Set(['NEUTRAL', 'SKIPPED']);

type CheckClass = 'pass' | 'fail' | 'pending' | 'ignore';

function classifyCheck(check: GhStatusCheck): CheckClass {
  const state = check.state?.toUpperCase();
  if (state) {
    if (state === 'SUCCESS') return 'pass';
    if (state === 'FAILURE' || state === 'ERROR') return 'fail';
    return 'pending'; // PENDING, EXPECTED
  }

  const status = check.status?.toUpperCase();
  if (status && status !== 'COMPLETED') return 'pending'; // QUEUED, IN_PROGRESS, WAITING, ...

  const conclusion = check.conclusion?.toUpperCase();
  if (!conclusion) return 'pending';
  if (FAILING.has(conclusion)) return 'fail';
  if (IGNORED.has(conclusion)) return 'ignore';
  if (conclusion === 'SUCCESS') return 'pass';
  return 'pending'; // unknown/in-flight conclusion — not yet a verdict
}

/**
 * Collapse a PR's check rollup into a single CI verdict. Any failure wins;
 * otherwise an unconcluded check leaves the verdict open (`undefined`); a PR
 * with only successes is a pass. Empty / all-ignored rollups have no verdict.
 */
export function mapCiRollup(checks: readonly GhStatusCheck[]): 'pass' | 'fail' | undefined {
  let sawPass = false;
  let sawPending = false;

  for (const check of checks) {
    const verdict = classifyCheck(check);
    if (verdict === 'fail') return 'fail';
    if (verdict === 'pending') sawPending = true;
    if (verdict === 'pass') sawPass = true;
  }

  if (sawPending) return undefined;
  return sawPass ? 'pass' : undefined;
}

/**
 * Map a resolved PR onto an Outcome. Merged PRs carry the merge commit; closed
 * and still-open PRs carry the branch tip. An open PR has no terminal
 * `pr_state` but still yields its number, commit, and CI signal.
 *
 * `repo` (`owner/name`) is threaded in from the resolver — it is known at ingest
 * but not on the PR JSON — and persisted so env reconstruction has the repo in
 * the record. `base_commit` (the PR's base-branch tip) is derived from the PR's
 * `baseRefOid` when gh reports one.
 */
export function mapPullRequestToOutcome(pr: GhPullRequest, repo: string): Outcome {
  const ci = mapCiRollup(pr.statusCheckRollup ?? []);
  const prState = pr.state === 'MERGED' ? 'merged' : pr.state === 'CLOSED' ? 'closed' : undefined;
  const commitSha = pr.state === 'MERGED' ? (pr.mergeCommit?.oid ?? pr.headRefOid) : pr.headRefOid;

  return OutcomeSchema.parse({
    pr: `#${pr.number}`,
    repo,
    ...(prState ? { pr_state: prState } : {}),
    ...(commitSha ? { commit_sha: commitSha } : {}),
    ...(pr.baseRefOid ? { base_commit: pr.baseRefOid } : {}),
    ...(ci ? { ci } : {}),
  });
}

const execFileAsync = promisify(execFile);

/** Default runner: invokes the real `gh` CLI. */
export const defaultGhRunner: GhRunner = async args => {
  const { stdout } = await execFileAsync('gh', args, { maxBuffer: 16 * 1024 * 1024 });
  return stdout;
};

/**
 * Resolve a branch's outcome in `repo` (`owner/name`). Returns `null` when no
 * PR has been opened for the branch — a legitimate "no outcome yet", distinct
 * from a real `gh` failure (missing CLI, auth, bad repo), which propagates.
 *
 * When a branch has had more than one PR (reopened work), gh lists newest
 * first and we take that one.
 */
export async function resolveBranchOutcome(
  repo: string,
  branch: string,
  runner: GhRunner = defaultGhRunner
): Promise<Outcome | null> {
  const args = [
    'pr',
    'list',
    '--repo',
    repo,
    '--head',
    branch,
    '--state',
    'all',
    '--json',
    GH_OUTCOME_FIELDS,
    '--limit',
    '1',
  ];

  let stdout: string;
  try {
    stdout = await runner(args);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`gh pr list failed for ${repo} branch "${branch}": ${message}`);
  }

  const prs = z.array(GhPullRequestSchema).parse(JSON.parse(stdout));
  if (prs.length === 0) return null;

  return mapPullRequestToOutcome(prs[0], repo);
}
