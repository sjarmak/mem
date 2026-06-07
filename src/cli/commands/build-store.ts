import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

import { CommandContext } from '../index.js';
import { storePath } from '../store.js';
import { openStore, writeRecords } from '../../store/index.js';
import { defaultConnection, doltRunner, readAllRigs, readRig } from '../../ingest/beads.js';
import { type SessionResolver, attachTraceRefs } from '../../ingest/trace-resolve.js';
import { type TraceReader, parseRecordTrace } from '../../parse/trace-parse.js';
import type { WorkRecord } from '../../schemas/workrecord.js';

export interface BuildStoreResult {
  store: string;
  rig: string | null;
  count: number;
  /** Records carrying ≥1 deterministic trace error after the P1.6 parse (0 when
   * `--with-traces` is off). The D8 failure signatures the `ours` arm fires on. */
  records_with_errors: number;
}

/** Options for {@link attachAndParse} — injectable so the P1.3→P1.6 wiring is
 * testable without a `gc` binary or real transcripts on disk. */
export interface AttachParseDeps {
  resolve?: SessionResolver;
  read?: TraceReader;
}

/**
 * Resolve each record's transcript (P1.3 `attachTraceRefs`: assignee → session →
 * JSONL) and parse its deterministic build/test/lint failure signal (P1.6
 * `parseRecordTrace`). Pure composition of the two existing primitives — it adds
 * no extraction logic of its own, so the ZFC and validity guarantees of P1.6
 * (deterministic tool-output errors only; never the outcome label) carry through
 * unchanged. Records are copied, never mutated.
 */
export function attachAndParse(records: WorkRecord[], deps: AttachParseDeps = {}): WorkRecord[] {
  const resolved = attachTraceRefs(records, deps.resolve ? { resolve: deps.resolve } : {});
  return resolved.map(record => parseRecordTrace(record, deps.read));
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
 * `mem build-store [--rig <name>] [--with-traces] [--store PATH]` — read the
 * WorkRecord spine from the dolt bead store (all rigs, or one `--rig`) and
 * persist it into the sidecar at `--store` (default `.mem/store.db`).
 *
 * With `--with-traces`, each record's transcript is resolved (P1.3) and parsed
 * for deterministic build/test/lint failure signatures (P1.6) before the write,
 * so the store carries the D8 signals the failure-triggered `ours` arm fires on.
 * Without it, the store is the bead spine only (fast, no `gc`/transcript IO).
 */
export async function buildStoreCommand(ctx: CommandContext): Promise<BuildStoreResult> {
  const rig = typeof ctx.options.rig === 'string' ? ctx.options.rig : null;
  const path = storePath(ctx.options);
  const withTraces = ctx.options['with-traces'] === true;

  const run = doltRunner(defaultConnection());
  const spine = rig === null ? await readAllRigs(run) : await readRig(run, rig);
  const records = withTraces ? attachAndParse(spine) : spine;
  const count = buildStoreFromRecords(path, records);
  const recordsWithErrors = records.filter(r => (r.trace?.errors?.length ?? 0) > 0).length;

  if (!ctx.options.json) {
    const scope = rig === null ? 'all rigs' : rig;
    const traceNote = withTraces ? `; ${recordsWithErrors} carry trace errors` : '';
    console.error(`built ${count} work records from ${scope} into ${path}${traceNote}`);
  }

  return { store: path, rig, count, records_with_errors: recordsWithErrors };
}
