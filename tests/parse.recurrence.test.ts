import { describe, expect, it } from 'vitest';

import {
  computeRecurrence,
  errorClass,
  failureSignature,
  recurrenceFromRecords,
  type FailureTrace,
} from '../src/parse/index.js';
import type { TraceError } from '../src/schemas/trace.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const tsError = (overrides: Partial<TraceError> = {}): TraceError => ({
  tool: 'tsc',
  severity: 'error',
  message: 'TS2345: bad argument',
  file: 'src/a.ts',
  line: 12,
  column: 5,
  ...overrides,
});

describe('errorClass', () => {
  it('extracts an explicit diagnostic code', () => {
    expect(errorClass(tsError())).toBe('TS2345');
  });

  it('extracts a trailing eslint rule id', () => {
    expect(
      errorClass(tsError({ tool: 'eslint', message: 'Unexpected console (no-console)' }))
    ).toBe('no-console');
  });

  it('falls back to a digit-normalized message prefix', () => {
    const a = errorClass(tsError({ message: 'Process exited with code 12' }));
    const b = errorClass(tsError({ message: 'Process exited with code 99' }));
    expect(a).toBe(b); // digits collapsed to '#', so different exit codes share a class
  });

  it('lifts each non-TS toolchain code from its native message position', () => {
    expect(
      errorClass(tsError({ tool: 'mypy', message: 'Incompatible return value [return-value]' }))
    ).toBe('return-value');
    expect(errorClass(tsError({ tool: 'ruff', message: 'F401 `os` imported but unused' }))).toBe(
      'F401'
    );
    expect(
      errorClass(tsError({ tool: 'cargo', message: 'E0382: borrow of moved value: `x`' }))
    ).toBe('E0382');
    expect(errorClass(tsError({ tool: 'pytest', message: 'AssertionError: assert 1 == 2' }))).toBe(
      'AssertionError'
    );
  });

  it('is tool-gated: a codeless tool never lifts an incidental trailing paren', () => {
    // The go message ends in `)`; a tool-blind `(...)$` rule would wrongly key on
    // `int`. go has no code entry, so it must fall back to the normalized message.
    expect(
      errorClass(tsError({ tool: 'go', message: 'not enough arguments (have (), want (int))' }))
    ).toBe('not enough arguments (have (), want (int))');
  });

  it('is tool-gated: a TS-shaped token in another tool does not read as a tsc code', () => {
    expect(errorClass(tsError({ tool: 'go', message: 'cannot find TS2345 in scope' }))).not.toBe(
      'TS2345'
    );
  });
});

describe('failureSignature', () => {
  it('keys on tool:file:line:error-class and normalizes the path', () => {
    expect(failureSignature(tsError({ file: './src/a.ts' }))).toBe('tsc:src/a.ts:12:TS2345');
  });

  it('separates different lines and tools', () => {
    expect(failureSignature(tsError({ line: 1 }))).not.toBe(failureSignature(tsError({ line: 2 })));
  });

  it('builds a canonical signature for each non-TS toolchain', () => {
    expect(
      failureSignature({
        tool: 'go',
        severity: 'error',
        message: 'undefined: helper',
        file: './pkg/svc.go',
        line: 42,
      })
    ).toBe('go:pkg/svc.go:42:undefined: helper');
    expect(
      failureSignature({
        tool: 'pytest',
        severity: 'error',
        message: 'AssertionError: assert 1 == 2',
        file: 'tests/test_app.py',
        line: 0,
      })
    ).toBe('pytest:tests/test_app.py:0:AssertionError');
  });
});

describe('computeRecurrence', () => {
  it('scores confidence as unique-traces / total', () => {
    const shared = tsError();
    const traces: FailureTrace[] = [
      { trace_id: 'a', errors: [shared] },
      { trace_id: 'b', errors: [shared] },
      { trace_id: 'c', errors: [tsError({ line: 99, message: 'TS9999: other' })] },
    ];
    const insights = computeRecurrence(traces);
    const top = insights[0];
    expect(top.signature).toBe('tsc:src/a.ts:12:TS2345');
    expect(top.trace_count).toBe(2);
    expect(top.confidence).toBeCloseTo(2 / 3);
    expect(top.trace_ids).toEqual(['a', 'b']);
  });

  it('counts repeats within one trace as frequency but not trace_count', () => {
    const e = tsError();
    const insights = computeRecurrence([{ trace_id: 'a', errors: [e, e, e] }]);
    expect(insights[0].frequency).toBe(3);
    expect(insights[0].trace_count).toBe(1);
    expect(insights[0].confidence).toBe(1);
  });

  it('returns [] for no traces', () => {
    expect(computeRecurrence([])).toEqual([]);
  });

  it('applies the minConfidence filter', () => {
    const traces: FailureTrace[] = [
      { trace_id: 'a', errors: [tsError()] },
      { trace_id: 'b', errors: [tsError({ line: 2, message: 'TS1: rare' })] },
    ];
    // Each signature appears in 1/2 traces → confidence 0.5.
    expect(computeRecurrence(traces, { minConfidence: 0.6 })).toHaveLength(0);
    expect(computeRecurrence(traces, { minConfidence: 0.5 })).toHaveLength(2);
  });

  it('ranks by confidence, then frequency, then signature', () => {
    const traces: FailureTrace[] = [
      { trace_id: 'a', errors: [tsError(), tsError({ line: 50, message: 'TS50: x' })] },
      { trace_id: 'b', errors: [tsError()] },
    ];
    const [first] = computeRecurrence(traces);
    expect(first.signature).toBe('tsc:src/a.ts:12:TS2345'); // confidence 1 beats 0.5
  });
});

describe('recurrenceFromRecords', () => {
  const record = (work_id: string, errors: TraceError[]): WorkRecord =>
    WorkRecordSchema.parse({
      work_id,
      rig: 'mem',
      title: 't',
      lifecycle: { created: '2026-06-04T00:00:00Z', status: 'closed' },
      trace: { jsonl_path: `/${work_id}.jsonl`, errors },
    });

  it('selects only error-bearing records and computes over them', () => {
    const records: WorkRecord[] = [
      record('a', [tsError()]),
      record('b', [tsError()]),
      record('c', []), // no errors → excluded from denominator
    ];
    const insights = recurrenceFromRecords(records);
    expect(insights[0].trace_count).toBe(2);
    expect(insights[0].confidence).toBe(1); // 2 of 2 error-bearing records
  });

  it('returns [] when no record has errors', () => {
    expect(recurrenceFromRecords([record('a', [])])).toEqual([]);
  });
});
