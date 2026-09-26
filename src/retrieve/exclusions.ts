import type { SiblingColumns } from '../store/reader.js';
import type { RetrievalQuery } from './retrieval.js';

export function isSibling(record: SiblingColumns, query: RetrievalQuery): boolean {
  return (
    (query.convoy_id !== undefined && record.convoy_id === query.convoy_id) ||
    (query.pr !== undefined && record.pr === query.pr) ||
    (query.external_ref !== undefined && record.external_ref === query.external_ref) ||
    (query.session_uuid !== undefined && record.session_uuids.includes(query.session_uuid)) ||
    (query.parent !== undefined &&
      (record.parent === query.parent || record.work_id === query.parent)) ||
    record.parent === query.work_id
  );
}
