import { readFile } from 'node:fs/promises';

import { CommandContext } from '../index.js';
import { readStdin } from '../io.js';
import { withWriteStore } from '../store.js';
import { importMemoryEvents } from '../../store/index.js';
import { MemoryEventSchema, type MemoryEvent } from '../../schemas/memory-event.js';

export interface ImportMemoryEventsCommandResult {
  appended: number;
  skipped: number;
}

/**
 * Parse `export-memory-events` output: NDJSON (one event per line) or a JSON
 * array. Every line/element is validated through the strict schema at this
 * boundary; a malformed event aborts the import with its line number — a
 * partial events file is a producer bug, not something to half-apply.
 */
export function parseMemoryEventLines(input: string): MemoryEvent[] {
  const trimmed = input.trim();
  if (trimmed === '') return [];
  if (trimmed.startsWith('[')) {
    return MemoryEventSchema.array().parse(JSON.parse(trimmed));
  }
  return trimmed.split('\n').map((line, index) => {
    try {
      return MemoryEventSchema.parse(JSON.parse(line));
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(`invalid memory event on line ${index + 1}: ${detail}`);
    }
  });
}

/**
 * `mem import-memory-events [--file FILE] [--store PATH]` — append exported
 * memory events (NDJSON or JSON array, from `--file` or stdin) into the store.
 * The import half of the schema-bump round-trip. Idempotent: an event whose
 * `id` already exists is skipped, never rewritten (append-only contract).
 */
export async function importMemoryEventsCommand(
  ctx: CommandContext
): Promise<ImportMemoryEventsCommandResult> {
  const fileOpt = ctx.options.file;
  let input: string;
  if (fileOpt !== undefined) {
    if (typeof fileOpt !== 'string') {
      throw new Error('--file requires a path: mem import-memory-events --file FILE');
    }
    input = await readFile(fileOpt, 'utf8');
  } else {
    input = await readStdin();
  }

  const events = parseMemoryEventLines(input);
  const result = withWriteStore(ctx.options, db => importMemoryEvents(db, events));

  if (!ctx.options.json) {
    console.error(
      `imported ${result.appended} memory event(s), skipped ${result.skipped} duplicate(s)`
    );
  }

  return result;
}
