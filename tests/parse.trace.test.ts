import { describe, expect, it } from 'vitest';

import { extractErrors, parseTranscript, parseRecordTrace } from '../src/parse/index.js';
import { matchRunner } from '../src/parse/runners.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

/** Build an assistant entry issuing one Bash tool_use. */
function bashCall(id: string, command: string): string {
  return JSON.stringify({
    type: 'assistant',
    message: { content: [{ type: 'tool_use', id, name: 'Bash', input: { command } }] },
  });
}

/** Build a user entry carrying the matching tool_result + toolUseResult. */
function bashResult(
  id: string,
  opts: { stdout?: string; stderr?: string; is_error?: boolean }
): string {
  return JSON.stringify({
    type: 'user',
    message: {
      content: [{ type: 'tool_result', tool_use_id: id, is_error: opts.is_error ?? false }],
    },
    toolUseResult: { stdout: opts.stdout ?? '', stderr: opts.stderr ?? '' },
  });
}

function transcript(...lines: string[]): string {
  return lines.join('\n') + '\n';
}

describe('matchRunner', () => {
  it('names recognized build/test/lint runners', () => {
    expect(matchRunner('tsc --noEmit')).toBe('tsc');
    expect(matchRunner('npx eslint src')).toBe('eslint');
    expect(matchRunner('npm run check')).toBe('npm');
    expect(matchRunner('go test ./...')).toBe('go');
    expect(matchRunner('cargo clippy')).toBe('cargo');
  });

  it('returns null for non-build commands', () => {
    expect(matchRunner('ls -la')).toBeNull();
    expect(matchRunner('git status')).toBeNull();
    expect(matchRunner('cat package.json')).toBeNull();
  });
});

describe('extractErrors', () => {
  it('parses tsc diagnostics in both emitted formats', () => {
    const paren = 'src/a.ts(12,5): error TS2345: Argument of type X.';
    const colon = 'src/b.ts:7:3 - error TS2304: Cannot find name Y.';
    const errors = extractErrors(`${paren}\n${colon}`);
    expect(errors).toHaveLength(2);
    expect(errors[0]).toMatchObject({
      tool: 'tsc',
      severity: 'error',
      file: 'src/a.ts',
      line: 12,
      column: 5,
      message: 'TS2345: Argument of type X.',
    });
    expect(errors[1]).toMatchObject({
      file: 'src/b.ts',
      line: 7,
      message: 'TS2304: Cannot find name Y.',
    });
  });

  it('parses eslint stylish output, attaching each detail to its file header', () => {
    const output = [
      '/repo/src/x.ts',
      '  3:10  error    Unexpected console statement  no-console',
      '  9:1   warning  Missing return type           @typescript-eslint/explicit-function-return-type',
      '',
      '✖ 2 problems (1 error, 1 warning)',
    ].join('\n');
    const errors = extractErrors(output);
    expect(errors).toHaveLength(2);
    expect(errors[0]).toMatchObject({
      tool: 'eslint',
      severity: 'error',
      file: '/repo/src/x.ts',
      line: 3,
      column: 10,
      message: 'Unexpected console statement (no-console)',
    });
    expect(errors[1].severity).toBe('warning');
  });

  it('captures an eslint detail line that has no rule id (parser errors)', () => {
    const output = ['/repo/src/x.ts', '  1:1  error  Parsing error: Unexpected token'].join('\n');
    const errors = extractErrors(output);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatchObject({
      tool: 'eslint',
      file: '/repo/src/x.ts',
      line: 1,
      message: 'Parsing error: Unexpected token',
    });
  });

  it('strips ANSI color before matching tsc and eslint output', () => {
    const ESC = String.fromCharCode(27);
    const tsc = `${ESC}[96msrc/a.ts${ESC}[0m:${ESC}[93m7${ESC}[0m:3 - ${ESC}[91merror${ESC}[0m TS2304: Cannot find name Y.`;
    const errors = extractErrors(tsc);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatchObject({ tool: 'tsc', file: 'src/a.ts', line: 7, column: 3 });
  });

  it('de-duplicates identical errors across formats', () => {
    const dup = 'src/a.ts(1,1): error TS1005: ; expected.';
    expect(extractErrors(`${dup}\n${dup}`)).toHaveLength(1);
  });
});

