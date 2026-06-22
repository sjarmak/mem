import { basename } from 'node:path';

import { MemoryEventSchema, type MemoryEvent, type MemoryOp } from '../schemas/memory-event.js';

/**
 * Write-time memory-op capture mechanism (mem-31kz forward-capture). Pure,
 * env-and-clock-injected so it is deterministic and unit-testable — the bash
 * PostToolUse hook (scripts/hooks/capture-memory-event.sh) is a thin pipe into
 * {@link buildCaptureEvent} via `mem memory-event capture`.
 *
 * ZFC boundary: everything here is MECHANISM — map a concrete tool to a
 * normalized op, decide structurally whether a path is a memory path, mint a
 * deterministic id. It NEVER judges whether a read was a "useful recall" or
 * classifies the memory's semantic type; that is model-delegated, downstream,
 * and never at capture time.
 */

/** Concrete file tools we map to a normalized memory op. mcp/agent memory tools
 * (tom-swe, continuous-learning) are CROSS-SYSTEM = mayor-owned, out of scope
 * here; this is the in-rig filesystem capture path only. */
const TOOL_OP: Record<string, MemoryOp> = {
  Read: 'read',
  Write: 'write',
  Edit: 'update',
  NotebookEdit: 'update',
  Grep: 'search',
  Glob: 'search',
};

/** Structural markers used when MEM_MEMORY_DIRS is not set: an agent memory dir
 * lives under `.claude` with a `/memory/` segment, `~/brains` is the brains
 * store, and `MEMORY.md` is the index file. Substring match — mechanical, no
 * semantic judgment. Configure MEM_MEMORY_DIRS (colon-separated path
 * substrings) to override for a different layout. */
const DEFAULT_BRAINS_MARKER = '/brains/';
const DEFAULT_MEMORY_INDEX = 'MEMORY.md';

/** The configured memory-path substrings, or `[]` when relying on the
 * structural defaults. Colon-separated, like a PATH. */
export function memoryDirsFromEnv(env: NodeJS.ProcessEnv): string[] {
  const raw = env.MEM_MEMORY_DIRS;
  if (raw === undefined || raw.trim() === '') return [];
  return raw
    .split(':')
    .map(s => s.trim())
    .filter(s => s.length > 0);
}

/** Is `path` a memory path? If MEM_MEMORY_DIRS is set, match its substrings;
 * else apply the structural defaults (claude `/memory/` dir, `/brains/`, or a
 * `MEMORY.md` index). Pure path logic — no filesystem access. */
export function isMemoryPath(path: string, env: NodeJS.ProcessEnv): boolean {
  const configured = memoryDirsFromEnv(env);
  if (configured.length > 0) {
    return configured.some(dir => path.includes(dir));
  }
  if (path.includes(DEFAULT_BRAINS_MARKER)) return true;
  if (basename(path) === DEFAULT_MEMORY_INDEX) return true;
  return path.includes('/memory/') && path.includes('.claude');
}

/** Map a concrete tool name to its normalized op, or null if the tool is not a
 * memory-relevant file tool. */
export function classifyMemoryOp(tool: string): MemoryOp | null {
  return TOOL_OP[tool] ?? null;
}

/** The PostToolUse hook payload fields we read (a subset; unknown fields are
 * ignored, not validated — this is the harness's shape, not ours to police). */
interface HookInput {
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  session_id?: string;
  cwd?: string;
}

/** Pull the operated-on path from a tool input: `file_path` for Read/Write/Edit,
 * `path` for Grep/Glob. Returns undefined when neither is a string. */
function pathFromToolInput(toolInput: Record<string, unknown>): string | undefined {
  const fp = toolInput.file_path;
  if (typeof fp === 'string') return fp;
  const p = toolInput.path;
  if (typeof p === 'string') return p;
  return undefined;
}

/** The work_id known at capture time, from the harness env (best-effort). When
 * absent the event is still captured session-keyed; the session->work_id join
 * is resolvable later from claim provenance. */
export function workIdFromEnv(env: NodeJS.ProcessEnv): string | undefined {
  const candidate = env.MEM_WORK_ID ?? env.GC_BEAD_ID ?? env.GC_WORK_ID;
  return candidate !== undefined && candidate.trim() !== '' ? candidate.trim() : undefined;
}

export interface CaptureContext {
  /** The harness env (MEM_MEMORY_DIRS, MEM_WORK_ID, GC_*, session fallback). */
  env: NodeJS.ProcessEnv;
  /** Injected ISO timestamp — the codebase convention for deterministic,
   * clock-free pure functions (cf. deriveProvenanceEvents' ingestedAt). */
  now: string;
}

/**
 * Project a PostToolUse hook payload into a {@link MemoryEvent}, or null when it
 * is not an in-scope memory operation (non-memory tool, or a path outside the
 * configured/structural memory dirs). Captures ONLY leak-safe join keys — op,
 * which-memory (path ref), session, work_id — never memory CONTENT and never an
 * outcome field. The id is deterministic so a re-fired hook is an idempotent
 * no-op at the store layer.
 */
export function buildCaptureEvent(input: HookInput, ctx: CaptureContext): MemoryEvent | null {
  const tool = input.tool_name;
  if (tool === undefined) return null;
  const op = classifyMemoryOp(tool);
  if (op === null) return null;

  const toolInput = input.tool_input ?? {};
  const memoryRef = pathFromToolInput(toolInput);
  if (memoryRef === undefined || !isMemoryPath(memoryRef, ctx.env)) return null;

  const session = input.session_id ?? ctx.env.GC_SESSION_NAME ?? ctx.env.GC_AGENT;
  if (session === undefined || session.trim() === '') return null;

  const workId = workIdFromEnv(ctx.env);

  const event: MemoryEvent = {
    id: `capture-hook:${session}:${ctx.now}:${op}:${memoryRef}`,
    session,
    ...(workId !== undefined && { work_id: workId }),
    op,
    backend: 'filesystem',
    memory_ref: memoryRef,
    ...(typeof input.cwd === 'string' && { used_in: input.cwd }),
    concrete_tool: tool,
    source: 'capture-hook',
    occurred_at: ctx.now,
    created_at: ctx.now,
  };

  // Validate through the strict allow-list before it leaves this layer — a
  // shape regression fails here, not silently at the store boundary.
  return MemoryEventSchema.parse(event);
}
