// Re-measure the live-ref join (mem-wanz.6, PRD §5.2, key #3, risks R2/R3).
//
// The "27% of sessions carry a live branch ref" headline was falsified — no
// substrate-derivable join reproduces it. The ONE join the store + git can derive
// is `gc-<id>` work record ↔ live `refs/heads/bd-gc-<id>` session branch. This
// runner measures the REAL percentage: it resolves that join against the Day-0
// FROZEN refs dump (mem-wanz.1 — decoupled from live-ref decay), computes each
// branch's merge-base against the AUTHORITATIVE remote's integration branch, and
// applies the fail-closed write-gate: a base with no merge-base on
// <authoritative>/main is DROPPED as decay, never counted. R3 silent-corruption
// protection is UPSTREAM of the classifier — the slug-based remote pick + the
// authRef-resolvability gate below (mem-zzzl4).
//
// Pure resolver + classifier + summary live in src/ingest/liveRef.ts (unit-tested);
// this runner is IO only: read the store + frozen refs, shell git for merge-base /
// is-ancestor, report, and write verify/live-ref.<date>.json. The authoritative
// remote is picked by slug (gascity has 14 remotes — never assume `origin`).
//
// Usage:
//   node scripts/measure-live-ref.mjs [--store <db>] [--date YYYY-MM-DD] [--rigs a,b]

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';

import {
  classifyMergeBase,
  parseForEachRef,
  resolveLiveRefs,
  summarize,
} from '../dist/ingest/liveRef.js';
import { resolveCommit } from '../dist/ingest/landedContent.js';
import { isAncestorOrNull } from '../dist/ingest/provenance.js';
import { RIG_REPOS, DEFAULT_BRANCH } from '../dist/ingest/rig-repo-map.js';
import { pickRemoteForSlug } from './verify/lib.mjs';
import { gitOut, readRemotes } from './verify/git.mjs';

function arg(flag, fallback = undefined) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}
const STORE = arg('--store', '.mem/store-v7-landed.db');
const DATE = arg('--date', '2026-06-17');
const RIGS = (arg('--rigs', 'gascity') || '')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean);

// ---- git shell (execFile — no shell, no interpolation) ----------------------

// The throwing GitRunner shape resolveCommit takes (mirrors measure-false-close.mjs).
const gitRunner = (dir, args) =>
  execFileSync('git', ['-C', dir, ...args], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  });

/** resolveCommit's shared rev-parse, but a git FAULT degrades this rig to a
 * named skip instead of crashing the sweep — see the call site for why. */
const commitOrNull = (dir, ref) => {
  try {
    return resolveCommit(gitRunner, dir, ref);
  } catch {
    return null;
  }
};

const db = new Database(STORE, { readonly: true });

// The freeze names each refs dump by its OBJECT STORE, not the rig key (one store
// can back several rigs). Map rig → store from the freeze manifest so a rig whose
// store name differs (gpk→packs, scix_experiments→scix, zeldascension→zelda)
// resolves to the right dump instead of silently skipping.
const rigToStore = {};
const manifestPath = join('freeze', DATE, 'manifest.json');
if (existsSync(manifestPath)) {
  for (const s of JSON.parse(readFileSync(manifestPath, 'utf8')).stores ?? []) {
    for (const r of s.rigs ?? []) rigToStore[r] = s.store;
  }
}

