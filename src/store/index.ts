/**
 * store/ — the WorkRecord graph + extracted signal (P1.5). Substrate: SQLite +
 * FTS5 sidecar (decided 2026-06-05); the dolt bead store remains the work
 * spine, this store holds the joined audit graph + trace-derived signal.
 */
export { SCHEMA_VERSION } from './schema.js';
export { type StoreDatabase, openStore } from './sqlite.js';
export { type LessonInput, appendLesson, writeRecords } from './writer.js';
export {
  type ErrorSearchHit,
  type RecordFilter,
  type StoredLesson,
  getRecord,
  lessonsFor,
  queryRecords,
  searchErrorMessages,
  supersedesClosure,
  workIdsBySignature,
} from './reader.js';
export { renderProjection, renderRecurrence, replaceBetweenMarkers } from './render.js';
