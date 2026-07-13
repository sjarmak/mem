/**
 * store/ — the WorkRecord graph + extracted signal (P1.5). Substrate: SQLite +
 * FTS5 sidecar (decided 2026-06-05); the dolt bead store remains the work
 * spine, this store holds the joined audit graph + trace-derived signal.
 */
export { SCHEMA_VERSION } from './schema.js';
export { toIsoUtc } from './timestamp.js';
export {
  type StoreDatabase,
  NON_REGENERABLE_TABLES,
  openStore,
  openStoreForExport,
  tableExists,
} from './sqlite.js';
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
  type SiblingColumns,
  type StoredLesson,
  type StoredProvLink,
  type StoredRun,
  allLessons,
  coverageReport,
  getRecord,
  lastKLessons,
  lessonsFor,
  lessonsForRig,
  linksFor,
  maxLessonId,
  queryRecords,
  runsFor,
  SEARCH_ERROR_DEFAULT_LIMIT,
  searchErrorMessages,
  siblingColumnsByWorkIds,
  supersedesClosure,
  workIdsBySignature,
  workIdsBySignatureSince,
} from './reader.js';
export {
  type ImportProvenanceEventsResult,
  deriveProvenanceEvents,
  importProvenanceEvents,
  producerProvenanceEvents,
  provenanceEventsByRef,
  provenanceEventsFor,
  recordProvenanceEvents,
} from './provenance.js';
export {
  type ImportMemoryEventsResult,
  recordMemoryEvents,
  memoryEventsFor,
  memoryEventsBySession,
  allMemoryEvents,
  importMemoryEvents,
} from './memory-events.js';
export { renderProjection, renderRecurrence, replaceBetweenMarkers } from './render.js';
