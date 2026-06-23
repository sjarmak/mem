// day0-freeze — capture perishable git provenance to integrity-hashed local
// storage before gc reclaims it (mem-wanz.1).
//
// What it preserves, per distinct object store (deduped by git-common-dir):
//   1. for-each-ref full index of every ref (objectname/refname/creatordate).
//   2. worktree list + the detached worktree HEAD SHAs (the polecat C1: parked on
//      a bare commit, invisible to refs/heads/*, gone after gc).
//   3. a git bundle (--branches --tags + detached SHAs) for session stores, with
//      named-ref parity, per-store floor, and detached-recovery assertions —
//      bundle verify alone cannot catch an empty/truncated bundle.
//   4. count-objects -vH + gc config (forensic: did we beat the gc).
//   5. dashboard PR→merge-SHA→CI: mergeCommit.oid → commits/<oid>/check-runs
//      (NOT statusCheckRollup, empty for squash-deleted heads), classified
//      fail-closed with head_ref_deleted + UNKNOWN-with-reason, raw JSON snapshotted.
//
// Fail-closed: writes to freeze/<date>.partial-<pid>/, runs every assertion, and
// atomically renames to freeze/<date>/ + touches MANIFEST.ok ONLY on a full pass.
// Refuses to clobber a passed freeze without --force. Gates the PR walk on
// gh-user==sjarmak and a rate budget.
//
// Usage: node scripts/day0-freeze.mjs [--date YYYY-MM-DD] [--force] [--no-pr]
//                                     [--dashboard-repo owner/name]

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { join } from 'node:path';

import {
  RIG_FLOOR_KEYS,
  bundleParity,
  classifyCiRow,
  dedupeStores,
  detachedRecovery,
  floorCheck,
  isSessionStore,
  parseDetachedHeads,
  parseRefIndex,
  summarizeCi,
} from './freeze/lib.mjs';

// Rig → durable local checkout. Inlined (mirrors src/ingest/rig-repo-map.ts)
// rather than imported from dist/ ON PURPOSE: the freeze is a one-shot forensic
// capture that must run even when the TypeScript build is broken — coupling it to
// `npm run build` would defeat its fail-closed reason for existing. Only the
// fields the freeze needs (dir + dashboard slug) are carried.
const RIG_DIRS = Object.freeze({
  gascity: '/home/ds/gascity-main',
  gascity_dashboard: '/home/ds/gascity-dashboard',
  mem: '/home/ds/projects/mem',
  GEO: '/home/ds/projects/GEO',
  codeprobe: '/home/ds/projects/codeprobe',
  gpk: '/home/ds/gascity-packs',
  scix_experiments: '/home/ds/projects/scix_experiments',
  zeldascension: '/home/ds/projects/zeldascension',
  CodeScaleBench: '/home/ds/projects/CodeScaleBench',
  EnterpriseBench: '/home/ds/projects/EnterpriseBench',
  migration_evals: '/home/ds/projects/migration-evals',
  code_intel_digest: '/home/ds/projects/code-intelligence-digest',
  website: '/home/ds/projects/website',
  mcp_ax: '/home/ds/projects/mcp-ax',
  agent_diagnostics: '/home/ds/projects/agent-diagnostics',
  live_docs: '/home/ds/projects/live_docs',
  background_agents: '/home/ds/projects/background-agents',
  brains: '/home/ds/projects/brains',
  tom_swe: '/home/ds/projects/tom-swe',
});

const DASHBOARD_REPO_DEFAULT = 'gastownhall/gascity-dashboard';
const EXPECTED_GH_USER = 'sjarmak';
// PR walk costs ~1 check-runs call per merged PR + a paginated heads call. Refuse
// to start the walk unless the core budget comfortably covers it with margin.
const RATE_MARGIN = 500;

// ---- arg parsing ------------------------------------------------------------

function arg(flag, fallback = undefined) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}
const FORCE = process.argv.includes('--force');
const SKIP_PR = process.argv.includes('--no-pr');
const DASHBOARD_REPO = arg('--dashboard-repo', DASHBOARD_REPO_DEFAULT);
// Date must be supplied (no clock in a reproducible script); default derived from
// the host date command so a bare run still works, but it is recorded verbatim.
const DATE = arg('--date') || hostDate();

