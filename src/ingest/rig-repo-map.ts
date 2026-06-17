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
  /** A durable LOCAL checkout of the rig's repo, on a stable path (a primary
   * clone, never an ephemeral `*-worktrees/*` worktree). It backfills
   * `provenance.work_dir` for the records — the majority — that never recorded a
   * `gc.work_dir`: work_dir is a rig constant, not a per-record fact, so a
   * record's git baseline is reconstructable from the rig alone. Absent for
   * `multi` rigs and for rigs with no resolved local checkout. */
  dir?: string;
  /** The repo's integration branch — the one work merges into and the one a
   * session-start baseline is dated against. Defaults to `main` when a `dir` is
   * set and this is omitted; set explicitly only where the repo differs. */
  branch?: string;
}

/**
 * Canonical rig → repo identity. Slugs are the verified upstream `owner/name`
 * (the origin/upstream remote of each rig's checkout), not the local clone path;
 * `dir`/`branch` are the verified durable local checkout used for git provenance.
 */
export const RIG_REPOS: Record<string, RigRepo> = {
  gascity: { slug: 'gastownhall/gascity', dir: '/home/ds/gascity-main' },
  gascity_dashboard: { slug: 'gastownhall/gascity-dashboard', dir: '/home/ds/gascity-dashboard' },
  mem: { slug: 'sjarmak/mem', dir: '/home/ds/projects/mem' },
  GEO: { slug: 'sjarmak/geo', dir: '/home/ds/projects/GEO' },
  codeprobe: { slug: 'sjarmak/codeprobe', dir: '/home/ds/projects/codeprobe' },
  gpk: { slug: 'sjarmak/gascity-packs', dir: '/home/ds/gascity-packs' },
  scix_experiments: { slug: 'sjarmak/scix-agent', dir: '/home/ds/projects/scix_experiments' },
  zeldascension: {
    slug: 'sjarmak/zeldascension',
    dir: '/home/ds/projects/zeldascension',
    branch: 'master',
  },
  CodeScaleBench: { slug: 'sjarmak/CodeScaleBench', dir: '/home/ds/projects/CodeScaleBench' },
  EnterpriseBench: { slug: 'sjarmak/EnterpriseBench', dir: '/home/ds/projects/EnterpriseBench' },
  migration_evals: { slug: 'sjarmak/migration-evals', dir: '/home/ds/projects/migration-evals' },
  code_intel_digest: {
    slug: 'sjarmak/code-intelligence-digest',
    dir: '/home/ds/projects/code-intelligence-digest',
  },
  // Long-tail rigs (mem-75t.14): each a 1:1 repo with a single durable local
  // checkout, verified by its origin remote slug. The local dir name does not
  // always match the rig or the upstream name (mcp_ax→mg-ax, live_docs→livedocs).
  website: { slug: 'sjarmak/website', dir: '/home/ds/projects/website' },
  mcp_ax: { slug: 'sjarmak/mg-ax', dir: '/home/ds/projects/mcp-ax' },
  agent_diagnostics: {
    slug: 'sjarmak/agent-diagnostics',
    dir: '/home/ds/projects/agent-diagnostics',
  },
  live_docs: { slug: 'sjarmak/livedocs', dir: '/home/ds/projects/live_docs' },
  background_agents: {
    slug: 'sjarmak/background-agents',
    dir: '/home/ds/projects/background-agents',
  },
  brains: { slug: 'sjarmak/brains', dir: '/home/ds/projects/brains' },
  tom_swe: { slug: 'sjarmak/tom-swe', dir: '/home/ds/projects/tom-swe' },
  // `dec` is a decisions/beads ledger, not a git repo (no checkout, no upstream),
  // so it stays unmapped rather than being guessed a path. gc orchestrates work
  // across many forks — the rig alone cannot name one repo.
  gc: { slug: '', multi: true },
};

/** The default integration branch assumed when a mapped rig sets a `dir` but no
 * `branch`. Empirically every recorded `gc.var.base_branch` in the corpus is
 * `main`, so this is a data-backed default, not a guess. */
export const DEFAULT_BRANCH = 'main';
