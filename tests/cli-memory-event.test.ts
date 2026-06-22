import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { CliOptions, CommandContext } from '../src/cli/index.js';
import {
  memoryEventCommand,
  type CaptureResult,
  type RecordResult,
} from '../src/cli/commands/memory-event.js';
import { exportMemoryEventsCommand } from '../src/cli/commands/export-memory-events.js';
import {
  importMemoryEventsCommand,
  parseMemoryEventLines,
} from '../src/cli/commands/import-memory-events.js';

function ctx(storePath: string, args: string[], options: Partial<CliOptions> = {}): CommandContext {
  return { args, options: { json: true, verbose: false, store: storePath, ...options } };
}

/** Feed a string to the next stdin read (the capture/import commands read it). */
function withStdin<T>(input: string, fn: () => Promise<T>): Promise<T> {
  const original = process.stdin;
  const fake = Readable.from([Buffer.from(input)]) as unknown as typeof process.stdin;
  Object.defineProperty(fake, 'isTTY', { value: false });
  Object.defineProperty(process, 'stdin', { value: fake, configurable: true });
  return fn().finally(() => {
    Object.defineProperty(process, 'stdin', { value: original, configurable: true });
  });
}

let dir: string;
let storePath: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-cli-memevent-'));
  storePath = join(dir, 'store.db');
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('mem memory-event CLI (mem-31kz)', () => {
  it('record appends one event from explicit flags', async () => {
    const result = (await memoryEventCommand(
      ctx(storePath, ['record'], {
        session: 'sess-1',
        op: 'read',
        ref: '/home/ds/brains/x.md',
        'work-id': 'mem-31kz',
        now: '2026-06-21T10:00:00Z',
      })
    )) as RecordResult;
    expect(result.recorded).toBe(1);

    const log = (await memoryEventCommand(ctx(storePath, ['log'], { 'work-id': 'mem-31kz' }))) as {
      events: { op: string }[];
    };
    expect(log.events.map(e => e.op)).toEqual(['read']);
  });

  it('record materializes a fresh store (capture must not need a built corpus)', async () => {
    const fresh = join(dir, 'nested', 'fresh.db');
    const result = (await memoryEventCommand(
      ctx(fresh, ['record'], { session: 's', op: 'write', ref: '/repo/MEMORY.md' })
    )) as RecordResult;
    expect(result.recorded).toBe(1);
  });

  it('capture projects a PostToolUse payload from stdin', async () => {
    const payload = JSON.stringify({
      tool_name: 'Read',
      tool_input: { file_path: '/home/ds/brains/lesson.md' },
      session_id: 'sess-1',
      cwd: '/repo',
    });
    const result = (await withStdin(payload, () =>
      memoryEventCommand(ctx(storePath, ['capture'], { now: '2026-06-21T10:00:00Z' }))
    )) as CaptureResult;
    expect(result.recorded).toBe(1);
    expect(result.event_id).toContain(':read:/home/ds/brains/lesson.md');
  });

  it('capture no-ops on a non-memory op without throwing', async () => {
    const payload = JSON.stringify({
      tool_name: 'Bash',
      tool_input: { command: 'ls' },
      session_id: 's',
    });
    const result = (await withStdin(payload, () =>
      memoryEventCommand(ctx(storePath, ['capture']))
    )) as CaptureResult;
    expect(result).toEqual({ recorded: 0, reason: 'not-a-memory-op' });
  });

  it('capture no-ops on empty / invalid input', async () => {
    expect(
      (await withStdin('', () => memoryEventCommand(ctx(storePath, ['capture'])))) as CaptureResult
    ).toEqual({ recorded: 0, reason: 'empty-input' });
    expect(
      (await withStdin('{not json', () =>
        memoryEventCommand(ctx(storePath, ['capture']))
      )) as CaptureResult
    ).toEqual({ recorded: 0, reason: 'invalid-json' });
  });

  it('rejects an unknown subcommand and a record with no session', async () => {
    await expect(memoryEventCommand(ctx(storePath, ['bogus']))).rejects.toThrow(/usage/);
    await expect(memoryEventCommand(ctx(storePath, ['record'], { op: 'read' }))).rejects.toThrow(
      /requires --session/
    );
  });

  it('export -> import round-trips through NDJSON', async () => {
    await memoryEventCommand(
      ctx(storePath, ['record'], {
        session: 'sess-1',
        op: 'read',
        ref: '/repo/MEMORY.md',
        now: '2026-06-21T10:00:00Z',
      })
    );
    const exported = exportMemoryEventsCommand(ctx(storePath, []));
    expect(exported.count).toBe(1);

    const ndjson = exported.events.map(e => JSON.stringify(e)).join('\n');
    expect(parseMemoryEventLines(ndjson)).toHaveLength(1);

    const dest = join(dir, 'dest.db');
    // seed the dest store so withWriteStore (read-store rule) finds it
    await memoryEventCommand(
      ctx(dest, ['record'], { session: 'seed', op: 'read', ref: '/repo/MEMORY.md' })
    );
    const result = await withStdin(ndjson, () => importMemoryEventsCommand(ctx(dest, [])));
    expect(result).toEqual({ appended: 1, skipped: 0 });
  });
});
