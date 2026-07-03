import { readFile } from 'node:fs/promises';

import { CommandContext } from '../index.js';
import { readStdin } from '../io.js';
import { withWriteStore } from '../store.js';
import { importProvenanceEvents } from '../../store/index.js';
import { ProvenanceEventSchema, type ProvenanceEvent } from '../../schemas/provenance-event.js';

export interface ImportProvenanceEventsCommandResult {
  appended: number;
  skipped: number;
}

/**
 * Parse `export-provenance-events` output: NDJSON (one event per line) or a
 * JSON array. Every line/element is validated through the schema at this
 * boundary; a malformed event aborts the import with its line number — a
 * partial events file is a producer bug, not something to half-apply.
 */
export function parseProvenanceEventLines(input: string): ProvenanceEvent[] {
  const trimmed = input.trim();
  if (trimmed === '') return [];
  if (trimmed.startsWith('[')) {
    return ProvenanceEventSchema.array().parse(JSON.parse(trimmed));
  }
  return trimmed.split('\n').map((line, index) => {
    try {
      return ProvenanceEventSchema.parse(JSON.parse(line));
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(`invalid provenance event on line ${index + 1}: ${detail}`);
    }
  });
}

/**
 * `mem import-provenance-events [--file FILE] [--store PATH]` — append exported
 * producer provenance events (NDJSON or JSON array, from `--file` or stdin)
 * into the store. The import half of the schema-bump round-trip. Idempotent: an
 * event whose `id` already exists is skipped, never rewritten (append-only
 * contract). Backfill-source rows are refused — build-store re-derives those.
 */
export async function importProvenanceEventsCommand(
  ctx: CommandContext
): Promise<ImportProvenanceEventsCommandResult> {
  const fileOpt = ctx.options.file;
  let input: string;
  if (fileOpt !== undefined) {
    if (typeof fileOpt !== 'string') {
      throw new Error('--file requires a path: mem import-provenance-events --file FILE');
    }
    input = await readFile(fileOpt, 'utf8');
  } else {
    input = await readStdin();
  }

  const events = parseProvenanceEventLines(input);
  const result = withWriteStore(ctx.options, db => importProvenanceEvents(db, events));

  if (!ctx.options.json) {
    console.error(
      `imported ${result.appended} provenance event(s), skipped ${result.skipped} duplicate(s)`
    );
  }

  return result;
}
