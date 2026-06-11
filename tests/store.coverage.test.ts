import { describe, expect, it } from 'vitest';

import { coverageReport, openStore, writeRecords } from '../src/store/index.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const spineRecord = (workId: string): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig: 'demo',
    title: `work ${workId}`,
    lifecycle: { created: '2026-06-01T00:00:00Z', status: 'open' },
  });

/** A record carrying every coverage axis: resolved transcript (trace_path),
 * a parsed failure (trace_errors), a run row (trace_runs), a git baseline
 * (base_commit) and a GitHub outcome SHA (commit_sha). */
const coveredRecord = (workId: string): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig: 'demo',
    title: `work ${workId}`,
    lifecycle: { created: '2026-06-01T00:00:00Z', status: 'closed' },
    agents: [{ agent_id: 'gc-1', trace_ref: '/traces/x.jsonl' }],
    trace: {
      jsonl_path: '/traces/x.jsonl',
      errors: [{ tool: 'tsc', severity: 'error', message: 'TS2345', file: 'a.ts', line: 1 }],
      run: {
        session_uuid: `sess-${workId}`,
        input_tokens: 10,
        output_tokens: 20,
        cache_creation_tokens: 0,
        cache_read_tokens: 0,
        n_tool_calls: 3,
        tool_calls_by_type: { Bash: 3 },
        n_turns: 5,
      },
    },
    outcome: { commit_sha: 'abc123' },
    provenance: {
      work_dir: '/w/demo',
      repo: 'owner/demo',
      base_branch: 'main',
      base_commit: 'a'.repeat(40),
      history_state: 'commit-by-date',
    },
  });

describe('coverageReport', () => {
  it('reports all zeros for an empty store', () => {
    const db = openStore(':memory:');
    expect(coverageReport(db)).toEqual({
      records: 0,
      with_trace: 0,
      trace_errors: 0,
      trace_runs: 0,
      with_base_commit: 0,
      with_commit_sha: 0,
      multi_session: 0,
      with_task_type: 0,
    });
    db.close();
  });

  it('counts only the axes each record populates', () => {
    const db = openStore(':memory:');
    writeRecords(db, [spineRecord('b-1'), coveredRecord('b-2')]);

    expect(coverageReport(db)).toEqual({
      records: 2,
      with_trace: 1,
      trace_errors: 1,
      trace_runs: 1,
      with_base_commit: 1,
      with_commit_sha: 1,
      multi_session: 0,
      with_task_type: 0,
    });
    db.close();
  });

  it('is stable across an idempotent re-write (counts do not double)', () => {
    const db = openStore(':memory:');
    writeRecords(db, [coveredRecord('b-1')]);
    writeRecords(db, [coveredRecord('b-1')]);

    const report = coverageReport(db);
    expect(report.records).toBe(1);
    expect(report.trace_errors).toBe(1);
    expect(report.trace_runs).toBe(1);
    db.close();
  });
});