const rigReports = [];
for (const rig of RIGS) {
  const entry = RIG_REPOS[rig];
  if (!entry || !entry.dir) {
    console.log(`${rig}: no checkout mapped — skipped\n`);
    continue;
  }
  const refsPath = join('freeze', DATE, `refs.${rigToStore[rig] ?? rig}.txt`);
  if (!existsSync(refsPath)) {
    console.log(`${rig}: no frozen refs at ${refsPath} — skipped\n`);
    continue;
  }
  if (!existsSync(entry.dir)) {
    console.log(`${rig}: checkout ${entry.dir} absent — skipped\n`);
    continue;
  }

  // Pick the AUTHORITATIVE remote by slug (R3): never assume origin.
  const picked = pickRemoteForSlug(readRemotes(entry.dir), entry.slug);
  if (picked === null) {
    console.log(`${rig}: no remote matches slug ${entry.slug} — cannot gate, skipped\n`);
    continue;
  }
  const authRef = `${picked.remote}/${entry.branch || DEFAULT_BRANCH}`;

  // Sanity-gate the authoritative ref itself: if it does not resolve, EVERY branch
  // would drop as no-merge-base and masquerade as 100% decay. Skip-with-reason
  // instead, so a broken checkout/ref can't be misread as a real measurement.
  //
  // The catch restores this gate's pre-dedup contract: it used to run through
  // this script's own bare-catch gitOut, so ANY failure meant "does not
  // resolve". Bare `resolveCommit` maps only a non-zero EXIT to null and
  // rethrows a fault (signal kill, maxBuffer overrun) — correct for its other
  // consumer, classifyLandedContent, but wrong for a per-rig gate in a loop
  // with no try/catch: a throw would lose every other rig. The skip-with-reason
  // this comment promises only holds if nothing escapes.
  if (commitOrNull(entry.dir, authRef) === null) {
    console.log(`${rig}: authoritative ref ${authRef} does not resolve — skipped\n`);
    continue;
  }

  const workIds = db
    .prepare('SELECT work_id FROM work_records WHERE rig=?')
    .all(rig)
    .map(r => r.work_id);
  const refs = parseForEachRef(readFileSync(refsPath, 'utf8'));
  const resolved = resolveLiveRefs(workIds, refs);

  const results = resolved.map(r => {
    // Day-0 FROZEN ref SHA (decay-proof) resolved against the live shared object
    // store. Objects are present today, so this equals the bundle answer; if they
    // are GC'd, merge-base returns null → DROP_NO_MERGE_BASE (decay), and the
    // Day-0 bundle is the documented fallback. The --is-ancestor call is a live
    // INVARIANT PROBE, not a corruption gate: base_sha is a merge-base of authRef,
    // so it is an ancestor by construction. Kept as an assertion — a non-true
    // answer means git faulted, and classifyMergeBase fails it safe to undecided
    // (mem-zzzl4), never kept, never a measurable corruption rate.
    const base_sha = gitOut(entry.dir, ['merge-base', r.sha, authRef]);
    // Tri-state, NOT a boolean: true confirms the invariant → keep; null means git
    // could not answer → undecided; a false is mathematically impossible for a
    // merge-base of authRef and also fails safe to undecided (mem-zzzl4). Null with
    // no merge-base too — the ancestry question is never asked.
    const is_ancestor =
      base_sha === null ? null : isAncestorOrNull(gitRunner, entry.dir, base_sha, authRef);
    return classifyMergeBase({
      work_id: r.work_id,
      refname: r.refname,
      branch_sha: r.sha,
      base_sha,
      is_ancestor,
    });
  });

  const report = summarize(workIds.length, results);
  rigReports.push({ rig, slug: entry.slug, authoritative: authRef, ...report });

  console.log(`=== ${rig} (auth ${authRef}) ===`);
  console.log(`  work records (denominator) : ${report.denominator}`);
  console.log(`  resolved to live bd- branch: ${report.resolved}`);
  console.log(`  kept (merge-base on auth)  : ${report.kept}`);
  console.log(
    `  dropped (decay/no-base)    : ${report.dropped}  ${JSON.stringify(report.drops_by_reason)}`
  );
  // Its own line, never folded into decay: that would relabel a git fault as a
  // resolved-but-decayed base — the reporting half of mem-y2x7n / mem-zzzl4.
  console.log(
    `  undecided (git no answer)  : ${report.undecided}  ${JSON.stringify(report.undecided_by_cause)}`
  );
  const bound = report.undecided > 0 ? ' (LOWER BOUND — see undecided)' : '';
  console.log(
    `  REAL live-ref base %       : ${report.pct.toFixed(2)}%  (was claimed 27%)${bound}\n`
  );
}

const outDir = 'verify';
mkdirSync(outDir, { recursive: true });
const outPath = join(outDir, `live-ref.${DATE}.json`);
writeFileSync(outPath, JSON.stringify({ date: DATE, store: STORE, rigs: rigReports }, null, 2));
console.log(`Wrote ${outPath}`);
