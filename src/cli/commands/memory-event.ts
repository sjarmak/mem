import { CommandContext } from '../index.js';
import { asEnum, asString, readStdin } from '../io.js';
import { storePath, withReadStore } from '../store.js';
import {
  openStore,
  recordMemoryEvents,
  memoryEventsFor,
  memoryEventsBySession,
} from '../../store/index.js';
import {
  MEMORY_OPS,
  MEMORY_BACKENDS,
  MemoryEventSchema,
  type MemoryEvent,
} from '../../schemas/memory-event.js';
import { buildCaptureEvent } from '../../ingest/memory-capture.js';

/**
 * `mem memory-event <subcommand>` — the write-time forward-capture surface
 * (mem-31kz). Subcommands:
 *
 *  - `capture` — read a PostToolUse hook payload on stdin, project it into a
 *    MemoryEvent (or no-op if not an in-scope memory op), append to the store.
 *    The bash hook (scripts/hooks/capture-memory-event.sh) pipes into this.
 *  - `record`  — append one event from explicit flags (manual / non-hook
 *    producers / tests).
 *  - `log`     — read events for `--work-id` or `--session`.
 *
 * Producer subcommands open the store with {@link openStore} (materializing a
 * fresh one if absent), since capture must not fail merely because the corpus
 * store has not been built yet. `log` is a read and errors on a missing store.
 */
export interface CaptureResult {
  recorded: number;
  event_id?: string;
  reason?: string;
}

export interface RecordResult {
  recorded: number;
  event_id: string;
}

/** Append the projected events and report. Shared by capture/record. */
function appendOne(options: CommandContext['options'], event: MemoryEvent): number {
  const db = openStore(storePath(options));
  try {
    return recordMemoryEvents(db, [event]);
  } finally {
    db.close();
  }
}

function captureSubcommand(ctx: CommandContext, raw: string): CaptureResult {
  if (raw.trim() === '') {
    return { recorded: 0, reason: 'empty-input' };
  }
  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    return { recorded: 0, reason: 'invalid-json' };
  }
  if (typeof payload !== 'object' || payload === null) {
    return { recorded: 0, reason: 'invalid-payload' };
  }
  const now = asString(ctx.options.now, 'now') ?? new Date().toISOString();
  const event = buildCaptureEvent(payload, { env: process.env, now });
  if (event === null) {
    return { recorded: 0, reason: 'not-a-memory-op' };
  }
  const recorded = appendOne(ctx.options, event);
  return { recorded, event_id: event.id };
}

function recordSubcommand(ctx: CommandContext): RecordResult {
  const session = asString(ctx.options.session, 'session');
  if (session === undefined) {
    throw new Error('record requires --session');
  }
  const op = asEnum(ctx.options.op, MEMORY_OPS, 'op');
  if (op === undefined) {
    throw new Error(`record requires --op (one of: ${MEMORY_OPS.join(', ')})`);
  }
  const backend = asEnum(ctx.options.backend, MEMORY_BACKENDS, 'backend') ?? 'filesystem';
  const memoryRef = asString(ctx.options.ref, 'ref');
  const usedIn = asString(ctx.options['used-in'], 'used-in');
  const workId = asString(ctx.options['work-id'], 'work-id');
  const tool = asString(ctx.options.tool, 'tool');
  const source = asString(ctx.options.source, 'source') ?? 'manual';
  const now = asString(ctx.options.now, 'now') ?? new Date().toISOString();
  const occurredAt = asString(ctx.options['occurred-at'], 'occurred-at') ?? now;

  const event = MemoryEventSchema.parse({
    id: `${source}:${session}:${occurredAt}:${op}:${memoryRef ?? ''}`,
    session,
    ...(workId !== undefined && { work_id: workId }),
    op,
    backend,
    ...(memoryRef !== undefined && { memory_ref: memoryRef }),
    ...(usedIn !== undefined && { used_in: usedIn }),
    ...(tool !== undefined && { concrete_tool: tool }),
    source,
    occurred_at: occurredAt,
    created_at: now,
  });

  const recorded = appendOne(ctx.options, event);
  return { recorded, event_id: event.id };
}

function logSubcommand(ctx: CommandContext): { events: MemoryEvent[] } {
  const workId = asString(ctx.options['work-id'], 'work-id');
  const session = asString(ctx.options.session, 'session');
  if (workId === undefined && session === undefined) {
    throw new Error('log requires --work-id or --session');
  }
  const op = asEnum(ctx.options.op, MEMORY_OPS, 'op');
  const events = withReadStore(ctx.options, db =>
    workId !== undefined
      ? memoryEventsFor(db, workId, op)
      : memoryEventsBySession(db, session as string)
  );
  if (!ctx.options.json) {
    for (const ev of events) {
      console.error(`${ev.occurred_at ?? ev.created_at} ${ev.op} ${ev.memory_ref ?? ''}`);
    }
  }
  return { events };
}

export async function memoryEventCommand(ctx: CommandContext): Promise<unknown> {
  const sub = ctx.args[0];
  switch (sub) {
    case 'capture':
      return captureSubcommand(ctx, await readStdin());
    case 'record':
      return recordSubcommand(ctx);
    case 'log':
      return logSubcommand(ctx);
    default:
      throw new Error('usage: mem memory-event <capture|record|log> [options]');
  }
}
