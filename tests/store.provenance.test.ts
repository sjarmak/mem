import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import {
  SCHEMA_VERSION,
  deriveProvenanceEvents,
  openStore,
  provenanceEventsByRef,
  provenanceEventsFor,
  recordProvenanceEvents,
} from '../src/store/index.js';
import { type ProvenanceEvent } from '../src/schemas/provenance-event.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const A = 'a'.repeat(40); // base commit
const B = 'b'.repeat(40); // landed commit
const INGESTED = '2026-06-19T12:00:00Z';

/** A record carrying every reconstructable provenance fact: a base SHA, two
 * sessions (interrupt → resume), a landed commit, and a PR outcome. */
const provRecord = (overrides: Partial<WorkRecord> = {}): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: 'demo-1a2b',
    rig: 'demo',
    title: 'Fix the build',
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      started: '2026-06-01T01:00:00Z',
      closed: '2026-06-02T00:00:00Z',
      status: 'closed',
      status_history: [],
    },
    agents: [
      { agent_id: 'gc-1001', role: 'polecat', sequence: 1, started_at: '2026-06-01T01:00:00Z', trace_ref: '/t/1.jsonl' },
      { agent_id: 'gc-1002', role: 'polecat', sequence: 2, started_at: '2026-06-01T18:00:00Z', trace_ref: '/t/2.jsonl' },
    ],
    provenance: {
      work_dir: '/w/demo',
      repo: 'demo',
      base_branch: 'main',
      base_commit: A,
      history_state: 'commit-by-date',
      base_branch_source: 'metadata',
    },
    landed: {
      base_commit: A,
      landed_commit: B,
      n_commits: 3,
      landed_state: 'landed',
    },
    outcome: { pr: '#42', pr_state: 'merged', commit_sha: B, ci: 'pass' },
    ...overrides,
  });

const tmp = () => mkdtempSync(join(tmpdir(), 'prov-'));
const dirs: string[] = [];
function store() {
  const dir = tmp();
  dirs.push(dir);
  return openStore(join(dir, 'store.db'));
}
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

describe('provenance event log', () => {
  it('provisions the table at schema v9', () => {
    expect(SCHEMA_VERSION).toBe(9);
    const db = store();
    const row = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='provenance_events'")
      .get();
    expect(row).toBeTruthy();
  });

  it('derives cut / claim / land from reconstructed facts and round-trips', () => {
    const db = store();
    const events = deriveProvenanceEvents(provRecord(), INGESTED);
    const n = recordProvenanceEvents(db, events);
    expect(n).toBe(events.length);

    const kinds = provenanceEventsFor(db, 'demo-1a2b').map((e) => e.kind);
    // cut(1) + claim(2) + land-by-commit(1) + land-by-pr(1)
    expect(kinds.sort()).toEqual(['claim', 'claim', 'cut', 'land', 'land']);
  });

  it('captures the base SHA as a first-class cut event (no date heuristic)', () => {
    const db = store();
    recordProvenanceEvents(db, deriveProvenanceEvents(provRecord(), INGESTED));
    const [cut] = provenanceEventsFor(db, 'demo-1a2b', 'cut');
    expect(cut.ref).toBe(A);
    expect(cut.ref_kind).toBe('git-sha');
    expect(cut.actor).toBe('gc-1001');
    // the approximation provenance is preserved for the dual-write comparison
    expect(cut.payload?.history_state).toBe('commit-by-date');
  });

  it('records every session as an ordered claim — the interrupt/resume signal', () => {
    const db = store();
    recordProvenanceEvents(db, deriveProvenanceEvents(provRecord(), INGESTED));
    const claims = provenanceEventsFor(db, 'demo-1a2b', 'claim');
    expect(claims.map((c) => c.actor)).toEqual(['gc-1001', 'gc-1002']);
    expect(claims.map((c) => c.payload?.sequence)).toEqual([1, 2]);
  });

  it('answers by-ref: which work bound to a SHA / PR (the exact join)', () => {
    const db = store();
    recordProvenanceEvents(db, deriveProvenanceEvents(provRecord(), INGESTED));
    expect(provenanceEventsByRef(db, B).map((e) => e.kind)).toEqual(['land']);
    expect(provenanceEventsByRef(db, '#42').map((e) => e.work_id)).toEqual(['demo-1a2b']);
    expect(provenanceEventsByRef(db, A).map((e) => e.kind)).toEqual(['cut']);
  });

  it('is append-only and idempotent: re-recording inserts nothing new', () => {
    const db = store();
    const events = deriveProvenanceEvents(provRecord(), INGESTED);
    expect(recordProvenanceEvents(db, events)).toBe(events.length);
    // same deterministic ids → INSERT OR IGNORE → zero new rows, no overwrite
    expect(recordProvenanceEvents(db, deriveProvenanceEvents(provRecord(), '2026-07-01T00:00:00Z'))).toBe(0);
    expect(provenanceEventsFor(db, 'demo-1a2b')).toHaveLength(events.length);
  });

  it('honestly emits no commit/used events — those gaps need real producers', () => {
    const events = deriveProvenanceEvents(provRecord(), INGESTED);
    const kinds = new Set(events.map((e) => e.kind));
    expect(kinds.has('commit')).toBe(false); // no per-commit attribution exists
    expect(kinds.has('used')).toBe(false); // retrieval causality is absent, not lossy
  });

  it('derives only what is present: a record with no provenance yields no cut/land', () => {
    const bare = provRecord({ provenance: undefined, landed: undefined, outcome: undefined });
    const kinds = new Set(deriveProvenanceEvents(bare, INGESTED).map((e) => e.kind));
    expect(kinds).toEqual(new Set(['claim']));
  });

  it('rejects an unknown kind at the boundary (structural validation)', () => {
    const db = store();
    const bad = { kind: 'merged' } as unknown as ProvenanceEvent;
    expect(() => recordProvenanceEvents(db, [bad])).toThrow();
  });
});
