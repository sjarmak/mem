import { z } from 'zod';
import { ExecutionSchema, PrLinkSchema, TraceErrorSchema, TraceRunSchema } from './trace.js';

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

/** An agent/session that worked the bead. Originally resolved from
 * `bead.assignee` (one per record); since the mem-75t.4 merged join a record
 * carries one entry per session iteration, ordered by `sequence` and tagged
 * with the join `sources` that established the link. `suspect` marks an
 * assignee-only link whose transcript content contradicts it. */
export const AgentRefSchema = z.object({
  agent_id: z.string().min(1),
  role: z.string().optional(),
  account: z.string().optional(),
  trace_ref: z.string().optional(),
  sequence: z.number().int().positive().optional(),
  started_at: z.string().optional(),
  ended_at: z.string().optional(),
  sources: z.array(z.string()).optional(),
  suspect: z.boolean().optional(),
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
  // Run-level metadata (tokens, model, harness, tool-call shape). Absent until
  // the transcript is parsed, like the sibling parsed fields above.
  run: TraceRunSchema.optional(),
  // `pr-link` transcript entries — the explicit transcript→GitHub PR bridge
  // (PRD §3 key #1). Absent until parsed, like the sibling parsed fields.
  pr_links: z.array(PrLinkSchema).optional(),
});

export type TraceRef = z.infer<typeof TraceRefSchema>;

/** The verifiable outcome label — what makes this a benchmark, not a log.
 * `repo` (`owner/name`) and `base_commit` (the PR's base-branch tip) are the
 * env-reconstruction anchors: they let a held-out WorkRecord be replayed against
 * the right repository at the right baseline without an out-of-band rig→repo map
 * (see memory-bench config/rigs.py, the interim workaround this field retires). */
export const OutcomeSchema = z.object({
  pr: z.string().optional(),
  repo: z.string().optional(),
  pr_state: z.enum(['merged', 'closed']).optional(),
  commit_sha: z.string().optional(),
  base_commit: z.string().optional(),
  ci: z.enum(['pass', 'fail']).optional(),
});

export type Outcome = z.infer<typeof OutcomeSchema>;

/** Locally-derived environment baseline (git-provenance ingest). Distinct from
 * `outcome`: `outcome` is the *verifiable* GitHub label (PR/CI) and its
 * `base_commit` is the PR's authoritative base-branch tip; `provenance` is the
 * repo + commit a session STARTED from, reconstructed from `gc.work_dir` and the
 * bead's start time when no PR or base SHA was ever recorded. It exists to make a
 * WorkRecord replayable as a CodeScaleBench-style git-checkout environment.
 *
 * `base_commit` is an APPROXIMATION: gc records the base *branch* but never the
 * exact base SHA, so the commit is derived by date — the newest commit on
 * `base_branch` at or before `started_at`
 * (`git rev-list -1 --before=<started_at> <base_branch>`), NOT a guaranteed-exact
 * base. It is resolved ONLY when a `base_branch` is recorded: resolving against
 * the work_dir's HEAD would walk the agent's own feature branch (whose history
 * may contain the solution) — a train/test leak — so an absent base_branch is
 * terminal `unresolved`, never guessed. `history_state` is self-describing:
 * `commit-by-date` when a commit resolved, `unresolved` when the work_dir is not
 * a reachable local repo, no base_branch was recorded, or no commit predates
 * `started_at`. */
export const ProvenanceSchema = z.object({
  work_dir: z.string().min(1),
  repo: z.string().min(1),
  base_branch: z.string().min(1).optional(),
  // A full 40-hex commit SHA (what `git rev-list -1` emits). The format guard is
  // the boundary contract: any other construction path (or git output corrupted
  // by argument injection) fails the parse rather than storing a bad anchor.
  base_commit: z
    .string()
    .regex(/^[0-9a-f]{40}$/)
    .optional(),
  history_state: z.enum(['commit-by-date', 'unresolved']),
  // Where the inputs came from, surfaced so a baseline reconstructed from rig
  // convention is never mistaken for one the session actually recorded.
  // `metadata` = read from `gc.work_dir`/`gc.var.base_branch`; `rig-map` =
  // backfilled from the rig's canonical dir; `default` = the assumed integration
  // branch. Absent on records built before this field existed.
  work_dir_source: z.enum(['metadata', 'rig-map']).optional(),
  base_branch_source: z.enum(['metadata', 'default']).optional(),
});

