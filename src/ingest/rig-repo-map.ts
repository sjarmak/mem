/**
 * ingest/rig-repo-map — the canonical rig → repo identity map (mem-bme).
 *
 * A bead carries no repo column; it only knows its `rig` (the dolt database it
 * lives in — see ingest/beads.ts). For the rigs that are 1:1 with a repository,
 * the rig name alone determines the GitHub `owner/name`, so this static map lets
 * {@link resolveRepo} backfill `repo` for *every* such record — not just the
 * minority that carry a PR `outcome` or a `gc.work_dir`.
 *
 * This is the TypeScript source of truth that retires the interim Python
 * stopgap (`memory-bench/membench/config/rigs.py` RIG_REPOS): once `repo` is
 * persisted on the WorkRecord, the Python side reads the persisted value.
 *
 * Seeded intentionally partial — a rig absent here resolves to `unmapped`
 * (recorded, never guessed), which the build-store coverage line surfaces as the
 * signal to add it. ZFC: a deterministic name→name lookup, no judgment.
 */

/** A rig's repository identity. `slug` is the GitHub `owner/name`. */
export interface RigRepo {
  /** GitHub `owner/name`. Empty string when `multi` (no single authoritative repo). */
  slug: string;
  /** An umbrella/orchestration rig that spans several repos (e.g. cross-fork
   * convoy work). Its `slug` is NOT authoritative — resolving a single repo from
   * the rig alone would be a guess, so such rigs fall through to `unmapped`
   * rather than being mislabeled. */
  multi?: true;
}

/**
 * Canonical rig → repo identity. Slugs are the verified upstream `owner/name`
 * (the origin/upstream remote of each rig's checkout), not the local clone path.
 */
export const RIG_REPOS: Record<string, RigRepo> = {
  gascity: { slug: 'gastownhall/gascity' },
  gascity_dashboard: { slug: 'gastownhall/gascity-dashboard' },
  mem: { slug: 'sjarmak/mem' },
  GEO: { slug: 'sjarmak/geo' },
  codeprobe: { slug: 'sjarmak/codeprobe' },
  // gc orchestrates work across many forks — the rig alone cannot name one repo.
  gc: { slug: '', multi: true },
};