function hostDate() {
  return execFileSync('date', ['+%Y-%m-%d']).toString().trim();
}

// ---- git / gh shells (execFile — no shell, no interpolation) ----------------

function git(dir, args, opts = {}) {
  return execFileSync('git', ['-C', dir, ...args], {
    encoding: 'utf8',
    maxBuffer: 256 * 1024 * 1024,
    stdio: ['ignore', 'pipe', opts.inheritErr ? 'inherit' : 'pipe'],
  });
}

function gitOk(dir, args) {
  try {
    git(dir, args);
    return true;
  } catch {
    return false;
  }
}

function gh(args) {
  return execFileSync('gh', args, {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
}

// ---- fail-closed abort ------------------------------------------------------

let PARTIAL_DIR = null;
function abort(msg) {
  console.error(`\nFREEZE ABORTED: ${msg}`);
  if (PARTIAL_DIR) console.error(`partial output left at: ${PARTIAL_DIR}`);
  process.exit(1);
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

// ---- preflight --------------------------------------------------------------

function preflightGh() {
  let login;
  try {
    login = gh(['api', 'user', '--jq', '.login']).trim();
  } catch (e) {
    abort(`gh api user failed (not authenticated?): ${e.message}`);
  }
  if (login !== EXPECTED_GH_USER) {
    abort(`gh user is "${login}", expected "${EXPECTED_GH_USER}" — refusing to walk PRs as the wrong identity`);
  }
  let remaining = 0;
  try {
    remaining = Number(
      gh(['api', 'rate_limit', '--jq', '.resources.core.remaining']).trim()
    );
  } catch (e) {
    abort(`gh rate_limit check failed: ${e.message}`);
  }
  return { login, remaining };
}

// ---- per-store capture ------------------------------------------------------

function captureStore(store, outRoot) {
  const { dir, commonDir, floorKey, rigs } = store;
  const slug = floorKey || rigs[0];
  const tag = `${slug} (${commonDir})`;
  console.log(`\n--- store ${tag}  rigs=[${rigs.join(', ')}] ---`);

  // 1. full ref index
  const refRaw = git(dir, [
    'for-each-ref',
    '--format=%(objectname) %(refname) %(creatordate:iso-strict)',
  ]);
  const idx = parseRefIndex(refRaw);
  writeFileSync(join(outRoot, `refs.${slug}.txt`), refRaw);
  console.log(`  refs: ${idx.total} total (${idx.heads} heads, ${idx.tags} tags)`);

  // 2. worktrees + detached HEADs
  const wtRaw = git(dir, ['worktree', 'list', '--porcelain']);
  const detached = parseDetachedHeads(wtRaw);
  writeFileSync(join(outRoot, `worktrees.${slug}.txt`), wtRaw);
  console.log(`  worktrees: ${wtRaw.split('\n').filter(l => l.startsWith('worktree ')).length}, detached HEADs: ${detached.length}`);

  // 4. forensic object/gc state (always; cheap)
  const objRaw = git(dir, ['count-objects', '-vH']);
  let gcRaw = '';
  for (const key of ['gc.auto', 'gc.pruneExpire', 'gc.reflogExpire', 'gc.reflogExpireUnreachable']) {
    const val = gitOk(dir, ['config', '--get', key]) ? git(dir, ['config', '--get', key]).trim() : '(unset)';
    gcRaw += `${key}=${val}\n`;
  }
  writeFileSync(join(outRoot, `objects.${slug}.txt`), `${objRaw}\n--- gc config ---\n${gcRaw}`);

  const record = {
    store: slug,
    commonDir,
    dir,
    rigs,
    refs: { total: idx.total, heads: idx.heads, tags: idx.tags },
    detached_heads: detached,
    session_store: false,
    bundle: null,
  };

  // 3. bundle (session stores only)
  if (!isSessionStore({ floorKey, refnames: idx.refnames })) {
    console.log('  (not a session store — indexed only, no bundle)');
    return record;
  }
  record.session_store = true;

  const bundleDir = join(outRoot, 'bundles');
  mkdirSync(bundleDir, { recursive: true });
  const bundlePath = join(bundleDir, `${slug}.bundle`);
  try {
    git(dir, ['bundle', 'create', bundlePath, '--branches', '--tags', ...detached], {
      inheritErr: true,
    });
  } catch (e) {
    abort(`bundle create failed for store "${slug}": ${e.message}`);
  }

  // verify well-formed
  if (!gitOk(dir, ['bundle', 'verify', bundlePath])) {
    abort(`bundle verify failed for store "${slug}"`);
  }

  // named-ref parity
  const listHeads = git(dir, ['bundle', 'list-heads', bundlePath])
    .split('\n')
    .filter(l => l.trim() !== '').length;
  const parity = bundleParity({
    listHeads,
    heads: idx.heads,
    tags: idx.tags,
    collisions: idx.collisions,
  });
  if (!parity.ok) {
    abort(`bundle parity failed for store "${slug}": list-heads=${parity.actual} expected heads+tags=${parity.expected}`);
  }

  // floor gate
  const floor = floorCheck(floorKey, idx.heads);
  if (!floor.ok) {
    abort(`floor breached for store "${slug}": ${floor.count} heads < floor ${floor.floor} — session branches may have been gc'd`);
  }

  // detached recovery: fetch every detached SHA back out of the bundle
  const recovered = recoverDetached(bundlePath, detached, slug, outRoot);
  const recov = detachedRecovery(detached, recovered);
  if (!recov.ok) {
    abort(`detached HEADs unrecoverable from bundle "${slug}": missing ${recov.missing.join(', ')}`);
  }

  const size = statSync(bundlePath).size;
  record.bundle = {
    path: `bundles/${slug}.bundle`,
    sha256: sha256(bundlePath),
    bytes: size,
    list_heads: listHeads,
    parity,
    floor,
    detached_recovered: recov.recovered,
  };
  console.log(
    `  bundle: ${(size / 1e6).toFixed(1)}MB  parity ok (${listHeads}=${parity.expected})  floor ${floor.applicable ? `${floor.count}>=${floor.floor}` : 'n/a'}  detached ${recov.recovered}/${recov.total} recovered`
  );
  return record;
}

// Prove each detached SHA is carried by the bundle, not merely referenced: fetch
// them into a throwaway repo and confirm the objects materialize. The scratch
// repo is removed immediately; only the assertion result matters.
function recoverDetached(bundlePath, detached, slug, outRoot) {
  if (detached.length === 0) return [];
  const scratch = join(outRoot, `.verify-${slug}`);
  rmSync(scratch, { recursive: true, force: true });
  mkdirSync(scratch, { recursive: true });
  try {
    git(scratch, ['init', '-q']);
    git(scratch, ['fetch', '-q', bundlePath, ...detached]);
    return detached.filter(sha => gitOk(scratch, ['cat-file', '-e', sha]));
  } catch (e) {
    abort(`detached-recovery fetch failed for store "${slug}": ${e.message}`);
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

// ---- dashboard CI -----------------------------------------------------------

function captureDashboardCi(outRoot, ghState) {
  console.log(`\n--- dashboard CI: ${DASHBOARD_REPO} ---`);
  const merged = JSON.parse(
    gh([
      'pr',
      'list',
      '-R',
      DASHBOARD_REPO,
      '--state',
      'merged',
      '--limit',
      '1000',
      '--json',
      'number,mergeCommit,headRefName',
    ])
  );
  console.log(`  merged PRs: ${merged.length}`);

  // rate gate: ~1 check-runs call per PR + the matching-refs page, plus margin.
  const need = merged.length + RATE_MARGIN;
  if (ghState.remaining < need) {
    abort(`rate budget too low for PR walk: remaining=${ghState.remaining} < need=${need} (PRs ${merged.length} + margin ${RATE_MARGIN})`);
  }

  // one paginated call → set of still-existing remote head refs, to derive
  // head_ref_deleted without an N-call-per-PR probe.
  const liveHeads = new Set(
    gh(['api', '--paginate', `repos/${DASHBOARD_REPO}/git/refs/heads`, '--jq', '.[].ref'])
      .split('\n')
      .filter(Boolean)
      .map(r => r.replace(/^refs\/heads\//, ''))
  );

  const rows = [];
  const raw = [];
  for (const pr of merged) {
    const oid = pr.mergeCommit?.oid ?? null;
    let checkRuns = null;
    if (oid) {
      try {
        checkRuns = JSON.parse(
          gh(['api', `repos/${DASHBOARD_REPO}/commits/${oid}/check-runs`, '--jq', '[.check_runs[] | {name, conclusion, status}]'])
        );
      } catch (e) {
        // network/permission failure → leave null so the classifier records
        // check-runs-not-fetched rather than silently calling it green.
        checkRuns = null;
        console.error(`  warn: check-runs fetch failed for PR #${pr.number}: ${e.message}`);
      }
    }
    const enriched = {
      number: pr.number,
      mergeCommit: pr.mergeCommit ?? null,
      headRefName: pr.headRefName ?? null,
      headRefDeleted: pr.headRefName ? !liveHeads.has(pr.headRefName) : false,
      checkRuns,
    };
    raw.push(enriched);
    rows.push(classifyCiRow(enriched));
  }

  writeFileSync(join(outRoot, 'dashboard-ci.raw.json'), JSON.stringify(raw, null, 2));
  const txt = rows
    .map(r => `#${r.pr}\t${r.ci_conclusion}\t${r.reason}\thead_ref_deleted=${r.head_ref_deleted}\t${r.merge_oid ?? '-'}`)
    .join('\n');
  writeFileSync(join(outRoot, 'dashboard-ci.txt'), `${txt}\n`);

  const summary = summarizeCi(rows);
  console.log(`  CI: ${summary.success} success, ${summary.failure} failure, ${summary.UNKNOWN} UNKNOWN (of ${summary.total})`);
  return { repo: DASHBOARD_REPO, summary, rows_file: 'dashboard-ci.txt', raw_file: 'dashboard-ci.raw.json' };
}

// ---- main -------------------------------------------------------------------

function main() {
  const repoRoot = process.cwd();
  const finalDir = join(repoRoot, 'freeze', DATE);
  const okMarker = join(finalDir, 'MANIFEST.ok');
  if (existsSync(okMarker) && !FORCE) {
    abort(`freeze/${DATE} already passed (MANIFEST.ok present) — rerun with --force to overwrite`);
  }

  PARTIAL_DIR = join(repoRoot, 'freeze', `${DATE}.partial-${process.pid}`);
  rmSync(PARTIAL_DIR, { recursive: true, force: true });
  mkdirSync(PARTIAL_DIR, { recursive: true });

  // gh preflight up front — fail before any expensive git work if identity/budget wrong.
  const ghState = SKIP_PR ? { login: '(skipped)', remaining: 0 } : preflightGh();
  if (!SKIP_PR) console.log(`gh user=${ghState.login} rate.remaining=${ghState.remaining}`);

  // resolve + dedup stores
  const entries = [];
  for (const [rig, dir] of Object.entries(RIG_DIRS)) {
    if (!existsSync(dir) || !gitOk(dir, ['rev-parse', '--git-dir'])) {
      console.log(`  skip ${rig}: no git checkout at ${dir}`);
      continue;
    }
    const commonDir = git(dir, ['rev-parse', '--path-format=absolute', '--git-common-dir']).trim();
    entries.push({ rig, dir, commonDir, floorKey: RIG_FLOOR_KEYS[rig] ?? null });
  }
  const stores = dedupeStores(entries);
  console.log(`\nresolved ${entries.length} checkouts → ${stores.length} distinct object stores`);

  const storeRecords = stores.map(s => captureStore(s, PARTIAL_DIR));

  const dashboard = SKIP_PR ? { skipped: true } : captureDashboardCi(PARTIAL_DIR, ghState);

  // manifest + integrity hashes of every committed text artifact
  const manifest = {
    date: DATE,
    captured_by: ghState.login,
    git_version: execFileSync('git', ['--version']).toString().trim(),
    stores: storeRecords,
    dashboard,
  };
  const manifestPath = join(PARTIAL_DIR, 'manifest.json');
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  // atomic promotion: only now that every assertion passed
  if (existsSync(finalDir)) {
    if (!FORCE) abort(`freeze/${DATE} exists — rerun with --force`);
    rmSync(finalDir, { recursive: true, force: true });
  }
  renameSync(PARTIAL_DIR, finalDir);
  writeFileSync(join(finalDir, 'MANIFEST.ok'), `${sha256(join(finalDir, 'manifest.json'))}\n`);
  PARTIAL_DIR = null;

  console.log(`\nFREEZE OK → freeze/${DATE}/  (${storeRecords.length} stores, ${storeRecords.filter(r => r.bundle).length} bundled)`);
}

main();
