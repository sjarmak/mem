import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import { queryRecords } from '../../store/index.js';
import { RIG_REPOS, type RigRepo } from '../../ingest/rig-repo-map.js';
import { linkRigOutcomes, type LinkRigOptions } from '../../ingest/commitLinkage.js';
import type { Linkage } from '../../ingest/commitLinkage.js';

/** One work id resolved to its landing commit on the rig's integration branch. */
export interface LinkedCommit {
  work_id: string;
  commit_sha: string;
  linkage: Linkage;
  pr?: string;
}

/** The per-rig linkage payload consumed by the Python ftp curator
 * (`membench curate-ftp`). */
export interface LinkOutcomesReport {
  rig: string;
  commits: LinkedCommit[];
}

/**
 * Resolve `workIds` to their landing commits against `dir`@`branch` and shape the
 * per-rig report — sorted by work id, dropping the rare link with no commit sha.
 * The injectable {@link LinkRigOptions} `run` keeps this unit-testable without a
 * real checkout; the command supplies the default git runner.
 */
export function buildLinkOutcomesReport(
  rig: string,
  workIds: readonly string[],
  dir: string,
  branch: string,
  opts: LinkRigOptions = {}
): LinkOutcomesReport {
  const linked = linkRigOutcomes(workIds, dir, branch, opts);
  const commits: LinkedCommit[] = [];
  for (const [work_id, { outcome, linkage }] of linked) {
    if (outcome.commit_sha === undefined) continue;
    commits.push({
      work_id,
      commit_sha: outcome.commit_sha,
      linkage,
      ...(outcome.pr !== undefined ? { pr: outcome.pr } : {}),
    });
  }
  commits.sort((a, b) => (a.work_id < b.work_id ? -1 : a.work_id > b.work_id ? 1 : 0));
  return { rig, commits };
}

/**
 * `mem link-outcomes <rig> [--store PATH]` — emit the rig's work→landing-commit
 * links, the sound landing commits the fail-to-pass curator runs over (mem-bxhh.1).
 *
 * A thin orchestration over {@link linkRigOutcomes}: it reads the rig's work ids
 * from the store, resolves each to its landing commit against the rig's local
 * checkout (`RIG_REPOS`), and reports `{work_id, commit_sha, linkage}` sorted by
 * work id. Keeping linkage here — the single TypeScript source of truth — means
 * the Python side never re-derives commit attribution. Read-only.
 */
export function linkOutcomesCommand(ctx: CommandContext): LinkOutcomesReport {
  const rig = ctx.args[0];
  if (!rig) {
    throw new Error('usage: mem link-outcomes <rig> [--store PATH] [--json]');
  }

  const repo: RigRepo | undefined = RIG_REPOS[rig];
  if (repo === undefined || repo.dir === undefined) {
    throw new Error(`rig '${rig}' has no local checkout in RIG_REPOS — cannot resolve linkage`);
  }
  const branch = repo.branch ?? 'main';

  const workIds = withReadStore(ctx.options, db =>
    queryRecords(db, { rig }).map(record => record.work_id)
  );

  const report = buildLinkOutcomesReport(rig, workIds, repo.dir, branch);

  if (!ctx.options.json) {
    const canonical = report.commits.filter(c => c.linkage === 'canonical').length;
    console.error(
      `${rig}: ${report.commits.length} linked landing commits (${canonical} canonical)`
    );
  }

  return report;
}
