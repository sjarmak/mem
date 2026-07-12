import { writeFileSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { asPositiveInt, asString } from '../io.js';
import { withReadStore, withWriteStore } from '../store.js';
import { importLessons } from '../../store/index.js';
import {
  claudeRunner,
  computeRegressions,
  DEFAULT_REGRESSION_WINDOW,
  distillLessons,
  selectCandidates,
  type DistillFailure,
  type DistillRunner,
  type EvidenceOptions,
} from '../../distill/distiller.js';
import type { RegressionFlag, RegressionSkip } from '../../distill/verify.js';

export interface DistillLessonsResult {
  candidates: number;
  distilled: number;
  failures: DistillFailure[];
  /** NDJSON destination when `--out` was given, else null. */
  out: string | null;
  /** Import counts when `--import` was given, else null. */
  imported: { appended: number; skipped: number } | null;
  /** mem-0r7l K-past-fix check: signatures from the most-recently-appended
   * lessons that recurred in later, non-sibling work — report-only. */
  regressions: RegressionFlag[];
  /** Non-null when the regression check itself failed. Always returned —
   * including for `--json` callers — so a failed check is never
   * indistinguishable from a clean one; the already-committed import (if
   * any) is never rolled back on account of this. */
  regressionError: string | null;
  /** Lessons in the K-window the regression check could not evaluate (source
   * record missing, or an unparseable `extracted_at`) — surfaced rather than
   * silently dropped. */
  regressionSkipped: RegressionSkip[];
}

const DEFAULT_MODEL = 'sonnet';

/**
 * `mem distill-lessons [--rig RIG] [--work-ids a,b,c] [--limit N]
 *   [--model sonnet] [--out FILE] [--import] [--force] [--store PATH]
 *   [--regression-window N]`
 *
 * Distill Decision-9 lesson payloads from closed WorkRecords that carry trace
 * errors, via headless Claude on the OAuth subscription (no-paid-API). Writes
 * import-ready NDJSON with `--out`, appends straight into the store with
 * `--import` (idempotent), or both. Records that already have lessons are
 * skipped unless `--force` (lessons are append-only).
 *
 * mem-0r7l verified-write gate: a candidate with no resolution evidence at
 * all (no landed diff, no readable transcript) is refused before any model
 * call — surfaced as a `no-resolution-evidence` failure. Admitted lessons
 * carry a mechanical `evidence_kind` provenance tag. `--regression-window`
 * (default 5) sets how many of the most-recently-appended lessons are
 * checked for K-past-fix regressions (report-only, see `regressions` below).
 */
export function distillLessonsCommand(
  ctx: CommandContext,
  runner?: DistillRunner,
  evidenceOpts: EvidenceOptions = {}
): DistillLessonsResult {
  const rig = asString(ctx.options.rig, 'rig');
  const model = asString(ctx.options.model, 'model');
  const out = asString(ctx.options.out, 'out');
  const workIdsOpt = asString(ctx.options['work-ids'], 'work-ids');
  const parsedLimit = asPositiveInt(ctx.options.limit, 'limit');
  const parsedRegressionWindow =
    asPositiveInt(ctx.options['regression-window'], 'regression-window') ??
    DEFAULT_REGRESSION_WINDOW;
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
  const { lessons, failures } = distillLessons(
    records,
    distill,
    new Date().toISOString(),
    evidenceOpts
  );

  let outPath: string | null = null;
  if (out !== undefined) {
    const ndjson = lessons.map(lesson => JSON.stringify(lesson)).join('\n');
    writeFileSync(out, ndjson === '' ? '' : `${ndjson}\n`, 'utf8');
    outPath = out;
  }

  // mem-0r7l: report-only, checked against lessons already in the store
  // BEFORE this run's own batch is appended below — a batch that imports >= k
  // new lessons would otherwise evict every pre-existing lesson from the
  // K-window before its signature could ever be checked (this run's own new
  // lessons can't recur "later than now" in the same run regardless of
  // ordering, so nothing is lost by checking first). A failure here must
  // degrade to an empty report rather than block the import below.
  let regressions: RegressionFlag[] = [];
  let regressionSkipped: RegressionSkip[] = [];
  let regressionError: string | null = null;
  try {
    const result = withReadStore(ctx.options, db =>
      computeRegressions(db, parsedRegressionWindow, rig)
    );
    regressions = result.flags;
    regressionSkipped = result.skipped;
  } catch (error: unknown) {
    regressionError = error instanceof Error ? error.message : String(error);
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
    if (regressionError !== null) {
      console.error(`REGRESSION CHECK FAILED (non-fatal): ${regressionError}`);
    }
    for (const flag of regressions) {
      console.error(
        `REGRESSION ${flag.lesson_work_id} (${flag.signature}) recurred in: ${flag.recurred_in.join(', ')}`
      );
    }
    for (const skip of regressionSkipped) {
      console.error(`REGRESSION CHECK SKIPPED ${skip.work_id}: ${skip.reason}`);
    }
  }

  return {
    candidates: records.length,
    distilled: lessons.length,
    failures,
    out: outPath,
    imported,
    regressions,
    regressionError,
    regressionSkipped,
  };
}
