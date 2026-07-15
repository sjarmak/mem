import { writeFileSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { asPositiveInt, asString } from '../io.js';
import { withReadStore, withWriteStore } from '../store.js';
import { importLessons, maxLessonId } from '../../store/index.js';
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
  /** Lessons the regression check could not evaluate (source record missing,
   * or an unparseable `extracted_at`) — surfaced rather than silently dropped.
   * With `--rig`, a skipped orphan lesson is neither necessarily in the
   * K-window nor necessarily in that rig; see {@link RegressionSkip}. */
  regressionSkipped: RegressionSkip[];
  /** `--rig` runs only: how many orphan lessons the check reported among
   * `regressionSkipped`, out of how many exist — the slice is bounded, so
   * these differ when orphans outnumber `--regression-window`. Null on the
   * unscoped path and when the check failed. */
  regressionOrphans: { reported: number; total: number } | null;
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
 * checked for K-past-fix regressions (report-only, see `regressions` below);
 * with `--rig` it additionally bounds how many orphan lessons — whose source
 * record is absent, and whose rig is therefore underivable — are reported as
 * skipped alongside that window, counted in `regressionOrphans` (mem-c7mf3).
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

  // mem-0r7l: the regression check is report-only, checked against lessons
  // already in the store BEFORE this run's own batch is appended below — a
  // batch that imports >= k new lessons would otherwise evict every
  // pre-existing lesson from the K-window before its signature could ever be
  // checked (this run's own new lessons can't recur "later than now" in the
  // same run regardless of ordering, so nothing is lost by checking first).
  // Nothing mutates the store between here and `importLessons` below, so it
  // shares `selectCandidates`' read handle rather than opening a second read
  // after the model calls (mem-0xz9b) — never held open across those calls,
  // since both queries run before `distillLessons` is invoked. A failure
  // here must degrade to an empty report rather than block the import below.
  const { records, regressions, regressionSkipped, regressionOrphans, regressionError } =
    withReadStore(ctx.options, db => {
      const selected = selectCandidates(db, {
        rig,
        workIds: workIdsOpt === undefined ? undefined : workIdsOpt.split(',').filter(s => s !== ''),
        limit: parsedLimit,
        force: ctx.options.force === true,
      });
      // Pin the K-window to the pre-import boundary explicitly, rather than
      // leaving it to this block happening to run before `importLessons`
      // (mem-ljp8b).
      const asOfLessonId = maxLessonId(db);
      let regressions: RegressionFlag[] = [];
      let regressionSkipped: RegressionSkip[] = [];
      let regressionOrphans: DistillLessonsResult['regressionOrphans'] = null;
      let regressionError: string | null = null;
      try {
        const result = computeRegressions(db, parsedRegressionWindow, rig, asOfLessonId);
        regressions = result.flags;
        regressionSkipped = result.skipped;
        regressionOrphans = result.orphans;
      } catch (error: unknown) {
        regressionError = error instanceof Error ? error.message : String(error);
      }
      return {
        records: selected,
        regressions,
        regressionSkipped,
        regressionOrphans,
        regressionError,
      };
    });

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
    if (regressionOrphans !== null && regressionOrphans.reported < regressionOrphans.total) {
      console.error(
        `REGRESSION CHECK: reported ${regressionOrphans.reported} of ${regressionOrphans.total} orphan lesson(s)`
      );
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
    regressionOrphans,
  };
}
