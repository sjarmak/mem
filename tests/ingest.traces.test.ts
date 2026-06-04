import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { defaultProjectsRoot, indexTraces, traceIndexByPath } from '../src/ingest/trace-index.js';
import {
  attachTraceRefs,
  parseSessionId,
  parseTranscriptPath,
  type SessionResolver,
} from '../src/ingest/trace-resolve.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

/** A transcript JSONL: a few entries, one carrying cwd/gitBranch. */
function transcript(uuid: string, cwd: string): string {
  return (
    [
      { type: 'last-prompt', sessionId: uuid, cwd: null },
      { type: 'attachment', sessionId: uuid, cwd, gitBranch: 'main' },
      { type: 'user', sessionId: uuid },
      { type: 'assistant', sessionId: uuid },
      { type: 'user', sessionId: uuid },
    ]
      .map(e => JSON.stringify(e))
      .join('\n') + '\n'
  );
}

describe('parseSessionId', () => {
  it('extracts the bare id from a full session name', () => {
    expect(parseSessionId('polecat-gc-335825')).toBe('gc-335825');
    expect(parseSessionId('mem-worker-gc-340053')).toBe('gc-340053');
  });

  it('returns a bare id unchanged', () => {
    expect(parseSessionId('gc-340053')).toBe('gc-340053');
  });

  it('returns null when there is no session id', () => {
    expect(parseSessionId('sjarmak@users.noreply.github.com')).toBeNull();
    expect(parseSessionId('controller')).toBeNull();
  });

  it('does not match a gc-like substring without a word boundary', () => {
    expect(parseSessionId('logc-12')).toBeNull();
  });
});

describe('parseTranscriptPath', () => {
  it('returns the transcript path on success', () => {
    const out = JSON.stringify({ ok: true, transcript_path: '/p/u.jsonl', target: 'gc-1' });
    expect(parseTranscriptPath(out)).toBe('/p/u.jsonl');
  });

  it('returns null when not ok or path missing', () => {
    expect(parseTranscriptPath(JSON.stringify({ ok: false }))).toBeNull();
    expect(parseTranscriptPath(JSON.stringify({ ok: true }))).toBeNull();
  });
});

describe('indexTraces', () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'mem-traces-'));
    const projA = join(root, '-home-ds-projects-mem');
    mkdirSync(projA);
    writeFileSync(join(projA, 'uuid-a.jsonl'), transcript('uuid-a', '/home/ds/projects/mem'));
    writeFileSync(join(projA, 'uuid-b.jsonl'), transcript('uuid-b', '/home/ds/projects/mem'));
    // A non-transcript file in the same dir must be ignored.
    writeFileSync(join(projA, 'notes.txt'), 'ignore me');
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('indexes every transcript with derived metadata', () => {
    const entries = indexTraces(root);
    expect(entries).toHaveLength(2);

    const a = entries.find(e => e.session_uuid === 'uuid-a');
    expect(a).toBeDefined();
    expect(a?.project_dir).toBe('-home-ds-projects-mem');
    expect(a?.cwd).toBe('/home/ds/projects/mem');
    expect(a?.git_branch).toBe('main');
    expect(a?.n_turns).toBe(3); // 2 user + 1 assistant
    expect(a?.jsonl_path.endsWith('uuid-a.jsonl')).toBe(true);
  });

  it('de-duplicates a transcript reachable through a symlink (realpath guard)', () => {
    // A symlinked .jsonl resolving to an already-indexed transcript must not
    // produce a second entry — this exercises the per-file realpath+seen guard
    // that protects against account-home symlinks pointing at the same file.
    const projA = join(root, '-home-ds-projects-mem');
    symlinkSync(join(projA, 'uuid-a.jsonl'), join(projA, 'uuid-a-link.jsonl'));
    const entries = indexTraces(root);
    expect(entries).toHaveLength(2);
    expect(entries.filter(e => e.session_uuid === 'uuid-a')).toHaveLength(1);
  });

  it('skips malformed lines without dropping the file', () => {
    const projA = join(root, '-home-ds-projects-mem');
    writeFileSync(join(projA, 'uuid-c.jsonl'), '{ not json\n' + transcript('uuid-c', '/x'));
    const entries = indexTraces(root);
    const c = entries.find(e => e.session_uuid === 'uuid-c');
    expect(c?.n_turns).toBe(3);
    expect(c?.cwd).toBe('/x');
  });
});

describe('traceIndexByPath', () => {
  it('keys entries by jsonl_path', () => {
    const map = traceIndexByPath([
      { jsonl_path: '/a.jsonl', session_uuid: 'a', project_dir: 'p', n_turns: 1, mtime_ms: 0 },
    ]);
    expect(map.get('/a.jsonl')?.session_uuid).toBe('a');
  });
});

describe('defaultProjectsRoot', () => {
  it('points at the canonical claude projects dir', () => {
    expect(defaultProjectsRoot().endsWith('/.claude/projects')).toBe(true);
  });
});

describe('attachTraceRefs', () => {
  const baseRecord = (agentId: string): WorkRecord =>
    WorkRecordSchema.parse({
      work_id: 'mem-7f9',
      rig: 'mem',
      title: 'P1.3',
      lifecycle: { created: '2026-06-04T00:00:00Z', status: 'closed' },
      agents: [{ agent_id: agentId }],
    });

  const resolver: SessionResolver = id =>
    id === 'gc-1' ? '/home/ds/.claude/projects/-p/uuid-1.jsonl' : null;

  it('sets agent.trace_ref and record.trace for a resolved session', () => {
    const [rec] = attachTraceRefs([baseRecord('mem-worker-gc-1')], { resolve: resolver });
    expect(rec.agents[0].trace_ref).toBe('/home/ds/.claude/projects/-p/uuid-1.jsonl');
    expect(rec.trace?.jsonl_path).toBe('/home/ds/.claude/projects/-p/uuid-1.jsonl');
  });

  it('pulls n_turns from the index when the path is indexed', () => {
    const index = [
      {
        jsonl_path: '/home/ds/.claude/projects/-p/uuid-1.jsonl',
        session_uuid: 'uuid-1',
        project_dir: '-p',
        n_turns: 12,
        mtime_ms: 0,
      },
    ];
    const [rec] = attachTraceRefs([baseRecord('gc-1')], { resolve: resolver, index });
    expect(rec.trace?.n_turns).toBe(12);
  });

  it('leaves trace unset and trace_ref absent when the session is unknown', () => {
    const [rec] = attachTraceRefs([baseRecord('gc-999')], { resolve: resolver });
    expect(rec.trace).toBeUndefined();
    expect(rec.agents[0].trace_ref).toBeUndefined();
  });

  it('does not mutate the input record or its agents', () => {
    const input = baseRecord('gc-1');
    const [out] = attachTraceRefs([input], { resolve: resolver });
    expect(input.trace).toBeUndefined();
    expect(input.agents[0].trace_ref).toBeUndefined();
    expect(out).not.toBe(input);
    expect(out.agents[0]).not.toBe(input.agents[0]);
  });

  it('memoizes resolution so repeated session ids resolve once', () => {
    const calls: string[] = [];
    const counting: SessionResolver = id => {
      calls.push(id);
      return '/t.jsonl';
    };
    attachTraceRefs([baseRecord('gc-1'), baseRecord('gc-1')], { resolve: counting });
    expect(calls).toEqual(['gc-1']);
  });
});
