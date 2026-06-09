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

/**
 * Run-level metadata for one session transcript — the cost/identity/shape
 * signal the transcript carries beyond build/test/lint executions. Every field
 * is a deterministic projection of the JSONL (token usage, model id, harness
 * version, tool-call shape, turn count, time span), so the row never embeds a
 * judgment the model would have to make.
 *
 * Two fields are *deterministic last-seen* picks, not aggregates, because the
 * row carries one scalar each: `model` and `harness_version` take the value
 * from the final assistant message — the model/harness that produced the
 * session's last output. `outcome` is that final assistant message's
 * `stop_reason` verbatim (`end_turn` / `tool_use` / `max_tokens` / …): a literal
 * transcript field, NOT a pass/fail oracle (the verifiable outcome lives on the
 * WorkRecord). Tokens are summed across every `message.usage`; `n_turns` counts
 * `user` + `assistant` entries (the same definition the trace index uses).
 */
export const TraceRunSchema = z.object({
  /** The Claude session UUID, read from the transcript's `sessionId`. */
  session_uuid: z.string().min(1),
  /** Model id of the final assistant message (e.g. `claude-opus-4-8`). */
  model: z.string().optional(),
  /** Harness version from the transcript's top-level `version`. */
  harness_version: z.string().optional(),
  input_tokens: z.number().int().nonnegative(),
  output_tokens: z.number().int().nonnegative(),
  cache_creation_tokens: z.number().int().nonnegative(),
  cache_read_tokens: z.number().int().nonnegative(),
  /** Total `tool_use` blocks across all assistant messages. */
  n_tool_calls: z.number().int().nonnegative(),
  /** `tool_use` count keyed by tool name (`Bash`, `Read`, …). */
  tool_calls_by_type: z.record(z.string(), z.number().int().nonnegative()).default({}),
  /** `user` + `assistant` entry count. */
  n_turns: z.number().int().nonnegative(),
  /** Earliest entry timestamp. */
  started_at: z.string().optional(),
  /** Latest entry timestamp. */
  ended_at: z.string().optional(),
  /** The final assistant message's `stop_reason`, verbatim. */
  outcome: z.string().optional(),
});

export type TraceRun = z.infer<typeof TraceRunSchema>;
