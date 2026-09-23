import { readFile } from 'node:fs/promises';

import { CommandContext } from '../index.js';
import { asString, readStdin } from '../io.js';
import {
  type ContextReach,
  countReachesByTool,
  scanContextReaches,
} from '../../parse/context-reach.js';

/** `mine-reaches` result: the reaches plus the two denominators needed to read
 * them honestly — how many transcript entries were parsed, and the per-tool
 * tally. */
export interface MineReachesResult {
  /** Transcript entries that parsed as JSON. Always > 0 (zero is an error). */
  entries_parsed: number;
  /** `reaches.length`, carried explicitly so a consumer reading only the
   * envelope's scalars does not have to hydrate the array. */
  reach_count: number;
  /** Reach count keyed by transcript tool name. */
  by_tool: Record<string, number>;
  reaches: ContextReach[];
}

/**
 * `mem mine-reaches [--file PATH] [--json]` — read one Claude transcript JSONL
 * (from `--file`, or stdin) and emit every deterministic context reach in it:
 * the file reads, globs, greps and read-only shell lookups the agent used to go
 * find context it did not hold.
 *
 * Input is a transcript JSONL, NOT raw tool output — it wraps
 * `scanContextReaches`, not `extractErrors`.
 *
 * **Silently-empty input is an error, not an empty result.** A file that is not
 * transcript JSONL (wrong path, raw log, truncated-to-nothing capture) parses to
 * zero entries, and returning `{reaches: []}` for it would be indistinguishable
 * from a real session that reached for nothing — exactly the masked-failure
 * shape this repo bans. Zero parsed entries therefore throws.
 */
export async function mineReachesCommand(ctx: CommandContext): Promise<MineReachesResult> {
  const file = asString(ctx.options.file, 'file');
  // Trust assumption matches extract-errors: the path is operator/harness-
  // supplied and the transcript text is never reflected back into `--file`.
  const text = file !== undefined ? await readFile(file, 'utf8') : await readStdin();

  const { entries_parsed, reaches } = scanContextReaches(text);
  if (entries_parsed === 0) {
    const source = file !== undefined ? file : 'stdin';
    throw new Error(
      `no transcript entries parsed from ${source}: input is not Claude transcript JSONL`
    );
  }

  const by_tool = countReachesByTool(reaches);

  if (!ctx.options.json) {
    for (const reach of reaches) {
      console.error(`${reach.tool}\t${reach.reach_kind}\t${reach.target}`);
    }
    for (const tool of Object.keys(by_tool).sort()) {
      console.error(`${tool}: ${by_tool[tool]}`);
    }
    console.error(`${reaches.length} reach(es) over ${entries_parsed} entries`);
  }

  return { entries_parsed, reach_count: reaches.length, by_tool, reaches };
}
