import { readFileSync } from 'node:fs';

import type { Execution, TraceError, TraceRun } from '../schemas/trace.js';
import type { WorkRecord } from '../schemas/workrecord.js';
import { dedupeErrors, extractErrors } from './error-extractors.js';
import { matchRunner } from './runners.js';

/**
 * Deterministic trace parse (P1.6) — read a Claude Code transcript JSONL and
 * extract the engram-style failure signal: build/test/lint **tool-call outcomes**
 * ({@link Execution}) and the **file:line errors** in their output
 * ({@link TraceError}). This is the deterministic half of the parse stage
 * (ARCHITECTURE.md §Pipeline); the model-backed semantic extractor is separate.
 *
 * The transcript format (see ingest/trace-index): assistant entries carry
 * `message.content[]` blocks, including `tool_use` ({id, name, input}); the
 * matching result is a `user` entry whose `message.content[]` holds a
 * `tool_result` ({tool_use_id, is_error}) and whose `toolUseResult` carries the
 * Bash `stdout`/`stderr`. We pair the two by id and keep only Bash calls that
 * run a recognized build/test/lint runner.
 *
 * Pure parse: unparseable lines are skipped (append-only logs may end mid-write,
 * exactly as the trace index tolerates), never silently corrupting the result.
 */

/** Parsed deterministic signal for one transcript. */
export interface ParsedTrace {
  /** One record per build/test/lint Bash execution, in transcript order. */
  tool_outcomes: Execution[];
  /** The de-duplicated union of every file:line error across all executions. */
  errors: TraceError[];
  /** Run-level metadata (tokens, model, harness, tool-call shape, turns, span).
   * Absent when the transcript has no user/assistant entries, or when no entry
   * carries a `sessionId` (the run's required natural key). */
  run?: TraceRun;
}

/** The `message.usage` block — only the token fields this parser sums. */
interface MessageUsage {
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
}

/** Transcript-entry shape — only the fields this parser reads. Cast (not `any`)
 * to stay within the type-checked lint, matching ingest/trace-index. */
interface TranscriptEntry {
  type?: string;
  sessionId?: string;
  version?: string;
  timestamp?: string;
  message?: {
    content?: unknown;
    model?: string;
    stop_reason?: string;
    usage?: MessageUsage;
  };
  toolUseResult?: unknown;
}

/** A content block inside `message.content[]`. */
interface ContentBlock {
  type?: string;
  // tool_use
  id?: string;
  name?: string;
  input?: { command?: unknown };
  // tool_result
  tool_use_id?: string;
  is_error?: boolean;
  content?: unknown;
}

/** A pending Bash tool call awaiting its result. */
interface PendingCall {
  command: string;
}

/** Coerce a value to string, treating null/undefined as empty. */
function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/** The content of a tool_result block can be a plain string or an array of
 * `{ type: 'text', text }` blocks; flatten either to text. */
function resultBlockText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map(part => {
      const block = part as { text?: unknown };
      return asText(block.text);
    })
    .join('\n');
}

/** Combined stdout+stderr for a Bash call, preferring the structured
 * `toolUseResult`, falling back to the tool_result block's content text. */
function executionOutput(entry: TranscriptEntry, block: ContentBlock): string {
  const tur = entry.toolUseResult as { stdout?: unknown; stderr?: unknown } | undefined;
  if (tur && (typeof tur.stdout === 'string' || typeof tur.stderr === 'string')) {
    return `${asText(tur.stdout)}\n${asText(tur.stderr)}`;
  }
  return resultBlockText(block.content);
}

/** Iterate `message.content[]` as typed blocks (empty when absent/non-array). */
function contentBlocks(entry: TranscriptEntry): ContentBlock[] {
  const content = entry.message?.content;
  return Array.isArray(content) ? (content as ContentBlock[]) : [];
}

