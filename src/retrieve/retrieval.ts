import { z } from 'zod';

import { errorClass, failureSignature, normalizePath } from '../parse/recurrence.js';
import { TraceErrorSchema } from '../schemas/trace.js';
import type { WorkRecord } from '../schemas/workrecord.js';
import {
  getRecord,
  lessonsFor,
  queryRecords,
  searchErrorMessages,
  supersedesClosure,
  type StoredLesson,
} from '../store/index.js';
import type { StoreDatabase } from '../store/sqlite.js';
import { isSibling } from './exclusions.js';

/**
 * Retrieval v1 (P2.1): structured/keyword retrieval over the work-audit graph,
 * implementing the locked contract D6–D10 (ARCHITECTURE.md). Deterministic and
 * zero-API by construction — every step is either a store primitive with a
 * fixed ORDER BY or explicit, documented tiebreaker arithmetic (the ZFC
 * deterministic-ranking exception). No embeddings, no semantic heuristics.
 *
 * - D6: the temporal boundary is the reader's strict `closedBefore`; the
 *   self / convoy / pr-or-branch / supersedes-chain exclusions live in
 *   `exclusions.ts`.
 * - D7: `scope` selects the track — `cross_rig` (strict/headline) or
 *   `same_rig_temporal` (realistic/secondary).
 * - D8: matching keys on the P1.6 failure-signature primitives. Tiers, strong
 *   to weak: exact signature → same tool+error-class → FTS message match.
 *   The FTS bm25 order is the "weak tiebreaker" *within* the structured tiers.
 * - D9: the payload is the append-only `lessons` rows (consumed, never
 *   re-distilled) plus a citation; literal `file:line` refs attach on the
 *   same-rig track only. The raw prior trace is never injected.
 * - D10: ranking = (tier, matched-signature count, matched-class count, FTS
 *   position, work_id) — nothing else.
 */

/** The query work context — what the retrieving side knows about the work
 * that just hit a failure. `started` is the D6 boundary and is deliberately
 * required: the caller must state when "memory as it existed" is measured. */
export const RetrievalQuerySchema = z.object({
  work_id: z.string().min(1),
  rig: z.string().min(1),
  started: z.string().min(1),
  errors: z.array(TraceErrorSchema).default(() => []),
  convoy_id: z.string().optional(),
  pr: z.string().optional(),
  external_ref: z.string().optional(),
});

export type RetrievalQuery = z.infer<typeof RetrievalQuerySchema>;

/** The Decision-7 track. */
export type RetrievalScope = 'cross_rig' | 'same_rig_temporal';

export interface RetrieveOptions {
  scope: RetrievalScope;
  /** Max items returned (default {@link DEFAULT_LIMIT}); `total_matched`
   * always reports the uncapped count so truncation is visible. */
  limit?: number;
}

/** How a candidate matched the query failure, strongest tier that fired. */
export type MatchTier = 'signature' | 'error_class' | 'message';

/** A literal prior fix locus (same-rig track only) — file:line, never the
 * prior message or trace content. */
export interface LiteralRef {
  file: string;
  line: number;
}

/** The Decision-9 citation: enough to audit the lesson and recover full
 * detail (`mem query <work_id>`), without injecting it. */
export interface Citation {
  work_id: string;
  commit_sha?: string;
  pr?: string;
}

export interface RetrievedItem {
  work_id: string;
  rig: string;
  title: string;
  match: MatchTier;
  /** Query failure signatures this prior work also exhibits (exact tier). */
  matched_signatures: string[];
  /** Weaker `tool:error_class` matches (errors not already counted above). */
  matched_classes: string[];
  citation: Citation;
  /** Append-only distilled lessons (Decision 9), in extraction order. */
  lessons: StoredLesson[];
  /** Same-rig track only: literal file:line of the matched prior errors. */
  literal?: LiteralRef[];
}

export interface RetrievalResult {
  scope: RetrievalScope;
  work_id: string;
  /** Number of query errors — 0 means retrieval had no trigger (D8). */
  trigger_count: number;
  /** Eligible matches before the limit cap. */
  total_matched: number;
  /** D6 duplicate audit: the top item matched on an exact fix signature. */
  near_duplicate_top: boolean;
  /** The FTS candidate scan hit its cap ({@link FTS_CANDIDATE_LIMIT}) — the
   * message tier may be incomplete. Surfaced, never silent. */
  fts_truncated: boolean;
  items: RetrievedItem[];
}

