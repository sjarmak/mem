import { z } from 'zod';

/**
 * Memory event (mem-31kz forward-capture substrate).
 *
 * The write-time dual of the post-hoc work-audit graph: a real memory-USING
 * session emits one of these per memory operation AT EXECUTION TIME, instead of
 * mem reconstructing "did this agent use memory" after the fact (which it
 * provably cannot — the `used` retrieval-causality edge is empty by
 * construction). The `capture-provenance.sh` hook already records bead-claim
 * GIT provenance; this is a NEW, parallel capture path for memory reads/writes.
 *
 * It is the OpenRath PROJECTOR boundary (docs/prd-openrath-incorporation.md):
 * we capture the leak-safe, label-free join keys (op, which memory, where used,
 * session, work_id) — NEVER a whole Session, NEVER memory CONTENT, NEVER an
 * outcome field. By construction a write-time event is PRE-OUTCOME: the session
 * has not yet succeeded or failed, so the record carries no label. The firewall
 * (validity.py `loo_bounded`/`assert_no_leak`) still governs whether a captured
 * event may ENTER eval input — capture feeds the corpus, scoring is separate.
 *
 * ZFC: the store validates only that `op`/`backend` are KNOWN values
 * (structural). It never interprets what a memory_ref MEANS, never classifies a
 * read as "lesson recall" vs "scratch" — that semantic labeling is
 * model-delegated, downstream, and never happens at capture time.
 *
 * `.strict()` is load-bearing (the OpenRath allow-list, not deny-list,
 * principle): a Session/producer that grows a novel field RAISES here rather
 * than smuggling an unscanned, possibly outcome-correlated column past the
 * firewall. Extend the allow-list deliberately; never silently widen it.
 */

/** The operation set this filesystem capture path emits — a SUBSET of the
 * membench MemoryOperation enum (memory-bench/membench/schemas/memory_event.py
 * §6.2), drawn from the same vocabulary so the producer and the eval consumer
 * agree on names. The two model-judgment ops there (`classify`, `discard`) are
 * deliberately omitted: they are semantic verdicts, not filesystem operations,
 * and the capture layer never makes them (ZFC). The store rejects unknown ops so
 * a typo is a loud failure, not a silent new class. */
export const MEMORY_OPS = [
  'read',
  'write',
  'update',
  'delete',
  'search',
  'consolidate',
  'promote',
  'forget',
] as const;
export type MemoryOp = (typeof MEMORY_OPS)[number];

/** The backend representation the operation acted on (membench MemoryBackend). */
export const MEMORY_BACKENDS = ['filesystem', 'vector_db', 'kg', 'mcp', 'hybrid'] as const;
export type MemoryBackend = (typeof MEMORY_BACKENDS)[number];

export const MemoryEventSchema = z
  .object({
    // Deterministic dedup key (the PK). For hook-captured events:
    // `<source>:<session>:<occurred_at>:<op>:<memory_ref>` — stable so a
    // re-fired PostToolUse hook is an idempotent no-op, never a duplicate row.
    id: z.string().min(1),
    // The runtime session/actor that performed the op. Opaque to the store —
    // always known at write time (every tool call has a session).
    session: z.string().min(1),
    // The bead/work this session is doing, when known at capture time
    // (env-supplied). Optional: a session may touch memory before claiming a
    // bead; the session->work_id join is resolvable later from claim provenance.
    work_id: z.string().min(1).optional(),
    // The normalized operation (allow-list).
    op: z.enum(MEMORY_OPS),
    // The backend the op acted on (allow-list).
    backend: z.enum(MEMORY_BACKENDS),
    // WHICH memory: a file path or item id. A reference, NEVER the content
    // (content can carry outcome-correlated text below any field scan).
    memory_ref: z.string().min(1).optional(),
    // WHERE used: the concrete operating context (e.g. the file being edited
    // while a memory was read). A reference, never content.
    used_in: z.string().min(1).optional(),
    // The concrete tool that produced the op (Read/Write/Edit/mcp__...), kept
    // for audit; the normalized `op` is the queryable key.
    concrete_tool: z.string().min(1).optional(),
    // Structured, NON-content extras (byte/line counts, ids) — never memory
    // content, never an outcome field. The firewall treats this as label-side.
    payload: z.record(z.string(), z.unknown()).optional(),
    // Who captured it: `capture-hook` (PostToolUse), `manual`, `test`.
    source: z.string().min(1),
    // Event-time (when the op happened).
    occurred_at: z.string().optional(),
    // Ingest-time (when this row was written).
    created_at: z.string().min(1),
  })
  .strict();

export type MemoryEvent = z.infer<typeof MemoryEventSchema>;
