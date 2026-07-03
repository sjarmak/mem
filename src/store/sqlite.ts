import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import Database from 'better-sqlite3';

import { SCHEMA_DDL, SCHEMA_VERSION } from './schema.js';

/**
 * Store opener (P1.5). One handle per call — engram's `DatabasePool` is
 * deliberately not ported (a CLI-shaped tool has no concurrent-handle problem
 * to solve). WAL + NORMAL sync follow engram's proven pragmas for concurrent
 * readers and crash recovery.
 */

export type StoreDatabase = Database.Database;

/** The append-only tables a rebuild from the bead spine CANNOT regenerate —
 * distilled lessons, write-time memory events, and producer-recorded
 * provenance events. Every schema bump must round-trip all three
 * (export → fresh build → import; `mem rebuild` does the cycle mechanically). */
export const NON_REGENERABLE_TABLES = ['lessons', 'memory_events', 'provenance_events'] as const;

/** Whether `table` exists in this store — an older schema version may predate
 * one of the non-regenerable tables, in which case there is nothing to carry. */
export function tableExists(db: StoreDatabase, table: string): boolean {
  return (
    db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(table) !==
    undefined
  );
}

/** One-line inventory of the non-regenerable tables for the version-mismatch
 * refusal, so the operator sees exactly what a blind delete would strand. */
function nonRegenerableInventory(db: StoreDatabase): string {
  return NON_REGENERABLE_TABLES.map(table => {
    if (!tableExists(db, table)) return `${table}: absent`;
    const row = db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number };
    return `${table}: ${row.n} row(s)`;
  }).join(', ');
}

/**
 * Open (and on first use, initialize) the sidecar store at `path`.
 * `:memory:` is supported for tests. A database whose `user_version` is
 * neither 0 (fresh) nor {@link SCHEMA_VERSION} fails loudly: there is no
 * in-place migration framework — a version bump (the store is a rebuildable
 * projection of the dolt spine) is handled by re-ingesting into a fresh store.
 */
export function openStore(path: string): StoreDatabase {
  if (path !== ':memory:') {
    mkdirSync(dirname(path), { recursive: true });
  }

  const db = new Database(path);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.pragma('synchronous = NORMAL');
  db.pragma('busy_timeout = 5000');

  const version = db.pragma('user_version', { simple: true }) as number;
  if (version === 0) {
    db.transaction(() => {
      db.exec(SCHEMA_DDL);
      db.pragma(`user_version = ${SCHEMA_VERSION}`);
    })();
  } else if (version !== SCHEMA_VERSION) {
    const inventory = nonRegenerableInventory(db);
    db.close();
    throw new Error(
      `Store at ${path} has schema version ${version}, expected ${SCHEMA_VERSION}. ` +
        'No in-place migration exists — run `mem rebuild` to re-ingest into a fresh store; ' +
        `it round-trips the non-regenerable tables this store holds (${inventory}).`
    );
  }

  return db;
}

/**
 * Open an EXISTING store read-only, WITHOUT the schema-version gate. The export
 * half of the rebuild round-trip must read the append-only, non-regenerable
 * tables out of a store whose version {@link openStore} refuses — that mismatch
 * is the round-trip's reason to exist. Safe because every export re-validates
 * rows through their zod schemas on the way out (shape drift fails loudly) and
 * the read-only handle cannot write projections. Exports only; every write path
 * stays behind {@link openStore}'s gate.
 */
export function openStoreForExport(path: string): StoreDatabase {
  const db = new Database(path, { readonly: true, fileMustExist: true });
  db.pragma('busy_timeout = 5000');

  const version = db.pragma('user_version', { simple: true }) as number;
  if (version === 0) {
    db.close();
    throw new Error(`Store at ${path} is not an initialized mem store (schema version 0).`);
  }

  return db;
}
