import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  SCHEMA_VERSION,
  allMemoryEvents,
  importMemoryEvents,
  memoryEventsBySession,
  memoryEventsFor,
  openStore,
  recordMemoryEvents,
  writeRecords,
  type StoreDatabase,
} from '../src/store/index.js';
import { MemoryEventSchema, type MemoryEvent } from '../src/schemas/memory-event.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const ev = (overrides: Partial<MemoryEvent> = {}): MemoryEvent =>
  MemoryEventSchema.parse({
    id: 'capture-hook:sess-1:2026-06-21T10:00:00Z:read:/home/ds/memory/MEMORY.md',
    session: 'sess-1',
    work_id: 'mem-31kz',
    op: 'read',
    backend: 'filesystem',
    memory_ref: '/home/ds/memory/MEMORY.md',
    source: 'capture-hook',
    occurred_at: '2026-06-21T10:00:00Z',
    created_at: '2026-06-21T10:00:00Z',
    ...overrides,
  });

let dir: string;
let db: StoreDatabase;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-memevents-'));
  db = openStore(join(dir, 'store.db'));
});

afterEach(() => {
  db.close();
  rmSync(dir, { recursive: true, force: true });
});

describe('memory_events store surface (mem-31kz)', () => {
  it('the schema is bumped to v9 for the memory_events table', () => {
    expect(SCHEMA_VERSION).toBe(9);
  });

  it('records and reads back events by work_id and by session', () => {
    expect(recordMemoryEvents(db, [ev()])).toBe(1);
    expect(memoryEventsFor(db, 'mem-31kz').map(e => e.memory_ref)).toEqual([
      '/home/ds/memory/MEMORY.md',
    ]);
    expect(memoryEventsBySession(db, 'sess-1')).toHaveLength(1);
  });

  it('is append-only: a duplicate id is an idempotent no-op (INSERT OR IGNORE)', () => {
    expect(recordMemoryEvents(db, [ev()])).toBe(1);
    expect(recordMemoryEvents(db, [ev()])).toBe(0); // same id, ignored
    expect(allMemoryEvents(db)).toHaveLength(1);
  });

  it('filters reads by op', () => {
    recordMemoryEvents(db, [
      ev(),
      ev({
        id: 'capture-hook:sess-1:2026-06-21T10:01:00Z:write:/home/ds/memory/x.md',
        op: 'write',
        memory_ref: '/home/ds/memory/x.md',
        occurred_at: '2026-06-21T10:01:00Z',
      }),
    ]);
    expect(memoryEventsFor(db, 'mem-31kz', 'write').map(e => e.op)).toEqual(['write']);
    expect(memoryEventsFor(db, 'mem-31kz')).toHaveLength(2);
  });

  it('orders events by event-time then id (deterministic)', () => {
    recordMemoryEvents(db, [
      ev({ id: 'b', occurred_at: '2026-06-21T12:00:00Z' }),
      ev({ id: 'a', occurred_at: '2026-06-21T09:00:00Z' }),
    ]);
    expect(allMemoryEvents(db).map(e => e.id)).toEqual(['a', 'b']);
  });

  it('captures only leak-safe join keys — no outcome columns exist', () => {
    const cols = (
      db.prepare("SELECT name FROM pragma_table_info('memory_events')").all() as {
        name: string;
      }[]
    ).map(c => c.name);
    for (const leakKey of ['pr', 'commit_sha', 'base_commit', 'outcome', 'ci', 'landed_state']) {
      expect(cols).not.toContain(leakKey);
    }
  });

  it('rejects an event carrying a novel field (strict allow-list, not deny-list)', () => {
    expect(() =>
      recordMemoryEvents(db, [
        // A producer that grows an unscanned, possibly outcome-correlated field
        // must RAISE here, not smuggle it past the firewall.
        { ...ev(), commit_sha: 'SENTINELLEAK0000' } as unknown as MemoryEvent,
      ])
    ).toThrow();
  });

  it('survives a record rebuild: writeRecords does NOT clear memory_events', () => {
    recordMemoryEvents(db, [ev()]);
    const rec: WorkRecord = WorkRecordSchema.parse({
      work_id: 'mem-31kz',
      rig: 'mem',
      title: 'forward capture',
      lifecycle: { created: '2026-06-01T00:00:00Z', status: 'open' },
    });
    writeRecords(db, [rec]); // rebuilds projections — memory_events is not one
    expect(allMemoryEvents(db)).toHaveLength(1);
  });

  it('round-trips through export/import idempotently', () => {
    recordMemoryEvents(db, [ev()]);
    const exported = allMemoryEvents(db);

    const other = openStore(join(dir, 'other.db'));
    try {
      expect(importMemoryEvents(other, exported)).toEqual({ appended: 1, skipped: 0 });
      // Re-import the same export: every row already present, none rewritten.
      expect(importMemoryEvents(other, exported)).toEqual({ appended: 0, skipped: 1 });
      expect(allMemoryEvents(other)).toEqual(exported);
    } finally {
      other.close();
    }
  });
});
