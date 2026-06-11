import { readFileSync } from 'node:fs';

import type { AgentRef, WorkRecord } from '../schemas/workrecord.js';

/**
 * Merged session-join attach (mem-75t.4). The Python driver
 * (`memory-bench/scripts/build_merged_join.py`) merges the three join sources
 * — gc events (PRIMARY), dolt assignee history, content scan — into one
 * artifact; this module attaches its per-bead ordered session list onto
 * WorkRecords as genuinely multi-row `agents`, replacing the single
 * final-assignee link.
 *
 * Resolution note: the artifact already carries each session's transcript path
 * (resolved in one pass from the events stream's `session_key` map), so
 * attaching it BEFORE P1.3 trace resolution means `gc session logs` shelling
 * only runs for the residue — this is the productionization fix for the
 * ~40-minute per-record resolver.
 */

/** One session entry of the merged-join artifact (`beads[work_id][i]`). */
export interface JoinSessionEntry {
  sequence: number;
  gc_session_id: string | null;
  session_key: string | null;
  transcript_path: string | null;
  t_first: string | null;
  t_last: string | null;
  sources: string[];
  strength: string;
  n_events: number;
  suspect: boolean;
}

export interface SessionJoin {
  /** work_id -> ordered session entries. */
  beads: Map<string, JoinSessionEntry[]>;
  /** gc session id -> transcript path, for EVERY session the events stream
   * keyed (joined or not) — the one-pass replacement for per-session
   * `gc session logs` shelling. */
  sessionPaths: Map<string, string>;
}

/** Parse the merged-join artifact. Throws on a file that exists but has no
 * `beads` object — a malformed artifact must fail the build, not silently
 * produce a single-session store. `session_paths` is optional (older
 * artifacts), degrading to gc-shelled resolution. */
export function loadSessionJoin(path: string): SessionJoin {
  const payload = JSON.parse(readFileSync(path, 'utf8')) as {
    beads?: Record<string, JoinSessionEntry[]>;
    session_paths?: Record<string, string>;
  };
  if (payload.beads === undefined || typeof payload.beads !== 'object') {
    throw new Error(`session-join artifact ${path} has no beads{} object`);
  }
  return {
    beads: new Map(Object.entries(payload.beads)),
    sessionPaths: new Map(Object.entries(payload.session_paths ?? {})),
  };
}

/** The agent identity of a join entry: the gc session id when known (joins to
 * assignee parsing and events), else the Claude session UUID, else the
 * transcript path (assignee-only entries with nothing else to key on). */
function entryAgentId(entry: JoinSessionEntry): string {
  return entry.gc_session_id ?? entry.session_key ?? `transcript:${entry.transcript_path ?? '?'}`;
}

/** Convert one join entry to an AgentRef row, inheriting role/account from the
 * record's existing assignee agent when the identities line up. */
function toAgentRef(entry: JoinSessionEntry, existing: AgentRef[]): AgentRef {
  const agentId = entryAgentId(entry);
  const match = existing.find(
    a =>
      a.agent_id === agentId ||
      (entry.gc_session_id !== null && a.agent_id.endsWith(entry.gc_session_id)) ||
      (entry.transcript_path !== null && a.trace_ref === entry.transcript_path)
  );
  return {
    agent_id: agentId,
    ...(match?.role !== undefined && { role: match.role }),
    ...(match?.account !== undefined && { account: match.account }),
    ...(entry.transcript_path !== null && { trace_ref: entry.transcript_path }),
    sequence: entry.sequence,
    ...(entry.t_first !== null && { started_at: entry.t_first }),
    ...(entry.t_last !== null && { ended_at: entry.t_last }),
    sources: entry.sources,
    suspect: entry.suspect,
  };
}

/**
 * Attach the merged join to records: each record with join entries gets the
 * full ordered multi-row `agents` list, and its primary `trace` pointer becomes
 * the LAST non-suspect resolved session (the closing iteration — the same
 * convention as the final-assignee status quo, now explicit). Records without
 * join entries pass through unchanged, so the assignee fallback still covers
 * them. Records are copied, never mutated.
 */
export function attachSessionJoin(records: WorkRecord[], join: SessionJoin): WorkRecord[] {
  return records.map(record => {
    const entries = join.beads.get(record.work_id);
    if (entries === undefined || entries.length === 0) return record;

    const agents = entries.map(entry => toAgentRef(entry, record.agents));
    const primary = [...entries]
      .reverse()
      .find(entry => !entry.suspect && entry.transcript_path !== null);

    const next: WorkRecord = { ...record, agents };
    if (primary?.transcript_path != null) {
      next.trace = { ...record.trace, jsonl_path: primary.transcript_path };
    }
    return next;
  });
}
