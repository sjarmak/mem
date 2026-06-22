/**
 * store/ — the WorkRecord graph + extracted signal (P1.5). Substrate: SQLite +
 * FTS5 sidecar (decided 2026-06-05); the dolt bead store remains the work
 * spine, this store holds the joined audit graph + trace-derived signal.
 */
export { SCHEMA_VERSION } from './schema.js';
export { type StoreDatabase, openStore } from './sqlite.js';
export {
  type ImportLessonsResult,
  type LessonInput,
  appendLesson,
  importLessons,
  writeRecords,
} from './writer.js';
export {
  type ErrorSearchHit,
  type CoverageReport,
  type RecordFilter,
  type StoredLesson,
  type StoredProvLink,
  type StoredRun,
  allLessons,
  coverageReport,
  getRecord,
  lessonsFor,
  linksFor,
  queryRecords,
  runsFor,
  SEARCH_ERROR_DEFAULT_LIMIT,
  searchErrorMessages,
  supersedesClosure,
  workIdsBySignature,
} from './reader.js';
export {
  type ImportMemoryEventsResult,
  recordMemoryEvents,
  memoryEventsFor,
  memoryEventsBySession,
  allMemoryEvents,
  importMemoryEvents,
} from './memory-events.js';
export { renderProjection, renderRecurrence, replaceBetweenMarkers } from './render.js';
