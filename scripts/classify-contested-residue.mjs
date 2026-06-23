// Classify the contested-window (landed_state='ambiguous-window') residue by
// blocking reason, and measure how far the mem-75t.16 session-commit recovery —
// plus the mem-75t.19 detached-HEAD shape extension — reaches into it.
//
// mem-75t.15/.16 recovered each session's TRUE per-worktree replay base from the
// `[branch sha]` commit-success line its trace records (src/ingest/sessionCommits.ts).
// This runner re-derives that recovery over every contested record in a store and
// sorts the rest into the blocking reasons the mem-75t.12 squash finding predicts:
//
//   recovered          parse >=1 local commit AND parent resolves in the rig clone
//                      (base_state='resolved') — the sound TRUE-base prize.
//   squash-erased      parse >=1 local commit BUT its parent is gone from the clone
//                      (base_state='commit-absent') — squashed/rebased away upstream.
//   no-local-commit    trace resolved + clone present, but the session printed no
//                      git commit-success line in any shape — genuinely nothing to
//                      anchor (reason a).
//   no-trace-resolved  no trace.jsonl_path on the record — the trace stage never
//                      resolved a transcript, so there is no text to parse (folds
//                      into reason a: no local commit is recorded in an available
//                      trace).
//   trace-reaped       trace.jsonl_path set but the file is gone from disk.
//
// The detached-HEAD subset (reason b) is attributed separately: a recovery is
// "new (detached)" when the legacy single-token-branch regex found NOTHING but the
// extended parser (which reads git's `[detached HEAD sha]` heading) did. Each new
// recovery's first_commit is SHA-verified against the rig clone (`git cat-file -e`).
//
// Pure parse/derive logic lives in src/ingest/sessionCommits.ts (unit-tested); this
// runner is read-only IO: it never writes the store, never invents a SHA.
//
// Usage: node scripts/classify-contested-residue.mjs [--store <db>] [--json]

import Database from 'better-sqlite3';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';

import {
  parseSessionCommits,
  deriveSessionCommits,
} from '../dist/ingest/sessionCommits.js';
import { defaultGitRunner } from '../dist/ingest/provenance.js';

function arg(flag, fallback = undefined) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}
const STORE = arg('--store', '.mem/store.db');
const JSON_OUT = process.argv.includes('--json');

// The pre-mem-75t.19 regex: single-token branch only. Used to attribute which
// recoveries are NEW (found only via the detached-HEAD heading the extension added).
const LEGACY_RE = /\[[\w./-]+ (?:\(root-commit\) )?([0-9a-f]{7,40})\]/g;
const legacyCommits = text => [...text.matchAll(LEGACY_RE)].map(m => m[1]);

// `git cat-file -e <sha>^{commit}` — exit 0 iff the object exists in the clone and
// is a commit. Explicit SHA-existence proof for each new recovery (deriveSession-
// Commits already implies it via parent resolution; this makes it auditable).
function commitExists(clone, sha) {
  try {
    execFileSync('git', ['-C', clone, 'cat-file', '-e', `${sha}^{commit}`], {
      stdio: 'ignore',
    });
    return true;
  } catch {
    return false;
  }
}

const db = new Database(STORE, { readonly: true });
const rows = db
  .prepare("SELECT record FROM work_records WHERE landed_state='ambiguous-window'")
  .all();
db.close();

const tally = {
  contested: rows.length,
  recovered: 0,
  recovered_legacy: 0,
  recovered_new_detached: 0,
  squash_erased: 0,
  no_local_commit: 0,
  no_trace_resolved: 0,
  trace_reaped: 0,
};
const newRecoveries = []; // {work_id, rig, first_commit, true_base, verified}

for (const { record } of rows) {
  const r = JSON.parse(record);
  const tracePath = r.trace?.jsonl_path;
  const clone = r.provenance?.work_dir;

  if (!tracePath || !clone) {
    tally.no_trace_resolved += 1;
    continue;
  }
  if (!existsSync(tracePath)) {
    tally.trace_reaped += 1;
    continue;
  }

  const text = readFileSync(tracePath, 'utf8');
  const commits = parseSessionCommits(text);
  if (commits.length === 0) {
    tally.no_local_commit += 1;
    continue;
  }

  const session = deriveSessionCommits(commits, clone, defaultGitRunner);
  if (session === null || session.base_state !== 'resolved') {
    tally.squash_erased += 1;
    continue;
  }

  tally.recovered += 1;
  const isNew = legacyCommits(text).length === 0; // only the detached heading found it
  if (isNew) {
    tally.recovered_new_detached += 1;
    newRecoveries.push({
      work_id: r.work_id,
      rig: r.rig,
      first_commit: session.first_commit,
      true_base: session.true_base,
      verified: commitExists(clone, session.first_commit),
    });
  } else {
    tally.recovered_legacy += 1;
  }
}

const residue =
  tally.squash_erased +
  tally.no_local_commit +
  tally.no_trace_resolved +
  tally.trace_reaped;

const summary = { store: STORE, ...tally, residue, new_recoveries: newRecoveries };

if (JSON_OUT) {
  console.log(JSON.stringify(summary, null, 2));
} else {
  console.error(`store: ${STORE}`);
  console.error(`contested (ambiguous-window):     ${tally.contested}`);
  console.error(`  recovered (sound TRUE base):    ${tally.recovered}`);
  console.error(`    via legacy single-token regex:${tally.recovered_legacy}`);
  console.error(`    new via detached-HEAD shape:  ${tally.recovered_new_detached}`);
  console.error(`  RESIDUE (not recovered):        ${residue}`);
  console.error(`    squash-erased (commit-absent):${tally.squash_erased}`);
  console.error(`    no-local-commit (empty parse):${tally.no_local_commit}`);
  console.error(`    no-trace-resolved:            ${tally.no_trace_resolved}`);
  console.error(`    trace-reaped (file gone):     ${tally.trace_reaped}`);
  console.error('');
  console.error('new detached-HEAD recoveries (SHA-verified against clone):');
  for (const n of newRecoveries) {
    console.error(
      `  ${n.work_id} [${n.rig}] first=${n.first_commit} base=${n.true_base} verified=${n.verified}`
    );
  }
}
