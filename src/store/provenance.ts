import {
  ProvenanceEventSchema,
  type ProvenanceEvent,
  type ProvenanceKind,
} from '../schemas/provenance-event.js';
import type { WorkRecord } from '../schemas/workrecord.js';
import type { StoreDatabase } from './sqlite.js';

/**
 * Provenance event log surface (mem-side prototype of the proposed beads
 * `provenance_events` primitive). Three operations, mirroring the proposed
 * `bd provenance record|log|by-ref` CLI:
 *
 *  - {@link recordProvenanceEvents} — append (INSERT OR IGNORE; id is the dedup
 *    key, so re-recording a deterministic backfilled event is a no-op and the
 *    log stays append-only — no update/delete path exists).
 *  - {@link provenanceEventsFor} — the `log <work_id>` read.
 *  - {@link provenanceEventsByRef} — the `by-ref <ref>` read (which work bound
 *    to this SHA / PR / transcript).
 *
 * {@link deriveProvenanceEvents} is the *consumer/backfill* path: it projects
 * the facts mem already reconstructs (provenance.base_commit, the agents list,
 * landed/outcome) into events, so the read path is exercised with real data and
 * the migration "dual-write" comparison is possible. It deliberately emits ONLY
 * the kinds that are honestly reconstructable today (`cut`, `claim`, `land`) —
 * never `commit` (no per-commit attribution exists; that is the ambiguous-window
 * gap) and never `used` (retrieval causality is absent, not lossy). The gaps are
 * exactly what real producers must fill.
 */

const BACKFILL_SOURCE = 'ingest-backfill';

/** Deterministic id for a backfilled event so re-ingest is idempotent. The
 * discriminator distinguishes events of the same kind on one record (e.g. the
 * per-agent `claim`s, or a `land` by-commit vs by-pr). */
function backfillId(workId: string, kind: ProvenanceKind, discriminator: string): string {
  return `${BACKFILL_SOURCE}:${workId}:${kind}:${discriminator}`;
}

interface ProvenanceRow {
  id: string;
  work_id: string;
  kind: string;
  actor: string | null;
  ref: string | null;
  ref_kind: string | null;
  payload: string | null;
  source: string;
  occurred_at: string | null;
  created_at: string;
}

/** Re-validate on the way out: a row was schema-conformant when written, so a
 * parse failure here means store corruption — fail loudly (the reader idiom). */
function parseRow(row: ProvenanceRow): ProvenanceEvent {
  return ProvenanceEventSchema.parse({
    id: row.id,
    work_id: row.work_id,
    kind: row.kind,
    actor: row.actor ?? undefined,
    ref: row.ref ?? undefined,
    ref_kind: row.ref_kind ?? undefined,
    payload: row.payload === null ? undefined : (JSON.parse(row.payload) as Record<string, unknown>),
    source: row.source,
    occurred_at: row.occurred_at ?? undefined,
    created_at: row.created_at,
  });
}

const INSERT_EVENT = `
INSERT OR IGNORE INTO provenance_events
  (id, work_id, kind, actor, ref, ref_kind, payload, source, occurred_at, created_at)
VALUES
  (@id, @work_id, @kind, @actor, @ref, @ref_kind, @payload, @source, @occurred_at, @created_at)
`;

/** Append events. Returns the number of NEW rows inserted (duplicates by `id`
 * are ignored, never overwritten — the append-only contract). Validates each
 * event's structure before writing. */