/** Read-as-you-go fold of run-level metadata across transcript entries. The
 * scalar `model`/`harness_version`/`outcome` are last-write-wins over assistant
 * entries (the final assistant message's values), matching {@link TraceRun}'s
 * documented contract. `seen` gates whether any run is emitted at all. */
interface RunAccumulator {
  seen: boolean;
  session_uuid?: string;
  model?: string;
  harness_version?: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  n_tool_calls: number;
  tool_calls_by_type: Record<string, number>;
  n_turns: number;
  started_at?: string;
  ended_at?: string;
  outcome?: string;
}

function newRunAccumulator(): RunAccumulator {
  return {
    seen: false,
    input_tokens: 0,
    output_tokens: 0,
    cache_creation_tokens: 0,
    cache_read_tokens: 0,
    n_tool_calls: 0,
    tool_calls_by_type: {},
    n_turns: 0,
  };
}

/** A non-negative integer, or 0 — usage fields are summed, so a missing or
 * malformed value contributes nothing rather than poisoning the total. A
 * non-integer (e.g. a malformed `10.5`) is truncated so the parser's output is
 * always store-safe against the schema's `int()` token columns. */
function asCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0;
}

/** Fold one parsed entry into the run accumulator (mutates `acc` in place — it
 * is loop-local scratch, never shared). Times span every timestamped entry;
 * turns count user + assistant; tokens/model/tool-calls come from assistants. */
function foldRunEntry(acc: RunAccumulator, entry: TranscriptEntry): void {
  if (acc.session_uuid === undefined && entry.sessionId) acc.session_uuid = entry.sessionId;
  if (typeof entry.timestamp === 'string') {
    if (acc.started_at === undefined) acc.started_at = entry.timestamp;
    acc.ended_at = entry.timestamp;
  }

  if (entry.type === 'user' || entry.type === 'assistant') {
    acc.seen = true;
    acc.n_turns += 1;
  }
  if (entry.type !== 'assistant') return;

  const message = entry.message;
  if (message?.model) acc.model = message.model;
  if (entry.version) acc.harness_version = entry.version;
  if (message?.stop_reason) acc.outcome = message.stop_reason;

  const usage = message?.usage;
  if (usage) {
    acc.input_tokens += asCount(usage.input_tokens);
    acc.output_tokens += asCount(usage.output_tokens);
    acc.cache_creation_tokens += asCount(usage.cache_creation_input_tokens);
    acc.cache_read_tokens += asCount(usage.cache_read_input_tokens);
  }

  for (const block of contentBlocks(entry)) {
    if (block.type !== 'tool_use' || !block.name) continue;
    acc.n_tool_calls += 1;
    acc.tool_calls_by_type[block.name] = (acc.tool_calls_by_type[block.name] ?? 0) + 1;
  }
}

/** Project the accumulator into a {@link TraceRun}, or `undefined` when the
 * transcript held no user/assistant turn, or carried no `sessionId` to key the
 * run on (the `NOT NULL` session_uuid column has nowhere to store such a run). */
function finalizeRun(acc: RunAccumulator): TraceRun | undefined {
  if (!acc.seen || acc.session_uuid === undefined) return undefined;
  return {
    session_uuid: acc.session_uuid,
    ...(acc.model !== undefined && { model: acc.model }),
    ...(acc.harness_version !== undefined && { harness_version: acc.harness_version }),
    input_tokens: acc.input_tokens,
    output_tokens: acc.output_tokens,
    cache_creation_tokens: acc.cache_creation_tokens,
    cache_read_tokens: acc.cache_read_tokens,
    n_tool_calls: acc.n_tool_calls,
    tool_calls_by_type: { ...acc.tool_calls_by_type },
    n_turns: acc.n_turns,
    ...(acc.started_at !== undefined && { started_at: acc.started_at }),
    ...(acc.ended_at !== undefined && { ended_at: acc.ended_at }),
    ...(acc.outcome !== undefined && { outcome: acc.outcome }),
  };
}

