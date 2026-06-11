import { execFileSync } from 'node:child_process';

import type { WorkRecord } from '../schemas/workrecord.js';
import { type TraceIndexEntry, traceIndexByPath } from './trace-index.js';

/**
 * Trace resolution (P1.3) — bridge a bead's assignee to its Claude transcript.
 *
 * The chain is `assignee → session id → JSONL path`:
 *  1. A bead's assignee embeds the Gas City session id (e.g. `polecat-gc-335825`
 *     or the bare `gc-335825`). {@link parseSessionId} extracts it.
 *  2. `gc session logs <id> --json` is the authoritative map from a session id
 *     — live, dormant, or reaped — to its transcript path. We shell out to it
 *     rather than re-deriving the UUID from cwd, which is lossy when a worktree
 *     hosts several sessions over time.
 *
 * Then {@link attachTraceRefs} writes the resolved path onto each WorkRecord's
 * agent and the record's `trace` pointer (parsed in P1.6).
 */

/** Matches a Gas City session id (`gc-` + digits) within an assignee string. */
const SESSION_ID_RE = /\bgc-\d+/;

/**
 * Extract the Gas City session id from a bead assignee or agent id. Accepts the
 * full session name (`polecat-gc-335825`, `mem-worker-gc-340053`) or the bare id
 * (`gc-340053`). Returns null when the string carries no session id (e.g. a
 * human owner like `sjarmak@users.noreply.github.com`).
 */
export function parseSessionId(assignee: string): string | null {
  const match = SESSION_ID_RE.exec(assignee);
  return match ? match[0] : null;
}

/** Resolves a Gas City session id to its transcript path, or null if unknown. */
export type SessionResolver = (sessionId: string) => string | null;

/** The subset of `gc session logs --json` output the resolver reads. */
interface GcSessionLogs {
  ok?: boolean;
  transcript_path?: string;
}

/** Pure parse of `gc session logs --json` stdout → transcript path (or null). */
export function parseTranscriptPath(stdout: string): string | null {
  const parsed = JSON.parse(stdout) as GcSessionLogs;
  return parsed.ok && parsed.transcript_path ? parsed.transcript_path : null;
}

/** True when `execFileSync` failed because the child exited non-zero (as
 * opposed to the binary being missing). `gc session logs` exits non-zero for an
 * unknown session, which is an expected "unresolved" outcome, not an error. */
function isNonZeroExit(err: unknown): boolean {
  return typeof (err as { status?: unknown }).status === 'number';
}

/**
 * Authoritative resolver: `gc session logs <id> --json`. Returns the transcript
 * path, or null when gc reports the session is unknown. A missing `gc` binary
 * (or any non-exit failure) propagates — that is a misconfiguration, not an
 * unresolved session, and must not be silently swallowed.
 */
export function gcSessionResolver(sessionId: string): string | null {
  try {
    const stdout = execFileSync('gc', ['session', 'logs', sessionId, '--json'], {
      encoding: 'utf8',
      maxBuffer: 32 * 1024 * 1024,
    });
    return parseTranscriptPath(stdout);
  } catch (err) {
    if (isNonZeroExit(err)) return null;
    throw err;
  }
}

/** Options for {@link attachTraceRefs}. */
export interface AttachTraceOptions {
  /** Session-id → transcript-path resolver. Defaults to {@link gcSessionResolver}. */
  resolve?: SessionResolver;
  /** Trace index (from `indexTraces`); supplies `n_turns` for resolved paths. */
  index?: TraceIndexEntry[];
}

/** Resolve one agent's transcript and return the agent with `trace_ref` set.
 * An agent that already carries a `trace_ref` (attached by the merged
 * session-join artifact) is returned as-is — no `gc` shelling. */
function resolveAgent(
  agent: WorkRecord['agents'][number],
  resolve: SessionResolver,
  cache: Map<string, string | null>
): { agent: WorkRecord['agents'][number]; path: string | null } {
  if (agent.trace_ref !== undefined) return { agent, path: agent.trace_ref };
  const sessionId = parseSessionId(agent.agent_id);
  if (sessionId === null) return { agent, path: null };

  let path = cache.get(sessionId);
  if (path === undefined) {
    path = resolve(sessionId);
    cache.set(sessionId, path);
  }

  if (path === null) return { agent, path: null };
  return { agent: { ...agent, trace_ref: path }, path };
}

/**
 * Attach trace pointers to WorkRecords. For each record, resolve every agent's
 * session id to a transcript path and set the agent's `trace_ref`; the record's
 * `trace` pointer is set from the first agent that resolves (the primary
 * transcript). Records and agents are copied, never mutated.
 *
 * Resolution is memoized per session id, so repeated assignees across many
 * records cost one resolver call each.
 */
export function attachTraceRefs(
  records: WorkRecord[],
  opts: AttachTraceOptions = {}
): WorkRecord[] {
  const resolve = opts.resolve ?? gcSessionResolver;
  const byPath = opts.index ? traceIndexByPath(opts.index) : undefined;
  const cache = new Map<string, string | null>();

  return records.map(record => {
    const agents = record.agents.map(agent => resolveAgent(agent, resolve, cache));
    // A pre-set trace pointer (merged session-join: the last non-suspect
    // session) wins over the first-resolved-agent default.
    const presetPath = record.trace?.jsonl_path;
    const primaryPath = presetPath ?? agents.find(a => a.path !== null)?.path ?? null;

    const next: WorkRecord = { ...record, agents: agents.map(a => a.agent) };
    if (primaryPath !== null) {
      const n_turns = record.trace?.n_turns ?? byPath?.get(primaryPath)?.n_turns;
      next.trace = {
        ...record.trace,
        jsonl_path: primaryPath,
        ...(n_turns !== undefined && { n_turns }),
      };
    }
    return next;
  });
}
