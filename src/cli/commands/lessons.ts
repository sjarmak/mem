import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import { lessonsFor, type StoredLesson } from '../../store/index.js';

export interface LessonsResult {
  work_id: string;
  count: number;
  lessons: StoredLesson[];
}

/**
 * `mem lessons <work_id> [--store PATH]` — the append-only lessons (Decision 9)
 * recorded for one bead, in insertion order. A thin pass-through over the
 * `lessonsFor` primitive; no ranking or composition (that is Phase-2 retrieval).
 */
export function lessonsCommand(ctx: CommandContext): LessonsResult {
  const workId = ctx.args[0];
  if (workId === undefined) {
    throw new Error('lessons requires a work_id: mem lessons <work_id>');
  }

  const lessons = withReadStore(ctx.options, db => lessonsFor(db, workId));

  if (!ctx.options.json) {
    for (const lesson of lessons) {
      console.error(`#${lesson.id}\t${lesson.extracted_at}\t${lesson.commit_sha ?? '-'}`);
    }
    console.error(`${lessons.length} lesson(s) for ${workId}`);
  }

  return { work_id: workId, count: lessons.length, lessons };
}
