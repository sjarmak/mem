import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Readable } from 'node:stream';
import { describe, it, expect, afterEach, vi } from 'vitest';

import { mineReachesCommand, type MineReachesResult } from '../src/cli/commands/mine-reaches.js';
import { scanContextReaches } from '../src/parse/index.js';
import type { CommandContext } from '../src/cli/index.js';

const FIXTURE_DIR = new URL('fixtures/context-reach/', import.meta.url);
const input = readFileSync(new URL('synthetic.input.jsonl', FIXTURE_DIR), 'utf8');
const expected = JSON.parse(
  readFileSync(new URL('synthetic.expected.json', FIXTURE_DIR), 'utf8')
) as MineReachesResult;

const ctx = (options: Record<string, string | boolean>): CommandContext => ({
  args: [],
  options: { json: true, verbose: false, ...options },
});

describe('mineReachesCommand — golden fixture', () => {
  let dir: string | undefined;
  const realStdin = process.stdin;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
    Object.defineProperty(process, 'stdin', { value: realStdin, configurable: true });
    vi.restoreAllMocks();
  });

  const write = (text: string): string => {
    dir = mkdtempSync(join(tmpdir(), 'mem-reaches-'));
    const path = join(dir, 'transcript.jsonl');
    writeFileSync(path, text);
    return path;
  };

  it('reproduces the golden result for the synthetic transcript', async () => {
    const result = await mineReachesCommand(ctx({ file: write(input) }));
    expect(result).toEqual(expected);
  });

  it('agrees with the pure scan it wraps', async () => {
    const result = await mineReachesCommand(ctx({ file: write(input) }));
    const scan = scanContextReaches(input);
    expect(result.reaches).toEqual(scan.reaches);
    expect(result.entries_parsed).toBe(scan.entries_parsed);
    expect(result.reach_count).toBe(scan.reaches.length);
  });

  it('excludes the build command and the Write call the fixture also contains', async () => {
    const result = await mineReachesCommand(ctx({ file: write(input) }));
    expect(result.by_tool).toEqual({ Read: 1, Glob: 1, Grep: 1, Bash: 4 });
    expect(result.reaches.map(r => r.target)).not.toContain('npm run check');
    expect(result.reaches.map(r => r.tool)).not.toContain('Write');
  });

  it('excludes every adversarial Bash call the fixture stages', async () => {
    const result = await mineReachesCommand(ctx({ file: write(input) }));
    const ids = result.reaches.map(r => r.tool_use_id);
    // Each of these quotes or pipes a read command while writing, mutating, or
    // running a stage the rule table cannot vouch for.
    for (const id of [
      'call_bash_heredoc',
      'call_bash_redirect',
      'call_bash_pipe_mutate',
      'call_bash_commit_msg',
      'call_bash_subst_arg',
      'call_bash_build_pipe',
      'call_bash_append',
      'call_bash_tee',
      'call_bash_backtick',
    ]) {
      expect(ids).not.toContain(id);
    }
  });

  it('keeps the genuine reads the adversarial turns are mixed with', async () => {
    const result = await mineReachesCommand(ctx({ file: write(input) }));
    expect(result.reaches.map(r => r.tool_use_id)).toEqual([
      'call_read_1',
      'call_glob_1',
      'call_grep_1',
      'call_bash_1',
      'call_bash_3',
      'call_bash_pipe_read',
      'call_bash_cd_read',
    ]);
  });

  it('drops the fixture call whose result never arrived', async () => {
    const result = await mineReachesCommand(ctx({ file: write(input) }));
    expect(result.reaches.map(r => r.tool_use_id)).not.toContain('call_read_2');
  });

  it('reads piped stdin when --file is absent', async () => {
    Object.defineProperty(process, 'stdin', {
      value: Readable.from([Buffer.from(input)]),
      configurable: true,
    });
    const result = await mineReachesCommand(ctx({}));
    expect(result).toEqual(expected);
  });

  it('throws a clear error when --file is given without a value', async () => {
    await expect(mineReachesCommand(ctx({ file: true }))).rejects.toThrow(/--file requires/);
  });

  it('errors — never returns an empty result — when nothing parses as a transcript', async () => {
    const path = write('this file is a raw build log, not transcript JSONL\n');
    await expect(mineReachesCommand(ctx({ file: path }))).rejects.toThrow(
      /no transcript entries parsed/
    );
  });

  it('names the source in the empty-input error', async () => {
    const path = write('not jsonl\n');
    await expect(mineReachesCommand(ctx({ file: path }))).rejects.toThrow(path);
  });

  it('still returns a result for a real transcript that reached for nothing', async () => {
    const path = write(`${JSON.stringify({ type: 'user', message: { content: 'hello' } })}\n`);
    const result = await mineReachesCommand(ctx({ file: path }));
    expect(result).toEqual({ entries_parsed: 1, reach_count: 0, by_tool: {}, reaches: [] });
  });

  it('propagates a missing --file rather than masking it as empty input', async () => {
    await expect(
      mineReachesCommand(ctx({ file: join(tmpdir(), 'mem-reaches-no-such-file.jsonl') }))
    ).rejects.toThrow(/ENOENT/);
  });

  it('prints per-reach lines, a per-tool tally and a count to stderr in non-json mode', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const result = await mineReachesCommand(ctx({ file: write(input), json: false }));
    expect(result).toEqual(expected);
    // one line per reach + one per tool + the trailing summary line.
    expect(errSpy).toHaveBeenCalledTimes(
      result.reaches.length + Object.keys(result.by_tool).length + 1
    );
  });
});
