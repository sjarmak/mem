import { provenanceEventsByRef } from '../store/provenance.js';
import { BACKFILL_SOURCE, GIT_SHA_RE } from '../schemas/provenance-event.js';
import type { StoreDatabase } from '../store/sqlite.js';

/**
 * Read-first provenance: resolve a record's base commit by READING a
 * producer-recorded `cut` event from the provenance log, instead of
 * reconstructing it by date (ingest/provenance). This is the consumer half of
 * the provenance_events primitive — the "stop reconstructing, start reading"
 * path. When a real producer (a git hook at worktree-creation, gascity) has
 * recorded the exact fork SHA, the provenance stage prefers it and skips git
 * entirely; the date-heuristic remains the fallback when no event exists.
 *
 * Honesty guard: only events whose `source` is NOT the ingest backfill count.
 * The backfill projector (deriveProvenanceEvents) writes `cut` events FROM the
 * date-heuristic reconstruction, so reading those back would be circular — it
 * would relabel an approximation as `recorded` (exact). A base is `recorded`
 * only when an independent producer wrote it.
 */

/** A 40-hex base SHA recorded by a producer for `workId`, or null. */
export type RecordedBaseLookup = (workId: string) => string | null;

interface CutRow {
  work_id: string;
  ref: string;
}

/**
 * Build an in-memory lookup of producer-recorded base commits from the store.
 * Reads once and holds no db handle, so callers may close the store afterward.
 * A work_id with several producer `cut` events resolves to the most recent by
 * event-time (deterministic): later producer knowledge supersedes earlier.
 */
export function loadRecordedBases(db: StoreDatabase): RecordedBaseLookup {
  const rows = db
    .prepare(
      `SELECT work_id, ref FROM provenance_events
       WHERE kind = 'cut' AND ref_kind = 'git-sha' AND source <> ?
       ORDER BY occurred_at IS NULL, occurred_at, id`
    )
    .all(BACKFILL_SOURCE) as CutRow[];

  // Last write wins: the ORDER BY puts the most recent event-time last, so a
  // plain Map.set leaves the newest recorded base per work_id. A ref that is not
  // a 40-hex SHA is skipped, never returned: it would abort provenanceFromRecorded
  // (ProvenanceSchema's 40-hex guard) and crash the build. The producer CLI
  // rejects malformed git-sha refs at write time; this is the defensive read.
  const byWork = new Map<string, string>();
  for (const row of rows) {
    if (GIT_SHA_RE.test(row.ref)) byWork.set(row.work_id, row.ref);
  }

  return workId => byWork.get(workId) ?? null;
}

/** True when any producer (non-backfill) `cut` event points at `sha` — the
 * exact join the date heuristic cannot make. Thin helper over the by-ref read,
 * surfaced for callers that have a SHA and want its recording status. */
export function isRecordedBase(db: StoreDatabase, sha: string): boolean {
  return provenanceEventsByRef(db, sha).some(e => e.kind === 'cut' && e.source !== BACKFILL_SOURCE);
}
