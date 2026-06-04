/**
 * ingest/ — readers per source (dolt bead store P1.2, trace JSONLs P1.3,
 * gh PR outcomes P1.4). Output: raw WorkRecords. Pure IO.
 */
export * from './beads.js';
export * from './outcomes.js';
export {
  type TraceIndexEntry,
  defaultProjectsRoot,
  indexTraces,
  traceIndexByPath,
} from './trace-index.js';
export {
  type SessionResolver,
  type AttachTraceOptions,
  attachTraceRefs,
  gcSessionResolver,
  parseSessionId,
  parseTranscriptPath,
} from './trace-resolve.js';
