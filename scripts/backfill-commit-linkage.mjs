// Backfill work→PR/commit outcomes from integration-branch commit messages.
//
// Recovers the linkage ingest dropped (every store record has null external_ref/
// pr, yet the landing commit names the work id). Pure linkage logic lives in
// src/ingest/commitLinkage.ts (unit-tested); this runner is IO only: read records
// from a store, resolve outcomes per rig against its checkout, report recovery +
// soundness, and (with --apply) write the outcome back into the record JSON and
// the pr/pr_state/commit_sha projection columns.
//
// Usage:
//   node scripts/backfill-commit-linkage.mjs --store <db> [--rigs a,b] [--apply]
// Default is a dry-run report; --apply writes (use a store COPY).

import Database from 'better-sqlite3';
import { linkRigOutcomes } from '../dist/ingest/commitLinkage.js';
import { RIG_REPOS, DEFAULT_BRANCH } from '../dist/ingest/rig-repo-map.js';

function arg(flag, fallback = undefined) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}
const STORE = arg('--store', '.mem/store-v7-landed.db');
const APPLY = process.argv.includes('--apply');
const RIGS = (arg('--rigs', 'gascity_dashboard,mem') || '')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean);

const db = new Database(STORE, { readonly: !APPLY });
const update = APPLY
  ? db.prepare(
      'UPDATE work_records SET record=@record, pr=@pr, pr_state=@pr_state, commit_sha=@commit_sha WHERE work_id=@work_id'
    )
  : null;

console.log(`store=${STORE}  mode=${APPLY ? 'APPLY' : 'dry-run'}\n`);

let grandSound = 0;
for (const rig of RIGS) {
  const repo = RIG_REPOS[rig];
  if (!repo || !repo.dir) {
    console.log(`${rig}: no checkout mapped — skipped`);
    continue;
  }
  const branch = repo.branch || DEFAULT_BRANCH;
  const rows = db
    .prepare("SELECT work_id, record FROM work_records WHERE rig=? AND status='closed'")
    .all(rig);
  const workIds = rows.map(r => r.work_id);
  const outcomes = linkRigOutcomes(workIds, repo.dir, branch);

  const tally = { canonical: 0, unique: 0, multiple: 0, withPr: 0, direct: 0 };
  const writes = [];
  for (const r of rows) {
    const linked = outcomes.get(r.work_id);
    if (!linked) continue;
    tally[linked.linkage]++;
    if (linked.outcome.pr) tally.withPr++;
    else tally.direct++;
    if (APPLY) {
      const rec = JSON.parse(r.record);
      rec.outcome = { ...(rec.outcome ?? {}), ...linked.outcome };
      writes.push({
        work_id: r.work_id,
        record: JSON.stringify(rec),
        pr: linked.outcome.pr ?? null,
        pr_state: linked.outcome.pr_state ?? null,
        commit_sha: linked.outcome.commit_sha ?? null,
      });
    }
  }
  const sound = tally.canonical + tally.unique; // high-precision attributions
  grandSound += sound;

  console.log(`=== ${rig} (${branch}) ===`);
  console.log(`  closed records      : ${rows.length}`);
  console.log(
    `  linked              : ${outcomes.size} (${((100 * outcomes.size) / rows.length).toFixed(0)}%)`
  );
  console.log(
    `    canonical/unique  : ${tally.canonical} / ${tally.unique}  -> SOUND outcome oracle = ${sound}`
  );
  console.log(`    multiple (review) : ${tally.multiple}`);
  console.log(`  oracle kind         : merged-PR ${tally.withPr} | direct-commit ${tally.direct}`);

  if (APPLY) {
    const tx = db.transaction(items => items.forEach(it => update.run(it)));
    tx(writes);
    console.log(`  WROTE ${writes.length} outcomes`);
  }
  console.log('');
}
console.log(`TOTAL sound outcome oracles recovered: ${grandSound}`);
