import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { attachAndParse, buildStoreFromRecords } from '../src/cli/commands/build-store.js';
import { attachProvenance } from '../src/ingest/provenance.js';
import { attachRepo } from '../src/ingest/repo-resolve.js';
import {
  openStore,
  provenanceEventsFor,
  queryRecords,
  workIdsBySignature,
} from '../src/store/index.js';
import { failureSignature } from '../src/parse/recurrence.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const record = (workId: string, rig: string): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig,
    title: `work ${workId}`,
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      closed: '2026-06-05T00:00:00Z',
      status: 'closed',
    },
  });

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-build-store-'));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('buildStoreFromRecords', () => {
  it('persists records into a fresh store readable by queryRecords', () => {
    const path = join(dir, 'nested', 'store.db'); // parent dir must be created
    const built = buildStoreFromRecords(path, [record('b-1', 'rigA'), record('b-2', 'rigB')]);

    expect(built.records).toBe(2);
    expect(existsSync(path)).toBe(true);

    const db = openStore(path);
    try {
      const ids = queryRecords(db).map(r => r.work_id);
      expect(ids).toEqual(['b-1', 'b-2']); // reader's deterministic ORDER BY work_id
    } finally {
      db.close();
    }
  });

  it('backfills the provenance event log from the records it writes', () => {
    const path = join(dir, 'store.db');
    const withAgents = WorkRecordSchema.parse({
      work_id: 'b-1',
      rig: 'rigA',
      title: 'work b-1',
      lifecycle: { created: '2026-06-01T00:00:00Z', started: '2026-06-01T01:00:00Z', closed: '2026-06-05T00:00:00Z', status: 'closed' },
      agents: [{ agent_id: 'gc-1', sequence: 1, started_at: '2026-06-01T01:00:00Z' }],
    });
    const built = buildStoreFromRecords(path, [withAgents], '2026-06-19T00:00:00Z');

    // no provenance/landed → only the agent's `claim` event derives (the honest subset)
    expect(built.provenance_events).toBe(1);
    const db = openStore(path);
    try {
      const events = provenanceEventsFor(db, 'b-1');
      expect(events.map(e => e.kind)).toEqual(['claim']);
      expect(events[0].actor).toBe('gc-1');
    } finally {
      db.close();
    }
  });

  it('writes an empty store for an empty corpus (no throw)', () => {
    const path = join(dir, 'store.db');
    expect(buildStoreFromRecords(path, []).records).toBe(0);
    const db = openStore(path);
    try {
      expect(queryRecords(db)).toEqual([]);
    } finally {
      db.close();
    }
  });
});

/** Assistant entry issuing one Bash tool_use. */
const bashCall = (id: string, command: string): string =>
  JSON.stringify({
    type: 'assistant',
    message: { content: [{ type: 'tool_use', id, name: 'Bash', input: { command } }] },
  });

/** Matching user entry carrying the tool_result + captured output. */
const bashResult = (id: string, stdout: string): string =>
  JSON.stringify({
    type: 'user',
    message: { content: [{ type: 'tool_result', tool_use_id: id, is_error: true }] },
    toolUseResult: { stdout, stderr: '' },
  });