/** Default item cap — bounds injected-context volume (Decision 10). */
export const DEFAULT_LIMIT = 10;

/** Mechanical cap on the FTS candidate scan; overflow sets `fts_truncated`. */
const FTS_CANDIDATE_LIMIT = 256;

/** Mechanical cap on FTS query size (token count), not a relevance judgment:
 * bm25 does the weighting, this only bounds the query we hand it. */
const MAX_FTS_TOKENS = 64;

/** Build a safe FTS5 MATCH query from raw error messages: alphanumeric
 * tokens, deduplicated in order of appearance, each quoted (neutralizing
 * FTS5 operators), OR-joined. Undefined when the messages yield no tokens. */
function buildFtsQuery(messages: string[]): string | undefined {
  const seen = new Set<string>();
  for (const message of messages) {
    for (const token of message.match(/[A-Za-z0-9_]+/g) ?? []) {
      seen.add(token.toLowerCase());
      if (seen.size >= MAX_FTS_TOKENS) break;
    }
    if (seen.size >= MAX_FTS_TOKENS) break;
  }
  if (seen.size === 0) return undefined;
  return [...seen].map(token => `"${token}"`).join(' OR ');
}

/** `tool:error_class` — the tier-2 match key (rig-agnostic: no file path). */
function classKey(tool: string, cls: string): string {
  return `${tool}:${cls}`;
}

interface RankedCandidate {
  record: WorkRecord;
  match: MatchTier;
  matchedSignatures: string[];
  matchedClasses: string[];
  /** Matched prior errors' file:line (for the same-rig literal attachment). */
  literal: LiteralRef[];
  /** Position of the record's best FTS hit; Infinity when none. */
  ftsPos: number;
}

const TIER_RANK: Record<MatchTier, number> = { signature: 0, error_class: 1, message: 2 };

/** The full, explicit D10 ordering — see the module doc. */
function compareCandidates(a: RankedCandidate, b: RankedCandidate): number {
  return (
    TIER_RANK[a.match] - TIER_RANK[b.match] ||
    b.matchedSignatures.length - a.matchedSignatures.length ||
    b.matchedClasses.length - a.matchedClasses.length ||
    a.ftsPos - b.ftsPos ||
    a.record.work_id.localeCompare(b.record.work_id)
  );
}

/** Dedup + deterministic order for literal refs. */
function sortedLiteral(refs: Map<string, LiteralRef>): LiteralRef[] {
  return [...refs.values()].sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
}

/**
 * Retrieve ranked prior work for a failure-triggered query under the locked
 * D6–D10 contract. Validates the query at the boundary; throws on a
 * malformed query or a negative/non-integer limit.
 */