export type Provenance = z.infer<typeof ProvenanceSchema>;

/** Locally-derived OUTCOME for the direct-to-main majority (ingest/landed).
 * Where `provenance` reconstructs the commit a session STARTED from, this
 * reconstructs what it LEFT on the integration branch: the tip at session close
 * and how many commits landed in the `[started, closed]` window. It is the
 * outcome oracle for repos with no PR/CI workflow — the corpus norm — where the
 * verifiable question is "did this work land on `main` and survive", a pure git
 * fact needing no GitHub linkage.
 *
 * `landed_state` is self-describing and never guesses:
 *  - `landed`          — commits landed in-window and the tip is still an
 *                        ancestor of the current branch (the work survives).
 *  - `reverted`        — a later commit on the branch reverts one of them.
 *  - `abandoned`       — the window tip is no longer reachable from the current
 *                        branch (history was rewritten; the work was dropped).
 *  - `empty-window`    — no commit landed between start and close (a session
 *                        that produced nothing on the branch — a real negative).
 *  - `ambiguous-window`— another session's window overlaps this one on the same
 *                        checkout+branch, so commits cannot be attributed by time
 *                        alone (left for author/SHA attribution, never guessed).
 *  - `unresolved`      — the branch/checkout is unreachable or the close commit
 *                        could not be resolved. */
export const LandedSchema = z.object({
  // The start anchor (echoed from provenance) the window is measured from.
  base_commit: z.string().regex(/^[0-9a-f]{40}$/),
  // The integration-branch tip at session close — the session's contribution tip.
  landed_commit: z
    .string()
    .regex(/^[0-9a-f]{40}$/)
    .optional(),
  // Commits in `base_commit..landed_commit` (0 for an empty window).
  n_commits: z.number().int().nonnegative().optional(),
  landed_state: z.enum([
    'landed',
    'reverted',
    'abandoned',
    'empty-window',
    'ambiguous-window',
    'unresolved',
  ]),
});

export type Landed = z.infer<typeof LandedSchema>;

/** How a record's canonical `repo` was resolved (mem-bme, ingest/repo-resolve):
 * `outcome` (owner/name of a verified PR), `rig-map` (a rig that is 1:1 with a
 * repo), or `unmapped` (no source — `repo` left absent, never guessed). */
export const RepoSourceSchema = z.enum(['outcome', 'rig-map', 'unmapped']);

export type RepoSource = z.infer<typeof RepoSourceSchema>;

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
  /** Task type plus how it was assigned (mem-75t.11). Sources: `formula`
   * (molecule/step beads carry their formula name mechanically), `structural`
   * (machine-generated title grammars), `model` (free-form beads classified
   * by a model — the artifact records which model and when). Absent until
   * typing runs; never silently defaulted. */
  task_type: z.string().optional(),
  task_type_source: z.enum(['formula', 'structural', 'model']).optional(),
  labels: z.array(z.string()).default([]),
  metadata: z.record(z.string(), z.unknown()).default({}),
  priority: z.number().int().optional(),
  // The bead's `external_ref` (e.g. "gh-1873"). Captured in the P1.2 spine so
  // P1.4 can resolve it to a PR/commit `outcome`; absent until a bead sets one.
  external_ref: z.string().optional(),
  // Canonical repository identity (`owner/name`), backfilled by
  // ingest/repo-resolve (mem-bme). Distinct from `provenance.repo` (a bare
  // work_dir basename for the env baseline): this is the grouping/retrieval key
  // and the source the memory-bench rig→repo stopgap now reads. Absent until the
  // resolve stage runs, and stays absent when `repo_source` is `unmapped`.
  repo: z.string().min(1).optional(),
  repo_source: RepoSourceSchema.optional(),
  lifecycle: LifecycleSchema,
  agents: z.array(AgentRefSchema).default([]),
  trace: TraceRefSchema.optional(),
  outcome: OutcomeSchema.optional(),
  provenance: ProvenanceSchema.optional(),
  landed: LandedSchema.optional(),
  signal: SignalSchema.optional(),
  // Factory form: a literal default would share one array instance across
  // every parsed record (zod does not deep-clone nested defaults).
  links: LinksSchema.default(() => ({ deps: [], supersedes: [] })),
});

export type WorkRecord = z.infer<typeof WorkRecordSchema>;
