// Measure the false-close rate across rigs (mem-z14cd).
//
// The question is a corpus-validity one, not a dispatch-hygiene one. mem treats
// `bead closed` as a VERIFIED outcome label — the premise that makes the city's
// exhaust a labeled benchmark rather than a log. This asks whether that premise
// holds: for every CLOSED bead that still has a branch naming it, is the change
// actually present on an integration branch?
//
// Ref-level tests cannot answer that (a squash-merge and a strand look identical
// by ancestry, and `git cherry` calls rebased-but-landed commits unlanded), so
// the decision is patch-id content equivalence. All of that logic is pure and
// unit-tested in src/ingest/landedContent.ts + src/ingest/falseClose.ts; this
// runner is IO only — read the store, list refs, shell git, tally, report.
//
// Two properties this runner owes the report it feeds:
//
//   PINNED. The corpus moves under measurement — mem's main advanced mid-session
//   and landed one of the very gold cases being measured — so every rig records
//   the integration SHAs it was decided against, and the run records its store
//   snapshot and timestamp. A rate without those anchors is not reproducible.
//
//   COVERAGE-BOUNDED. Only closed beads with a surviving branch are visible at
//   all (~2% of closed). That is a sampling frame, not a corpus rate, and the
//   output states it on every row so no reader can lift the rate out of context.
//
// Usage:
//   node scripts/measure-false-close.mjs [--store <db>] [--rigs a,b] [--out <path>]

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';

import { classifyLandedContent } from '../dist/ingest/landedContent.js';
import {
  combineRefVerdicts,
  joinBranches,
  tallyPooled,
  tallyRig,
  hasPower,
  MIN_DECIDED_FOR_RATE,
} from '../dist/ingest/falseClose.js';
import { RIG_REPOS, DEFAULT_BRANCH } from '../dist/ingest/rig-repo-map.js';
import { pickRemoteForSlug } from './verify/lib.mjs';

function arg(flag, fallback = undefined) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}
const STORE = arg('--store', '/home/ds/projects/mem/.mem/store.db');
const OUT = arg('--out', null);
const ONLY_RIGS = (arg('--rigs', '') || '')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean);

// ---- git shell (execFile — no shell, no interpolation) ----------------------

/** The GitRunner the pure modules take. Throws with `.status` on a non-zero exit
 * so isNonZeroExit recognizes it; a missing git binary throws WITHOUT a status
 * and propagates, which is what keeps a misconfiguration from being reported as
 * a corpus-wide coverage gap. */
const gitRunner = (dir, args, stdin) =>
  execFileSync('git', ['-C', dir, ...args], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    stdio: ['pipe', 'pipe', 'ignore'],
    ...(stdin === undefined ? {} : { input: stdin }),
  });

/** stdout, or null on a non-zero exit (a missing ref / pruned object). Only for
 * this runner's own probing; the pure modules take `gitRunner`, which must keep
 * throwing so they can tell a negative answer from a fault. */
function gitOut(dir, args) {
  try {
    return gitRunner(dir, args);
  } catch {
    return null;
  }
}

function readRemotes(dir) {
  const names = gitOut(dir, ['remote']);
  if (names === null) return {};
  const remotes = {};
  for (const name of names.split('\n').filter(n => n.trim() !== '')) {
    const url = gitOut(dir, ['remote', 'get-url', name]);
    if (url !== null) remotes[name] = url.trim();
  }
  return remotes;
}

const pct = n => (n * 100).toFixed(1).padStart(5) + '%';

// ---- run --------------------------------------------------------------------

const db = new Database(STORE, { readonly: true });
const started_at = new Date().toISOString();

const rigs = Object.keys(RIG_REPOS).filter(r => ONLY_RIGS.length === 0 || ONLY_RIGS.includes(r));
const tallies = [];
const skippedRigs = [];
const details = [];

