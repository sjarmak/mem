import { readFileSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import {
  RetrievalQuerySchema,
  queryFromRecord,
  retrieve,
  type RetrievalQuery,
  type RetrievalResult,
  type RetrievalScope,
} from '../../retrieve/index.js';

type OptionValue = string | boolean | undefined;

/** CLI scope spellings → the internal {@link RetrievalScope} (Decision 7). */
const SCOPES: Record<string, RetrievalScope> = {
  'cross-rig': 'cross_rig',
  'same-rig': 'same_rig_temporal',
};

/** Require a string value for a flag that takes one; a bare `--flag` throws. */
function asString(value: OptionValue, flag: string): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw new Error(`--${flag} requires a value`);
  return value;
}

/** Parse the required `--scope`; reject anything outside the two tracks. */
function parseScope(value: OptionValue): RetrievalScope {
  const str = asString(value, 'scope');
  if (str === undefined) {
    throw new Error(`--scope is required: one of ${Object.keys(SCOPES).join(', ')}`);
  }
  const scope = SCOPES[str];
  if (scope === undefined) {
    throw new Error(`--scope must be one of: ${Object.keys(SCOPES).join(', ')}`);
  }
  return scope;
}

/** Parse the optional `--limit` (Decision-10 cap); 0 is valid (return nothing
 * but still report `total_matched`). */
function parseLimit(value: OptionValue): number | undefined {
  const str = asString(value, 'limit');
  if (str === undefined) return undefined;
  const n = Number(str);
  if (!Number.isInteger(n) || n < 0) {
    throw new Error('--limit must be a non-negative integer');
  }
  return n;
}

/** Load and validate an externally supplied query context from a JSON file. */
function readQueryFile(path: string): RetrievalQuery {
  const raw = readFileSync(path, 'utf8');
  return RetrievalQuerySchema.parse(JSON.parse(raw));
}

/**
 * `mem retrieve (<work_id> | --query FILE) --scope cross-rig|same-rig
 *  [--limit N] [--store PATH]`
 *
 * Retrieval-v1 (contract D6–D10). Two query sources, exactly one required:
 * a stored `work_id` (replay mode — derive the query from the closed record,
 * the P2.2 harness path) or `--query FILE` (a live query context as JSON).
 * Returns the ranked prior memories + distilled lessons under the chosen scope.
 */
export function retrieveCommand(ctx: CommandContext): RetrievalResult {
  const scope = parseScope(ctx.options.scope);
  const limit = parseLimit(ctx.options.limit);
  const workId = ctx.args[0];
  const queryFile = asString(ctx.options.query, 'query');

  if ((workId === undefined) === (queryFile === undefined)) {
    throw new Error('retrieve takes exactly one of a work_id or --query FILE');
  }

  const result = withReadStore(ctx.options, db => {
    const query =
      workId !== undefined ? queryFromRecord(db, workId) : readQueryFile(queryFile as string);
    return retrieve(db, query, { scope, ...(limit !== undefined && { limit }) });
  });

  if (!ctx.options.json) {
    for (const item of result.items) {
      const dup =
        item.work_id === result.items[0]?.work_id && result.near_duplicate_top ? ' [near-dup]' : '';
      console.error(
        `${item.work_id}\t${item.rig}\t${item.match}\t${item.lessons.length} lesson(s)${dup}\t${item.title}`
      );
    }
    console.error(
      `${result.items.length}/${result.total_matched} item(s) [${result.scope}]` +
        (result.fts_truncated ? ' (fts truncated)' : '')
    );
  }

  return result;
}
