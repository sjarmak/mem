import { spawnSync } from 'node:child_process';

import { z } from 'zod';

import { LessonPayloadSchema, ConceptTagSchema, type LessonPayload } from '../schemas/lesson.js';
import type { WorkRecord } from '../schemas/workrecord.js';
import type { LessonInput } from '../store/index.js';
import { lessonsFor, queryRecords, type StoreDatabase } from '../store/index.js';

/**
 * Lessons distiller (Decision 9): produce append-only lesson payloads from
 * closed WorkRecords so retrieval has content to inject, not just citations.
 *
 * ZFC split: this module is pure plumbing — candidate selection (mechanical
 * predicates), prompt assembly, JSON-shape validation, store IO. The actual
 * distillation (what the lesson IS) is delegated to a model via the injectable
 * {@link DistillRunner}; the default runner shells to headless Claude Code on
 * the OAuth subscription (no-paid-API, Decision 4/D16).
 */

// --- Candidate selection -------------------------------------------------------------

export interface CandidateFilter {
  rig?: string;
  /** Explicit work_ids; when set, rig/limit narrow within this list. */
  workIds?: readonly string[];
  limit?: number;
  /** Re-distill records that already have lessons (default: skip them —
   * lessons are append-only, so a re-run would stack near-duplicates). */
  force?: boolean;
}

/**
 * Closed records carrying at least one parsed trace error — the corpus slice
 * with distillable failure evidence — minus records already lessoned.
 */
export function selectCandidates(db: StoreDatabase, filter: CandidateFilter): WorkRecord[] {
  const records = queryRecords(db, { rig: filter.rig, status: 'closed' });
  const wanted = filter.workIds === undefined ? null : new Set(filter.workIds);
  const picked: WorkRecord[] = [];
  for (const record of records) {
    if (wanted !== null && !wanted.has(record.work_id)) continue;
    if ((record.trace?.errors ?? []).length === 0) continue;
    if (filter.force !== true && lessonsFor(db, record.work_id).length > 0) continue;
    picked.push(record);
    if (filter.limit !== undefined && picked.length >= filter.limit) break;
  }
  return picked;
}

// --- Prompt assembly -----------------------------------------------------------------

const MAX_PROMPT_ERRORS = 20;

/**
 * The distillation prompt: everything the record knows about the failure and
 * its toolchain context, plus the exact payload contract. The model judges
 * what the lesson is; this function only formats evidence.
 */
export function buildDistillPrompt(record: WorkRecord): string {
  const allErrors = record.trace?.errors ?? [];
  const errors = allErrors.slice(0, MAX_PROMPT_ERRORS);
  const omitted = allErrors.length - errors.length;
  const outcomes = record.trace?.tool_outcomes ?? [];
  const runs = outcomes.map(o => `${o.runner}:${o.status}`).join(', ');

  const lines = [
    `You are distilling ONE reusable lesson from a completed engineering work record, for future agents working in the same codebase.`,
    ``,
    `Work record:`,
    `- work_id: ${record.work_id}`,
    `- rig (project): ${record.rig}`,
    `- title: ${record.title}`,
    ...(record.task_type !== undefined ? [`- task type: ${record.task_type}`] : []),
    `- tool runs (runner:status): ${runs === '' ? 'none recorded' : runs}`,
    ``,
    `Errors hit during the work (tool, file:line, message):`,
    ...errors.map(e => `- [${e.tool}] ${e.file}:${e.line} ${e.message}`),
    ...(omitted > 0 ? [`- (${omitted} further errors omitted)`] : []),
    ``,
    `The work CLOSED successfully, so these errors were overcome. Distill the durable lesson: what went wrong, why, and what a future agent should do differently or watch out for. Be concrete and codebase-specific — name the real files, types, flags, and error codes from the evidence. Do not invent facts the evidence does not support.`,
    ``,
    `Respond with ONLY a JSON object (no markdown fence, no prose) of this exact shape:`,
    `{`,
    `  "subtitle": "<one-sentence essence of the lesson>",`,
    `  "facts": ["<self-contained statement>", ...],   // 2-6 entries`,
    `  "narrative": "<short paragraph: root cause and resolution context>",`,
    `  "concepts": ["<tag>", ...]                      // subset of: ${ConceptTagSchema.options.join(', ')}`,
    `}`,
  ];
  return lines.join('\n');
}

// --- Model output validation ---------------------------------------------------------

/** The distiller's required payload: unlike historical lessons the convention
 * fields are mandatory here — an empty distillation is a failure, not a row. */
const DistilledPayloadSchema = LessonPayloadSchema.extend({
  subtitle: z.string().min(1),
  facts: z.array(z.string().min(1)).min(1),
  narrative: z.string().min(1),
  concepts: z.array(ConceptTagSchema).min(1),
});

/**
 * Parse the model's lesson JSON. Tolerates a markdown code fence (a model
 * formatting habit, stripped mechanically) but nothing else: non-JSON or a
 * payload missing required fields is a per-record failure, never a guess.
 */
export function parseDistilledPayload(text: string): LessonPayload {
  let body = text.trim();
  const fenced = body.match(/^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/);
  if (fenced !== null) {
    body = fenced[1];
  }
  return DistilledPayloadSchema.parse(JSON.parse(body));
}

// --- Model invocation ----------------------------------------------------------------

/** Runs one distillation prompt and returns the model's raw text. Injectable
 * so the distiller is testable without a Claude binary or network. */
export type DistillRunner = (prompt: string) => string;

const CliResultSchema = z.looseObject({ result: z.string() });

const DEFAULT_RUN_TIMEOUT_MS = 120_000;

/**
 * Headless Claude Code on the OAuth subscription — the no-paid-API lane. No
 * tools: the prompt carries all the evidence, so the run is a single turn.
 */
export function claudeRunner(model: string, timeoutMs = DEFAULT_RUN_TIMEOUT_MS): DistillRunner {
  return prompt => {
    const proc = spawnSync(
      'claude',
      ['-p', prompt, '--model', model, '--output-format', 'json', '--allowedTools', ''],
      { encoding: 'utf-8', timeout: timeoutMs, maxBuffer: 16 * 1024 * 1024 }
    );
    if (proc.error) {
      throw new Error(`claude spawn failed: ${proc.error.message}`);
    }
    if (proc.status !== 0) {
      throw new Error(`claude exited ${proc.status}: ${(proc.stderr ?? '').slice(0, 300)}`);
    }
    return CliResultSchema.parse(JSON.parse(proc.stdout)).result;
  };
}

// --- Distillation loop ---------------------------------------------------------------

export interface DistillFailure {
  work_id: string;
  error: string;
}

export interface DistillOutcome {
  lessons: LessonInput[];
  failures: DistillFailure[];
}

/**
 * Distill one lesson per record. A per-record model failure (bad JSON, missing
 * fields, spawn error) is recorded and the loop continues — one flaky
 * generation must not discard a batch — but every failure is surfaced in the
 * outcome, never swallowed.
 */
export function distillLessons(
  records: readonly WorkRecord[],
  runner: DistillRunner,
  extractedAt: string
): DistillOutcome {
  const lessons: LessonInput[] = [];
  const failures: DistillFailure[] = [];
  for (const record of records) {
    try {
      const payload = parseDistilledPayload(runner(buildDistillPrompt(record)));
      lessons.push({
        work_id: record.work_id,
        extracted_at: extractedAt,
        ...(record.outcome?.commit_sha !== undefined
          ? { commit_sha: record.outcome.commit_sha }
          : {}),
        payload,
      });
    } catch (error: unknown) {
      failures.push({
        work_id: record.work_id,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return { lessons, failures };
}
