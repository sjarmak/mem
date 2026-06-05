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

/**
 * Open (and on first use, initialize) the sidecar store at `path`.
 * `:memory:` is supported for tests. A database whose `user_version` is
 * neither 0 (fresh) nor {@link SCHEMA_VERSION} fails loudly — there is no
 * migration framework until a second schema version actually exists.
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
    db.close();
    throw new Error(
      `Store at ${path} has schema version ${version}, expected ${SCHEMA_VERSION}. ` +
        'Re-ingest into a fresh store (no migration path exists yet).'
    );
  }

  return db;
}
