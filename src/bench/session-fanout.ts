/**
 * Leakage gate — SESSION FAN-OUT DISAMBIGUATION (mem-wanz.10, PRD §5.7, risk R1,
 * keys #7/#9). R1 is the highest-severity validity threat: a session_uuid fans
 * out to multiple work_ids (557/1008 sessions), so a single session's outcome
 * verdict can be attached to the WRONG work_id — manufacturing ~15-18pp of false
 * memory-arm "successes". The rule this gate enforces: ONE session → at most ONE
 * scored outcome; the verdict anchors on LOO canonical identity; an ambiguous
 * fan-out is store-only/T3 and is NEVER scored.
 *
 * A fan-out is RESCUED (still scorable) in exactly two ways:
 *  1. CANONICAL COLLAPSE — the fanned work_ids are the same underlying work
 *     (run-1/run-2 of one bead: shared branch-root or landed_commit). They
 *     collapse to one canonical identity, scored against its representative. This
 *     reuses loo-dedup's {@link mergeKeys} so "same work" means the same thing
 *     here as in the LOO split (the two must never diverge).
 *  2. SECOND-GATE DISAMBIGUATION — an EXTERNAL resolver (LLM-rerank of PR-body→
 *     bead, EasyLink ~76% P@1; or Dolt actor+ts→session) picks one member. That
 *     judgment is delegated, not coded: it arrives as a resolved
 *     `session_uuid → work_id` map (ZFC — mechanism generates candidates and
 *     enforces the gate; the model judges which bead a PR-body names).
 *
 * Everything else is mechanical: dedup associations, count fan-out degree,
 * union-find the canonical collapse, apply the fail-closed gate, and tally the
 * R1 early-warning counters. No semantic judgment lives in this file.
 */
import { mergeKeys, type CanonicalIdentity } from './loo-dedup.js';

/**
 * One session→work association, carrying the work's {@link CanonicalIdentity}
 * (computed upstream via loo-dedup `canonicalIdentity`, free of WorkRecord
 * coupling). The gate derives the collapse keys itself via `mergeKeys` — taking
 * the struct rather than pre-namespaced strings makes the slug/branch/landed key
 * scheme unbypassable, so a caller cannot accidentally feed bare keys and
 * over-collapse distinct work into a false success (the R1 failure this gate
 * exists to prevent). Repeated (session, work) rows are deduped before the
 * fan-out degree is counted.
 */
export interface SessionAssoc {
  session_uuid: string;
  work_id: string;
  canonical: CanonicalIdentity;
}

/** Whether a session's outcome may be scored, or must stay store-only/T3. */
export type ScoreEligibility = 'scorable' | 'ambiguous';

/** Reason codes for a session's classification (stable strings for the report). */
export const SINGLE_WORK_ID = 'single_work_id';
export const COLLAPSED_TO_CANONICAL = 'fanout_collapses_to_one_canonical';
export const DISAMBIGUATED = 'disambiguated_by_second_gate';
export const AMBIGUOUS_FANOUT = 'ambiguous_fanout_distinct_canonical';

/** A session's resolved verdict target: the single work_id it may score, or null
 * when the fan-out is ambiguous (never scored). */
export interface SessionVerdictTarget {
  session_uuid: string;
  /** The one work_id whose outcome this session may score, or null when ambiguous. */
  primary_work_id: string | null;
  eligibility: ScoreEligibility;
  /** Distinct work_ids associated with the session. */
  fanout_degree: number;
  /** Distinct canonical identities after collapse — 1 ⇒ unambiguous. */
  canonical_degree: number;
  reason: string;
}

/** Union-find root with path compression. */
function find(parent: number[], i: number): number {
  let root = i;
  while (parent[root] !== root) root = parent[root];
  while (parent[i] !== root) {
    const next = parent[i];
    parent[i] = root;
    i = next;
  }
  return root;
}

/** One session's distinct work_ids and the keys each contributes. */
interface SessionGroup {
  workIds: string[];
  keysByWork: Map<string, Set<string>>;
}

/** Collect distinct work_ids per session (deduped), preserving first-seen order
 * within a session and ordering sessions by uuid for a reproducible report. */