export function recordProvenanceEvents(db: StoreDatabase, events: ProvenanceEvent[]): number {
  const stmt = db.prepare(INSERT_EVENT);
  const insert = db.transaction((rows: ProvenanceEvent[]) => {
    let inserted = 0;
    for (const raw of rows) {
      const ev = ProvenanceEventSchema.parse(raw);
      const result = stmt.run({
        id: ev.id,
        work_id: ev.work_id,
        kind: ev.kind,
        actor: ev.actor ?? null,
        ref: ev.ref ?? null,
        ref_kind: ev.ref_kind ?? null,
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

/** All events for one bead, optionally filtered to a single kind, ordered by
 * event-time then id (deterministic — null occurred_at sorts last). */
export function provenanceEventsFor(
  db: StoreDatabase,
  workId: string,
  kind?: ProvenanceKind
): ProvenanceEvent[] {
  const rows = (
    kind === undefined
      ? db
          .prepare(
            `SELECT * FROM provenance_events WHERE work_id = ?
             ORDER BY occurred_at IS NULL, occurred_at, id`
          )
          .all(workId)
      : db
          .prepare(
            `SELECT * FROM provenance_events WHERE work_id = ? AND kind = ?
             ORDER BY occurred_at IS NULL, occurred_at, id`
          )
          .all(workId, kind)
  ) as ProvenanceRow[];
  return rows.map(parseRow);
}

/** Every event pointing at a given ref (which work bound to this SHA / PR).
 * The exact join the ambiguous-window reconstruction can only approximate. */
export function provenanceEventsByRef(db: StoreDatabase, ref: string): ProvenanceEvent[] {
  const rows = db
    .prepare(
      `SELECT * FROM provenance_events WHERE ref = ?
       ORDER BY occurred_at IS NULL, occurred_at, id`
    )
    .all(ref) as ProvenanceRow[];
  return rows.map(parseRow);
}

/**
 * Project a built WorkRecord's reconstructed provenance into events. Pure: takes
 * the ingest timestamp rather than reading a clock, so it is deterministic and
 * testable (the codebase convention — cf. LessonInput.extracted_at).
 *
 * Emits, when the underlying fact is present:
 *  - `cut`   from provenance.base_commit (payload carries history_state so the
 *            dual-write comparison can see it was a commit-by-date approximation)
 *  - `claim` one per agent in sequence order (multi-session → multiple claims;
 *            this is the interrupt/resume signal made first-class)
 *  - `land`  from landed.landed_commit (git fact) and, separately, from
 *            outcome.pr (PR fact) so a by-ref lookup finds either
 */
export function deriveProvenanceEvents(record: WorkRecord, ingestedAt: string): ProvenanceEvent[] {
  const events: ProvenanceEvent[] = [];
  const { work_id } = record;
  const primaryActor = record.agents[0]?.agent_id;

  const base = record.provenance?.base_commit;
  if (base !== undefined) {
    events.push({
      id: backfillId(work_id, 'cut', base),
      work_id,
      kind: 'cut',
      actor: primaryActor,
      ref: base,
      ref_kind: 'git-sha',
      payload: {
        history_state: record.provenance?.history_state,
        base_branch: record.provenance?.base_branch,
        base_branch_source: record.provenance?.base_branch_source,
      },
      source: BACKFILL_SOURCE,
      occurred_at: record.lifecycle.started,
      created_at: ingestedAt,
    });
  }

  record.agents.forEach((agent, index) => {
    const disc = agent.sequence !== undefined ? `seq${agent.sequence}` : `idx${index}`;
    events.push({
      id: backfillId(work_id, 'claim', `${agent.agent_id}:${disc}`),
      work_id,
      kind: 'claim',
      actor: agent.agent_id,
      ref: agent.trace_ref,
      ref_kind: agent.trace_ref !== undefined ? 'transcript' : undefined,
      payload: {
        role: agent.role,
        sequence: agent.sequence,
        sources: agent.sources,
        suspect: agent.suspect ?? false,
      },
      source: BACKFILL_SOURCE,
      occurred_at: agent.started_at ?? record.lifecycle.started,
      created_at: ingestedAt,
    });
  });

  const landedCommit = record.landed?.landed_commit;
  if (landedCommit !== undefined) {
    events.push({
      id: backfillId(work_id, 'land', landedCommit),
      work_id,
      kind: 'land',
      actor: primaryActor,
      ref: landedCommit,
      ref_kind: 'git-sha',
      payload: {
        landed_state: record.landed?.landed_state,
        n_commits: record.landed?.n_commits,
      },
      source: BACKFILL_SOURCE,
      occurred_at: record.lifecycle.closed,
      created_at: ingestedAt,
    });
  }

  const pr = record.outcome?.pr;
  if (pr !== undefined) {
    events.push({
      id: backfillId(work_id, 'land', `pr:${pr}`),
      work_id,
      kind: 'land',
      actor: primaryActor,
      ref: pr,
      ref_kind: 'pr',
      payload: {
        pr_state: record.outcome?.pr_state,
        ci: record.outcome?.ci,
        commit_sha: record.outcome?.commit_sha,
      },
      source: BACKFILL_SOURCE,
      occurred_at: record.lifecycle.closed,
      created_at: ingestedAt,
    });
  }

  return events;
}
