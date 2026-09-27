import { execFileSync } from 'node:child_process';

import type { WorkRecord } from '../schemas/workrecord.js';
import { type TraceIndexEntry, traceIndexByPath } from './trace-index.js';
import type { TranscriptArchive } from './trace-archive.js';

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

export const MAX_PRIMARY_TRACE_RECORDS = 5;

export function isPrimaryTraceShare(recordIds: ReadonlySet<string>): boolean {
  return recordIds.size <= MAX_PRIMARY_TRACE_RECORDS;
}

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
  let parsed: GcSessionLogs;
  try {
    parsed = JSON.parse(stdout) as GcSessionLogs;
  } catch (err: unknown) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(`gc session logs returned non-JSON output: ${detail}`);
  }
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
  /** Transcript archive (from `loadTranscriptArchive`). When set, every resolved
   * path is run through {@link TranscriptArchive.materialize}: a reaped path that
   * names an archived transcript is rewritten to its restored copy, recovering
   * trace signal the rolling-window prune would otherwise lose. Live wins. */
  archive?: TranscriptArchive;
  primaryPathRecordIds?: ReadonlyMap<string, ReadonlySet<string>>;
}

/** Apply the archive fallback to a resolved path (identity when no archive). */
function materialize(path: string, archive?: TranscriptArchive): string {
  return archive === undefined ? path : archive.materialize(path);
}

/** Resolve one agent's transcript and return the agent with `trace_ref` set.
 * An agent that already carries a `trace_ref` (attached by the merged
 * session-join artifact) is returned as-is — no `gc` shelling — but still passes
 * through the archive fallback, since the join may point at a reaped path. */
function resolveAgent(
  agent: WorkRecord['agents'][number],
  resolve: SessionResolver,
  cache: Map<string, string | null>,
  archive?: TranscriptArchive
): { agent: WorkRecord['agents'][number]; path: string | null; countPath: string | null } {
  if (agent.trace_ref !== undefined) {
    const recovered = materialize(agent.trace_ref, archive);
    return recovered === agent.trace_ref
      ? { agent, path: agent.trace_ref, countPath: agent.trace_ref }
      : { agent: { ...agent, trace_ref: recovered }, path: recovered, countPath: agent.trace_ref };
  }
  const sessionId = parseSessionId(agent.agent_id);
  if (sessionId === null) return { agent, path: null, countPath: null };

  let path = cache.get(sessionId);
  if (path === undefined) {
    path = resolve(sessionId);
    cache.set(sessionId, path);
  }

  if (path === null) return { agent, path: null, countPath: null };
  const recovered = materialize(path, archive);
  return { agent: { ...agent, trace_ref: recovered }, path: recovered, countPath: path };
}

export function attachTraceRefs(
  records: WorkRecord[],
  opts: AttachTraceOptions = {}
): WorkRecord[] {
  const resolve = opts.resolve ?? gcSessionResolver;
  const byPath = opts.index ? traceIndexByPath(opts.index) : undefined;
  const archive = opts.archive;
  const cache = new Map<string, string | null>();

  const resolved = records.map(record => {
    const agents = record.agents.map(agent => resolveAgent(agent, resolve, cache, archive));
    const presetPath =
      record.trace?.jsonl_path === undefined
        ? undefined
        : materialize(record.trace.jsonl_path, archive);
    const paths = new Map<string, string>();
    if (presetPath !== undefined && record.trace !== undefined) {
      paths.set(presetPath, record.trace.jsonl_path);
    }
    for (const { agent, path, countPath } of agents) {
      if (agent.suspect !== true && path !== null && countPath !== null) {
        paths.set(path, countPath);
      }
    }
    return { record, agents, presetPath, paths };
  });

  const recordsPerPath = new Map<string, Set<string>>();
  for (const { record, paths } of resolved) {
    for (const path of paths.keys()) {
      const workIds = recordsPerPath.get(path) ?? new Set<string>();
      workIds.add(record.work_id);
      recordsPerPath.set(path, workIds);
    }
  }

  return resolved.map(({ record, agents, presetPath, paths }) => {
    const primaryPath = [...paths].find(([path, countPath]) => {
      const workIds = new Set([
        ...(opts.primaryPathRecordIds?.get(countPath) ?? []),
        ...(opts.primaryPathRecordIds?.get(path) ?? []),
        ...(recordsPerPath.get(path) ?? []),
      ]);
      return isPrimaryTraceShare(workIds);
    })?.[0];
    const nextAgents = agents.map(({ agent }) => agent);

    if (primaryPath === undefined) {
      const { trace: _trace, ...recordWithoutTrace } = record;
      return { ...recordWithoutTrace, agents: nextAgents };
    }

    if (primaryPath === presetPath) {
      const n_turns = record.trace?.n_turns ?? byPath?.get(primaryPath)?.n_turns;
      return {
        ...record,
        agents: nextAgents,
        trace: {
          ...record.trace,
          jsonl_path: primaryPath,
          ...(n_turns !== undefined && { n_turns }),
        },
      };
    }

    const n_turns = byPath?.get(primaryPath)?.n_turns;
    return {
      ...record,
      agents: nextAgents,
      trace: {
        jsonl_path: primaryPath,
        ...(n_turns !== undefined && { n_turns }),
      },
    };
  });
}
