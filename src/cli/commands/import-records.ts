import { readFile } from 'node:fs/promises';
import { z } from 'zod';

import { CommandContext } from '../index.js';
import { readStdin } from '../io.js';
import { storePath } from '../store.js';
import { buildStoreFromRecords } from './build-store.js';
import { WorkRecordSchema, type WorkRecord } from '../../schemas/workrecord.js';

export interface ImportRecordsCommandResult {
  imported: number;
  store: string;
}

/**
 * Parse `import-records` input: NDJSON (one WorkRecord per line) or a JSON array.
 * Every line/element is validated against {@link WorkRecordSchema} at this
 * boundary; a malformed record aborts the import with its line number — a partial
 * records file is a producer bug, not something to half-apply (mirrors
 * `parseLessonLines`).
 */
export function parseRecordLines(input: string): WorkRecord[] {
  const trimmed = input.trim();
  if (trimmed === '') {
    return [];
  }
  if (trimmed.startsWith('[')) {
    return z.array(WorkRecordSchema).parse(JSON.parse(trimmed));
  }
  return trimmed.split('\n').map((line, index) => {
    try {
      return WorkRecordSchema.parse(JSON.parse(line));
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(`invalid work record on line ${index + 1}: ${detail}`);
    }
  });
}

/**
 * `mem import-records [--file FILE] [--store PATH]` — upsert hand-authored or
 * externally-derived WorkRecords (NDJSON or JSON array, from `--file` or stdin)
 * into the store, creating it if absent. The non-dolt counterpart to
 * `build-store`: the spine reader materializes records from the bead graph, this
 * materializes them from a producer that has no bead spine (e.g. a per-rig
 * fail-to-pass substrate built from a code repo's git history). Reuses
 * {@link buildStoreFromRecords}, so the same validate→project→FTS write path and
 * the append-only `lessons` invariant hold; lessons are attached separately via
 * `import-lessons`.
 */
export async function importRecordsCommand(
  ctx: CommandContext
): Promise<ImportRecordsCommandResult> {
  const fileOpt = ctx.options.file;
  let input: string;
  if (fileOpt !== undefined) {
    if (typeof fileOpt !== 'string') {
      throw new Error('--file requires a path: mem import-records --file FILE');
    }
    input = await readFile(fileOpt, 'utf8');
  } else {
    input = await readStdin();
  }

  const records = parseRecordLines(input);
  const path = storePath(ctx.options);
  const counts = buildStoreFromRecords(path, records);

  if (!ctx.options.json) {
    console.error(
      `imported ${counts.records} work record(s) into ${path} ` +
        `(${counts.provenance_events} provenance event(s) derived)`
    );
  }

  return { imported: counts.records, store: path };
}
