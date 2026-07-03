import { writeFileSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { withExportStore } from '../store.js';
import { producerProvenanceEvents } from '../../store/index.js';
import type { ProvenanceEvent } from '../../schemas/provenance-event.js';

export interface ExportProvenanceEventsResult {
  count: number;
  /** NDJSON destination when `--out` was given, else null. */
  out: string | null;
  events: ProvenanceEvent[];
}

/**
 * `mem export-provenance-events [--store PATH] [--out FILE]` — dump every
 * PRODUCER-recorded provenance event (source ≠ `ingest-backfill`), in
 * event-time order. The export half of the schema-bump round-trip: producer
 * rows are the non-regenerable subset of the append-only log (backfilled rows
 * re-derive deterministically on every build, so they are never carried), so a
 * version bump is export → rebuild → `import-provenance-events`. Reads across
 * a schema-version mismatch — rescuing rows out of a store the current version
 * refuses to open is exactly what this exists for. With `--out` the events are
 * NDJSON (`import-provenance-events`' input format); otherwise they ride the
 * `--json` envelope.
 */
export function exportProvenanceEventsCommand(ctx: CommandContext): ExportProvenanceEventsResult {
  const outOpt = ctx.options.out;
  if (outOpt !== undefined && typeof outOpt !== 'string') {
    throw new Error('--out requires a path: mem export-provenance-events --out FILE');
  }

  const events = withExportStore(ctx.options, db => producerProvenanceEvents(db));

  let out: string | null = null;
  if (outOpt !== undefined) {
    const ndjson = events.map(event => JSON.stringify(event)).join('\n');
    writeFileSync(outOpt, ndjson === '' ? '' : `${ndjson}\n`, 'utf8');
    out = outOpt;
  }

  if (!ctx.options.json) {
    console.error(
      `exported ${events.length} producer provenance event(s)${out === null ? '' : ` to ${out}`}`
    );
  }

  return { count: events.length, out, events };
}