describe('attachAndParse (P1.3 resolve → P1.6 parse)', () => {
  it('lands deterministic trace errors as queryable D8 signatures', () => {
    const rec = WorkRecordSchema.parse({
      work_id: 'w-trace',
      rig: 'mem',
      title: 'work that failed a build',
      lifecycle: {
        created: '2026-06-01T00:00:00Z',
        closed: '2026-06-05T00:00:00Z',
        status: 'closed',
      },
      agents: [{ agent_id: 'gc-999' }],
    });
    const transcript = [
      bashCall('c1', 'tsc --noEmit'),
      bashResult('c1', 'src/a.ts(12,5): error TS2345: bad arg.'),
    ].join('\n');

    // Injected resolver + reader: no `gc` binary, no transcript on disk.
    const parsed = attachAndParse([rec], {
      resolve: sessionId => (sessionId === 'gc-999' ? '/fake/t.jsonl' : null),
      read: path => (path === '/fake/t.jsonl' ? transcript : ''),
    });

    expect(parsed[0].trace?.errors).toHaveLength(1);
    const sig = failureSignature(parsed[0].trace!.errors![0]);

    const path = join(dir, 'store.db');
    buildStoreFromRecords(path, parsed);
    const db = openStore(path);
    try {
      // The signature the `ours` arm keys on is recoverable from the store.
      expect(workIdsBySignature(db, sig)).toEqual(['w-trace']);
    } finally {
      db.close();
    }
  });

  it('leaves records without a resolvable session untouched', () => {
    const rec = WorkRecordSchema.parse({
      work_id: 'w-human',
      rig: 'mem',
      title: 'human-owned',
      lifecycle: { created: '2026-06-01T00:00:00Z', status: 'open' },
      agents: [{ agent_id: 'sjarmak@users.noreply.github.com' }],
    });
    const parsed = attachAndParse([rec], { resolve: () => null, read: () => '' });
    expect(parsed[0].trace).toBeUndefined();
  });
});

describe('build-store provenance projection', () => {
  it('promotes base_commit / commit_state into queryable columns', () => {
    const rec = WorkRecordSchema.parse({
      work_id: 'w-prov',
      rig: 'mem',
      title: 'work with a git baseline',
      metadata: { 'gc.work_dir': '/home/ds/projects/mem', 'gc.var.base_branch': 'main' },
      lifecycle: {
        created: '2026-06-01T00:00:00Z',
        started: '2026-06-05 03:24:03',
        status: 'closed',
      },
    });
    const sha = '0123456789abcdef0123456789abcdef01234567';
    const withProv = attachProvenance([rec], { run: () => `${sha}\n` });

    const path = join(dir, 'store.db');
    buildStoreFromRecords(path, withProv);
    const db = openStore(path);
    try {
      const row = db
        .prepare('SELECT base_commit, commit_state FROM work_records WHERE work_id = ?')
        .get('w-prov') as { base_commit: string; commit_state: string };
      expect(row).toEqual({ base_commit: sha, commit_state: 'commit-by-date' });

      // Round-trips through the JSON blob as well.
      expect(queryRecords(db, { rig: 'mem' })[0].provenance?.base_commit).toBe(sha);
    } finally {
      db.close();
    }
  });

  it('leaves the provenance columns NULL for a record without a work_dir', () => {
    const rec = record('w-noprov', 'mem');
    const path = join(dir, 'store.db');
    buildStoreFromRecords(path, [rec]);
    const db = openStore(path);
    try {
      const row = db
        .prepare('SELECT base_commit, commit_state FROM work_records WHERE work_id = ?')
        .get('w-noprov') as {
        base_commit: string | null;
        commit_state: string | null;
      };
      expect(row).toEqual({ base_commit: null, commit_state: null });
    } finally {
      db.close();
    }
  });
});

describe('build-store repo projection (mem-bme)', () => {
  it('promotes the canonical repo + repo_source from the rig→repo map', () => {
    const located = attachRepo([record('w-repo', 'mem')]);
    const path = join(dir, 'store.db');
    buildStoreFromRecords(path, located);
    const db = openStore(path);
    try {
      const row = db
        .prepare('SELECT repo, repo_source FROM work_records WHERE work_id = ?')
        .get('w-repo') as { repo: string | null; repo_source: string | null };
      expect(row).toEqual({ repo: 'sjarmak/mem', repo_source: 'rig-map' });
    } finally {
      db.close();
    }
  });

  it('leaves repo NULL and tags unmapped for an umbrella rig', () => {
    const located = attachRepo([record('w-gc', 'gc')]);
    const path = join(dir, 'store.db');
    buildStoreFromRecords(path, located);
    const db = openStore(path);
    try {
      const row = db
        .prepare('SELECT repo, repo_source FROM work_records WHERE work_id = ?')
        .get('w-gc') as { repo: string | null; repo_source: string | null };
      expect(row).toEqual({ repo: null, repo_source: 'unmapped' });
    } finally {
      db.close();
    }
  });
});
