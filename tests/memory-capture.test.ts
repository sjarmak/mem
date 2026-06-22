import { describe, expect, it } from 'vitest';

import {
  buildCaptureEvent,
  classifyMemoryOp,
  isMemoryPath,
  memoryDirsFromEnv,
  workIdFromEnv,
} from '../src/ingest/memory-capture.js';

const NOW = '2026-06-21T10:00:00Z';

describe('memory-capture mechanism (mem-31kz)', () => {
  it('classifies concrete tools into normalized ops, null for non-memory tools', () => {
    expect(classifyMemoryOp('Read')).toBe('read');
    expect(classifyMemoryOp('Write')).toBe('write');
    expect(classifyMemoryOp('Edit')).toBe('update');
    expect(classifyMemoryOp('Grep')).toBe('search');
    expect(classifyMemoryOp('Bash')).toBeNull();
  });

  it('detects memory paths via structural defaults', () => {
    const env = {} as NodeJS.ProcessEnv;
    expect(isMemoryPath('/home/ds/.claude/projects/x/memory/foo.md', env)).toBe(true);
    expect(isMemoryPath('/home/ds/brains/note.md', env)).toBe(true);
    expect(isMemoryPath('/repo/MEMORY.md', env)).toBe(true);
    expect(isMemoryPath('/repo/src/index.ts', env)).toBe(false);
  });

  it('honors MEM_MEMORY_DIRS override (config beats structural default)', () => {
    const env = { MEM_MEMORY_DIRS: '/custom/mem/:/other/' } as NodeJS.ProcessEnv;
    expect(memoryDirsFromEnv(env)).toEqual(['/custom/mem/', '/other/']);
    expect(isMemoryPath('/custom/mem/a.md', env)).toBe(true);
    // structural default no longer applies once an explicit list is configured
    expect(isMemoryPath('/home/ds/brains/note.md', env)).toBe(false);
  });

  it('reads work_id from the harness env, best-effort', () => {
    expect(workIdFromEnv({ MEM_WORK_ID: 'mem-31kz' })).toBe('mem-31kz');
    expect(workIdFromEnv({ GC_BEAD_ID: 'mem-99' })).toBe('mem-99');
    expect(workIdFromEnv({})).toBeUndefined();
  });

  it('projects a memory Read into a leak-safe MemoryEvent', () => {
    const event = buildCaptureEvent(
      {
        tool_name: 'Read',
        tool_input: { file_path: '/home/ds/brains/lesson.md' },
        session_id: 'sess-1',
        cwd: '/repo',
      },
      { env: { MEM_WORK_ID: 'mem-31kz' }, now: NOW }
    );
    expect(event).not.toBeNull();
    expect(event).toMatchObject({
      session: 'sess-1',
      work_id: 'mem-31kz',
      op: 'read',
      backend: 'filesystem',
      memory_ref: '/home/ds/brains/lesson.md',
      used_in: '/repo',
      concrete_tool: 'Read',
      source: 'capture-hook',
      occurred_at: NOW,
    });
    // id is deterministic — a re-fired hook for the same op is an idempotent
    // no-op at the store layer.
    expect(event?.id).toBe(
      'capture-hook:sess-1:2026-06-21T10:00:00Z:read:/home/ds/brains/lesson.md'
    );
  });

  it('no-ops (null) on a non-memory tool', () => {
    expect(
      buildCaptureEvent(
        { tool_name: 'Bash', tool_input: { command: 'ls' }, session_id: 's' },
        { env: {}, now: NOW }
      )
    ).toBeNull();
  });

  it('no-ops (null) on a memory tool touching a non-memory path', () => {
    expect(
      buildCaptureEvent(
        { tool_name: 'Read', tool_input: { file_path: '/repo/src/index.ts' }, session_id: 's' },
        { env: {}, now: NOW }
      )
    ).toBeNull();
  });

  it('captures session-keyed even when no work_id is in env', () => {
    const event = buildCaptureEvent(
      { tool_name: 'Write', tool_input: { file_path: '/repo/MEMORY.md' }, session_id: 'sess-2' },
      { env: {}, now: NOW }
    );
    expect(event?.work_id).toBeUndefined();
    expect(event?.op).toBe('write');
    expect(event?.session).toBe('sess-2');
  });

  it('falls back to GC_SESSION_NAME when the hook omits session_id', () => {
    const event = buildCaptureEvent(
      { tool_name: 'Read', tool_input: { file_path: '/repo/MEMORY.md' } },
      { env: { GC_SESSION_NAME: 'gc-worker-1' }, now: NOW }
    );
    expect(event?.session).toBe('gc-worker-1');
  });
});
