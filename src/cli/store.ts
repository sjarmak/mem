import { existsSync } from 'node:fs';

import { openStore, type StoreDatabase } from '../store/index.js';
import type { CliOptions } from './index.js';

/**
 * Store resolution for the query commands. The store is the SQLite+FTS5 sidecar
 * (P1.5); these commands only read it.
 */

/** Default sidecar location, relative to the working directory. */
const DEFAULT_STORE_PATH = '.mem/store.db';

/** The `--store <path>` override, or {@link DEFAULT_STORE_PATH}. */
function storePath(options: CliOptions): string {
  return typeof options.store === 'string' ? options.store : DEFAULT_STORE_PATH;
}

/**
 * Open the store for a read-only query. Unlike {@link openStore}, a missing
 * file is a user error — not a reason to silently materialize an empty store
 * (which would make every query return nothing with no indication why).
 */
function openStoreForRead(path: string): StoreDatabase {
  if (!existsSync(path)) {
    throw new Error(`No store at ${path}. Build one first, or pass --store <path>.`);
  }
  return openStore(path);
}

/**
 * Open the resolved store, run `fn`, and always close the handle. Every query
 * command shares this open→use→close lifecycle; centralizing it keeps the
 * command bodies to their actual query.
 */
export function withReadStore<T>(options: CliOptions, fn: (db: StoreDatabase) => T): T {
  const db = openStoreForRead(storePath(options));
  try {
    return fn(db);
  } finally {
    db.close();
  }
}
