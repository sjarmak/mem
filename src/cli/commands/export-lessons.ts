import { writeFileSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import { allLessons, type StoredLesson } from '../../store/index.js';

export interface ExportLessonsResult {
  count: number;
  /** NDJSON destination when `--out` was given, else null (lessons ride the
   * `--json` envelope). */
  out: string | null;
  lessons: StoredLesson[];
}

/**
 * `mem export-lessons [--store PATH] [--out FILE]` — dump every append-only
 * lesson (Decision 9), in insertion order. The export half of the schema-bump
 * migration path: lessons are the one table a store rebuild cannot regenerate,
 * so a version bump is export → rebuild → `import-lessons`. With `--out` the
 * lessons are written as NDJSON (one lesson per line, `import-lessons`' input
 * format); otherwise they ride the `--json` envelope.
 */
export function exportLessonsCommand(ctx: CommandContext): ExportLessonsResult {
  const outOpt = ctx.options.out;
  if (outOpt !== undefined && typeof outOpt !== 'string') {
    throw new Error('--out requires a path: mem export-lessons --out FILE');
  }

  const lessons = withReadStore(ctx.options, db => allLessons(db));

  let out: string | null = null;
  if (outOpt !== undefined) {
    const ndjson = lessons.map(lesson => JSON.stringify(lesson)).join('\n');
    writeFileSync(outOpt, ndjson === '' ? '' : `${ndjson}\n`, 'utf8');
    out = outOpt;
  }

  if (!ctx.options.json) {
    console.error(`exported ${lessons.length} lesson(s)${out === null ? '' : ` to ${out}`}`);
  }

  return { count: lessons.length, out, lessons };
}
