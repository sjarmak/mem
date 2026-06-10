import { readFile } from 'node:fs/promises';
import { z } from 'zod';

import { CommandContext } from '../index.js';
import { readStdin } from '../io.js';
import { withWriteStore } from '../store.js';
import { importLessons, type LessonInput } from '../../store/index.js';

/** One exported lesson line. Unknown keys (notably the source store's `id`) are
 * dropped — ids are assigned by the destination store on append. */
const LessonLineSchema = z.object({
  work_id: z.string().min(1),
  extracted_at: z.string().min(1),
  commit_sha: z.string().min(1).optional(),
  payload: z.record(z.string(), z.unknown()),
});

export interface ImportLessonsCommandResult {
  appended: number;
  skipped: number;
}

/** Parse `export-lessons` output: NDJSON (one lesson per line) or a JSON array.
 * Every line/element is validated at this boundary; a malformed lesson aborts
 * the import with its line number — a partial lessons file is a producer bug,
 * not something to half-apply. */
export function parseLessonLines(input: string): LessonInput[] {
  const trimmed = input.trim();
  if (trimmed === '') {
    return [];
  }
  if (trimmed.startsWith('[')) {
    return z.array(LessonLineSchema).parse(JSON.parse(trimmed));
  }
  return trimmed.split('\n').map((line, index) => {
    try {
      return LessonLineSchema.parse(JSON.parse(line));
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(`invalid lesson on line ${index + 1}: ${detail}`);
    }
  });
}

/**
 * `mem import-lessons [--file FILE] [--store PATH]` — append exported lessons
 * (NDJSON or JSON array, from `--file` or stdin) into the store. The import
 * half of the schema-bump migration path. Idempotent: a lesson whose full
 * content already exists is skipped, never rewritten (Decision 9 append-only).
 */
export async function importLessonsCommand(
  ctx: CommandContext
): Promise<ImportLessonsCommandResult> {
  const fileOpt = ctx.options.file;
  let input: string;
  if (fileOpt !== undefined) {
    if (typeof fileOpt !== 'string') {
      throw new Error('--file requires a path: mem import-lessons --file FILE');
    }
    input = await readFile(fileOpt, 'utf8');
  } else {
    input = await readStdin();
  }

  const lessons = parseLessonLines(input);
  const result = withWriteStore(ctx.options, db => importLessons(db, lessons));

  if (!ctx.options.json) {
    console.error(`imported ${result.appended} lesson(s), skipped ${result.skipped} duplicate(s)`);
  }

  return result;
}
