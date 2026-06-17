import { RIG_REPOS } from './rig-repo-map.js';
import type { RepoSource, WorkRecord } from '../schemas/workrecord.js';

/**
 * ingest/repo-resolve — backfill the canonical `repo` (`owner/name`) onto every
 * record (mem-bme). A sibling stage to ingest/provenance, but distinct: this
 * resolves repository *identity* for grouping and retrieval filters, where
 * provenance reconstructs the git *baseline* (work_dir + commit) for replay.
 *
 * Pure mapping, no IO — a precedence over signal already on the record:
 *   1. `outcome.repo`  — the owner/name of a real resolved PR (verified).
 *   2. RIG_REPOS slug  — for rigs that are 1:1 with a repo (the high-coverage win).
 *   3. unmapped        — recorded with `repo` absent, never guessed.
 *
 * `gc.work_dir` is deliberately NOT a source here: its basename is a bare repo
 * name, not the `owner/name` this field contracts, and trusting it for a
 * multi-repo rig would mislabel the record. Provenance keeps using it for the
 * bare-name env baseline; trace-derived work_dir is a separate follow-on. ZFC:
 * mechanical lookup, no judgment — an unmapped rig is surfaced, not inferred.
 */

/** The resolved repo identity plus the source that established it. */
export interface RepoResolution {
  repo?: string;
  repo_source: RepoSource;
}

/** Resolve one record's canonical `owner/name` repo via the precedence above. */
export function resolveRepo(record: WorkRecord): RepoResolution {
  if (record.outcome?.repo !== undefined) {
    return { repo: record.outcome.repo, repo_source: 'outcome' };
  }
  const mapped = RIG_REPOS[record.rig];
  if (mapped !== undefined && mapped.multi !== true) {
    return { repo: mapped.slug, repo_source: 'rig-map' };
  }
  return { repo_source: 'unmapped' };
}

/** Attach the resolved repo identity to every record. Records are copied, never
 * mutated; an unmapped record keeps `repo` absent and is tagged `unmapped`. */
export function attachRepo(records: WorkRecord[]): WorkRecord[] {
  return records.map(record => {
    const { repo, repo_source } = resolveRepo(record);
    return { ...record, ...(repo !== undefined && { repo }), repo_source };
  });
}
