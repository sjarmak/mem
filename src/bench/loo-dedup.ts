import type { WorkRecord } from '../schemas/workrecord.js';

/**
 * Leakage gate (c) — LOO DEDUP (PRD §6c, premortem R5). Leave-one-out scoring
 * leaks if two records of the SAME underlying work fall on opposite sides of the
 * split: run-1 of a bead in the store, run-2 held out as the task, lets the agent
 * "remember" its own answer. Canonical identity is the UNION of three keys —
 * FULL-anchored-slug (the `(work_id)` anchor), branch-root, and landed_commit —
 * and records sharing ANY key are partitioned WHOLE, never split.
 *
 * {@link looPartitions} forms those groups (union-find over shared keys);
 * {@link assertNoSharedBranchRootAcrossPartitions} is the build assertion that
 * fails the eval loudly if any branch-root straddles two partitions — the
 * mechanical proof the split is leak-free before a single task is scored.
 *
 * Pure functions over WorkRecords; never guesses a key it cannot read.
 */

/** The three identity keys a record contributes, each absent when unrecorded —
 * an absent key links nothing rather than collapsing distinct work. */
export interface CanonicalIdentity {
  /** The full anchored slug — the bead id itself, always present. */
  slug: string;
  /** The feature-branch root, from the recorded ref. */
  branchRoot?: string;
  /** The integration-branch landing commit, when the work landed. */
  landedCommit?: string;
}

/**
 * The branch-root of a record's recorded `external_ref` (its branch/PR ref), or
 * undefined when none is recorded. Only the source prefix (`bd-`) is normalized:
 * identical branches share a root, and that is the strongest claim we can make
 * without guessing. A trailing run/retry suffix is deliberately NOT stripped —
 * doing so would conflate sibling child beads (`…wanz.7` vs `…wanz.8`), a far
 * worse error than missing a same-branch merge the landed_commit key still
 * catches.
 */
export function branchRoot(record: WorkRecord): string | undefined {
  const ref = record.external_ref;
  if (ref === undefined || ref === '') return undefined;
  return ref.replace(/^bd-/, '');
}

/** The canonical identity keys of one record. */
export function canonicalIdentity(record: WorkRecord): CanonicalIdentity {
  const root = branchRoot(record);
  const landed = record.landed?.landed_commit;
  return {
    slug: record.work_id,
    ...(root !== undefined && { branchRoot: root }),
    ...(landed !== undefined && { landedCommit: landed }),
  };
}

/** Namespaced merge keys, so a slug can never collide with a like-valued commit.
 * Exported so the session-fanout gate (mem-wanz.10) collapses fanned work_ids on
 * the SAME canonical-identity definition this gate uses — the two must never
 * diverge on what "the same underlying work" means. */
export function mergeKeys(id: CanonicalIdentity): string[] {
  const keys = [`slug:${id.slug}`];
  if (id.branchRoot !== undefined) keys.push(`branch:${id.branchRoot}`);
  if (id.landedCommit !== undefined) keys.push(`landed:${id.landedCommit}`);
  return keys;
}

/** Union-find root of `i` with path compression. */
function find(parent: number[], i: number): number {
  let root = i;
  while (parent[root] !== root) root = parent[root];
  while (parent[i] !== root) {
    const next = parent[i];
    parent[i] = root;
    i = next;
  }
  return root;
}

/**
 * Partition records into LOO groups: any two records sharing a slug, branch-root,
 * or landed_commit land in the same group, so a group is only ever split off as a
 * whole. Group order follows each group's first record in `records`; within a
 * group, input order is preserved — the partition is reproducible for a fixed
 * input (the Decision-10 precision guard).
 */
export function looPartitions(records: readonly WorkRecord[]): WorkRecord[][] {
  const parent = records.map((_, i) => i);
  const firstByKey = new Map<string, number>();
  records.forEach((record, i) => {
    for (const key of mergeKeys(canonicalIdentity(record))) {
      const seen = firstByKey.get(key);
      if (seen === undefined) {
        firstByKey.set(key, i);
      } else {
        parent[find(parent, i)] = find(parent, seen);
      }
    }
  });

  const groups = new Map<number, WorkRecord[]>();
  records.forEach((record, i) => {
    const root = find(parent, i);
    (groups.get(root) ?? groups.set(root, []).get(root)!).push(record);
  });
  return [...groups.values()];
}

/**
 * Build assertion (PRD §6c): no branch-root may appear in two different LOO
 * partitions. A violation means run-1 and run-2 of the same branch were split
 * across the train/test wall — throws rather than scoring a leaked task.
 */
export function assertNoSharedBranchRootAcrossPartitions(
  partitions: readonly WorkRecord[][]
): void {
  const partitionByRoot = new Map<string, number>();
  partitions.forEach((partition, p) => {
    const rootsHere = new Set<string>();
    for (const record of partition) {
      const root = branchRoot(record);
      if (root !== undefined) rootsHere.add(root);
    }
    for (const root of rootsHere) {
      const prior = partitionByRoot.get(root);
      if (prior !== undefined && prior !== p) {
        throw new Error(
          `LOO leak: branch-root "${root}" spans partitions ${prior} and ${p} ` +
            `— run-1/run-2 of the same branch were split across the wall`
        );
      }
      partitionByRoot.set(root, p);
    }
  });
}
