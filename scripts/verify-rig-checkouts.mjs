// verify-rig-checkouts — Step-0 per-rig checkout+remote verification table
// (mem-wanz.2). BLOCKING, fail-closed on worktree aliasing.
//
// Asserts that every rig in the canonical rig→repo map resolves to the CORRECT
// durable local checkout and the CORRECT upstream remote, so downstream
// git-provenance work (the merge-base / work→landed-commit oracle) cannot
// silently run against an aliased or wrong-repo checkout. For each rig it:
//   1. confirms a git checkout exists at the rig's `dir`;
//   2. reads its origin remote and asserts it parses to the rig's expected slug
//      (skipped for `multi` rigs, which have no single authoritative repo);
//   3. classifies the checkout as primary-clone vs linked-worktree from its
//      git-dir vs git-common-dir;
//   4. detects shared-object-store aliasing (two distinct rigs whose checkouts
//      share one `.git`).
// It prints the verdict table, writes a JSON report to
// verify/rig-checkouts.<date>.json, and EXITS NON-ZERO naming the offending
// rig(s) on any remote mismatch or any UNEXPECTED aliasing.
//
// Failure policy (fail-closed):
//   - A wrong / unparseable remote on a non-multi rig is ALWAYS a hard fail.
//   - A missing checkout is a hard fail.
//   - A rig living on a linked worktree is REPORTED but not by itself fatal — a
//     rig may legitimately be anchored on a worktree. What IS fatal is
//     *unexpected* aliasing: two distinct rigs sharing one object store. Each
//     such collision is named and fails the run.
//
// Usage: node scripts/verify-rig-checkouts.mjs [--date YYYY-MM-DD] [--force]

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import {
  aliasesFor,
  buildVerdict,
  groupByObjectStore,
} from './verify/lib.mjs';

// Rig → {dir, slug, multi}. Inlined (mirrors src/ingest/rig-repo-map.ts) rather
// than imported from dist/ ON PURPOSE: this Step-0 verification is the preflight
// the build runs BEFORE trusting any provenance, and must run even when the
// TypeScript build is broken — coupling it to `npm run build` would defeat its
// fail-closed reason for existing. Only the fields verification needs (dir, slug,
// multi) are carried; kept byte-for-byte in sync with the canonical map.
const RIG_REPOS = Object.freeze({
  gascity: { slug: 'gastownhall/gascity', dir: '/home/ds/gascity-main' },
  gascity_dashboard: {
    slug: 'gastownhall/gascity-dashboard',
    dir: '/home/ds/gascity-dashboard',
  },
  mem: { slug: 'sjarmak/mem', dir: '/home/ds/projects/mem' },
  GEO: { slug: 'sjarmak/geo', dir: '/home/ds/projects/GEO' },
  codeprobe: { slug: 'sjarmak/codeprobe', dir: '/home/ds/projects/codeprobe' },
  gpk: { slug: 'sjarmak/gascity-packs', dir: '/home/ds/gascity-packs' },
  scix_experiments: { slug: 'sjarmak/scix-agent', dir: '/home/ds/projects/scix_experiments' },
  zeldascension: { slug: 'sjarmak/zeldascension', dir: '/home/ds/projects/zeldascension' },
  CodeScaleBench: { slug: 'sjarmak/CodeScaleBench', dir: '/home/ds/projects/CodeScaleBench' },
  EnterpriseBench: { slug: 'sjarmak/EnterpriseBench', dir: '/home/ds/projects/EnterpriseBench' },
  migration_evals: { slug: 'sjarmak/migration-evals', dir: '/home/ds/projects/migration-evals' },
  code_intel_digest: {
    slug: 'sjarmak/code-intelligence-digest',
    dir: '/home/ds/projects/code-intelligence-digest',
  },
  website: { slug: 'sjarmak/website', dir: '/home/ds/projects/website' },
  mcp_ax: { slug: 'sjarmak/mg-ax', dir: '/home/ds/projects/mcp-ax' },
  agent_diagnostics: { slug: 'sjarmak/agent-diagnostics', dir: '/home/ds/projects/agent-diagnostics' },
  live_docs: { slug: 'sjarmak/livedocs', dir: '/home/ds/projects/live_docs' },
  background_agents: { slug: 'sjarmak/background-agents', dir: '/home/ds/projects/background-agents' },
  brains: { slug: 'sjarmak/brains', dir: '/home/ds/projects/brains' },
  tom_swe: { slug: 'sjarmak/tom-swe', dir: '/home/ds/projects/tom-swe' },
  // `gc` orchestrates work across many forks — the rig alone cannot name one
  // repo, so it has no authoritative slug or dir. Recorded, remote-skipped.
  gc: { slug: '', multi: true, dir: null },
});

