import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

import { CommandContext } from '../index.js';
import { storePath } from '../store.js';
import { openStore, writeRecords } from '../../store/index.js';
import { defaultConnection, doltRunner, readAllRigs, readRig } from '../../ingest/beads.js';
import type { WorkRecord } from '../../schemas/workrecord.js';

export interface BuildStoreResult {
  store: string;
  rig: string | null;
  count: number;
}

/**
 * Materialize the P1.5 SQLite+FTS5 sidecar from the dolt bead spine — the
 * write counterpart to the read-only query commands. Reuses the existing ingest
 * readers and {@link writeRecords}; it adds no new substrate, only the wiring
 * that puts real WorkRecords into the store the retrieval/eval path reads.
 *
 * The parent dir is created (a fresh checkout has no `.mem/`); {@link openStore}
 * initializes the schema on a new file. Extracted so the persistence half is
 * testable without a live dolt connection.
 */
export function buildStoreFromRecords(path: string, records: WorkRecord[]): number {
  mkdirSync(dirname(path), { recursive: true });
  const db = openStore(path);
  try {
    writeRecords(db, records);
  } finally {
    db.close();
  }
  return records.length;
}

/**
 * `mem build-store [--rig <name>] [--store PATH]` — read the WorkRecord spine
 * from the dolt bead store (all rigs, or one `--rig`) and persist it into the
 * sidecar at `--store` (default `.mem/store.db`). The records ride the `--json`
 * envelope count; the store is the durable output.
 */
export async function buildStoreCommand(ctx: CommandContext): Promise<BuildStoreResult> {
  const rig = typeof ctx.options.rig === 'string' ? ctx.options.rig : null;
  const path = storePath(ctx.options);

  const run = doltRunner(defaultConnection());
  const records = rig === null ? await readAllRigs(run) : await readRig(run, rig);
  const count = buildStoreFromRecords(path, records);

  if (!ctx.options.json) {
    const scope = rig === null ? 'all rigs' : rig;
    console.error(`built ${count} work records from ${scope} into ${path}`);
  }

  return { store: path, rig, count };
}