/**
 * Parse a transcript's JSONL text into deterministic build/test/lint signal.
 *
 * `status` is `fail` when the runner exited non-zero (`tool_result.is_error`)
 * **or** when its output contains parseable errors. The second clause is load-
 * bearing for this corpus: agents pervasively pipe build/test output
 * (`npm run check 2>&1 | tail`, `… | grep`), and a pipe makes the shell report
 * the *pager's* exit code, so `is_error` is `false` even when the build failed.
 * Falling back to parsed errors recovers those masked failures — the
 * recurring-failure signal we must keep. (A failure that is both masked *and*
 * unparseable is indistinguishable from success by deterministic means; that is
 * an accepted limit, not something to paper over with keyword sniffing.)
 */
export function parseTranscript(text: string): ParsedTrace {
  const pending = new Map<string, PendingCall>();
  const tool_outcomes: Execution[] = [];
  const allErrors: TraceError[] = [];
  const run = newRunAccumulator();

  for (const line of text.split('\n')) {
    if (line.trim() === '') continue;

    let entry: TranscriptEntry;
    try {
      entry = JSON.parse(line) as TranscriptEntry;
    } catch {
      continue;
    }

    foldRunEntry(run, entry);

    if (entry.type === 'assistant') {
      for (const block of contentBlocks(entry)) {
        if (block.type !== 'tool_use' || block.name !== 'Bash' || !block.id) continue;
        pending.set(block.id, { command: asText(block.input?.command) });
      }
      continue;
    }

    if (entry.type !== 'user') continue;

    for (const block of contentBlocks(entry)) {
      if (block.type !== 'tool_result' || !block.tool_use_id) continue;
      const call = pending.get(block.tool_use_id);
      if (!call) continue;
      pending.delete(block.tool_use_id);
      const runner = matchRunner(call.command);
      if (runner === null) continue;

      const errors = extractErrors(executionOutput(entry, block));
      const status = block.is_error === true || errors.length > 0 ? 'fail' : 'pass';
      tool_outcomes.push({ runner, command: call.command, status, errors });
      allErrors.push(...errors);
    }
  }

  const finalizedRun = finalizeRun(run);
  return {
    tool_outcomes,
    errors: dedupeErrors(allErrors),
    ...(finalizedRun !== undefined && { run: finalizedRun }),
  };
}

/** Reads a transcript file's text. Injectable so {@link parseRecordTrace} is
 * unit-testable without touching the filesystem. */
export type TraceReader = (path: string) => string;

const defaultReader: TraceReader = path => readFileSync(path, 'utf8');

/** True for a "file not found" error — a reaped transcript, an expected
 * outcome, not a misconfiguration. */
function isFileNotFound(err: unknown): boolean {
  return (err as { code?: unknown }).code === 'ENOENT';
}

/**
 * Attach parsed deterministic signal to a WorkRecord's `trace`. Reads the
 * record's `trace.jsonl_path` (resolved in P1.3), parses it, and returns a new
 * record with `trace.tool_outcomes`, `trace.errors`, and `trace.run` populated.
 *
 * Records without a resolved trace are returned unchanged. A reaped transcript
 * (ENOENT) leaves the parsed fields absent — preserving the schema's "not yet
 * parsed" vs "parsed, found nothing" distinction — while any other IO error
 * propagates rather than being swallowed. The input is never mutated.
 */
export function parseRecordTrace(
  record: WorkRecord,
  read: TraceReader = defaultReader
): WorkRecord {
  const path = record.trace?.jsonl_path;
  if (!path) return record;

  let text: string;
  try {
    text = read(path);
  } catch (err) {
    if (isFileNotFound(err)) return record;
    throw err;
  }

  const { tool_outcomes, errors, run } = parseTranscript(text);
  return {
    ...record,
    trace: { ...record.trace, jsonl_path: path, tool_outcomes, errors, ...(run && { run }) },
  };
}
