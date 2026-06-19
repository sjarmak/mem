import { z } from 'zod';

/**
 * Provenance event (mem-side prototype of the beads `provenance_events`
 * primitive — see docs/mem-bead-provenance-upstream-contribution.md).
 *
 * An append-only causal fact bound to a `work_id`: what base commit a worktree
 * was cut from (`cut`), which actor touched it when (`claim`/`suspend`/
 * `resume`/`handoff`), what commit/PR landed it (`land`), what other work it
 * drew on (`used`). Modelled here so mem can stop *reconstructing* provenance
 * after the fact (date-heuristic base SHA, time-window attribution, trailer
 * scan) and start *reading* it — and so the event vocabulary can stabilise
 * against real eval pressure before it is proposed upstream into core bd.
 *
 * `id`/`actor`/`ref` are opaque to the store: it validates only that `kind`
 * and `ref_kind` are known (structural validation, ZFC), never what a SHA
 * *means*. The future upstream table mirrors these columns exactly.
 */

/** The open event vocabulary. Extends as new producers appear; the store
 * rejects unknown kinds so a typo is a loud failure, not a silent new class. */
export const PROVENANCE_KINDS = [
  'cut',
  'claim',
  'suspend',
  'resume',
  'handoff',
  'commit',
  'land',
  'used',
] as const;
export type ProvenanceKind = (typeof PROVENANCE_KINDS)[number];

/** Namespaces what `ref` points at, so a `by-ref` lookup is unambiguous. */
export const PROVENANCE_REF_KINDS = ['git-sha', 'pr', 'work-id', 'transcript', 'branch'] as const;
export type ProvenanceRefKind = (typeof PROVENANCE_REF_KINDS)[number];

export const ProvenanceEventSchema = z.object({
  // Deterministic for backfilled events (idempotent re-ingest), a ulid for
  // genuine producer events. Either way the PK is the dedup key.
  id: z.string().min(1),
  work_id: z.string().min(1),
  kind: z.enum(PROVENANCE_KINDS),
  // Opaque runtime identity (a session name/id). The store never interprets it.
  actor: z.string().optional(),
  // Opaque pointer: a SHA, a PR url, a work_id, a transcript path.
  ref: z.string().optional(),
  ref_kind: z.enum(PROVENANCE_REF_KINDS).optional(),
  // Kind-specific structured extras (history_state, landed_state, ci, …).
  payload: z.record(z.string(), z.unknown()).optional(),
  // Who appended it: `ingest-backfill` today; `gascity`/`git-hook`/`ci-webhook`
  // once real producers exist.
  source: z.string().min(1),
  // Event-time (when the fact became true) — may precede ingest-time.
  occurred_at: z.string().optional(),
  // Ingest-time (when this row was written).
  created_at: z.string().min(1),
});

export type ProvenanceEvent = z.infer<typeof ProvenanceEventSchema>;
