import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { buildStoreFromRecords, type BuildStoreResult } from '../src/cli/commands/build-store.js';
import { rebuildCommand } from '../src/cli/commands/rebuild.js';
import {
  allLessons,
  allMemoryEvents,
  appendLesson,
  openStore,
  producerProvenanceEvents,
  provenanceEventsFor,
  recordMemoryEvents,
  recordProvenanceEvents,
} from '../src/store/index.js';
import { BACKFILL_SOURCE, type ProvenanceEvent } from '../src/schemas/provenance-event.js';
import type { MemoryEvent } from '../src/schemas/memory-event.js';
import { WorkRecordSchema } from '../src/schemas/workrecord.js';

const SHA = '0123456789abcdef0123456789abcdef01234567';

const producerCut: ProvenanceEvent = {
  id: 'git-hook:demo-1:cut:a',
  work_id: 'demo-1',
  kind: 'cut',
  actor: 'gc-1',
  ref: SHA,
  ref_kind: 'git-sha',
  source: 'git-hook',
  occurred_at: '2026-07-01T00:00:00Z',
  created_at: '2026-07-01T00:00:01Z',
};

const staleBackfill: ProvenanceEvent = {
  id: `${BACKFILL_SOURCE}:stale-9:claim:gone:idx0`,
  work_id: 'stale-9',
  kind: 'claim',
  actor: 'gone',
  source: BACKFILL_SOURCE,
  occurred_at: '2026-07-01T00:00:00Z',
  created_at: '2026-07-01T00:00:01Z',
};

const memoryEvent: MemoryEvent = {
  id: 'capture-hook:s-1:2026-07-01T00:00:00Z:read:notes.md',
  session: 's-1',
  work_id: 'demo-1',
  op: 'read',
  backend: 'filesystem',
  memory_ref: 'notes.md',
  source: 'capture-hook',
  occurred_at: '2026-07-01T00:00:00Z',
  created_at: '2026-07-01T00:00:01Z',
};

const lesson = {
  work_id: 'demo-1',
  extracted_at: '2026-06-03T00:00:00Z',
  commit_sha: 'abc123',
  payload: { root_cause: 'missing flag', resolution: 'add --no-tls' },
};

/** A record with one agent so the rebuild re-derives a fresh backfill claim. */
const fixtureRecord = WorkRecordSchema.parse({
  work_id: 'demo-1',
  rig: 'mem',
  title: 'work demo-1',
  lifecycle: {
    created: '2026-06-01T00:00:00Z',
    started: '2026-06-01T01:00:00Z',
    closed: '2026-06-05T00:00:00Z',
    status: 'closed',
  },
  agents: [{ agent_id: 'gc-1', sequence: 1 }],
});

/** Injected build step: the real buildStoreFromRecords, no dolt connection. */
const fakeBuild = (path: string) => (): Promise<BuildStoreResult> => {
  const built = buildStoreFromRecords(path, [fixtureRecord], '2026-07-02T00:00:00Z');
  return Promise.resolve({
    store: path,
    rig: null,
    count: built.records,
    records_with_errors: 0,
    records_with_repo: 0,
    records_with_provenance: 0,
    records_with_commit: 0,
    records_recorded_base: 0,
    records_with_commit_sha: 0,
    records_landed: 0,
    records_multi_session: 0,
    records_multiple_linkage: 0,
    records_provenance_events: built.provenance_events,
    records_with_session_base: 0,
    records_with_session_commits: 0,
    record_links: 0,
  });
};

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-rebuild-'));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

/** An "old" store carrying all three non-regenerable tables, stamped with a
 * stale schema version so openStore would refuse it — the rebuild scenario. */
function seedStaleStore(name: string): string {
  const path = join(dir, name);
  const db = openStore(path);
  try {
    appendLesson(db, lesson);
    recordMemoryEvents(db, [memoryEvent]);
    recordProvenanceEvents(db, [producerCut, staleBackfill]);
    db.pragma('user_version = 8');
  } finally {
    db.close();
  }
  return path;
}

describe('mem rebuild (export-all → fresh build → import-all)', () => {
  it('round-trips all three non-regenerable tables across a version bump', async () => {
    const path = seedStaleStore('store.db');

    const result = await rebuildCommand(
      { args: [], options: { json: true, verbose: false, store: path } },
      fakeBuild(path)
    );

    expect(result.store).toBe(path);
    expect(result.exported).toEqual({ lessons: 1, memory_events: 1, provenance_events: 1 });
    expect(result.imported.lessons).toEqual({ appended: 1, skipped: 0 });
    expect(result.imported.memory_events).toEqual({ appended: 1, skipped: 0 });
    expect(result.imported.provenance_events).toEqual({ appended: 1, skipped: 0 });
    expect(result.build.count).toBe(1);

    // The rebuilt store opens at the current version (openStore would throw on
    // a mismatch) and holds the carried rows byte-identical.
    const db = openStore(path);
    try {
      const lessons = allLessons(db);
      expect(lessons).toHaveLength(1);
      expect(lessons[0]).toMatchObject(lesson);
      expect(allMemoryEvents(db)).toEqual([memoryEvent]);
      expect(producerProvenanceEvents(db)).toEqual([producerCut]);
      // The stale backfill row was NOT carried; the build re-derived a fresh
      // claim for the fixture record instead.
      expect(provenanceEventsFor(db, 'stale-9')).toEqual([]);
      expect(
        provenanceEventsFor(db, 'demo-1')
          .map(e => e.kind)
          .sort()
      ).toEqual(['claim', 'cut']);
    } finally {
      db.close();
    }

    // The pre-rebuild store is preserved, still at its old version.
    expect(existsSync(result.backup)).toBe(true);
    const backup = new Database(result.backup, { readonly: true });
    try {
      expect(backup.pragma('user_version', { simple: true })).toBe(8);
    } finally {
      backup.close();
    }
  });

  it('is a loud user error when there is no store to rebuild', async () => {
    await expect(
      rebuildCommand(
        { args: [], options: { json: true, verbose: false, store: join(dir, 'absent.db') } },
        fakeBuild(join(dir, 'absent.db'))
      )
    ).rejects.toThrow(/no store/i);
  });

  it('names the preserved backup when the build step fails', async () => {
    const path = seedStaleStore('store.db');
    const failing = (): Promise<BuildStoreResult> => Promise.reject(new Error('dolt exploded'));

    await expect(
      rebuildCommand({ args: [], options: { json: true, verbose: false, store: path } }, failing)
    ).rejects.toThrow(/dolt exploded.*pre-rebuild/s);
  });
});
