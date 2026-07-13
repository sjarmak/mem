import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

import { z } from 'zod';

import { defaultGitRunner, isNonZeroExit, type GitRunner } from '../ingest/provenance.js';
import { RIG_REPOS } from '../ingest/rig-repo-map.js';
import { isSibling } from '../retrieve/exclusions.js';
import { queryFromRecord } from '../retrieve/index.js';
import { LessonPayloadSchema, ConceptTagSchema, type LessonPayload } from '../schemas/lesson.js';
import type { TraceError } from '../schemas/trace.js';
import type { WorkRecord } from '../schemas/workrecord.js';
import type { LessonInput } from '../store/index.js';
import {
  getRecord,
  lastKLessons,
  lessonsFor,
  queryRecords,
  siblingColumnsByWorkIds,
  supersedesClosure,
  toIsoUtc,
  workIdsBySignatureSince,
  type StoreDatabase,
} from '../store/index.js';
import {
  checkPriorFixRegression,
  recordSignatures,
  verifyFixEvidence,
  type RegressionCandidate,
  type RegressionFlag,
  type RegressionSkip,
} from './verify.js';

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
 * fields are mandatory here — an empty distillation is a failure, not a row.
 * `evidence_kind` is omitted from validation (mem-0r7l): it is never asked of
 * the model (see `buildDistillPrompt`'s JSON contract) and is always
 * overwritten mechanically after parsing (see the `distillLessons` loop
 * below) — validating it here would only give a model-hallucinated value in
 * that field the power to fail an otherwise well-formed lesson. */
const DistilledPayloadSchema = LessonPayloadSchema.omit({ evidence_kind: true }).extend({
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
      // mem-0r7l gate: refuse before spending a model call. Everywhere this
      // module builds a prompt, it does so from `admission.evidence`, not a
      // bare `resolveResolutionEvidence(...)` result — so a future admission
      // criterion added to verifyFixEvidence is honored here for free.
      const admission = verifyFixEvidence(resolveResolutionEvidence(record, evidence));
      if (!admission.admitted) {
        failures.push({ work_id: record.work_id, error: admission.reason });
        continue;
      }
      const prompt = buildDistillPrompt(record, admission.evidence);
      const payload = parseDistilledPayload(runner(prompt));
      lessons.push({
        work_id: record.work_id,
        extracted_at: extractedAt,
        ...(record.outcome?.commit_sha !== undefined
          ? { commit_sha: record.outcome.commit_sha }
          : {}),
        // Mechanical provenance — a plain field read, never asked of the model (ZFC).
        payload: { ...payload, evidence_kind: admission.evidence.kind },
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

// --- K-past-fix regression check ------------------------------------------------------

/** Default {@link computeRegressions} window — also the CLI's `--regression-window` default. */
export const DEFAULT_REGRESSION_WINDOW = 5;

/**
 * Every later-closed work_id carrying each of `lesson`'s failure signatures,
 * IN THE SAME RIG — one {@link workIdsBySignatureSince} SQL lookup per
 * signature, keyed by signature so a signature that matches nothing still
 * gets an (empty) entry in `candidatesBySignature` downstream. The rig/
 * temporal narrowing runs in SQL (joined against the promoted `work_records.
 * rig`/`closed_at` columns) so a coincidental file:line:tool:error_class
 * match in an unrelated rig, or one that predates the lesson, is never
 * fetched at all.
 */
function workIdsBySignatures(
  db: StoreDatabase,
  signatures: readonly string[],
  lesson: { extractedAtUtc: string; sourceQuery: ReturnType<typeof queryFromRecord> }
): Map<string, string[]> {
  return new Map(
    signatures.map(signature => [
      signature,
      workIdsBySignatureSince(db, signature, {
        rig: lesson.sourceQuery.rig,
        closedAfter: lesson.extractedAtUtc,
      }),
    ])
  );
}

/**
 * Builds each later-closed candidate's {@link RegressionCandidate} at most
 * once, keyed by work_id, for the K-past-fix check. A single later record can
 * carry 2+ of a lesson's failure signatures (e.g. two errors that both
 * recurred in the same later commit) — the sibling check depends only on the
 * candidate and the lesson, not on which signature matched it, so computing
 * it once here (rather than once per matching signature) avoids redundant
 * work. The sibling check itself only needs {@link SiblingColumns}
 * (convoy_id/pr/external_ref/parent) — one batched query for the whole
 * dedup'd work_id set, not a `getRecord`-and-Zod-parse per candidate
 * (mem-0xz9b): a candidate the SQL join already proved existed at query time
 * is never re-fetched just to run isSibling.
 */
function buildCandidateCache(
  db: StoreDatabase,
  workIds: readonly string[],
  lesson: { work_id: string; sourceQuery: ReturnType<typeof queryFromRecord> },
  superseded: ReadonlySet<string>
): Map<string, RegressionCandidate> {
  const columnsByWorkId = siblingColumnsByWorkIds(
    db,
    workIds.filter(workId => workId !== lesson.work_id)
  );
  const cache = new Map<string, RegressionCandidate>();
  for (const [workId, columns] of columnsByWorkId) {
    const sibling = isSibling(columns, lesson.sourceQuery) || superseded.has(workId);
    cache.set(workId, { work_id: workId, is_sibling: sibling });
  }
  return cache;
}

/**
 * Regression-aware check (mem-0r7l): for the K most-recently-appended
 * lessons, does the failure signature they documented recur in LATER-closed,
 * non-sibling work? A recurrence means that lesson's fix did not durably
 * prevent the failure — the concrete, mechanical signal that a
 * transcript-tail-only lesson (see the payload's `evidence_kind`) needed a
 * stronger layer than memory. Reuses the existing `trace_errors` signature
 * index and the Decision-6 sibling/supersedes exclusions (a later record on
 * the SAME work continuing through a convoy follow-up is expected
 * iteration, not a regression) — mechanical throughout (ZFC), never a
 * keyword or semantic judgment. Report-only: lessons are append-only
 * (Decision 9) and are never rewritten by this check.
 */
export function computeRegressions(
  db: StoreDatabase,
  k: number = DEFAULT_REGRESSION_WINDOW,
  rig?: string
): { flags: RegressionFlag[]; skipped: RegressionSkip[] } {
  // Rig-scoped (mem-0r7l): matching `selectCandidates`' own `--rig` scope —
  // otherwise a multi-rig store's K-window fills with an unrelated rig's
  // lessons and this rig's own lessons are never checked, with no signal
  // that anything was skipped (the window just silently contains 0 of them).
  // `lastKLessons` guards `k <= 0` itself (a non-positive value means "check
  // nothing", not SQLite's `LIMIT`-with-negative-value "no limit").
  const recentLessons = lastKLessons(db, k, rig);
  const flags: RegressionFlag[] = [];
  const skipped: RegressionSkip[] = [];

  for (const lesson of recentLessons) {
    // Lessons carry no FK to work_records (Decision 9): the source may have
    // been deleted, re-ingested, or (rarer) now fail schema validation since
    // (`getRecord` fails loudly on a corrupt/stale-schema row — store
    // corruption, not absence). Skip gracefully, never throw a single bad
    // lesson into aborting every other lesson's check in this run — but
    // surface it, rather than let it look identical to "checked, clean".
    let sourceRecord: WorkRecord | null;
    try {
      sourceRecord = getRecord(db, lesson.work_id);
    } catch (error: unknown) {
      skipped.push({
        work_id: lesson.work_id,
        reason: `source-record-invalid: ${error instanceof Error ? error.message : String(error)}`,
      });
      continue;
    }
    if (sourceRecord === null) {
      skipped.push({ work_id: lesson.work_id, reason: 'source-record-missing' });
      continue;
    }
    const signatures = recordSignatures(sourceRecord);
    if (signatures.length === 0) {
      skipped.push({ work_id: lesson.work_id, reason: 'no-signatures' });
      continue;
    }

    // `extracted_at` crosses the unvalidated `import-lessons` boundary
    // (LessonLineSchema only requires a non-empty string, no format
    // enforcement) — unlike WorkRecord lifecycle timestamps, which come from
    // the controlled ingest pipeline's known producer formats. Skip rather
    // than throw: lessons are append-only, so an uncaught throw here would
    // permanently poison every `distill-lessons` invocation once a single
    // malformed lesson entered the window. Surfaced via `skipped`, same as
    // the missing-source-record case above, rather than a silent continue.
    let extractedAtUtc: string;
    try {
      extractedAtUtc = toIsoUtc(lesson.extracted_at);
    } catch {
      skipped.push({ work_id: lesson.work_id, reason: 'unparseable-extracted-at' });
      continue;
    }

    const sourceQuery = queryFromRecord(db, lesson.work_id, { record: sourceRecord });
    const superseded = new Set(supersedesClosure(db, lesson.work_id));
    const lessonCtx = { work_id: lesson.work_id, extractedAtUtc, sourceQuery };

    const workIdsBySignature = workIdsBySignatures(db, signatures, lessonCtx);
    const candidateCache = buildCandidateCache(
      db,
      [...workIdsBySignature.values()].flat(),
      lessonCtx,
      superseded
    );
    const candidatesBySignature = new Map<string, RegressionCandidate[]>(
      [...workIdsBySignature].map(([signature, workIds]) => [
        signature,
        workIds.flatMap(workId => {
          const candidate = candidateCache.get(workId);
          return candidate === undefined ? [] : [candidate];
        }),
      ])
    );

    flags.push(...checkPriorFixRegression(lesson.work_id, extractedAtUtc, candidatesBySignature));
  }

  return { flags, skipped };
}
