import type { WorkRecord } from '../schemas/workrecord.js';
import type { RetrievalQuery } from './retrieval.js';

/**
 * Decision-6 same-work test (the non-temporal half). The temporal boundary is
 * the store reader's `closedBefore`; the supersedes-chain closure is the store
 * reader's `supersedesClosure`. What remains here is the pure, DB-free predicate
 * for "same work dodging the timestamp filter": a record sharing the query
 * work's convoy, PR, or branch.
 */

/**
 * NULL-safe sibling test: a record is the query work's sibling when it shares
 * the query's convoy, PR, or branch (`external_ref`). Each comparison only
 * fires when the query side names a value — absence on either side never
 * matches absence.
 */
export function isSibling(record: WorkRecord, query: RetrievalQuery): boolean {
  return (
    (query.convoy_id !== undefined && record.links.convoy_id === query.convoy_id) ||
    (query.pr !== undefined && record.outcome?.pr === query.pr) ||
    (query.external_ref !== undefined && record.external_ref === query.external_ref)
  );
}
