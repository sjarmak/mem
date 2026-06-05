import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import { searchErrorMessages, type ErrorSearchHit } from '../../store/index.js';

export interface SearchErrorsResult {
  query: string;
  limit: number;
  count: number;
  hits: ErrorSearchHit[];
}

/** The reader's own default; restated so the result envelope can report it. */
const DEFAULT_LIMIT = 20;

/** Parse `--limit` into a positive integer, or fall back to the default. */
function parseLimit(value: string | boolean | undefined): number {
  if (value === undefined) return DEFAULT_LIMIT;
  if (typeof value !== 'string') throw new Error('--limit requires a value');
  const n = Number(value);
  if (!Number.isInteger(n) || n < 1) {
    throw new Error('--limit must be a positive integer');
  }
  return n;
}

/**
 * `mem search-errors <fts-query> [--limit N] [--store PATH]` — full-text search
 * over trace-error messages (the Decision-8 weak tiebreaker), best match first.
 * `<fts-query>` is raw FTS5 MATCH syntax; this is a trusted-operator surface,
 * not a composed retrieval policy (that is Phase 2).
 */
export function searchErrorsCommand(ctx: CommandContext): SearchErrorsResult {
  const query = ctx.args[0];
  if (query === undefined) {
    throw new Error('search-errors requires a query: mem search-errors <fts-query>');
  }
  const limit = parseLimit(ctx.options.limit);

  const hits = withReadStore(ctx.options, db => searchErrorMessages(db, query, limit));

  if (!ctx.options.json) {
    for (const hit of hits) {
      console.error(`${hit.work_id}\t${hit.signature}\t${hit.message}`);
    }
    console.error(`${hits.length} hit(s) for "${query}"`);
  }

  return { query, limit, count: hits.length, hits };
}
