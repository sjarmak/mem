import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  type JoinSessionEntry,
  attachSessionJoin,
  loadSessionJoin,
} from '../src/ingest/session-merge.js';
import { attachTraceRefs } from '../src/ingest/trace-resolve.js';
import { openStore } from '../src/store/index.js';
import { writeRecords } from '../src/store/writer.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const record = (workId: string, assignee?: { id: string; trace?: string }): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig: 'demo',
    title: `work ${workId}`,
    lifecycle: { created: '2026-06-01T00:00:00Z', status: 'closed' },
    agents: assignee
      ? [{ agent_id: assignee.id, role: 'polecat', ...(assignee.trace && { trace_ref: assignee.trace }) }]
      : [],
  });

const entry = (overrides: Partial<JoinSessionEntry>): JoinSessionEntry => ({
  sequence: 1,
  gc_session_id: null,
  session_key: null,
  transcript_path: null,
  t_first: null,
  t_last: null,
  sources: ['events'],
  strength: 'strong',
  n_events: 1,
  suspect: false,
  ...overrides,
});

describe('loadSessionJoin', () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'mem-join-'));
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it('parses the beads map', () => {
    const path = join(dir, 'join.json');
    writeFileSync(path, JSON.stringify({ beads: { 'demo-1': [entry({ sequence: 1 })] } }));
    const join_ = loadSessionJoin(path);
    expect(join_.beads.get('demo-1')).toHaveLength(1);
    expect(join_.sessionPaths.size).toBe(0);
  });

  it('parses session_paths when present', () => {
    const path = join(dir, 'join2.json');
    writeFileSync(
      path,
      JSON.stringify({ beads: {}, session_paths: { 'gc-100': '/t/a.jsonl' } })
    );
    expect(loadSessionJoin(path).sessionPaths.get('gc-100')).toBe('/t/a.jsonl');
  });

  it('throws on an artifact without beads', () => {
    const path = join(dir, 'bad.json');
    writeFileSync(path, JSON.stringify({ rows: [] }));
    expect(() => loadSessionJoin(path)).toThrow(/no beads/);
  });
});

describe('attachSessionJoin', () => {
  it('replaces agents with the ordered multi-row session list', () => {
    const beads = new Map([
      [
        'demo-1',
        [
          entry({
            sequence: 1,
            gc_session_id: 'gc-100',
            session_key: 'uuid-a',
            transcript_path: '/t/a.jsonl',
            t_first: '2026-06-01T10:00:00+00:00',
            t_last: '2026-06-01T11:00:00+00:00',
            sources: ['events', 'content-scan'],
          }),
          entry({
            sequence: 2,
            gc_session_id: 'gc-200',
            transcript_path: '/t/b.jsonl',
            sources: ['events'],
          }),
        ],
      ],
    ]);
    const join_ = { beads, sessionPaths: new Map<string, string>() };
    const [next] = attachSessionJoin([record('demo-1', { id: 'polecat-gc-100' })], join_);

    expect(next.agents).toHaveLength(2);
    expect(next.agents[0]).toMatchObject({
      agent_id: 'gc-100',
      role: 'polecat', // inherited from the matching assignee agent
      trace_ref: '/t/a.jsonl',
      sequence: 1,
      started_at: '2026-06-01T10:00:00+00:00',
      sources: ['events', 'content-scan'],
    });
    // primary trace = LAST non-suspect resolved session (closing iteration)
    expect(next.trace?.jsonl_path).toBe('/t/b.jsonl');
  });

  it('skips suspect entries when picking the primary trace', () => {
    const beads = new Map([
      [
        'demo-1',
        [
          entry({ sequence: 1, transcript_path: '/t/good.jsonl', sources: ['content-scan'] }),
          entry({
            sequence: 2,
            transcript_path: '/t/wrong-conversation.jsonl',
            sources: ['assignee'],
            suspect: true,
          }),
        ],
      ],
    ]);
    const join_ = { beads, sessionPaths: new Map<string, string>() };
    const [next] = attachSessionJoin([record('demo-1')], join_);
    expect(next.trace?.jsonl_path).toBe('/t/good.jsonl');
    expect(next.agents[1].suspect).toBe(true);
  });

  it('passes through records without join entries', () => {
    const original = record('demo-2', { id: 'polecat-gc-300' });
    const empty = { beads: new Map<string, never[]>(), sessionPaths: new Map<string, string>() };
    const [next] = attachSessionJoin([original], empty);
    expect(next).toEqual(original);
  });
});

describe('attachTraceRefs with pre-attached join', () => {
  it('does not re-resolve agents that already carry a trace_ref', () => {
    const calls: string[] = [];
    const resolve = (id: string): string | null => {
      calls.push(id);
      return null;
    };
    const joined: WorkRecord = {
      ...record('demo-1'),
      agents: [
        { agent_id: 'gc-100', trace_ref: '/t/a.jsonl', sequence: 1, sources: ['events'] },
        { agent_id: 'gc-200', trace_ref: '/t/b.jsonl', sequence: 2, sources: ['events'] },
      ],
      trace: { jsonl_path: '/t/b.jsonl' },
    };
    const [next] = attachTraceRefs([joined], { resolve });
    expect(calls).toEqual([]); // no gc shelling for pre-resolved agents
    expect(next.trace?.jsonl_path).toBe('/t/b.jsonl'); // pre-set primary kept
  });
});

describe('writer multi-row agents', () => {
  it('persists sequence, timestamps, sources, and suspect per row', () => {
    const db = openStore(':memory:');
    const joined: WorkRecord = {
      ...record('demo-1'),
      agents: [
        {
          agent_id: 'gc-100',
          trace_ref: '/t/a.jsonl',
          sequence: 1,
          started_at: '2026-06-01T10:00:00+00:00',
          ended_at: '2026-06-01T11:00:00+00:00',
          sources: ['events', 'dolt-history'],
        },
        { agent_id: 'gc-200', sequence: 2, sources: ['assignee'], suspect: true },
      ],
    };
    writeRecords(db, [joined]);

    const rows = db
      .prepare('SELECT * FROM record_agents WHERE work_id = ? ORDER BY sequence')
      .all('demo-1') as Array<Record<string, unknown>>;
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      agent_id: 'gc-100',
      sequence: 1,
      started_at: '2026-06-01T10:00:00+00:00',
      sources: 'events+dolt-history',
      suspect: 0,
    });
    expect(rows[1]).toMatchObject({ agent_id: 'gc-200', sources: 'assignee', suspect: 1 });

    // re-ingest converges: child rows rebuilt, not accumulated
    writeRecords(db, [joined]);
    const again = db
      .prepare('SELECT COUNT(*) AS n FROM record_agents WHERE work_id = ?')
      .get('demo-1') as { n: number };
    expect(again.n).toBe(2);
    db.close();
  });
});
