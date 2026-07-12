import { CommandContext } from '../index.js';
import { asPositiveInt } from '../io.js';
import { withReadStore } from '../store.js';
import {
  searchErrorMessages,
  SEARCH_ERROR_DEFAULT_LIMIT,
  type ErrorSearchHit,
} from '../../store/index.js';

export interface SearchErrorsResult {
  query: string;
  limit: number;
  count: number;
  hits: ErrorSearchHit[];
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
  const limit = asPositiveInt(ctx.options.limit, 'limit') ?? SEARCH_ERROR_DEFAULT_LIMIT;

  const hits = withReadStore(ctx.options, db => searchErrorMessages(db, query, limit));

  if (!ctx.options.json) {
    for (const hit of hits) {
      console.error(`${hit.work_id}\t${hit.signature}\t${hit.message}`);
    }
    console.error(`${hits.length} hit(s) for "${query}"`);
  }

  return { query, limit, count: hits.length, hits };
}
