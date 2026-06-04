import { z } from 'zod';
import { ExecutionSchema, TraceErrorSchema } from './trace.js';

/**
 * WorkRecord — the atomic unit of the work-audit graph (ARCHITECTURE.md,
 * "Data model"). Keyed by a bead id, it joins the whole audit:
 * bead → assignee(=agent/session) → trace JSONL → PR/commit → outcome.
 *
 * `trace` is attached in P1.3 and parsed in P1.6; `outcome` is attached in
 * P1.4; `signal` shapes are extracted in P1.6+ (deliberately open records
 * until then).
 */

/** Bead lifecycle. `status` stays a plain string until P1.2's dolt reader
 * reveals the real cross-rig value set. */
export const LifecycleSchema = z.object({
  created: z.string(),
  started: z.string().optional(),
  closed: z.string().optional(),
  status: z.string().min(1),
  status_history: z.array(z.object({ status: z.string(), at: z.string() })).default([]),
});

export type Lifecycle = z.infer<typeof LifecycleSchema>;

/** An agent/session that worked the bead, resolved from `bead.assignee`. */
export const AgentRefSchema = z.object({
  agent_id: z.string().min(1),
  role: z.string().optional(),
  account: z.string().optional(),
  trace_ref: z.string().optional(),
});

export type AgentRef = z.infer<typeof AgentRefSchema>;

/** Trace pointer (attached in P1.3) plus parsed signal (populated in P1.6).
 * The parsed fields stay absent — not defaulted — until parsing runs, so
 * "not yet parsed" is distinguishable from "parsed, found nothing".
 * `tool_calls` is added in P1.6 once JSONL parsing defines its shape. */
export const TraceRefSchema = z.object({
  jsonl_path: z.string().min(1),
  n_turns: z.number().int().optional(),
  tool_outcomes: z.array(ExecutionSchema).optional(),
  errors: z.array(TraceErrorSchema).optional(),
});

export type TraceRef = z.infer<typeof TraceRefSchema>;

/** The verifiable outcome label — what makes this a benchmark, not a log. */
export const OutcomeSchema = z.object({
  pr: z.string().optional(),
  pr_state: z.enum(['merged', 'closed']).optional(),
  commit_sha: z.string().optional(),
  ci: z.enum(['pass', 'fail']).optional(),
});

export type Outcome = z.infer<typeof OutcomeSchema>;

/** Extracted memory signal. Shapes are open until P1.6+/Phase 2 settle them. */
export const SignalSchema = z.object({
  deterministic: z.record(z.string(), z.unknown()).default({}),
  semantic: z.record(z.string(), z.unknown()).default({}),
});

export type Signal = z.infer<typeof SignalSchema>;

/** Graph edges to other work. */
export const LinksSchema = z.object({
  deps: z.array(z.string()).default([]),
  convoy_id: z.string().optional(),
  supersedes: z.array(z.string()).default([]),
});

export type Links = z.infer<typeof LinksSchema>;

export const WorkRecordSchema = z.object({
  work_id: z.string().min(1),
  rig: z.string().min(1),
  title: z.string(),
  labels: z.array(z.string()).default([]),
  metadata: z.record(z.string(), z.unknown()).default({}),
  priority: z.number().int().optional(),
  lifecycle: LifecycleSchema,
  agents: z.array(AgentRefSchema).default([]),
  trace: TraceRefSchema.optional(),
  outcome: OutcomeSchema.optional(),
  signal: SignalSchema.optional(),
  // Factory form: a literal default would share one array instance across
  // every parsed record (zod does not deep-clone nested defaults).
  links: LinksSchema.default(() => ({ deps: [], supersedes: [] })),
});

export type WorkRecord = z.infer<typeof WorkRecordSchema>;
