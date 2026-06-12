import { writeFileSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { asString } from '../io.js';
import { withReadStore, withWriteStore } from '../store.js';
import { importLessons } from '../../store/index.js';
import {
  claudeRunner,
  distillLessons,
  selectCandidates,
  type DistillFailure,
  type DistillRunner,
} from '../../distill/distiller.js';

export interface DistillLessonsResult {
  candidates: number;
  distilled: number;
  failures: DistillFailure[];
  /** NDJSON destination when `--out` was given, else null. */
  out: string | null;
  /** Import counts when `--import` was given, else null. */
  imported: { appended: number; skipped: number } | null;
}

const DEFAULT_MODEL = 'sonnet';

/**
 * `mem distill-lessons [--rig RIG] [--work-ids a,b,c] [--limit N]
 *   [--model sonnet] [--out FILE] [--import] [--force] [--store PATH]`
 *
 * Distill Decision-9 lesson payloads from closed WorkRecords that carry trace
 * errors, via headless Claude on the OAuth subscription (no-paid-API). Writes
 * import-ready NDJSON with `--out`, appends straight into the store with
 * `--import` (idempotent), or both. Records that already have lessons are
 * skipped unless `--force` (lessons are append-only).
 */
export function distillLessonsCommand(
  ctx: CommandContext,
  runner?: DistillRunner
): DistillLessonsResult {
  const rig = asString(ctx.options.rig, 'rig');
  const model = asString(ctx.options.model, 'model');
  const out = asString(ctx.options.out, 'out');
  const workIdsOpt = asString(ctx.options['work-ids'], 'work-ids');
  const limit = ctx.options.limit;
  let parsedLimit: number | undefined;
  if (limit !== undefined) {
    parsedLimit = Number(limit);
    if (!Number.isInteger(parsedLimit) || parsedLimit <= 0) {
      throw new Error(`--limit must be a positive integer, got ${String(limit)}`);
    }
  }
  if (out === undefined && ctx.options.import !== true) {
    throw new Error('nothing to do: pass --out FILE and/or --import');
  }

  const records = withReadStore(ctx.options, db =>
    selectCandidates(db, {
      rig,
      workIds: workIdsOpt === undefined ? undefined : workIdsOpt.split(',').filter(s => s !== ''),
      limit: parsedLimit,
      force: ctx.options.force === true,
    })
  );

  const distill = runner ?? claudeRunner(model ?? DEFAULT_MODEL);
  const { lessons, failures } = distillLessons(records, distill, new Date().toISOString());

  let outPath: string | null = null;
  if (out !== undefined) {
    const ndjson = lessons.map(lesson => JSON.stringify(lesson)).join('\n');
    writeFileSync(out, ndjson === '' ? '' : `${ndjson}\n`, 'utf8');
    outPath = out;
  }

  let imported: DistillLessonsResult['imported'] = null;
  if (ctx.options.import === true && lessons.length > 0) {
    imported = withWriteStore(ctx.options, db => importLessons(db, lessons));
  }

  if (!ctx.options.json) {
    console.error(
      `distilled ${lessons.length}/${records.length} lesson(s)` +
        (outPath === null ? '' : ` -> ${outPath}`) +
        (imported === null ? '' : `; imported ${imported.appended}, skipped ${imported.skipped}`)
    );
    for (const failure of failures) {
      console.error(`FAILED ${failure.work_id}: ${failure.error}`);
    }
  }

  return {
    candidates: records.length,
    distilled: lessons.length,
    failures,
    out: outPath,
    imported,
  };
}
