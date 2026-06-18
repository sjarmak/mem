import type { WorkRecord } from '../schemas/workrecord.js';

/**
 * Leakage gate (a) — the TEMPORAL WALL (PRD §6a). A scored eval task may only be
 * judged against memory that demonstrably predates it, and only when its own
 * timeline is trustworthy. Two mechanical checks, both fail-closed:
 *
 *  - **Task admissibility** ({@link temporalWallDrop}): a record can anchor a
 *    wall only if its replay baseline is EXACT. A `commit-by-date` baseline is an
 *    approximation of where the work started (see ProvenanceSchema), so it cannot
 *    separate before-from-after and the task is dropped from the scored set — it
 *    may still live in the store as a T3 memory. The start/outcome instants must
 *    also be present and strictly ordered (an outcome at or before the start is a
 *    corrupt timeline, never a real one).
 *  - **Memory eligibility** ({@link memoryPredatesTaskStart}): a retrieved memory
 *    is usable for a task only if the memory's work finished STRICTLY before the
 *    task began. A memory with no resolvable outcome instant is ineligible — we
 *    never assume an artifact predates the wall.
 *
 * Pure predicates over the WorkRecord timeline; the store query that narrows
 * candidates and the eval harness that scores them live elsewhere.
 */

/** Why a record cannot be a scored task, or null when it is admissible. */
export type TemporalDrop =
  | 'approximate_start'
  | 'missing_start'
  | 'missing_outcome_time'
  | 'outcome_not_after_start';

/** The instant a record's work began — the bead's recorded start. */
function taskStart(record: WorkRecord): string | undefined {
  return record.lifecycle.started;
}

/** The instant a record's work produced its result — the bead close, the best
 * outcome instant the spine carries (the verifiable commit/PR time is not stored
 * per-record). Used both as a task's t_outcome and as a memory's "available
 * from" time. */
function outcomeTime(record: WorkRecord): string | undefined {
  return record.lifecycle.closed;
}

/**
 * Admissibility of a record as a SCORED eval task. Returns the drop reason, or
 * null when the record may anchor a temporal wall. ISO-8601 timestamps compare
 * lexically, so the strict `>` is a real chronological test.
 */
export function temporalWallDrop(record: WorkRecord): TemporalDrop | null {
  // An approximate (by-date) baseline cannot anchor a wall — drop, don't guess.
  if (record.provenance?.history_state === 'commit-by-date') return 'approximate_start';
  const start = taskStart(record);
  if (start === undefined) return 'missing_start';
  const outcome = outcomeTime(record);
  if (outcome === undefined) return 'missing_outcome_time';
  if (!(outcome > start)) return 'outcome_not_after_start';
  return null;
}

/** True when `record` is admissible as a scored task (no temporal drop). */
export function isTemporallySoundTask(record: WorkRecord): boolean {
  return temporalWallDrop(record) === null;
}

/**
 * Whether `memory` is eligible to be retrieved for a task starting at
 * `taskStartIso`: its work must have finished STRICTLY before the task began.
 * Fail-closed — a memory with no outcome instant is never assumed to predate the
 * wall.
 */
export function memoryPredatesTaskStart(memory: WorkRecord, taskStartIso: string): boolean {
  const available = outcomeTime(memory);
  return available !== undefined && available < taskStartIso;
}
