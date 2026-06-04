import { CommandContext } from '../index.js';
import { WorkRecord } from '../../schemas/workrecord.js';
import { defaultConnection, doltRunner, readAllRigs, readRig } from '../../ingest/beads.js';

export interface IngestBeadsResult {
  rig: string | null;
  count: number;
  records: WorkRecord[];
}

/**
 * `mem ingest-beads [--rig <name>]` — read the WorkRecord spine from the dolt
 * bead store across all rigs (or one `--rig`). Prints a count to stderr; the
 * records ride the `--json` envelope.
 */
export async function ingestBeadsCommand(ctx: CommandContext): Promise<IngestBeadsResult> {
  const rig = typeof ctx.options.rig === 'string' ? ctx.options.rig : null;
  const run = doltRunner(defaultConnection());
  const records = rig === null ? await readAllRigs(run) : await readRig(run, rig);

  if (!ctx.options.json) {
    const scope = rig === null ? 'all rigs' : rig;
    console.error(`ingested ${records.length} work records from ${scope}`);
  }

  return { rig, count: records.length, records };
}
