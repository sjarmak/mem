import type { GitRunner } from './provenance.js';
import { defaultGitRunner, isNonZeroExit } from './provenance.js';
import { OutcomeSchema, type Outcome } from '../schemas/workrecord.js';

/**
 * ingest/commitLinkage — recover the work→PR/commit OUTCOME that ingest dropped.
 *
 * The corpus was framed as "direct-to-main, no PR/CI oracle, ~1/6000 records
 * carry an external ref". A cross-rig probe showed that framing is an ingest
 * artifact, not reality: every record carries a null `external_ref`/`pr` in the
 * store, yet the orchestrator wrote each unit of work's id into its landing
 * commit message — `... (gascity-dashboard-2j8e.7) (#104)` for PR rigs, a bare
 * `(<work_id>)` trailer for direct-commit rigs. That message IS the linkage.
 *
 * This module reads a rig's integration-branch history and, for each work id it
 * recognizes verbatim in a commit message, derives the verifiable {@link Outcome}
 * (the squash/landing commit, and the PR number when the rig used one). ZFC: git
 * reports the commits; we match ids as exact tokens and map them onto the schema
 * with no semantic guessing. Where an id resolves to more than one commit with no
 * canonical landing trailer, the linkage is reported `multiple` and the caller
 * decides whether to trust it — it is never silently collapsed.
 */

/** One commit on the integration branch, with its PR number parsed out. `pr` is
 * the `(#NN)` GitHub squash-merge reference, null for a direct commit. */
export interface CommitMeta {
  sha: string;
  author_date: string;
  committer_name: string;
  subject: string;
  body: string;
  pr: number | null;
}

/** How confidently a work id maps to its landing commit. */
export type Linkage = 'canonical' | 'unique' | 'multiple';

/** A derived outcome plus the confidence of the commit→work-id attribution. */
export interface CommitOutcome {
  outcome: Outcome;
  linkage: Linkage;
}

/** Field/record separators for the `git log` format — chosen to never collide
 * with commit message content (commit bodies can contain newlines, never these). */
const FS = '\x1f';
const RS = '\x1e';

/** A GitHub squash-merge PR reference, e.g. `(#104)`. The last one wins when a
 * message names several (the merge ref is conventionally the trailing token). */
const PR_RE = /\(#(\d+)\)/g;

export function extractPr(message: string): number | null {
  let last: number | null = null;
  for (const m of message.matchAll(PR_RE)) last = Number(m[1]);
  return last;
}

/** Candidate work-id tokens in a message: segments joined by `-` or `_` with an
 * optional dotted child suffix (`gascity-dashboard-2j8e.7`, `gc-00lpsm`, and the
 * underscore rigs `scix_experiments-0c73`, `migration_evals-0qd2`). The extractor
 * is deliberately permissive — precision comes from intersecting whole tokens
 * with the known id set, which also makes matching boundary-exact: a parent id
 * `...-2j8e` cannot match inside a child `...-2j8e.7`. */
const ID_TOKEN_RE = /[a-z0-9]+(?:[_-][a-z0-9]+)+(?:\.[a-z0-9]+)?/gi;

/** The known work ids referenced verbatim by `message`, as exact-token matches
 * against `workIds`. */
export function referencedWorkIds(message: string, workIds: ReadonlySet<string>): string[] {
  const hits = new Set<string>();
  for (const m of message.matchAll(ID_TOKEN_RE)) {
    const token = m[0].toLowerCase();
    if (workIds.has(token)) hits.add(token);
  }
  return [...hits];
}

/** Fetch the integration branch's commit metadata, newest first. `--end-of-options`
 * pins the branch as a revision so a hostile value cannot inject a git flag.
 * Returns [] when the branch/checkout is gone (non-zero exit), mirroring landed. */
export function gitLogCommits(run: GitRunner, work_dir: string, branch: string): CommitMeta[] {
  let stdout: string;
  try {
    stdout = run(work_dir, [
      'log',
      `--format=%H${FS}%aI${FS}%cn${FS}%s${FS}%b${RS}`,
      '--end-of-options',
      branch,
    ]);
  } catch (err) {
    if (isNonZeroExit(err)) return [];
    throw err;
  }
  return parseGitLog(stdout);
}

/** Parse the delimited `git log` output produced by {@link gitLogCommits}. */
export function parseGitLog(stdout: string): CommitMeta[] {
  return stdout
    .split(RS)
    .map(block => block.replace(/^\n+/, ''))
    .filter(block => block.includes(FS))
    .map(block => {
      const [sha, author_date, committer_name, subject, body = ''] = block.split(FS);
      const message = `${subject}\n${body}`;
      return { sha, author_date, committer_name, subject, body, pr: extractPr(message) };
    });
}

/** Index each known work id to the commits whose message references it. A commit
 * naming several ids is attached to each. Ids with no commit are absent from the
 * map (an unlinked record, not an error). */
export function linkCommits(
  commits: readonly CommitMeta[],
  workIds: ReadonlySet<string>
): Map<string, CommitMeta[]> {
  const byId = new Map<string, CommitMeta[]>();
  for (const commit of commits) {
    for (const id of referencedWorkIds(`${commit.subject}\n${commit.body}`, workIds)) {
      (byId.get(id) ?? byId.set(id, []).get(id)!).push(commit);
    }
  }
  return byId;
}

/** A subject whose trailing token is `(<work_id>)`, optionally followed by the
 * `(#NN)` merge ref — the convention for "this commit landed THIS work". */
function isCanonicalLanding(subject: string, workId: string): boolean {
  const esc = workId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`\\(${esc}\\)(?:\\s*\\(#\\d+\\))?\\s*$`, 'i').test(subject);
}