export function retrieve(
  db: StoreDatabase,
  query: RetrievalQuery,
  opts: RetrieveOptions
): RetrievalResult {
  const q = RetrievalQuerySchema.parse(query);
  const limit = opts.limit ?? DEFAULT_LIMIT;
  if (!Number.isInteger(limit) || limit < 0) {
    throw new Error(`limit must be a non-negative integer, got ${String(opts.limit)}`);
  }

  const empty: RetrievalResult = {
    scope: opts.scope,
    work_id: q.work_id,
    trigger_count: q.errors.length,
    total_matched: 0,
    near_duplicate_top: false,
    fts_truncated: false,
    items: [],
  };
  if (q.errors.length === 0) return empty; // no failure, no trigger (D8)

  // D6 temporal boundary via the store's strict closedBefore; D7 scope.
  const eligible = queryRecords(db, {
    closedBefore: q.started,
    ...(opts.scope === 'same_rig_temporal' && { rig: q.rig }),
  }).filter(record => opts.scope !== 'cross_rig' || record.rig !== q.rig);

  // D6 non-temporal exclusions: self, supersedes chain, convoy/pr/branch.
  const chain = new Set(supersedesClosure(db, q.work_id));
  const retrievable = eligible.filter(
    record => record.work_id !== q.work_id && !chain.has(record.work_id) && !isSibling(record, q)
  );

  // D8 match keys from the query failure.
  const querySignatures = new Set(q.errors.map(failureSignature));
  const queryClasses = new Set(q.errors.map(e => classKey(e.tool, errorClass(e))));

  // FTS scan: defines the message tier and tiebreaks the structured tiers.
  const ftsQuery = buildFtsQuery(q.errors.map(e => e.message));
  const hits = ftsQuery === undefined ? [] : searchErrorMessages(db, ftsQuery, FTS_CANDIDATE_LIMIT);
  const ftsPos = new Map<string, number>();
  const ftsSignatures = new Map<string, Set<string>>();
  hits.forEach((hit, index) => {
    if (!ftsPos.has(hit.work_id)) ftsPos.set(hit.work_id, index);
    let sigs = ftsSignatures.get(hit.work_id);
    if (!sigs) {
      sigs = new Set();
      ftsSignatures.set(hit.work_id, sigs);
    }
    sigs.add(hit.signature);
  });

  const candidates: RankedCandidate[] = [];
  for (const record of retrievable) {
    const matchedSignatures = new Set<string>();
    const matchedClasses = new Set<string>();
    const literal = new Map<string, LiteralRef>();
    const ftsMatched = ftsSignatures.get(record.work_id);

    for (const error of record.trace?.errors ?? []) {
      const signature = failureSignature(error);
      const cls = classKey(error.tool, errorClass(error));
      // Count each prior error at its strongest level.
      if (querySignatures.has(signature)) matchedSignatures.add(signature);
      else if (queryClasses.has(cls)) matchedClasses.add(cls);
      else if (!ftsMatched?.has(signature)) continue;
      const file = normalizePath(error.file);
      literal.set(`${file}:${error.line}`, { file, line: error.line });
    }

    const match: MatchTier | undefined =
      matchedSignatures.size > 0
        ? 'signature'
        : matchedClasses.size > 0
          ? 'error_class'
          : literal.size > 0
            ? 'message'
            : undefined;
    if (match === undefined) continue;

    candidates.push({
      record,
      match,
      matchedSignatures: [...matchedSignatures].sort(),
      matchedClasses: [...matchedClasses].sort(),
      literal: sortedLiteral(literal),
      ftsPos: ftsPos.get(record.work_id) ?? Infinity,
    });
  }

  candidates.sort(compareCandidates);
  const top = candidates.slice(0, limit);

  const items: RetrievedItem[] = top.map(candidate => {
    const { record } = candidate;
    return {
      work_id: record.work_id,
      rig: record.rig,
      title: record.title,
      match: candidate.match,
      matched_signatures: candidate.matchedSignatures,
      matched_classes: candidate.matchedClasses,
      citation: {
        work_id: record.work_id,
        ...(record.outcome?.commit_sha !== undefined && {
          commit_sha: record.outcome.commit_sha,
        }),
        ...(record.outcome?.pr !== undefined && { pr: record.outcome.pr }),
      },
      lessons: lessonsFor(db, record.work_id),
      ...(opts.scope === 'same_rig_temporal' && { literal: candidate.literal }),
    };
  });

  return {
    ...empty,
    total_matched: candidates.length,
    near_duplicate_top: items[0]?.match === 'signature',
    fts_truncated: hits.length === FTS_CANDIDATE_LIMIT,
    items,
  };
}

/**
 * Build the query context from a stored record — replay mode, for evaluating
 * a closed historical bead (Decision 5). The boundary is the record's
 * `started`, falling back to `created` (earlier, so strictly leak-safe) when
 * the work never recorded a start.
 */
export function queryFromRecord(db: StoreDatabase, workId: string): RetrievalQuery {
  const record = getRecord(db, workId);
  if (record === null) {
    throw new Error(`No record for work_id ${workId} — cannot build a retrieval query from it.`);
  }

  return {
    work_id: record.work_id,
    rig: record.rig,
    started: record.lifecycle.started ?? record.lifecycle.created,
    errors: record.trace?.errors ?? [],
    ...(record.links.convoy_id !== undefined && { convoy_id: record.links.convoy_id }),
    ...(record.outcome?.pr !== undefined && { pr: record.outcome.pr }),
    ...(record.external_ref !== undefined && { external_ref: record.external_ref }),
  };
}
