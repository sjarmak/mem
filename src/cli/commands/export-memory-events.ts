import { writeFileSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import { allMemoryEvents } from '../../store/index.js';
import type { MemoryEvent } from '../../schemas/memory-event.js';

export interface ExportMemoryEventsResult {
  count: number;
  /** NDJSON destination when `--out` was given, else null. */
  out: string | null;
  events: MemoryEvent[];
}

/**
 * `mem export-memory-events [--store PATH] [--out FILE]` — dump every
 * write-time-captured memory event (mem-31kz), in event-time order. The export
 * half of the schema-bump round-trip: like lessons, this table is runtime
 * exhaust a rebuild cannot regenerate, so a version bump is export → rebuild →
 * `import-memory-events`. With `--out` the events are NDJSON
 * (`import-memory-events`' input format); otherwise they ride the `--json`
 * envelope.
 */
export function exportMemoryEventsCommand(ctx: CommandContext): ExportMemoryEventsResult {
  const outOpt = ctx.options.out;
  if (outOpt !== undefined && typeof outOpt !== 'string') {
    throw new Error('--out requires a path: mem export-memory-events --out FILE');
  }

  const events = withReadStore(ctx.options, db => allMemoryEvents(db));

  let out: string | null = null;
  if (outOpt !== undefined) {
    const ndjson = events.map(event => JSON.stringify(event)).join('\n');
    writeFileSync(outOpt, ndjson === '' ? '' : `${ndjson}\n`, 'utf8');
    out = outOpt;
  }

  if (!ctx.options.json) {
    console.error(`exported ${events.length} memory event(s)${out === null ? '' : ` to ${out}`}`);
  }

  return { count: events.length, out, events };
}
