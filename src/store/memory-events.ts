import { MemoryEventSchema, type MemoryEvent, type MemoryOp } from '../schemas/memory-event.js';
import type { StoreDatabase } from './sqlite.js';

/**
 * Memory-event log surface (mem-31kz forward-capture). The write-time producer
 * sink for memory reads/writes, mirroring the provenance-event log shape:
 *
 *  - {@link recordMemoryEvents} — append (INSERT OR IGNORE; `id` is the dedup
 *    key, so a re-fired PostToolUse capture hook is an idempotent no-op and the
 *    log stays append-only — no update/delete path exists).
 *  - {@link memoryEventsFor} / {@link memoryEventsBySession} — the read paths.
 *  - {@link allMemoryEvents} / {@link importMemoryEvents} — the schema-bump
 *    round-trip (this table cannot be regenerated from the spine).
 *
 * The store validates only structure (known op/backend, via the strict schema);
 * it never interprets memory_ref semantics (ZFC). Whether a captured row may
 * enter eval INPUT is the firewall's call (validity.py), not this layer's.
 */

interface MemoryEventRow {
  id: string;
  session: string;
  work_id: string | null;
  op: string;
  backend: string;
  memory_ref: string | null;
  used_in: string | null;
  concrete_tool: string | null;
  payload: string | null;
  source: string;
  occurred_at: string | null;
  created_at: string;
}

/** Re-validate on the way out: a row was schema-conformant when written, so a
 * parse failure here means store corruption — fail loudly (the reader idiom). */
function parseRow(row: MemoryEventRow): MemoryEvent {
  return MemoryEventSchema.parse({
    id: row.id,
    session: row.session,
    work_id: row.work_id ?? undefined,
    op: row.op,
    backend: row.backend,
    memory_ref: row.memory_ref ?? undefined,
    used_in: row.used_in ?? undefined,
    concrete_tool: row.concrete_tool ?? undefined,
    payload:
      row.payload === null ? undefined : (JSON.parse(row.payload) as Record<string, unknown>),
    source: row.source,
    occurred_at: row.occurred_at ?? undefined,
    created_at: row.created_at,
  });
}

const INSERT_EVENT = `
INSERT OR IGNORE INTO memory_events
  (id, session, work_id, op, backend, memory_ref, used_in, concrete_tool,
   payload, source, occurred_at, created_at)
VALUES
  (@id, @session, @work_id, @op, @backend, @memory_ref, @used_in, @concrete_tool,
   @payload, @source, @occurred_at, @created_at)
`;

/** Append events. Returns the number of NEW rows inserted (duplicates by `id`
 * are ignored, never overwritten — the append-only contract). Validates each
 * event's structure (strict allow-list) before writing. */
export function recordMemoryEvents(db: StoreDatabase, events: MemoryEvent[]): number {
  const stmt = db.prepare(INSERT_EVENT);
  const insert = db.transaction((rows: MemoryEvent[]) => {
    let inserted = 0;
    for (const raw of rows) {
      const ev = MemoryEventSchema.parse(raw);
      const result = stmt.run({
        id: ev.id,
        session: ev.session,
        work_id: ev.work_id ?? null,
        op: ev.op,
        backend: ev.backend,
        memory_ref: ev.memory_ref ?? null,
        used_in: ev.used_in ?? null,
        concrete_tool: ev.concrete_tool ?? null,
        payload: ev.payload === undefined ? null : JSON.stringify(ev.payload),
        source: ev.source,
        occurred_at: ev.occurred_at ?? null,
        created_at: ev.created_at,
      });
      inserted += result.changes;
    }
    return inserted;
  });
  return insert(events);
}

/** All events for one bead, optionally filtered to a single op, ordered by
 * event-time then id (deterministic — null occurred_at sorts last). */
export function memoryEventsFor(db: StoreDatabase, workId: string, op?: MemoryOp): MemoryEvent[] {
  const rows = (
    op === undefined
      ? db
          .prepare(
            `SELECT * FROM memory_events WHERE work_id = ?
             ORDER BY occurred_at IS NULL, occurred_at, id`
          )
          .all(workId)
      : db
          .prepare(
            `SELECT * FROM memory_events WHERE work_id = ? AND op = ?
             ORDER BY occurred_at IS NULL, occurred_at, id`
          )
          .all(workId, op)
  ) as MemoryEventRow[];
  return rows.map(parseRow);
}

/** All events emitted by one session, in event-time order. The session is
 * always known at write time even when the work_id is not yet resolved. */
export function memoryEventsBySession(db: StoreDatabase, session: string): MemoryEvent[] {
  const rows = db
    .prepare(
      `SELECT * FROM memory_events WHERE session = ?
       ORDER BY occurred_at IS NULL, occurred_at, id`
    )
    .all(session) as MemoryEventRow[];
  return rows.map(parseRow);
}

/** Every memory event in the store, in append (occurred_at, id) order — the
 * export side of the schema-bump round-trip. This table is runtime exhaust a
 * rebuild cannot regenerate, so it must be carriable across rebuilds (the same
 * contract as lessons). */
export function allMemoryEvents(db: StoreDatabase): MemoryEvent[] {
  const rows = db
    .prepare('SELECT * FROM memory_events ORDER BY occurred_at IS NULL, occurred_at, id')
    .all() as MemoryEventRow[];
  return rows.map(parseRow);
}

/** Outcome of {@link importMemoryEvents}: appended vs already-present (by id). */
export interface ImportMemoryEventsResult {
  appended: number;
  skipped: number;
}

/**
 * Append exported memory events into this store — the import side of the
 * schema-bump round-trip. INSERT OR IGNORE on the `id` PK, so importing the
 * same export twice is idempotent (duplicates skipped, never rewritten). Each
 * event is re-validated; a malformed event aborts the import (a partial export
 * is a producer bug, not something to half-apply).
 */
export function importMemoryEvents(
  db: StoreDatabase,
  events: MemoryEvent[]
): ImportMemoryEventsResult {
  // recordMemoryEvents counts NEW rows; everything else (pre-existing rows AND
  // duplicate ids within this batch) was IGNOREd, hence skipped.
  const appended = recordMemoryEvents(db, events);
  return { appended, skipped: events.length - appended };
}
