import { existsSync, renameSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { storePath } from '../store.js';
import { buildStoreCommand, type BuildStoreResult } from './build-store.js';
import {
  type ImportLessonsResult,
  type ImportMemoryEventsResult,
  type ImportProvenanceEventsResult,
  type StoredLesson,
  allLessons,
  allMemoryEvents,
  importLessons,
  importMemoryEvents,
  importProvenanceEvents,
  openStore,
  openStoreForExport,
  producerProvenanceEvents,
  tableExists,
} from '../../store/index.js';
import type { MemoryEvent } from '../../schemas/memory-event.js';
import type { ProvenanceEvent } from '../../schemas/provenance-event.js';

export interface RebuildResult {
  store: string;
  /** Where the pre-rebuild store was moved — preserved, never deleted; the
   * operator removes it once the rebuilt store is verified. */
  backup: string;
  /** Rows read out of the old store, per non-regenerable table. */
  exported: { lessons: number; memory_events: number; provenance_events: number };
  /** Rows carried into the fresh store, per non-regenerable table. */
  imported: {
    lessons: ImportLessonsResult;
    memory_events: ImportMemoryEventsResult;
    provenance_events: ImportProvenanceEventsResult;
  };
  build: BuildStoreResult;
}

/** The build step, injectable so tests exercise the round-trip without a live
 * dolt connection (the {@link buildStoreCommand} default needs one). */
export type BuildRunner = (ctx: CommandContext) => Promise<BuildStoreResult>;

/**
 * `mem rebuild [--store PATH] [build-store flags...]` — the enforced
 * schema-bump round-trip: export the three non-regenerable, append-only tables
 * (`lessons`, `memory_events`, producer-source `provenance_events`) out of the
 * old store, move it aside, then re-ingest a fresh store via the existing
 * `build-store` path (all its flags — `--rig`, `--with-traces`,
 * `--with-provenance`, `--session-join`, `--task-types` — pass through) with
 * the exported rows imported back in. Exists so the round-trip is mechanical,
 * not remembered; the manual export/import pairs remain for surgical use.
 *
 * The export step reads across a schema-version mismatch — the normal reason
 * to rebuild is a store the current version refuses to open. The import runs
 * BEFORE the build: the carried tables have no FK to `work_records`, and
 * build-store's read-first provenance path prefers producer-recorded `cut`
 * events already present in the target store over the git date heuristic.
 */
export async function rebuildCommand(
  ctx: CommandContext,
  build: BuildRunner = buildStoreCommand
): Promise<RebuildResult> {
  const path = storePath(ctx.options);
  if (!existsSync(path)) {
    throw new Error(`No store at ${path} to rebuild. A first build is plain \`mem build-store\`.`);
  }

  // 1. Export the non-regenerable tables. A table an older schema never had
  //    holds nothing to carry — honestly zero, noted on the coverage line.
  const old = openStoreForExport(path);
  let lessons: StoredLesson[] = [];
  let memoryEvents: MemoryEvent[] = [];
  let provenanceEvents: ProvenanceEvent[] = [];
  try {
    if (tableExists(old, 'lessons')) lessons = allLessons(old);
    if (tableExists(old, 'memory_events')) memoryEvents = allMemoryEvents(old);
    if (tableExists(old, 'provenance_events')) provenanceEvents = producerProvenanceEvents(old);
  } finally {
    old.close();
  }

  // 2. Move the old store aside (with any WAL sidecars) so build-store starts
  //    from a fresh schema at the canonical path.
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backup = `${path}.pre-rebuild-${stamp}`;
  renameSync(path, backup);
  for (const suffix of ['-wal', '-shm']) {
    if (existsSync(`${path}${suffix}`)) renameSync(`${path}${suffix}`, `${backup}${suffix}`);
  }

  if (!ctx.options.json) {
    console.error(
      `exported ${lessons.length} lesson(s), ${memoryEvents.length} memory event(s), ` +
        `${provenanceEvents.length} producer provenance event(s); old store preserved at ${backup}`
    );
  }

  try {
    // 3. Import into the fresh store, then 4. run the actual rebuild.
    const fresh = openStore(path);
    let imported: RebuildResult['imported'];
    try {
      imported = {
        lessons: importLessons(fresh, lessons),
        memory_events: importMemoryEvents(fresh, memoryEvents),
        provenance_events: importProvenanceEvents(fresh, provenanceEvents),
      };
    } finally {
      fresh.close();
    }

    const built = await build(ctx);

    if (!ctx.options.json) {
      console.error(
        `rebuild complete: round-tripped ${imported.lessons.appended} lesson(s), ` +
          `${imported.memory_events.appended} memory event(s), ` +
          `${imported.provenance_events.appended} producer provenance event(s) into ${path}`
      );
    }

    return {
      store: path,
      backup,
      exported: {
        lessons: lessons.length,
        memory_events: memoryEvents.length,
        provenance_events: provenanceEvents.length,
      },
      imported,
      build: built,
    };
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(
      `rebuild failed after the export step: ${detail} ` +
        `(the pre-rebuild store is preserved at ${backup}; the fresh store at ${path} is partial — ` +
        're-run `mem rebuild` after fixing the cause, or restore the backup)'
    );
  }
}