function groupBySession(assocs: readonly SessionAssoc[]): Map<string, SessionGroup> {
  const bySession = new Map<string, SessionGroup>();
  for (const a of assocs) {
    let group = bySession.get(a.session_uuid);
    if (group === undefined) {
      group = { workIds: [], keysByWork: new Map() };
      bySession.set(a.session_uuid, group);
    }
    let keys = group.keysByWork.get(a.work_id);
    if (keys === undefined) {
      keys = new Set();
      group.keysByWork.set(a.work_id, keys);
      group.workIds.push(a.work_id);
    }
    for (const key of mergeKeys(a.canonical)) keys.add(key);
  }
  return new Map([...bySession.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/** Partition a session's work_ids into canonical groups (union-find over shared
 * keys), returning the distinct-group count and each group's representative (the
 * lexicographically smallest work_id, for a deterministic scoring anchor). */
function collapse(group: SessionGroup): { canonicalDegree: number; representatives: string[] } {
  const { workIds, keysByWork } = group;
  const parent = workIds.map((_, i) => i);
  const firstByKey = new Map<string, number>();
  workIds.forEach((work, i) => {
    for (const key of keysByWork.get(work)!) {
      const seen = firstByKey.get(key);
      if (seen === undefined) firstByKey.set(key, i);
      else parent[find(parent, i)] = find(parent, seen);
    }
  });

  const byRoot = new Map<number, string[]>();
  workIds.forEach((work, i) => {
    const root = find(parent, i);
    (byRoot.get(root) ?? byRoot.set(root, []).get(root)!).push(work);
  });
  const representatives = [...byRoot.values()].map(members =>
    members.reduce((min, w) => (w < min ? w : min))
  );
  return { canonicalDegree: byRoot.size, representatives };
}

/**
 * Classify every session into its single scorable verdict target, or `ambiguous`
 * (never scored). A fan-out is scorable only when it collapses to one canonical
 * identity, or the optional second-gate `disambiguator` (a resolved
 * `session_uuid → work_id` map) names a member of the session. Output is ordered
 * by session_uuid.
 */
export function classifySessions(
  assocs: readonly SessionAssoc[],
  disambiguator?: ReadonlyMap<string, string>
): SessionVerdictTarget[] {
  const out: SessionVerdictTarget[] = [];
  for (const [session_uuid, group] of groupBySession(assocs)) {
    const fanout_degree = group.workIds.length;

    if (fanout_degree === 1) {
      out.push({
        session_uuid,
        primary_work_id: group.workIds[0],
        eligibility: 'scorable',
        fanout_degree,
        canonical_degree: 1,
        reason: SINGLE_WORK_ID,
      });
      continue;
    }

    const { canonicalDegree, representatives } = collapse(group);
    if (canonicalDegree === 1) {
      out.push({
        session_uuid,
        primary_work_id: representatives[0],
        eligibility: 'scorable',
        fanout_degree,
        canonical_degree: canonicalDegree,
        reason: COLLAPSED_TO_CANONICAL,
      });
      continue;
    }

    // Genuinely distinct work on one session: scorable only if the second gate
    // picks a member of THIS session (fail-closed on an off-session choice).
    const chosen = disambiguator?.get(session_uuid);
    if (chosen !== undefined && group.keysByWork.has(chosen)) {
      out.push({
        session_uuid,
        primary_work_id: chosen,
        eligibility: 'scorable',
        fanout_degree,
        canonical_degree: canonicalDegree,
        reason: DISAMBIGUATED,
      });
      continue;
    }

    out.push({
      session_uuid,
      primary_work_id: null,
      eligibility: 'ambiguous',
      fanout_degree,
      canonical_degree: canonicalDegree,
      reason: AMBIGUOUS_FANOUT,
    });
  }
  return out;
}

/** The fan-out gate's headline tally. */
export interface FanoutReport {
  sessions: number;
  /** Sessions touching more than one work_id. */
  fanout_sessions: number;
  /** Sessions with a single scorable primary (the eval-eligible population). */
  scorable: number;
  /** Ambiguous fan-outs excluded from scoring (store-only/T3). */
  ambiguous: number;
  /** Fan-outs rescued by canonical collapse. */
  collapsed: number;
  /** Fan-outs rescued by the second-gate disambiguator. */
  disambiguated: number;
  by_reason: Record<string, number>;
}

/** Aggregate the classification into the reportable headline. Pure arithmetic. */
export function summarizeFanout(targets: readonly SessionVerdictTarget[]): FanoutReport {
  const by_reason: Record<string, number> = {};
  let fanout_sessions = 0;
  let scorable = 0;
  let ambiguous = 0;
  let collapsed = 0;
  let disambiguated = 0;
  for (const t of targets) {
    by_reason[t.reason] = (by_reason[t.reason] ?? 0) + 1;
    if (t.fanout_degree > 1) fanout_sessions += 1;
    if (t.eligibility === 'scorable') scorable += 1;
    else ambiguous += 1;
    if (t.reason === COLLAPSED_TO_CANONICAL) collapsed += 1;
    if (t.reason === DISAMBIGUATED) disambiguated += 1;
  }
  return {
    sessions: targets.length,
    fanout_sessions,
    scorable,
    ambiguous,
    collapsed,
    disambiguated,
    by_reason,
  };
}

/** A scored verdict's claimed source — the work_id whose outcome was used. */
export interface VerdictSource {
  session_uuid: string;
  verdict_source_work_id: string;
}

/**
 * R1 early-warning counter (PRD §risk R1): the number of verdicts attributed to a
 * work_id that is NOT its session's scorable primary — a verdict on an ambiguous
 * session (primary null), or on a non-representative member of a scorable one. A
 * nonzero count means a verdict is being scored against the wrong work, i.e. the
 * gate is being bypassed; it should stay zero.
 */
export function countMisattributions(
  targets: readonly SessionVerdictTarget[],
  verdicts: readonly VerdictSource[]
): number {
  const primaryBySession = new Map(targets.map(t => [t.session_uuid, t.primary_work_id]));
  let count = 0;
  for (const v of verdicts) {
    const primary = primaryBySession.get(v.session_uuid);
    if (primary === undefined || primary !== v.verdict_source_work_id) count += 1;
  }
  return count;
}