describe('extractErrors — non-TS toolchains', () => {
  it('parses go build/vet diagnostics, with and without a column', () => {
    const output = ['./pkg/svc.go:42:6: undefined: helper', './main.go:10: missing return'].join(
      '\n'
    );
    const errors = extractErrors(output);
    expect(errors).toHaveLength(2);
    expect(errors[0]).toMatchObject({
      tool: 'go',
      severity: 'error',
      file: './pkg/svc.go',
      line: 42,
      column: 6,
      message: 'undefined: helper',
    });
    expect(errors[1]).toMatchObject({ tool: 'go', file: './main.go', line: 10 });
    expect(errors[1].column).toBeUndefined();
  });

  it('skips go panic stack frames (no `: message` after the line)', () => {
    expect(extractErrors('\tmain.go:12 +0x1d')).toHaveLength(0);
  });

  it('parses mypy diagnostics and keeps the [code] for the error class', () => {
    const output = [
      'app.py:12: error: Name "os" is not defined  [name-defined]',
      'app.py:20:5: error: Incompatible return value type  [return-value]',
      'app.py:20: note: Revealed type is "builtins.int"',
    ].join('\n');
    const errors = extractErrors(output);
    expect(errors).toHaveLength(2); // the note: line is dropped
    expect(errors[0]).toMatchObject({
      tool: 'mypy',
      severity: 'error',
      file: 'app.py',
      line: 12,
      message: 'Name "os" is not defined  [name-defined]',
    });
    expect(errors[0].column).toBeUndefined();
    expect(errors[1]).toMatchObject({ tool: 'mypy', line: 20, column: 5 });
  });

  it('parses ruff diagnostics, preserving the rule code (with or without [*])', () => {
    const output = [
      'app.py:12:5: F401 [*] `os` imported but unused',
      'app.py:30:1: E501 Line too long',
    ].join('\n');
    const errors = extractErrors(output);
    expect(errors).toHaveLength(2);
    expect(errors[0]).toMatchObject({
      tool: 'ruff',
      file: 'app.py',
      line: 12,
      column: 5,
      message: 'F401 `os` imported but unused',
    });
    expect(errors[1].message).toBe('E501 Line too long');
  });

  it('parses cargo/rustc two-line diagnostics, lifting the E-code into the message', () => {
    const output = [
      'error[E0382]: borrow of moved value: `x`',
      '  --> src/main.rs:12:9',
      '',
      'warning: unused variable: `y`',
      '  --> src/lib.rs:3:5',
    ].join('\n');
    const errors = extractErrors(output);
    expect(errors).toHaveLength(2);
    expect(errors[0]).toMatchObject({
      tool: 'cargo',
      severity: 'error',
      file: 'src/main.rs',
      line: 12,
      column: 9,
      message: 'E0382: borrow of moved value: `x`',
    });
    expect(errors[1]).toMatchObject({ tool: 'cargo', severity: 'warning', file: 'src/lib.rs' });
  });

  it('drops a cargo header with no following .rs location', () => {
    const output = ['error: could not compile `mycrate` due to 2 previous errors'].join('\n');
    expect(extractErrors(output)).toHaveLength(0);
  });

  it('does not pair a stray error: header with a later cargo location across a gap', () => {
    // A foreign `error:` summary (e.g. from make/npm) must not reach forward
    // across intervening output to attach itself to a real cargo `.rs` location.
    const output = [
      'error: make target `check` failed',
      '   Compiling foo v0.1.0',
      '   Compiling bar v0.2.0',
      '  --> src/main.rs:5:1',
    ].join('\n');
    expect(extractErrors(output)).toHaveLength(0);
  });

  it('strips ANSI color before matching cargo output', () => {
    const ESC = String.fromCharCode(27);
    const output = [
      `${ESC}[1m${ESC}[31merror[E0277]${ESC}[0m: the trait bound is not satisfied`,
      `  ${ESC}[34m-->${ESC}[0m src/main.rs:5:1`,
    ].join('\n');
    const errors = extractErrors(output);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatchObject({ tool: 'cargo', file: 'src/main.rs', line: 5, column: 1 });
  });

  it('parses pytest FAILED/ERROR summary lines (line 0 by design)', () => {
    const output = [
      'FAILED tests/test_app.py::test_foo - AssertionError: assert 1 == 2',
      'ERROR tests/conftest.py::fixture - ValueError: bad fixture',
      'FAILED tests/test_app.py::test_no_reason',
    ].join('\n');
    const errors = extractErrors(output);
    expect(errors).toHaveLength(2); // the reason-less FAILED carries no class and is skipped
    expect(errors[0]).toMatchObject({
      tool: 'pytest',
      severity: 'error',
      file: 'tests/test_app.py',
      line: 0,
      message: 'AssertionError: assert 1 == 2',
    });
    expect(errors[1]).toMatchObject({ tool: 'pytest', file: 'tests/conftest.py' });
  });

  it('parses gradle javac and kotlinc diagnostics', () => {
    const output = [
      '/work/src/main/java/com/x/Foo.java:12: error: cannot find symbol',
      'e: /work/src/Bar.kt:7:3: unresolved reference: baz',
      'w: /work/src/Bar.kt:9:1: parameter is never used',
    ].join('\n');
    const errors = extractErrors(output);
    expect(errors).toHaveLength(3);
    expect(errors).toContainEqual(
      expect.objectContaining({
        tool: 'gradle',
        severity: 'error',
        file: '/work/src/main/java/com/x/Foo.java',
        line: 12,
        message: 'cannot find symbol',
      })
    );
    expect(errors).toContainEqual(
      expect.objectContaining({
        tool: 'gradle',
        severity: 'error',
        file: '/work/src/Bar.kt',
        line: 7,
        column: 3,
      })
    );
    expect(errors).toContainEqual(
      expect.objectContaining({ tool: 'gradle', severity: 'warning', line: 9 })
    );
  });

  it('attributes each line of interleaved polyglot output to exactly one tool', () => {
    const output = [
      'src/a.ts(1,1): error TS1005: ; expected.',
      './pkg/svc.go:42:6: undefined: helper',
      'app.py:12: error: Name "os" is not defined  [name-defined]',
      'app.py:30:1: E501 Line too long',
      'error[E0382]: borrow of moved value: `x`',
      '  --> src/main.rs:12:9',
      'FAILED tests/test_app.py::test_foo - AssertionError: boom',
      '/work/Foo.java:5: error: cannot find symbol',
      'e: /work/Bar.kt:7:3: unresolved reference: baz',
    ].join('\n');
    const byTool = extractErrors(output)
      .map(e => e.tool)
      .sort();
    expect(byTool).toEqual(['cargo', 'go', 'gradle', 'gradle', 'mypy', 'pytest', 'ruff', 'tsc']);
  });
});

