import type { SiblingColumns } from '../store/reader.js';
import type { WorkRecord } from '../schemas/workrecord.js';
import type { RetrievalQuery } from './retrieval.js';

/**
 * Decision-6 same-work test (the non-temporal half). The temporal boundary is
 * the store reader's `closedBefore`; the supersedes-chain closure is the store
 * reader's `supersedesClosure`. What remains here is the pure, DB-free predicate
 * for "same work dodging the timestamp filter": a record sharing the query
 * work's convoy, PR, or branch.
 */

/** {@link isSibling} needs only these four scalar fields, never the rest of a
 * WorkRecord — {@link siblingColumnsFromRecord} projects them off a record
 * already in hand; `store/reader.ts`'s `siblingColumnsByWorkIds` fetches them
 * straight from SQL for a candidate that hasn't been loaded at all. */
export function siblingColumnsFromRecord(record: WorkRecord): SiblingColumns {
  return {
    work_id: record.work_id,
    convoy_id: record.links.convoy_id ?? null,
    pr: record.outcome?.pr ?? null,
    external_ref: record.external_ref ?? null,
    parent: record.links.parent ?? null,
  };
}

/**
 * NULL-safe sibling test: a record is the query work's sibling when it shares
 * the query's convoy, PR, branch (`external_ref`), or epic parent — or sits on
 * the parent-child axis itself (the record IS the query's epic, or a child of
 * the query work). The parent key is ingest-derived data (`record.links.parent`,
 * mem-qgdz), never re-parsed here. Each comparison only fires when the query
 * side names a value — absence on either side never matches absence.
 */
export function isSibling(record: SiblingColumns, query: RetrievalQuery): boolean {
  return (
    (query.convoy_id !== undefined && record.convoy_id === query.convoy_id) ||
    (query.pr !== undefined && record.pr === query.pr) ||
    (query.external_ref !== undefined && record.external_ref === query.external_ref) ||
    (query.parent !== undefined &&
      (record.parent === query.parent || record.work_id === query.parent)) ||
    record.parent === query.work_id
  );
}
