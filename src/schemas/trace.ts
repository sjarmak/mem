import { z } from 'zod';

/**
 * Deterministic-layer trace shapes, ported from engram src/schemas/knowledge.ts
 * (the input shapes of engram's capture.ts/reflect.ts). These are the proven
 * structures for build/test/lint signal extracted from agent traces.
 *
 * The extraction logic itself (error-pattern grouping, cross-task recurrence
 * confidence) lands in P1.6 (parse/deterministic), where the trace JSONL
 * producer exists.
 */

/** A single build/test/lint error with file:line provenance. */
export const TraceErrorSchema = z.object({
  tool: z.string(),
  severity: z.enum(['error', 'warning', 'info']),
  message: z.string(),
  file: z.string(),
  line: z.number().int(),
  column: z.number().int().optional(),
});

export type TraceError = z.infer<typeof TraceErrorSchema>;

/** One tool execution (build/test/lint run) and its outcome. */
export const ExecutionSchema = z.object({
  runner: z.string(),
  command: z.string(),
  status: z.enum(['pass', 'fail']),
  errors: z.array(TraceErrorSchema),
});

export type Execution = z.infer<typeof ExecutionSchema>;