describe('parseTranscript', () => {
  it('captures a failing tsc execution with its errors', () => {
    const text = transcript(
      bashCall('t1', 'tsc --noEmit'),
      bashResult('t1', { stdout: 'src/a.ts(2,3): error TS2345: bad.', is_error: true })
    );
    const { tool_outcomes, errors } = parseTranscript(text);
    expect(tool_outcomes).toHaveLength(1);
    expect(tool_outcomes[0]).toMatchObject({ runner: 'tsc', status: 'fail' });
    expect(tool_outcomes[0].errors).toHaveLength(1);
    expect(errors).toHaveLength(1);
    expect(errors[0].file).toBe('src/a.ts');
  });

  it('marks a clean run as pass with no errors', () => {
    const text = transcript(
      bashCall('t1', 'npm test'),
      bashResult('t1', { stdout: 'All tests passed', is_error: false })
    );
    const { tool_outcomes, errors } = parseTranscript(text);
    expect(tool_outcomes[0].status).toBe('pass');
    expect(errors).toHaveLength(0);
  });

  it('treats a masked exit (is_error false) as fail when output has errors', () => {
    const text = transcript(
      bashCall('t1', 'tsc --noEmit || true'),
      bashResult('t1', { stdout: 'src/a.ts(2,3): error TS2345: bad.', is_error: false })
    );
    const { tool_outcomes } = parseTranscript(text);
    expect(tool_outcomes[0].status).toBe('fail');
  });

  it('ignores non-build Bash calls and non-Bash tools', () => {
    const text = transcript(
      bashCall('t1', 'ls -la'),
      bashResult('t1', { stdout: 'a\nb', is_error: false }),
      JSON.stringify({
        type: 'assistant',
        message: { content: [{ type: 'tool_use', id: 't2', name: 'Read', input: {} }] },
      }),
      JSON.stringify({
        type: 'user',
        message: { content: [{ type: 'tool_result', tool_use_id: 't2', is_error: false }] },
      })
    );
    expect(parseTranscript(text).tool_outcomes).toHaveLength(0);
  });

  it('unions errors from a wrapper command (tsc + eslint) and de-dupes', () => {
    const combined = [
      'src/a.ts(2,3): error TS2345: bad.',
      '/repo/src/b.ts',
      '  1:1  error  Unexpected var  no-var',
    ].join('\n');
    const text = transcript(
      bashCall('t1', 'npm run check'),
      bashResult('t1', { stdout: combined, is_error: true })
    );
    const { errors } = parseTranscript(text);
    expect(errors.map(e => e.tool).sort()).toEqual(['eslint', 'tsc']);
  });

  it('reads error output from a string tool_result content block', () => {
    const text = transcript(
      bashCall('t1', 'tsc'),
      JSON.stringify({
        type: 'user',
        message: {
          content: [
            {
              type: 'tool_result',
              tool_use_id: 't1',
              is_error: true,
              content: 'src/a.ts(1,1): error TS1005: x.',
            },
          ],
        },
      })
    );
    expect(parseTranscript(text).errors).toHaveLength(1);
  });

  it('skips malformed lines without dropping later executions', () => {
    const text =
      '{ not json\n' + transcript(bashCall('t1', 'tsc'), bashResult('t1', { stdout: 'ok' }));
    expect(parseTranscript(text).tool_outcomes).toHaveLength(1);
  });
});