for (const rig of rigs) {
  const entry = RIG_REPOS[rig];
  const skip = reason => {
    skippedRigs.push({ rig, reason });
    console.log(`${rig.padEnd(18)} SKIPPED — ${reason}`);
  };

  // A `multi` rig spans several repos, so no single checkout is authoritative;
  // resolving one would be a guess.
  if (entry.multi === true) {
    skip('multi-repo rig — no single authoritative checkout');
    continue;
  }
  if (!entry.dir) {
    skip('no checkout mapped in RIG_REPOS');
    continue;
  }
  if (!existsSync(entry.dir)) {
    skip(`checkout ${entry.dir} absent`);
    continue;
  }

  const integration = entry.branch || DEFAULT_BRANCH;

  // The rig's closed beads ARE the population; the join intersects against them.
  const closedIds = new Set(
    db
      .prepare("SELECT work_id FROM work_records WHERE rig=? AND status='closed'")
      .all(rig)
      .map(r => r.work_id)
  );
  if (closedIds.size === 0) {
    skip('no closed beads in the store');
    continue;
  }

  // The authoritative remote is resolved BY SLUG, never assumed to be `origin`:
  // gascity's checkout carries fourteen remotes, most of them contributor forks,
  // and a fork's copy of a branch attests nothing about upstream.
  const picked = pickRemoteForSlug(readRemotes(entry.dir), entry.slug);
  const authRemote = picked === null ? null : picked.remote;

  const refsOut = gitOut(entry.dir, [
    'for-each-ref',
    '--format=%(refname)',
    'refs/heads',
    'refs/remotes',
  ]);
  if (refsOut === null) {
    skip('could not list refs');
    continue;
  }
  const refnames = refsOut.split('\n').filter(s => s.trim() !== '');

  const { joined, skipped } = joinBranches({
    refnames,
    closedIds,
    integration,
    // null when no remote matched the rig's slug: joinBranches then treats every
    // remote ref as non-authoritative and falls back to local heads alone.
    authoritativeRemote: authRemote,
  });

  /** A ref's tip, or null when it does not resolve. */
  const tipOf = ref => {
    // --end-of-options for consistency with the invariant landedContent holds:
    // every ref reaching git is read strictly as a revision. These particular
    // refs come from the static RIG_REPOS map and locally-configured remote
    // names, so nothing here is attacker-influenced today — but the rule is
    // cheaper to keep everywhere than to re-audit each call site.
    const sha = gitOut(entry.dir, [
      'rev-parse',
      '--verify',
      '--quiet',
      '--end-of-options',
      `${ref}^{commit}`,
    ]);
    return sha === null ? null : sha.trim();
  };

  // The integration refs a branch could legitimately have landed on, each pinned
  // to the tip it is decided against — the verdicts are only reproducible against
  // those exact commits. Both refs are consulted because neither is universally
  // right: mem's local main is 53 commits AHEAD of its remote (workers never
  // push), while gpk's is 14 BEHIND upstream. combineRefVerdicts takes the
  // strongest verdict, making the rate a lower bound rather than an artifact of
  // ref selection. The remote is included only once resolved, so a pinned tip of
  // null can only ever be the local integration branch.
  const pinned = { [integration]: tipOf(integration) };
  if (authRemote !== null) {
    const remoteRef = `${authRemote}/${integration}`;
    const remoteTip = tipOf(remoteRef);
    if (remoteTip !== null) pinned[remoteRef] = remoteTip;
  }
  const integrationRefs = Object.keys(pinned);
  if (pinned[integration] === null && integrationRefs.length === 1) {
    // Every branch would drop to `integration-unresolvable` and masquerade as a
    // coverage hole. Skip with a reason instead.
    skip(`integration ref ${integration} does not resolve`);
    continue;
  }

  const decided = joined.map(j => {
    const per_ref = integrationRefs.map(ref => ({
      ref,
      result: classifyLandedContent(
        { work_dir: entry.dir, branch: j.ref, integration: ref },
        { run: gitRunner }
      ),
    }));
    const winner = combineRefVerdicts(per_ref);
    return { ...j, result: winner.result, decided_by: winner.ref, per_ref };
  });

  const tally = tallyRig(rig, closedIds.size, decided);
  tallies.push({ ...tally, slug: entry.slug, work_dir: entry.dir, integration_refs: pinned });
  details.push({
    rig,
    branches: decided.map(d => ({
      work_id: d.work_id,
      ref: d.ref,
      verdict: d.result.verdict,
      decided_by: d.decided_by,
      cause: d.result.cause,
      n_commits: d.result.n_commits,
      n_matched: d.result.n_matched,
    })),
    join_skips: skipped.length,
  });

  const rateCell = tally.rate === null ? '     —' : pct(tally.rate);
  const ciCell =
    tally.ci === null ? '' : ` [${pct(tally.ci.low).trim()}, ${pct(tally.ci.high).trim()}]`;
  const power = hasPower(tally) ? '' : '  (UNDERPOWERED)';
  console.log(
    `${rig.padEnd(18)} closed=${String(tally.n_closed).padStart(5)}` +
      ` joined=${String(tally.n_joined).padStart(4)}` +
      ` decided=${String(tally.n_decided).padStart(4)}` +
      ` not-landed=${String(tally.n_not_landed).padStart(3)}` +
      `  rate=${rateCell}${ciCell}` +
      `  coverage=${pct(tally.coverage)}${power}`
  );
}

const pooled = tallyPooled(tallies);

console.log('\n=== POOLED (counts summed across rigs, NOT a mean of per-rig rates) ===');
console.log(`  closed beads (corpus)      : ${pooled.n_closed}`);
console.log(
  `  joined to a branch (frame) : ${pooled.n_joined}  (coverage ${pct(pooled.coverage)})`
);
console.log(`  decided by git             : ${pooled.n_decided}`);
console.log(`  NOT landed (absent+partial): ${pooled.n_not_landed}`);
console.log(
  `  false-close rate           : ${pooled.rate === null ? '—' : pct(pooled.rate)}` +
    (pooled.ci === null
      ? ''
      : `  95% CI [${pct(pooled.ci.low).trim()}, ${pct(pooled.ci.high).trim()}]`)
);
console.log(`  verdicts                   : ${JSON.stringify(pooled.verdicts)}`);
console.log(`  undecidable causes         : ${JSON.stringify(pooled.undecidable_causes)}`);
console.log(
  `\n  NOTE: the rate is over the ${pct(pooled.coverage).trim()} of closed beads that still have a` +
    ` branch.\n  It is a coverage-bounded rate for that frame, NOT a corpus-wide false-close rate.`
);

const report = {
  bead: 'mem-z14cd',
  started_at,
  store: STORE,
  min_decided_for_rate: MIN_DECIDED_FOR_RATE,
  pooled,
  rigs: tallies,
  skipped_rigs: skippedRigs,
  details,
};
const outPath = OUT ?? join('verify', `false-close.${started_at.slice(0, 10)}.json`);
mkdirSync(join(outPath, '..'), { recursive: true });
writeFileSync(outPath, JSON.stringify(report, null, 2));
console.log(`\nWrote ${outPath}`);
