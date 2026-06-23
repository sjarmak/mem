/**
 * ingest/ — readers per source (dolt bead store P1.2, trace JSONLs P1.3,
 * gh PR outcomes P1.4). Output: raw WorkRecords. Pure IO.
 */
export * from './beads.js';
export * from './outcomes.js';
export { type RigRepo, RIG_REPOS } from './rig-repo-map.js';
export { type RepoResolution, attachRepo, resolveRepo } from './repo-resolve.js';
export {
  type TraceIndexEntry,
  defaultProjectsRoot,
  indexTraces,
  traceIndexByPath,
} from './trace-index.js';
export {
  type TranscriptArchive,
  defaultArchiveRoot,
  loadTranscriptArchive,
} from './trace-archive.js';
export {
  type SessionResolver,
  type AttachTraceOptions,
  attachTraceRefs,
  gcSessionResolver,
  parseSessionId,
  parseTranscriptPath,
} from './trace-resolve.js';
export {
  type JoinSessionEntry,
  type SessionJoin,
  attachSessionJoin,
  loadSessionJoin,
} from './session-merge.js';
export {
  type TaskTypeArtifact,
  type TaskTypeEntry,
  MODEL_TASK_TAXONOMY,
  attachTaskTypes,
  deriveMechanicalType,
  loadTaskTypes,
} from './task-type.js';