/** Newest commit by author date (ISO-8601 sorts lexically). Ties break on sha so
 * the choice is deterministic — a neutral (0) compare on equal dates would leave
 * the winner engine-dependent. */
function newest(commits: readonly CommitMeta[]): CommitMeta {
  return [...commits].sort((a, b) =>
    a.author_date !== b.author_date
      ? a.author_date < b.author_date
        ? 1
        : -1
      : a.sha < b.sha
        ? 1
        : -1
  )[0];
}

/**
 * Derive the verifiable outcome for one work id from the commits that reference
 * it, or null when none do. The landing commit is the one carrying the canonical
 * `(<work_id>)` trailer; absent that, the sole referencing commit; absent that,
 * the newest, reported `multiple` so the caller can gate on confidence. A commit
 * on the integration branch is by definition merged, so a parsed PR number yields
 * `pr_state: 'merged'`; a direct commit yields the landing `commit_sha` alone.
 */
export function deriveCommitOutcome(
  workId: string,
  candidates: readonly CommitMeta[]
): CommitOutcome | null {
  if (candidates.length === 0) return null;

  const canonical = candidates.find(c => isCanonicalLanding(c.subject, workId));
  let chosen: CommitMeta;
  let linkage: Linkage;
  if (canonical !== undefined) {
    chosen = canonical;
    linkage = 'canonical';
  } else if (candidates.length === 1) {
    chosen = candidates[0];
    linkage = 'unique';
  } else {
    chosen = newest(candidates);
    linkage = 'multiple';
  }

  const outcome = OutcomeSchema.parse({
    commit_sha: chosen.sha,
    ...(chosen.pr !== null ? { pr: String(chosen.pr), pr_state: 'merged' as const } : {}),
  });
  return { outcome, linkage };
}

/** Options for {@link linkRigOutcomes}. */
export interface LinkRigOptions {
  run?: GitRunner;
}

/**
 * Resolve outcomes for every work id of one rig against its checkout. Returns a
 * map of work id → derived outcome for the ids that linked; unlinked ids are
 * absent. A pure orchestration of {@link gitLogCommits} → {@link linkCommits} →
 * {@link deriveCommitOutcome}, kept here so the runner stays IO-only.
 */
export function linkRigOutcomes(
  workIds: readonly string[],
  work_dir: string,
  branch: string,
  opts: LinkRigOptions = {}
): Map<string, CommitOutcome> {
  const run = opts.run ?? defaultGitRunner;
  const idSet = new Set(workIds.map(id => id.toLowerCase()));
  const commits = gitLogCommits(run, work_dir, branch);
  const byId = linkCommits(commits, idSet);

  const out = new Map<string, CommitOutcome>();
  for (const id of workIds) {
    const candidates = byId.get(id.toLowerCase());
    if (candidates === undefined) continue;
    const derived = deriveCommitOutcome(id.toLowerCase(), candidates);
    if (derived !== null) out.set(id, derived);
  }
  return out;
}
