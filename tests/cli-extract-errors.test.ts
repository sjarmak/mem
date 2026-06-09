import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Readable } from 'node:stream';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { extractErrorRows, extractErrorsCommand } from '../src/cli/commands/extract-errors.js';
import type { CommandContext } from '../src/cli/index.js';

const FIXTURE_DIR = new URL('fixtures/extract-errors/', import.meta.url);
const input = readFileSync(new URL('polyglot.input.txt', FIXTURE_DIR), 'utf8');
const expected = JSON.parse(
  readFileSync(new URL('polyglot.expected.json', FIXTURE_DIR), 'utf8')
) as unknown[];

const ctx = (options: Record<string, string | boolean>): CommandContext => ({
  args: [],
  options: { json: true, verbose: false, ...options },
});

describe('extractErrorRows — store-projection parity', () => {
  it('reproduces the golden store projection byte-for-byte', () => {
    expect(extractErrorRows(input)).toEqual(expected);
  });

  it('emits the NORMALIZED file, not the raw extractor path (architect C1)', () => {
    // The go error printed `./pkg/svc.go`; the held-out side persists the
    // normalized `pkg/svc.go`. Fresh extraction must match or path_reached breaks.
    const go = extractErrorRows(input).find(r => r.tool === 'go');
    expect(go?.file).toBe('pkg/svc.go');
    expect(go?.signature).toBe('go:pkg/svc.go:42:undefined: helper');
  });

  it('emits the canonical tsc signature', () => {
    const tsc = extractErrorRows(input).find(r => r.tool === 'tsc');
    expect(tsc).toMatchObject({
      file: 'src/a.ts',
      line: 12,
      column: 5,
      error_class: 'TS2345',
      signature: 'tsc:src/a.ts:12:TS2345',
    });
  });

  it('serializes pytest line 0 as the number 0, never null (architect C2)', () => {
    const pytest = extractErrorRows(input).find(r => r.tool === 'pytest');
    expect(pytest?.line).toBe(0);
    expect(Number.isInteger(pytest?.line)).toBe(true);
  });

  it('omits column as null when the tool gives none', () => {
    const mypy = extractErrorRows(input).find(r => r.tool === 'mypy');
    expect(mypy?.column).toBeNull();
  });

  it('returns an empty list for output with no parseable errors', () => {
    expect(extractErrorRows('just some narrative text, no diagnostics\n')).toEqual([]);
  });

  it('every row carries exactly the TraceErrorRef.from_mapping keys', () => {
    for (const row of extractErrorRows(input)) {
      expect(row).toHaveProperty('tool');
      expect(row).toHaveProperty('file');
      expect(typeof row.line).toBe('number');
      expect(row).toHaveProperty('error_class');
      expect(row).toHaveProperty('signature');
    }
  });
});

describe('extractErrorsCommand', () => {
  let dir: string | undefined;
  const realStdin = process.stdin;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
    Object.defineProperty(process, 'stdin', { value: realStdin, configurable: true });
    vi.restoreAllMocks();
  });

  it('reads --file and returns { errors } matching the pure function', async () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-extract-'));
    const path = join(dir, 'out.txt');
    writeFileSync(path, input);
    const result = await extractErrorsCommand(ctx({ file: path }));
    expect(result.errors).toEqual(extractErrorRows(input));
  });

  it('reads piped stdin when --file is absent', async () => {
    // Real process.stdin yields Buffer chunks (binary mode); mirror that.
    Object.defineProperty(process, 'stdin', {
      value: Readable.from([Buffer.from(input)]),
      configurable: true,
    });
    const result = await extractErrorsCommand(ctx({}));
    expect(result.errors).toEqual(extractErrorRows(input));
  });

  it('throws a clear error when --file is given without a value', async () => {
    await expect(extractErrorsCommand(ctx({ file: true }))).rejects.toThrow(/--file requires/);
  });

  it('prints human lines to stderr in non-json mode and still returns the rows', async () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-extract-'));
    const path = join(dir, 'out.txt');
    writeFileSync(path, input);
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const result = await extractErrorsCommand(ctx({ file: path, json: false }));
    expect(result.errors).toEqual(extractErrorRows(input));
    // one line per error + the trailing count line.
    expect(errSpy).toHaveBeenCalledTimes(result.errors.length + 1);
  });
});
