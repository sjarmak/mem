import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

import { z } from 'zod';

import { defaultGitRunner, isNonZeroExit, type GitRunner } from '../ingest/provenance.js';
import { RIG_REPOS } from '../ingest/rig-repo-map.js';
import { LessonPayloadSchema, ConceptTagSchema, type LessonPayload } from '../schemas/lesson.js';
import type { TraceError } from '../schemas/trace.js';
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

// --- Resolution evidence -------------------------------------------------------------

/** Character budget for the resolution-evidence block. Diffs are HEAD-truncated
 * (the file headers + leading hunks orient the model); transcript tails are
 * TAIL-truncated (the resolution sits at session close). */
const RESOLUTION_CHAR_BUDGET = 6_000;

/** What resolved the record's failures: the diff that landed for its
 * `outcome.commit_sha`, or — when no commit resolved — the transcript slice from
 * the last recorded failure to session close. Truncation is always explicit
 * (`truncated` + `total_chars`) so the prompt can say what was cut. */
export type ResolutionEvidence =
  | {
      kind: 'landed-diff';
      commit_sha: string;
      text: string;
      truncated: boolean;
      total_chars: number;
    }
  | { kind: 'transcript-tail'; text: string; truncated: boolean; total_chars: number };

/** IO seams for evidence resolution, injectable so the distiller stays testable
 * without a git checkout or a transcript on disk. */
export interface EvidenceOptions {
  /** work_dir + args → stdout runner. Defaults to the real git CLI. */
  run?: GitRunner;
  /** jsonl_path → transcript text. Defaults to a filesystem read. */
  readTranscript?: (path: string) => string;
}

/** Cut `text` to the budget at a line boundary, keeping the HEAD. */
function truncateHead(text: string): { text: string; truncated: boolean } {
  if (text.length <= RESOLUTION_CHAR_BUDGET) return { text, truncated: false };
  const cut = text.lastIndexOf('\n', RESOLUTION_CHAR_BUDGET);
  return { text: text.slice(0, cut > 0 ? cut : RESOLUTION_CHAR_BUDGET), truncated: true };
}

/** Cut `text` to the budget at a line boundary, keeping the TAIL. */
function truncateTail(text: string): { text: string; truncated: boolean } {
  if (text.length <= RESOLUTION_CHAR_BUDGET) return { text, truncated: false };
  const cut = text.indexOf('\n', text.length - RESOLUTION_CHAR_BUDGET);
  return {
    text: text.slice(cut >= 0 ? cut + 1 : text.length - RESOLUTION_CHAR_BUDGET),
    truncated: true,
  };
}

/** The rig's canonical checkout dir, when it is mapped 1:1 with a repo. */
function rigDir(rig: string): string | undefined {
  const mapped = RIG_REPOS[rig];
  return mapped !== undefined && mapped.multi !== true ? mapped.dir : undefined;
}

/** The landed diff for the record's `outcome.commit_sha`, shown from its
 * provenance work_dir (or the rig's canonical checkout). Null — degrade to the
 * transcript, never abort — when no sha/work_dir exists or git exits non-zero
 * (sha absent from this clone, checkout gone), mirroring ingest/landed. */
function landedDiff(record: WorkRecord, run: GitRunner): ResolutionEvidence | null {
  const sha = record.outcome?.commit_sha;
  if (sha === undefined) return null;
  const work_dir = record.provenance?.work_dir ?? rigDir(record.rig);
  if (work_dir === undefined) return null;
  let diff: string;
  try {
    // `--end-of-options` pins the DB-sourced sha as a revision, so a hostile
    // value cannot inject a git flag (same guard as ingest/provenance).
    diff = run(work_dir, ['show', '--no-color', '--format=%s', '--end-of-options', sha]);
  } catch (err) {
    if (isNonZeroExit(err)) return null;
    throw err; // a missing git binary is a misconfiguration, not absent evidence
  }
  if (diff.trim() === '') return null;
  const { text, truncated } = truncateHead(diff);
  return { kind: 'landed-diff', commit_sha: sha, text, truncated, total_chars: diff.length };
}

