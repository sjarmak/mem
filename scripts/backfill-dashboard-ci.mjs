// Elevate dashboard pr-link links to the CI-verified T1 tier (mem-wanz.5).
//
// mem-wanz.7 writes a T2 `wasGeneratedBy` link per transcript pr-link entry — a
// verifiable PR reference, not yet a CI/merge oracle. This post-pass closes the
// gap using the Day-0 frozen dashboard merged-PR snapshot (NEVER a live gh call):
// for every green merged PR it elevates the matching link to T1, accretes
// `ci-rollup` onto its provenance, and writes the replayable outcome (merge SHA +
// ci=pass) back into the WorkRecord — the honest sound core the headline stands on.
//
// Pure classification + join logic lives in src/ingest/dashboardCi.ts (unit-tested);
// this runner is IO only: read pr-link links from a store, load+validate the
// freeze, plan elevations, report, and (with --apply) write them. Fail-closed:
// failed/UNKNOWN CI never elevates.
//
// Usage:
//   node scripts/backfill-dashboard-ci.mjs --store <db> [--freeze <json>] [--rig gascity_dashboard] [--apply]
// Default is a dry-run report; --apply writes (use a store COPY).

import Database from 'better-sqlite3';
import { readFileSync } from 'node:fs';
import {
  indexSnapshot,
  planCiElevations,
  CI_ROLLUP_PROVENANCE,
} from '../dist/ingest/dashboardCi.js';
import { RIG_REPOS } from '../dist/ingest/rig-repo-map.js';

function arg(flag, fallback = undefined) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}
const STORE = arg('--store', '.mem/store-v8.db');
const FREEZE = arg('--freeze', 'freeze/2026-06-17/dashboard-ci.raw.json');
const RIG = arg('--rig', 'gascity_dashboard');
const APPLY = process.argv.includes('--apply');

const repo = RIG_REPOS[RIG]?.slug;
if (!repo) {
  console.error(`rig "${RIG}" has no repo slug in RIG_REPOS — cannot resolve PR urls`);
  process.exit(1);
}

const index = indexSnapshot(JSON.parse(readFileSync(FREEZE, 'utf8')));
const ci = { success: 0, failure: 0, UNKNOWN: 0 };
for (const c of index.values()) ci[c.ci]++;

const db = new Database(STORE, { readonly: !APPLY });

// All pr-link edges live in the links table at tier T2 until elevated here.
const prLinks = db.prepare("SELECT work_id, entity_ref FROM links WHERE key_type='pr-link'").all();

const elevations = planCiElevations(index, repo, prLinks);

console.log(
  `store=${STORE}  freeze=${FREEZE}  repo=${repo}  mode=${APPLY ? 'APPLY' : 'dry-run'}\n`
);
console.log(
  `frozen PRs       : ${index.size}  (success ${ci.success} | failure ${ci.failure} | UNKNOWN ${ci.UNKNOWN})`
);
console.log(`pr-link edges    : ${prLinks.length}`);
console.log(`-> T1 elevations : ${elevations.length}  (green merged PRs joined to a pr-link edge)`);

if (!APPLY) {
  console.log('\ndry-run — no writes. Re-run with --apply on a store COPY.');
  process.exit(0);
}

const updateLink = db.prepare(
  "UPDATE links SET tier='T1', provenance=@provenance " +
    "WHERE work_id=@work_id AND entity_ref=@entity_ref AND key_type='pr-link'"
);
const selectRecord = db.prepare('SELECT record FROM work_records WHERE work_id=?');
const updateRecord = db.prepare(
  'UPDATE work_records SET record=@record, pr=@pr, pr_state=@pr_state, commit_sha=@commit_sha WHERE work_id=@work_id'
);

const tx = db.transaction(items => {
  for (const e of items) {
    // Fetch the record first: links and work_records are cleared together on
    // re-ingest, so a link with no record is unreachable in practice — but if it
    // happens, skip the whole elevation rather than leave a T1 link with no
    // outcome written.
    const row = selectRecord.get(e.work_id);
    if (!row) continue;
    updateLink.run({
      work_id: e.work_id,
      entity_ref: e.entity_ref,
      provenance: CI_ROLLUP_PROVENANCE,
    });
    const rec = JSON.parse(row.record);
    rec.outcome = { ...(rec.outcome ?? {}), ...e.outcome };
    updateRecord.run({
      work_id: e.work_id,
      record: JSON.stringify(rec),
      pr: e.outcome.pr,
      pr_state: e.outcome.pr_state,
      commit_sha: e.outcome.commit_sha,
    });
  }
});
tx(elevations);

console.log(`\nWROTE ${elevations.length} T1 elevations (links tier + WorkRecord outcome).`);
