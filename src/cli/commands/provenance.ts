import { CommandContext } from '../index.js';
import type { CliOptions } from '../index.js';
import { storePath, withReadStore, withWriteStore } from '../store.js';
import {
  provenanceEventsByRef,
  provenanceEventsFor,
  recordProvenanceEvents,
} from '../../store/index.js';
import {
  PROVENANCE_KINDS,
  ProvenanceEventSchema,
  type ProvenanceEvent,
  type ProvenanceKind,
} from '../../schemas/provenance-event.js';

/**
 * `mem provenance record|log|by-ref` — the PRODUCER + reader surface for the
 * append-only provenance log (mirror of the proposed `bd provenance` CLI). This
 * is what a git hook / orchestrator calls to record the facts mem otherwise
 * reconstructs: the exact fork SHA at worktree creation (`cut`), a claim, a land.
 * Once a producer records a `cut`, build-store's read-first path uses it instead
 * of the date heuristic (ingest/provenance-from-log) — `commit-by-date` becomes
 * the exact `recorded`.
 *
 *   mem provenance record --issue <id> --kind cut --ref <sha> --ref-kind git-sha
 *                         --source git-hook [--actor <a>] [--at <iso>] [--payload <json>]
 *   mem provenance log <issue-id> [--kind <k>]
 *   mem provenance by-ref <ref>
 *
 * Append-only: there is no update/delete verb. Re-recording the same event is a
 * no-op (the deterministic id dedups), so a hook that fires twice is harmless.
 */

/** The source the ingest backfill projector owns; a producer may not claim it,
 * or the read-first honesty guard (which excludes backfill) would skip it. */
const BACKFILL_SOURCE = 'ingest-backfill';

function requireString(options: CliOptions, key: string): string {
  const value = options[key];
  if (typeof value !== 'string' || value === '') {
    throw new Error(`--${key} is required`);
  }
  return value;
}

function optionalString(options: CliOptions, key: string): string | undefined {
  const value = options[key];
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/** Result of `provenance record`: whether a NEW row was written (0 when the event
 * already existed — append-only/idempotent) and its deterministic id. */
export interface RecordProvenanceResult {
  recorded: number;
  id: string;
  work_id: string;
  kind: ProvenanceKind;
}

/** Deterministic id so a hook firing twice for the same checkout dedups via
 * INSERT OR IGNORE. Keyed on the fields that identify the fact, not the clock. */
function eventId(source: string, workId: string, kind: string, discriminator: string): string {
  return `${source}:${workId}:${kind}:${discriminator}`;
}

function recordSubcommand(ctx: CommandContext, nowIso: string): RecordProvenanceResult {
  const workId = requireString(ctx.options, 'issue');
  const kind = requireString(ctx.options, 'kind');
  if (!(PROVENANCE_KINDS as readonly string[]).includes(kind)) {
    throw new Error(`--kind must be one of: ${PROVENANCE_KINDS.join(', ')}`);
  }
  const source = optionalString(ctx.options, 'source') ?? 'cli';
  if (source === BACKFILL_SOURCE) {
    throw new Error(`--source '${BACKFILL_SOURCE}' is reserved for the ingest projector`);
  }
  const ref = optionalString(ctx.options, 'ref');
  const occurredAt = optionalString(ctx.options, 'at') ?? nowIso;
  const payloadRaw = optionalString(ctx.options, 'payload');
  let payload: Record<string, unknown> | undefined;
  if (payloadRaw !== undefined) {
    try {
      payload = JSON.parse(payloadRaw) as Record<string, unknown>;
    } catch {
      throw new Error('--payload must be valid JSON');
    }
  }

  const event: ProvenanceEvent = ProvenanceEventSchema.parse({
    id: eventId(source, workId, kind, ref ?? occurredAt),
    work_id: workId,
    kind,
    actor: optionalString(ctx.options, 'actor'),
    ref,
    ref_kind: optionalString(ctx.options, 'ref-kind'),
    payload,
    source,
    occurred_at: occurredAt,
    created_at: nowIso,
  });

  const recorded = withWriteStore(ctx.options, db => recordProvenanceEvents(db, [event]));

  if (!ctx.options.json) {
    const verb = recorded === 1 ? 'recorded' : 'already present';
    console.error(`provenance ${event.kind} for ${workId} ${verb} (${storePath(ctx.options)})`);
  }
  return { recorded, id: event.id, work_id: workId, kind: event.kind };
}

function logSubcommand(ctx: CommandContext): ProvenanceEvent[] {
  const workId = ctx.args[1];
  if (workId === undefined) throw new Error('usage: mem provenance log <issue-id> [--kind <k>]');
  const kind = optionalString(ctx.options, 'kind');
  if (kind !== undefined && !(PROVENANCE_KINDS as readonly string[]).includes(kind)) {
    throw new Error(`--kind must be one of: ${PROVENANCE_KINDS.join(', ')}`);
  }
  const events = withReadStore(ctx.options, db =>
    provenanceEventsFor(db, workId, kind as ProvenanceKind | undefined)
  );
  if (!ctx.options.json) {
    console.error(`${events.length} provenance events for ${workId}`);
  }
  return events;
}

function byRefSubcommand(ctx: CommandContext): ProvenanceEvent[] {
  const ref = ctx.args[1];
  if (ref === undefined) throw new Error('usage: mem provenance by-ref <ref>');
  const events = withReadStore(ctx.options, db => provenanceEventsByRef(db, ref));
  if (!ctx.options.json) {
    console.error(`${events.length} provenance events reference ${ref}`);
  }
  return events;
}

/**
 * Dispatch on the subcommand (`record` | `log` | `by-ref`). The clock is read
 * once here and threaded into `record` so the rest is a pure function of its
 * inputs (testable without mocking time).
 */
export function provenanceCommand(ctx: CommandContext): RecordProvenanceResult | ProvenanceEvent[] {
  const sub = ctx.args[0];
  switch (sub) {
    case 'record':
      return recordSubcommand(ctx, new Date().toISOString());
    case 'log':
      return logSubcommand(ctx);
    case 'by-ref':
      return byRefSubcommand(ctx);
    default:
      throw new Error('usage: mem provenance <record|log|by-ref> ...');
  }
}
