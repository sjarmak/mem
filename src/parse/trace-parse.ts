import { readFileSync } from 'node:fs';

import type { Execution, TraceError } from '../schemas/trace.js';
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
}

/** Transcript-entry shape — only the fields this parser reads. Cast (not `any`)
 * to stay within the type-checked lint, matching ingest/trace-index. */
interface TranscriptEntry {
  type?: string;
  message?: { content?: unknown };
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

  for (const line of text.split('\n')) {
    if (line.trim() === '') continue;

    let entry: TranscriptEntry;
    try {
      entry = JSON.parse(line) as TranscriptEntry;
    } catch {
      continue;
    }

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

  return { tool_outcomes, errors: dedupeErrors(allErrors) };
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
 * record with `trace.tool_outcomes` and `trace.errors` populated.
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

  const { tool_outcomes, errors } = parseTranscript(text);
  return { ...record, trace: { ...record.trace, jsonl_path: path, tool_outcomes, errors } };
}