// ---- arg parsing ------------------------------------------------------------

function arg(flag, fallback = undefined) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}
const FORCE = process.argv.includes('--force');
const DATE = arg('--date') || hostDate();

function hostDate() {
  return execFileSync('date', ['+%Y-%m-%d']).toString().trim();
}

// ---- git shell (execFile — no shell, no interpolation) ----------------------

function git(dir, args) {
  return execFileSync('git', ['-C', dir, ...args], {
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

function gitTry(dir, args) {
  try {
    return git(dir, args).trim();
  } catch {
    return null;
  }
}

// ---- fail-closed abort ------------------------------------------------------

function abort(msg) {
  console.error(`\nVERIFY ABORTED: ${msg}`);
  process.exit(1);
}

// ---- per-rig fact gathering -------------------------------------------------

// Read the full `name → url` remote map of a checkout. Gathering ALL remotes
// (not just origin) lets the pure layer accept a canonical upstream present under
// a non-standard remote name, while still failing closed when none match.
function readRemotes(dir) {
  const names = gitTry(dir, ['remote']);
  if (names === null || names === '') return {};
  const remotes = {};
  for (const name of names.split('\n').filter(n => n.trim() !== '')) {
    const url = gitTry(dir, ['remote', 'get-url', name]);
    if (url !== null) remotes[name] = url;
  }
  return remotes;
}

// Resolve the on-disk facts for one rig WITHOUT making any pass/fail judgment —
// that is buildVerdict's job (kept pure). A multi rig with no dir is recorded as
// non-existent-but-not-a-failure by buildVerdict via its multi flag.
function gatherRig(rig, entry) {
  const dir = entry.dir ?? null;
  if (dir === null || !existsSync(dir) || gitTry(dir, ['rev-parse', '--git-dir']) === null) {
    return {
      rig,
      dir: dir ?? '(none)',
      slug: entry.slug,
      multi: entry.multi === true,
      exists: false,
      remotes: {},
      gitDir: null,
      commonDir: null,
    };
  }
  const gitDir = git(dir, ['rev-parse', '--path-format=absolute', '--git-dir']).trim();
  const commonDir = git(dir, ['rev-parse', '--path-format=absolute', '--git-common-dir']).trim();
  return {
    rig,
    dir,
    slug: entry.slug,
    multi: entry.multi === true,
    exists: true,
    remotes: readRemotes(dir),
    gitDir,
    commonDir,
  };
}

// ---- table rendering --------------------------------------------------------

function renderTable(verdicts) {
  const header = ['rig', 'ok', 'remote', 'checkout_kind', 'observed/slug', 'aliases', 'reason'];
  const rows = verdicts.map(v => [
    v.rig,
    v.ok ? 'PASS' : 'FAIL',
    v.multi ? 'n/a(multi)' : v.remote_ok === true ? (v.remote_name ?? 'ok') : 'MISMATCH',
    v.checkout_kind ?? '(none)',
    v.multi ? '(multi)' : `${v.remote_observed ?? '?'} / ${v.slug}`,
    v.aliases.length ? v.aliases.join('+') : '-',
    v.reason,
  ]);
  const widths = header.map((h, i) =>
    Math.max(h.length, ...rows.map(r => String(r[i]).length))
  );
  const fmt = cells =>
    cells.map((c, i) => String(c).padEnd(widths[i])).join('  ');
  console.log(fmt(header));
  console.log(widths.map(w => '-'.repeat(w)).join('  '));
  for (const r of rows) console.log(fmt(r));
}

// ---- main -------------------------------------------------------------------

function main() {
  const repoRoot = process.cwd();

  // Phase 1: gather raw facts for every rig (IO only).
  const facts = Object.entries(RIG_REPOS).map(([rig, entry]) => gatherRig(rig, entry));

  // Phase 2: cross-rig object-store grouping over the real (existing) checkouts.
  const storeGroups = groupByObjectStore(
    facts.filter(f => f.exists && f.commonDir !== null).map(f => ({ rig: f.rig, commonDir: f.commonDir }))
  );

  // Phase 3: build the pure verdict per rig, threading in its aliases.
  const verdicts = facts.map(f =>
    buildVerdict({
      rig: f.rig,
      dir: f.dir,
      slug: f.slug,
      multi: f.multi,
      exists: f.exists,
      remotes: f.remotes,
      gitDir: f.gitDir,
      commonDir: f.commonDir,
      aliases: f.exists && f.commonDir !== null ? aliasesFor(storeGroups, f.rig, f.commonDir) : [],
    })
  );

  console.log(`Step-0 rig checkout+remote verification (${DATE})\n`);
  renderTable(verdicts);

  // Classify failures. A wrong remote or missing checkout fails via the pure
  // verdict's ok:false. UNEXPECTED aliasing (any object store shared by >1
  // distinct rig) is a hard fail surfaced here, regardless of the per-rig ok —
  // two rigs sharing a store means provenance for one runs against the other's.
  const remoteFailures = verdicts.filter(v => !v.ok);
  const aliasCollisions = [...storeGroups.entries()].filter(([, rigs]) => rigs.length > 1);
  const worktrees = verdicts.filter(v => v.worktree);

  const report = {
    date: DATE,
    git_version: execFileSync('git', ['--version']).toString().trim(),
    total: verdicts.length,
    pass: verdicts.filter(v => v.ok).length,
    fail: remoteFailures.length,
    worktrees: worktrees.map(v => ({ rig: v.rig, dir: v.dir, common_dir: v.common_dir })),
    alias_collisions: aliasCollisions.map(([commonDir, rigs]) => ({ commonDir, rigs })),
    verdicts,
  };

  const outDir = join(repoRoot, 'verify');
  mkdirSync(outDir, { recursive: true });
  const outPath = join(outDir, `rig-checkouts.${DATE}.json`);
  if (existsSync(outPath) && !FORCE) {
    abort(`${outPath} already exists — rerun with --force to overwrite`);
  }
  writeFileSync(outPath, `${JSON.stringify(report, null, 2)}\n`);
  console.log(`\nreport → verify/rig-checkouts.${DATE}.json`);

  // Summary lines.
  if (worktrees.length > 0) {
    console.log(
      `\nlinked-worktree checkouts (reported, not fatal): ${worktrees.map(v => v.rig).join(', ')}`
    );
  }

  let failed = false;
  if (remoteFailures.length > 0) {
    failed = true;
    console.error(
      `\nFAIL: ${remoteFailures.length} rig(s) failed remote/checkout verification: ` +
        remoteFailures.map(v => `${v.rig}(${v.reason})`).join(', ')
    );
  }
  if (aliasCollisions.length > 0) {
    failed = true;
    console.error('\nFAIL: shared object stores (distinct rigs aliasing one .git):');
    for (const [commonDir, rigs] of aliasCollisions) {
      console.error(`  ${commonDir} ← ${rigs.join(', ')}`);
    }
  }

  if (failed) {
    abort('one or more rigs failed Step-0 verification (see above)');
  }
  console.log(`\nVERIFY OK → ${report.pass}/${report.total} rigs verified`);
}

main();
