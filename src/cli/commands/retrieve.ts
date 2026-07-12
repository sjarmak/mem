import { readFileSync } from 'node:fs';

import { CommandContext } from '../index.js';
import { asEnum, asNonNegativeInt, asString, type OptionValue } from '../io.js';
import { withReadStore } from '../store.js';
import {
  RetrievalQuerySchema,
  queryFromRecord,
  retrieve,
  toDetails,
  toIndex,
  type RetrievalDetails,
  type RetrievalIndex,
  type RetrievalQuery,
  type RetrievalResult,
  type RetrievalScope,
} from '../../retrieve/index.js';

/** CLI scope spellings → the internal {@link RetrievalScope} (Decision 7). */
const SCOPES: Record<string, RetrievalScope> = {
  'cross-rig': 'cross_rig',
  'same-rig': 'same_rig_temporal',
};

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

/** Load and validate an externally supplied query context from a JSON file. */
function readQueryFile(path: string): RetrievalQuery {
  const raw = readFileSync(path, 'utf8');
  return RetrievalQuerySchema.parse(JSON.parse(raw));
}

/** The progressive-disclosure layer selector (P2.5). */
type RetrieveFormat = 'full' | 'index' | 'details';

/** Parse the optional `--format` (default `full`, the pre-disclosure shape). */
function parseFormat(value: OptionValue): RetrieveFormat {
  return asEnum(value, ['full', 'index', 'details'] as const, 'format') ?? 'full';
}

/** Parse the optional `--pick a,b,c` (details-layer selection). Duplicate
 * ids are harmless — selection is set-based downstream. */
function parsePick(value: OptionValue, format: RetrieveFormat): string[] | undefined {
  const str = asString(value, 'pick');
  if (str === undefined) return undefined;
  if (format !== 'details') {
    throw new Error('--pick only applies to --format details');
  }
  const ids = str
    .split(',')
    .map(id => id.trim())
    .filter(id => id !== '');
  if (ids.length === 0) throw new Error('--pick requires at least one work_id');
  return ids;
}

function printFull(result: RetrievalResult): void {
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

function printIndex(index: RetrievalIndex): void {
  for (const item of index.items) {
    const dup =
      item.work_id === index.items[0]?.work_id && index.near_duplicate_top ? ' [near-dup]' : '';
    console.error(
      `${item.uri}\t${item.rig}\t${item.match}\t${item.lesson_count} lesson(s)\t~${item.token_cost} tok${dup}\t${item.title}`
    );
  }
  console.error(
    `${index.items.length}/${index.total_matched} item(s) [${index.scope}] ` +
      `~${index.token_cost_total} tok to hydrate all` +
      (index.fts_truncated ? ' (fts truncated)' : '')
  );
}

function printDetails(details: RetrievalDetails): void {
  for (const item of details.items) {
    console.error(`${item.uri}\t${item.rig}\t${item.match}\t${item.title}`);
  }
  console.error(`${details.items.length} item(s) hydrated [${details.scope}]`);
}

/** Parse the bare `--no-trace-query` flag (mem-tnyo): replay-mode-only switch
 * to the `issue-text` trigger — the query is formed WITHOUT the held record's
 * stored trace errors, from its title/task_type instead. */
function parseNoTraceQuery(value: OptionValue, workId: string | undefined): boolean {
  if (value === undefined) return false;
  if (value !== true) throw new Error('--no-trace-query takes no value');
  if (workId === undefined) {
    throw new Error('--no-trace-query applies to replay mode only (needs a work_id)');
  }
  return true;
}

/**
 * `mem retrieve (<work_id> [--no-trace-query] | --query FILE)
 *  --scope cross-rig|same-rig
 *  [--limit N] [--format full|index|details] [--pick a,b] [--store PATH]`
 *
 * Retrieval-v1 (contract D6–D10). Two query sources, exactly one required:
 * a stored `work_id` (replay mode — derive the query from the closed record,
 * the P2.2 harness path) or `--query FILE` (a live query context as JSON).
 * Returns the ranked prior memories + distilled lessons under the chosen scope.
 *
 * Replay mode defaults to the `trace` trigger — the held record's own stored
 * errors, an ORACLE trigger (mem-tnyo). `--no-trace-query` switches to the
 * `issue-text` trigger (title/task_type only, the dispatch-time fields), the
 * separable control; the boundary and D6 exclusions are unchanged.
 *
 * `--format` selects the progressive-disclosure layer (P2.5): `index` is the
 * L1 listing with per-item hydration token costs, `details` is the L2
 * hydration of the `--pick`ed work_ids (all items when omitted), and `full`
 * (the default) is the original flat result. Retrieval is deterministic, so
 * an index call followed by a details call sees the same ranking.
 */
export function retrieveCommand(
  ctx: CommandContext
): RetrievalResult | RetrievalIndex | RetrievalDetails {
  const scope = parseScope(ctx.options.scope);
  const limit = asNonNegativeInt(ctx.options.limit, 'limit');
  const format = parseFormat(ctx.options.format);
  const pick = parsePick(ctx.options.pick, format);
  const workId = ctx.args[0];
  const queryFile = asString(ctx.options.query, 'query');

  if ((workId === undefined) === (queryFile === undefined)) {
    throw new Error('retrieve takes exactly one of a work_id or --query FILE');
  }
  const noTraceQuery = parseNoTraceQuery(ctx.options['no-trace-query'], workId);

  const result = withReadStore(ctx.options, db => {
    const query =
      workId !== undefined
        ? queryFromRecord(db, workId, { trigger: noTraceQuery ? 'issue-text' : 'trace' })
        : readQueryFile(queryFile as string);
    return retrieve(db, query, { scope, ...(limit !== undefined && { limit }) });
  });

  if (format === 'index') {
    const index = toIndex(result);
    if (!ctx.options.json) printIndex(index);
    return index;
  }
  if (format === 'details') {
    const details = toDetails(result, pick);
    if (!ctx.options.json) printDetails(details);
    return details;
  }
  if (!ctx.options.json) printFull(result);
  return result;
}
