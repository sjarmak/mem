/**
 * parse/ — deterministic extractor (P1.6 ports engram capture/reflect:
 * tool-call outcomes + file:line errors, cross-task recurrence confidence)
 * plus a model-backed semantic extractor. ZFC: mechanical in code, judgment
 * via model. These are the shapes it consumes/produces.
 */
export type { Execution, TraceError, TraceRun } from '../schemas/trace.js';

export { type ErrorExtractor, extractErrors } from './error-extractors.js';
export {
  type ParsedTrace,
  type TraceReader,
  parseTranscript,
  parseRecordTrace,
} from './trace-parse.js';
export {
  type FailureTrace,
  type RecurrenceInsight,
  type RecurrenceOptions,
  computeRecurrence,
  errorClass,
  failureSignature,
  recurrenceFromRecords,
} from './recurrence.js';
