#!/usr/bin/env node
// Read-only store inspector: schema version + per-table row counts, with the
// non-regenerable tables and the producer/backfill provenance split called out.
// Never writes. Run from the mem repo root (needs node_modules/better-sqlite3):
//
//   node .claude/skills/mem-store-schema-and-rebuild/scripts/inspect-store.mjs [path/to/store.db]
//
// Default path: .mem/store.db
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';

const require = createRequire(import.meta.url);
const Database = require('better-sqlite3');

const path = process.argv[2] ?? '.mem/store.db';
if (!existsSync(path)) {
  console.error(`no store at ${path}`);
  process.exit(1);
}

const db = new Database(path, { readonly: true, fileMustExist: true });

const version = db.pragma('user_version', { simple: true });
console.log(`store: ${path}`);
console.log(`user_version (schema): ${version}`);

const tables = db
  .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'trace_errors_fts%' ORDER BY name")
  .all()
  .map(r => r.name);

const NON_REGENERABLE = new Set(['lessons', 'memory_events', 'provenance_events']);

console.log('\ntable                 rows      class');
for (const t of tables) {
  const { n } = db.prepare(`SELECT COUNT(*) AS n FROM "${t}"`).get();
  const cls = NON_REGENERABLE.has(t)
    ? 'APPEND-ONLY, NON-REGENERABLE'
    : t === 'work_records'
      ? 'record JSON = truth; columns projected'
      : 'projection (rebuilt on upsert)';
  console.log(`${t.padEnd(20)} ${String(n).padStart(8)}  ${cls}`);
}

if (tables.includes('provenance_events')) {
  const producer = db
    .prepare("SELECT COUNT(*) AS n FROM provenance_events WHERE source != 'ingest-backfill'")
    .get().n;
  const backfill = db
    .prepare("SELECT COUNT(*) AS n FROM provenance_events WHERE source = 'ingest-backfill'")
    .get().n;
  console.log(`\nprovenance_events split: ${producer} producer (round-tripped by rebuild), ${backfill} backfilled (re-derived every build)`);
}

db.close();
