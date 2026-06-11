import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import { coverageReport, type CoverageReport } from '../../store/index.js';

/** The coverage axes in report order — shared by `coverage` and the
 * `ingest-traces` delta so both render the same rows in the same order. */
export const COVERAGE_AXES: readonly (keyof CoverageReport)[] = [
  'records',
  'with_trace',
  'trace_errors',
  'trace_runs',
  'with_base_commit',
  'with_commit_sha',
  'multi_session',
  'with_task_type',
];

/** Axes counted per-record, so they print against a `/records` denominator.
 * The remaining axes (`records` itself, and the bare `trace_errors`/`trace_runs`
 * row counts) have no denominator. */
const PER_RECORD_AXES = new Set<keyof CoverageReport>([
  'with_trace',
  'with_base_commit',
  'with_commit_sha',
  'multi_session',
  'with_task_type',
]);

/** Human-readable line per axis: `with_trace  42/1214` against the record total
 * for the per-record axes, a bare count otherwise. */
export function formatCoverage(report: CoverageReport): string[] {
  return COVERAGE_AXES.map(axis => {
    const value = report[axis];
    const suffix = PER_RECORD_AXES.has(axis) ? `/${report.records}` : '';
    return `${axis.padEnd(16)} ${value}${suffix}`;
  });
}

/**
 * `mem coverage [--store PATH]` — report how much of the trace substrate the
 * store actually carries (mem-75t): record total, resolved transcripts, parsed
 * trace errors, run-metadata rows, and git/GitHub commit anchors. Read-only;
 * the diagnostic counterpart to `build-store`/`ingest-traces`.
 */
export function coverageCommand(ctx: CommandContext): CoverageReport {
  const report = withReadStore(ctx.options, db => coverageReport(db));

  if (!ctx.options.json) {
    for (const line of formatCoverage(report)) console.error(line);
  }

  return report;
}