describe('parseRecordTrace', () => {
  const record = (overrides: Partial<WorkRecord> = {}): WorkRecord =>
    WorkRecordSchema.parse({
      work_id: 'mem-1',
      rig: 'mem',
      title: 't',
      lifecycle: { created: '2026-06-04T00:00:00Z', status: 'closed' },
      ...overrides,
    });

  it('populates trace.tool_outcomes and trace.errors immutably', () => {
    const input = record({ trace: { jsonl_path: '/t.jsonl' } });
    const text = transcript(
      bashCall('t1', 'tsc'),
      bashResult('t1', { stdout: 'src/a.ts(2,3): error TS2345: bad.', is_error: true })
    );
    const out = parseRecordTrace(input, () => text);
    expect(out.trace?.tool_outcomes).toHaveLength(1);
    expect(out.trace?.errors).toHaveLength(1);
    expect(input.trace?.tool_outcomes).toBeUndefined();
    expect(out).not.toBe(input);
  });

  it('returns the record unchanged when it has no resolved trace', () => {
    const input = record();
    expect(parseRecordTrace(input, () => 'unused')).toBe(input);
  });

  it('leaves parsed fields absent when the transcript was reaped (ENOENT)', () => {
    const input = record({ trace: { jsonl_path: '/gone.jsonl' } });
    const reader = (): string => {
      throw Object.assign(new Error('missing'), { code: 'ENOENT' });
    };
    const out = parseRecordTrace(input, reader);
    expect(out.trace?.tool_outcomes).toBeUndefined();
  });

  it('propagates non-ENOENT read errors', () => {
    const input = record({ trace: { jsonl_path: '/x.jsonl' } });
    const reader = (): string => {
      throw Object.assign(new Error('denied'), { code: 'EACCES' });
    };
    expect(() => parseRecordTrace(input, reader)).toThrow('denied');
  });
});
