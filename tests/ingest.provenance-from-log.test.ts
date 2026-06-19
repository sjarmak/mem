import { describe, expect, it } from 'vitest';

import { openStore, recordProvenanceEvents } from '../src/store/index.js';
import { isRecordedBase, loadRecordedBases } from '../src/ingest/provenance-from-log.js';
import {
  type GitRunner,
  attachProvenance,
  provenanceFromRecorded,
  provenanceInput,
} from '../src/ingest/provenance.js';
import { temporalWallDrop } from '../src/bench/temporal.js';
import { type ProvenanceEvent } from '../src/schemas/provenance-event.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const RECORDED = 'c'.repeat(40); // producer-recorded fork SHA
const BYDATE = 'd'.repeat(40); // what the git date-heuristic would return

const rec = (overrides: Partial<WorkRecord> = {}): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: 'demo-1',
    rig: 'demo',
    title: 't',
    metadata: { 'gc.work_dir': '/w/demo', 'gc.var.base_branch': 'main' },
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      started: '2026-06-01T01:00:00Z',
      closed: '2026-06-02T00:00:00Z',
      status: 'closed',
    },
    ...overrides,
  });

const producerCut = (workId: string, ref: string, occurredAt: string): ProvenanceEvent => ({
  id: `git-hook:${workId}:cut:${ref}`,
  work_id: workId,
  kind: 'cut',
  ref,
  ref_kind: 'git-sha',
  source: 'git-hook',
  occurred_at: occurredAt,
  created_at: '2026-06-19T00:00:00Z',
});

const backfillCut = (workId: string, ref: string): ProvenanceEvent => ({
  id: `ingest-backfill:${workId}:cut:${ref}`,
  work_id: workId,
  kind: 'cut',
  ref,
  ref_kind: 'git-sha',
  source: 'ingest-backfill',
  occurred_at: '2026-06-01T01:00:00Z',
  created_at: '2026-06-19T00:00:00Z',
});

/** A runner that fails the test if git is ever shelled — the proof that the
 * read-first path reconstructs nothing. */
const forbiddenRunner: GitRunner = () => {
  throw new Error('git runner was called — read-first path should not reconstruct');
};

describe('loadRecordedBases', () => {
  it('returns producer cuts and ignores backfilled (reconstructed) cuts', () => {
    const db = openStore(':memory:');
    recordProvenanceEvents(db, [
      producerCut('demo-1', RECORDED, '2026-06-01T00:00:00Z'),
      backfillCut('demo-2', BYDATE), // a reconstruction — must NOT count as recorded
    ]);
    const lookup = loadRecordedBases(db);
    expect(lookup('demo-1')).toBe(RECORDED);
    expect(lookup('demo-2')).toBeNull(); // backfill excluded — reading it back would be circular
    expect(lookup('absent')).toBeNull();
  });

  it('takes the newest producer cut by event-time when several exist', () => {
    const db = openStore(':memory:');
    const newer = 'e'.repeat(40);
    recordProvenanceEvents(db, [
      producerCut('demo-1', RECORDED, '2026-06-01T00:00:00Z'),
      producerCut('demo-1', newer, '2026-06-05T00:00:00Z'),
    ]);
    expect(loadRecordedBases(db)('demo-1')).toBe(newer);
  });

  it('isRecordedBase distinguishes a producer SHA from a backfilled one', () => {
    const db = openStore(':memory:');
    recordProvenanceEvents(db, [
      producerCut('demo-1', RECORDED, '2026-06-01T00:00:00Z'),
      backfillCut('demo-2', BYDATE),
    ]);
    expect(isRecordedBase(db, RECORDED)).toBe(true);
    expect(isRecordedBase(db, BYDATE)).toBe(false);
  });
});

describe('attachProvenance read-first', () => {
  it('uses the recorded base and never shells git', () => {
    const [out] = attachProvenance([rec()], {
      run: forbiddenRunner,
      recordedBase: () => RECORDED,
    });
    expect(out.provenance?.base_commit).toBe(RECORDED);
    expect(out.provenance?.history_state).toBe('recorded');
  });

  it('falls back to date reconstruction when no recorded base exists', () => {
    let called = 0;
    const runner: GitRunner = () => {
      called += 1;
      return BYDATE;
    };
    const [out] = attachProvenance([rec()], { run: runner, recordedBase: () => null });
    expect(called).toBe(1); // reconstruction ran
    expect(out.provenance?.base_commit).toBe(BYDATE);
    expect(out.provenance?.history_state).toBe('commit-by-date');
  });

  it('records a base even for a rig the date heuristic cannot resolve', () => {
    // No base_branch anywhere → provenanceInput yields no branch → date path is
    // `unresolved`; a recorded cut still pins the exact base.
    const noBranch = rec({ rig: 'unmapped', metadata: { 'gc.work_dir': '/w/demo' } });
    const input = provenanceInput(noBranch);
    expect(input?.base_branch).toBeUndefined();
    const prov = provenanceFromRecorded(input!, RECORDED);
    expect(prov.history_state).toBe('recorded');
    expect(prov.base_commit).toBe(RECORDED);
    expect(prov.base_branch).toBeUndefined();
  });
});

describe('read-first payoff: recorded bases are temporally admissible', () => {
  const withProvenance = (history_state: 'recorded' | 'commit-by-date'): WorkRecord =>
    rec({
      provenance: {
        work_dir: '/w/demo',
        repo: 'demo',
        base_branch: 'main',
        base_commit: RECORDED,
        history_state,
      },
    });

  it('drops a commit-by-date base as approximate_start but admits a recorded one', () => {
    expect(temporalWallDrop(withProvenance('commit-by-date'))).toBe('approximate_start');
    // The exact, READ base is not approximate — it may anchor a temporal wall.
    expect(temporalWallDrop(withProvenance('recorded'))).toBeNull();
  });
});