/** Offset of the LAST occurrence of any recorded error message in the raw
 * transcript — exact-substring matching of already-extracted evidence (raw and
 * JSON-escaped forms, since JSONL escapes quotes/newlines), not a keyword
 * heuristic. -1 when no message is found. */
function lastFailureIndex(content: string, errors: readonly TraceError[]): number {
  let best = -1;
  for (const error of errors) {
    for (const needle of [error.message, JSON.stringify(error.message).slice(1, -1)]) {
      const at = content.lastIndexOf(needle);
      if (at > best) best = at;
    }
  }
  return best;
}

/** The transcript slice from the last recorded failure to session close — the
 * span where the fix happened. Null when the record has no trace, the file is
 * unreadable (moved/archived since ingest: an expected degraded case, recognized
 * by the fs error `code`), or the transcript is empty. */
function transcriptTail(
  record: WorkRecord,
  read: (path: string) => string
): ResolutionEvidence | null {
  const path = record.trace?.jsonl_path;
  if (path === undefined) return null;
  let content: string;
  try {
    content = read(path);
  } catch (err) {
    if (typeof (err as NodeJS.ErrnoException).code !== 'string') throw err;
    return null;
  }
  if (content.trim() === '') return null;
  const from = lastFailureIndex(content, record.trace?.errors ?? []);
  const slice = from >= 0 ? content.slice(from) : content;
  const { text, truncated } = truncateTail(slice);
  return { kind: 'transcript-tail', text, truncated, total_chars: slice.length };
}

/**
 * Resolve the record's resolution evidence: the landed diff when a commit_sha
 * resolved against a checkout, else the transcript tail, else null. Pure IO +
 * mechanical truncation (ZFC) — what the evidence MEANS is the model's job.
 */
export function resolveResolutionEvidence(
  record: WorkRecord,
  opts: EvidenceOptions = {}
): ResolutionEvidence | null {
  const diff = landedDiff(record, opts.run ?? defaultGitRunner);
  if (diff !== null) return diff;
  return transcriptTail(record, opts.readTranscript ?? (path => readFileSync(path, 'utf8')));
}

// --- Prompt assembly -----------------------------------------------------------------

const MAX_PROMPT_ERRORS = 20;

/** The prompt's resolution-evidence block, truncation stated explicitly. */
function evidenceLines(evidence: ResolutionEvidence | null): string[] {
  if (evidence === null) {
    return ['Resolution evidence: none available (no landed diff, no readable transcript).'];
  }
  if (evidence.kind === 'landed-diff') {
    return [
      `Resolution evidence — the landed diff for commit ${evidence.commit_sha}:`,
      evidence.text,
      ...(evidence.truncated
        ? [
            `(diff truncated: first ${evidence.text.length} of ${evidence.total_chars} characters; later hunks omitted)`,
          ]
        : []),
    ];
  }
  return [
    'Resolution evidence — transcript tail from the last recorded failure to session close:',
    evidence.text,
    ...(evidence.truncated
      ? [
          `(transcript truncated: final ${evidence.text.length} of ${evidence.total_chars} characters)`,
        ]
      : []),
  ];
}

/**
 * The distillation prompt: everything the record knows about the failure, its
 * toolchain context, and the resolution evidence, plus the exact payload
 * contract. The model judges what the lesson is; this function only formats
 * evidence — resolving it is {@link resolveResolutionEvidence}'s job.
 */
export function buildDistillPrompt(
  record: WorkRecord,
  evidence: ResolutionEvidence | null
): string {
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
    ...evidenceLines(evidence),
    ``,
    `The work CLOSED successfully, so these errors were overcome. Distill the durable lesson: the root cause, what actually resolved it, and what a future agent should do differently or watch out for. Be concrete and codebase-specific — name the real files, types, flags, and error codes from the evidence. Do not invent facts the evidence does not support; when the evidence does not show what resolved the errors, say so instead of guessing.`,
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
  extractedAt: string,
  evidence: EvidenceOptions = {}
): DistillOutcome {
  const lessons: LessonInput[] = [];
  const failures: DistillFailure[] = [];
  for (const record of records) {
    try {
      const prompt = buildDistillPrompt(record, resolveResolutionEvidence(record, evidence));
      const payload = parseDistilledPayload(runner(prompt));
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
