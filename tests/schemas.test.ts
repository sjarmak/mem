import { describe, it, expect } from 'vitest';
import { TraceErrorSchema, ExecutionSchema } from '../src/schemas/trace.js';
import { WorkRecordSchema } from '../src/schemas/workrecord.js';

describe('TraceErrorSchema', () => {
  const valid = {
    tool: 'tsc',
    severity: 'error',
    message: "Type 'string' is not assignable to type 'number'",
    file: 'src/cli/index.ts',
    line: 42,
  };

  it('accepts a valid trace error', () => {
    expect(TraceErrorSchema.parse(valid)).toMatchObject(valid);
  });

  it('accepts an optional column', () => {
    expect(TraceErrorSchema.parse({ ...valid, column: 7 }).column).toBe(7);
  });

  it('rejects an unknown severity', () => {
    expect(() => TraceErrorSchema.parse({ ...valid, severity: 'fatal' })).toThrow();
  });

  it('rejects a missing file', () => {
    const { file: _file, ...rest } = valid;
    expect(() => TraceErrorSchema.parse(rest)).toThrow();
  });
});

describe('ExecutionSchema', () => {
  it('accepts a passing execution with no errors', () => {
    const exec = { runner: 'vitest', command: 'npm test', status: 'pass', errors: [] };
    expect(ExecutionSchema.parse(exec)).toEqual(exec);
  });

  it('rejects an unknown status', () => {
    expect(() =>
      ExecutionSchema.parse({ runner: 'vitest', command: 'npm test', status: 'flaky', errors: [] })
    ).toThrow();
  });
});

describe('WorkRecordSchema', () => {
  const minimal = {
    work_id: 'gascity-dashboard-tnqw',
    rig: 'gascity-dashboard',
    title: 'Fix the dashboard alert DTO',
    lifecycle: { created: '2026-06-04T20:00:00Z', status: 'closed' },
  };

  it('accepts a minimal record and applies defaults', () => {
    const rec = WorkRecordSchema.parse(minimal);
    expect(rec.work_id).toBe('gascity-dashboard-tnqw');
    expect(rec.labels).toEqual([]);
    expect(rec.metadata).toEqual({});
    expect(rec.agents).toEqual([]);
    expect(rec.links).toEqual({ deps: [], supersedes: [] });
    expect(rec.trace).toBeUndefined();
    expect(rec.outcome).toBeUndefined();
  });

  it('accepts a full record with agents, trace, outcome, and signal', () => {
    const rec = WorkRecordSchema.parse({
      ...minimal,
      labels: ['phase1'],
      priority: 1,
      lifecycle: {
        created: '2026-06-04T20:00:00Z',
        started: '2026-06-04T20:10:00Z',
        closed: '2026-06-04T21:00:00Z',
        status: 'closed',
        status_history: [{ status: 'in_progress', at: '2026-06-04T20:10:00Z' }],
      },
      agents: [
        {
          agent_id: 'gc-339244',
          role: 'claude-2',
          account: 'account4',
          trace_ref: '/traces/gc-339244.jsonl',
        },
      ],
      trace: {
        jsonl_path: '/traces/gc-339244.jsonl',
        n_turns: 12,
        tool_outcomes: [
          {
            runner: 'tsc',
            command: 'npm run typecheck',
            status: 'fail',
            errors: [
              {
                tool: 'tsc',
                severity: 'error',
                message: 'TS2322',
                file: 'src/a.ts',
                line: 3,
              },
            ],
          },
        ],
        errors: [{ tool: 'tsc', severity: 'error', message: 'TS2322', file: 'src/a.ts', line: 3 }],
      },
      outcome: { pr: '#63', pr_state: 'merged', commit_sha: 'abc1234', ci: 'pass' },
      signal: { deterministic: { recurring: [] }, semantic: {} },
      links: { deps: ['mem-sxe'], convoy_id: 'cv-1', supersedes: [] },
    });
    expect(rec.agents[0].agent_id).toBe('gc-339244');
    expect(rec.trace?.tool_outcomes?.[0].status).toBe('fail');
    expect(rec.outcome?.pr_state).toBe('merged');
    expect(rec.links.deps).toEqual(['mem-sxe']);
  });

  it('rejects a record without a work_id', () => {
    const { work_id: _id, ...rest } = minimal;
    expect(() => WorkRecordSchema.parse(rest)).toThrow();
  });

  it('rejects an empty work_id', () => {
    expect(() => WorkRecordSchema.parse({ ...minimal, work_id: '' })).toThrow();
  });

  it('rejects an unknown ci value', () => {
    expect(() => WorkRecordSchema.parse({ ...minimal, outcome: { ci: 'maybe' } })).toThrow();
  });

  it('does not share defaulted links arrays across records', () => {
    const a = WorkRecordSchema.parse(minimal);
    const b = WorkRecordSchema.parse(minimal);
    expect(a.links.deps).not.toBe(b.links.deps);
    expect(a.links.supersedes).not.toBe(b.links.supersedes);
  });

  it('rejects an unknown pr_state', () => {
    expect(() =>
      WorkRecordSchema.parse({ ...minimal, outcome: { pr: '#1', pr_state: 'draft' } })
    ).toThrow();
  });
});
