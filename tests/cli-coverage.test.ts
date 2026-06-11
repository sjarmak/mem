import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { coverageCommand, formatCoverage } from '../src/cli/commands/coverage.js';
import { coverageDelta } from '../src/cli/commands/ingest-traces.js';
import { buildStoreFromRecords } from '../src/cli/commands/build-store.js';
import type { CliOptions } from '../src/cli/index.js';
import type { CoverageReport } from '../src/store/index.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const tracedRecord = (workId: string): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig: 'demo',
    title: `work ${workId}`,
    lifecycle: { created: '2026-06-01T00:00:00Z', status: 'closed' },
    trace: {
      jsonl_path: '/traces/x.jsonl',
      errors: [{ tool: 'tsc', severity: 'error', message: 'TS2345', file: 'a.ts', line: 1 }],
    },
  });

const options = (store: string): CliOptions => ({ json: true, verbose: false, store });

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-coverage-'));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('coverageCommand', () => {
  it('reports store coverage for a built store', () => {
    const path = join(dir, 'store.db');
    buildStoreFromRecords(path, [tracedRecord('b-1'), tracedRecord('b-2')]);

    const report = coverageCommand({ args: [], options: options(path) });

    expect(report.records).toBe(2);
    expect(report.with_trace).toBe(2);
    expect(report.trace_errors).toBe(2);
  });

  it('errors on a missing store rather than returning empty coverage', () => {
    const path = join(dir, 'absent.db');
    expect(() => coverageCommand({ args: [], options: options(path) })).toThrow(/No store/);
  });
});

describe('formatCoverage', () => {
  it('prints per-record axes with a /records denominator and bare counts otherwise', () => {
    const report: CoverageReport = {
      records: 10,
      with_trace: 4,
      trace_errors: 7,
      trace_runs: 3,
      with_base_commit: 2,
      with_commit_sha: 1,
      multi_session: 2,
    };
    const lines = formatCoverage(report);
    expect(lines.some(l => l.includes('with_trace') && l.includes('4/10'))).toBe(true);
    expect(lines.some(l => l.includes('trace_errors') && l.trim().endsWith('7'))).toBe(true);
  });
});

describe('coverageDelta', () => {
  it('subtracts per axis', () => {
    const before: CoverageReport = {
      records: 5,
      with_trace: 0,
      trace_errors: 0,
      trace_runs: 0,
      with_base_commit: 0,
      with_commit_sha: 0,
      multi_session: 0,
    };
    const after: CoverageReport = {
      records: 5,
      with_trace: 3,
      trace_errors: 12,
      trace_runs: 3,
      with_base_commit: 2,
      with_commit_sha: 1,
      multi_session: 1,
    };
    expect(coverageDelta(before, after)).toEqual({
      records: 0,
      with_trace: 3,
      trace_errors: 12,
      trace_runs: 3,
      with_base_commit: 2,
      with_commit_sha: 1,
      multi_session: 1,
    });
  });
});
