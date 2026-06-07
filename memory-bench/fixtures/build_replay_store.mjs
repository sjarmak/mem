// Build a deterministic replay store for the `ours`-arm integration test.
//
// Usage: node build_replay_store.mjs <db-path>
//
// Uses the public P1.5 store API (the same surface retrieval-v1 reads). Lays out
// one query work `B` plus three prior records exercising the D6 boundary:
//   - prior-cross (rigB, closed before B.started, same failure signature) — the
//     cross-rig match `mem retrieve B --scope cross-rig` must return.
//   - prior-same  (rigA, closed before B.started, same signature) — same-rig only.
//   - future      (rigB, closed AFTER B.started) — must be excluded (leak guard).
//
// Path is resolved from this file to the repo-root dist/ build.

import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const storeMod = await import(resolve(here, '../../dist/store/index.js'));
const { openStore, writeRecords, appendLesson } = storeMod;

const dbPath = process.argv[2];
if (!dbPath) {
  console.error('usage: node build_replay_store.mjs <db-path>');
  process.exit(2);
}

const tscError = {
  tool: 'tsc',
  severity: 'error',
  message: 'TS2345: bad argument',
  file: 'src/a.ts',
  line: 12,
  column: 5,
};

const record = (work_id, rig, closed, started) => ({
  work_id,
  rig,
  title: `work ${work_id}`,
  lifecycle: { created: '2026-06-01T00:00:00Z', started, closed, status: 'closed', status_history: [] },
  trace: { jsonl_path: `/t/${work_id}.jsonl`, errors: [tscError] },
  outcome: { pr: `#${work_id}`, pr_state: 'merged', commit_sha: `sha-${work_id}`, ci: 'pass' },
  links: { deps: [], supersedes: [] },
});

const db = openStore(dbPath);
try {
  writeRecords(db, [
    record('B', 'rigA', '2026-06-11T00:00:00Z', '2026-06-10T00:00:00Z'),
    record('prior-cross', 'rigB', '2026-06-05T00:00:00Z', '2026-06-04T00:00:00Z'),
    record('prior-same', 'rigA', '2026-06-05T00:00:00Z', '2026-06-04T00:00:00Z'),
    record('future', 'rigB', '2026-06-20T00:00:00Z', '2026-06-19T00:00:00Z'),
  ]);
  appendLesson(db, {
    work_id: 'prior-cross',
    extracted_at: '2026-06-05T01:00:00Z',
    commit_sha: 'sha-prior-cross',
    payload: { root_cause: 'missing import', resolution: 'add the import' },
  });
} finally {
  db.close();
}
