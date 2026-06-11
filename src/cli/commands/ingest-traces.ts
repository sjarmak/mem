import { existsSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { storePath } from '../store.js';
import { openStore, coverageReport, type CoverageReport } from '../../store/index.js';
import { buildStoreCommand, type BuildStoreResult } from './build-store.js';
import { COVERAGE_AXES, formatCoverage } from './coverage.js';

export interface IngestTracesResult {
  store: string;
  rig: string | null;
  /** What `build-store --with-traces --with-provenance` wrote this run. */
  build: BuildStoreResult;
  /** Store-wide coverage before the ingest (all-zero when the store is new). */
  before: CoverageReport;
  /** Store-wide coverage after the ingest. */
  after: CoverageReport;
  /** Per-axis `after - before` — the headline "what this run lifted". */
  delta: CoverageReport;
}

/** The all-zero baseline for an absent store. A literal (not derived from
 * `COVERAGE_AXES`) so it stays `keyof`-checked: adding an axis to
 * `CoverageReport` is a compile error here until it is filled in. */
const ZERO_COVERAGE: CoverageReport = {
  records: 0,
  with_trace: 0,
  trace_errors: 0,
  trace_runs: 0,
  with_base_commit: 0,
  with_commit_sha: 0,
};

/** Read store coverage without materializing a store that does not exist yet
 * (a fresh checkout has no `.mem/`): an absent store is all-zero by definition,
 * and `build-store` creates it on the very next step. */
function coverageOf(path: string): CoverageReport {
  if (!existsSync(path)) return ZERO_COVERAGE;
  const db = openStore(path);
  try {
    return coverageReport(db);
  } finally {
    db.close();
  }
}

/** Per-axis subtraction. New errors/runs only ever accumulate, but on an
 * idempotent re-run every delta is 0 — that flat report is the signal that the
 * substrate is already complete, not that the run did nothing wrong. */
export function coverageDelta(before: CoverageReport, after: CoverageReport): CoverageReport {
  return {
    records: after.records - before.records,
    with_trace: after.with_trace - before.with_trace,
    trace_errors: after.trace_errors - before.trace_errors,
    trace_runs: after.trace_runs - before.trace_runs,
    with_base_commit: after.with_base_commit - before.with_base_commit,
    with_commit_sha: after.with_commit_sha - before.with_commit_sha,
  };
}

/**
 * `mem ingest-traces [--rig <name>] [--store PATH]` — the packaged, idempotent
 * trace-substrate ingest (mem-75t.4). It is `build-store` with `--with-traces`
 * and `--with-provenance` always on (resolve transcript → parse errors +
 * run-metadata → attach git baseline), wrapped in a before/after coverage diff
 * so a run reports exactly which axes it lifted off zero.
 *
 * Idempotent because the writer upserts records and rebuilds child rows
 * (errors/runs/agents) on every write — re-running converges instead of
 * double-counting, which is what makes it safe to put on the nightly cron.
 *
 * Must be run from a directory whose `gc` resolves the city store (the
 * transcript and bead readers need it); from the wrong cwd the spine still
 * loads but every trace/provenance axis resolves to zero. The
 * `/ingest-trace-substrate` skill documents that requirement.
 */
export async function ingestTracesCommand(ctx: CommandContext): Promise<IngestTracesResult> {
  const path = storePath(ctx.options);
  const rig = typeof ctx.options.rig === 'string' ? ctx.options.rig : null;

  const before = coverageOf(path);

  // Reuse build-store wholesale with the two ingest flags forced on. A fresh
  // options object (never a mutation of ctx.options) keeps the override local.
  const build = await buildStoreCommand({
    ...ctx,
    options: { ...ctx.options, 'with-traces': true, 'with-provenance': true },
  });

  const after = coverageOf(path);
  const delta = coverageDelta(before, after);

  if (!ctx.options.json) {
    console.error('coverage after ingest:');
    for (const line of formatCoverage(after)) console.error(`  ${line}`);
    const lifted = COVERAGE_AXES.filter(axis => delta[axis] !== 0)
      .map(axis => `${axis} +${delta[axis]}`)
      .join(', ');
    console.error(lifted ? `delta: ${lifted}` : 'delta: none (substrate already complete)');
  }

  return { store: path, rig, build, before, after, delta };
}
