# CSB run-validity QA — port map for membench

Bead: mem-t0vj (doc-only). Directive: Stephanie via mayor mail gc-453048 —
adopt CodeScaleBench's run-validity discipline in membench beyond the
mechanism-fires gate. Surveyed 2026-07-05: CodeScaleBench @2072ccd377
(`/home/ds/projects/CodeScaleBench`), mem local main @90628fa. All CSB refs
are `<repo-relative path>:<line>` at that commit; all membench refs are
relative to `memory-bench/`.

Scope guards honored here:

- The **pre-run mechanism-fires gate is already shipped** (mem-xe2p,
  @90628fa): `membench/grading/mechanism_gate.py`, wired in all three
  leg-burning drivers including dry runs, `--override-mechanism-gate REASON`,
  verdict in `summary["mechanism_fires"]`. It is referenced below as the
  pattern to follow, not re-proposed. Port provenance: it is a *composition*
  of CSB's `harbor_run_guarded` preflight scaffold (`configs/_common.sh`) with
  the `mcp_never_used` predicate
  (`scripts/authoring/validate_task_run.py:92-190`) lifted from post-run to
  pre-run — CSB itself has no pre-run mechanism smoke.
- **No eval-design changes are proposed** (lift definition, held-out set,
  headline instrument). Items that brush that territory are listed in §5 as
  flags for mem-pl, not specced.

## 1. What CSB's discipline actually is

The load-bearing idea is stated in its pipeline spec: invariants are
**"enforced by code, not agent compliance"** (`docs/PIPELINE_SPEC.md:477-512`,
invariants I-1…I-10 with a per-invariant enforcement map). Three properties
recur across its ~45 mechanisms:

1. **Every run leaves a verdict artifact, even on crash** — a synthetic
   `validation_result.json` with `status=verifier_error` is written by the
   harness wrapper on any exception (I-2, `lib/csb/validation_writer.py`).
   Silence is structurally impossible.
2. **Failure handling is bounded and classified** — attempt caps quarantine
   rather than retry forever (I-7); rate-limited results are auto-moved out of
   the analysis set; a watchdog kills batches whose accounts died.
3. **Provenance is stamped at launch and checked at publication** — run
   manifests at dispatch, spec-SHA stamps on every result, a publication gate
   that refuses stale stamps (`scripts/maintenance/publication_gate.py`).

membench already matches CSB on admission validity and beats it on pre-run
mechanism checking. The gaps are concentrated in **per-leg run accounting**
(what CSB does between dispatch and scoring) and **claim-time verification**
(what CSB does before results become citable).

## 2. Already covered — no port needed

| CSB mechanism (ref)                                                                                              | membench counterpart (ref)                                                                                                                                                          | Notes                                                                                                                                                             |
| ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Post-run `mcp_never_used` CRITICAL flag (`scripts/authoring/validate_task_run.py:92-190`)                        | Pre-run mechanism-fires gate (`membench/grading/mechanism_gate.py:73,111`; wired `scripts/run_grid_3arm.py:376`, `_graded:314,376`, `_ftp:180`)                                    | mem-xe2p. membench is stronger: fires before legs burn, fail-closed on zero anchors.                                                                              |
| Fail-to-pass oracle admission (`scripts/evaluation/verify_oracle_fail2pass.py:128-201`)                          | `validity_gate` — gold reproduces, empty fails (`membench/grading/validity_gate.py:46`); two-stage admission in `scripts/admit_batch_guarded.py:193`                                | Equivalent discipline; membench runs it pre-admission (mem-1eph), closing CSB's own historical ordering bug (validity as post-hoc annotation).                    |
| Prompt leakage audit (`scripts/evaluation/prompt_hygiene.py`, ABC criterion `scripts/evaluation/abc_audit.py:39`) | Leak guard, SSOT identifying keys, hard-fail at 7+ construction sites (`membench/grading/leak_guard.py:29,79`)                                                                      | membench is stronger: raises at task-construction time instead of auditing after.                                                                                 |
| Agent-harness fingerprint staleness (`scripts/maintenance/config_fingerprint.py:35-186`)                         | Pin discipline: `assert_run_pins`/`pinned_stream_exec` (`membench/harbor/probe_gate.py:538,600`), hard `PinMismatchError`, `summary["pins"]` in all three drivers                   | Model + CLI-version drift covered and hard-fail. Residual (launch manifest) → P4.                                                                                 |
| Frozen-spec determinism (`lib/runner/manifest.py:97-129` config/invariant hashes)                                 | World determinism manifest — SHA-256 freeze + re-materialization check (`membench/generators/world_manifest.py`)                                                                    | Covered for the synthetic track; the real-grid analog is P4/P5.                                                                                                   |
| Paired-run completeness (`lib/runner/task_validator.py:15-122`)                                                   | ITT-primary paired inference — missing rung → delta 0, never a silent drop (`membench/grading/paired_ci.py`)                                                                        | Different design, same failure mode closed.                                                                                                                       |
| Judge JSON/parse discipline (`docs/conventions/validation-scoring.md:52`)                                         | Parse-retry isolation `_score_round_retrying_parse` (`membench/grading/graded.py:379`, cap `GRADED_JUDGE_MAX_ATTEMPTS=4`), blinding (`graded.py:147`), N-round median (`:434-437`) | Retry-only-on-parse-error; every other failure propagates. Judge *config isolation* (the mem-eacq fix) is in flight as mem-9ld4 — not re-proposed here.           |

One membench-internal finding from the survey, recorded so "already covered"
claims stay honest: `safety_gates.py`, `provenance_gate.py`, and `coverage.py`
are **library-only** — nothing in the grid path calls them. For
`safety_gates`/`provenance_gate` this is a known consequence of the grid never
running consolidation writes (the arms they audit are homeless in the grid);
they are correctly designed as summary-block gates and should be wired the day
consolidation/retention legs exist, per CSB's I-8 lesson that an unwired gate
is a checklist item, not a gate. No bead proposed here; noted for whoever
lands consolidation legs.

## 3. Recommended ports

Effort classes: **S** = mechanical, single small bead; **M** = one bead with
tests; **L** = needs new data/labels or multiple beads. Ordered by expected
value. All verdicts follow the mem-xe2p pattern: persisted as summary sibling
keys, never inside `metrics()`; hard-fail with a logged `--override REASON`
escape hatch.

### P1 — Per-leg mandatory verdict + attempt-cap quarantine

- **CSB source:** I-2 mandatory `validation_result.json` even on crash
  (`lib/csb/validation_writer.py:42-51,160-173`); I-7 circuit breaker —
  `MAX_ATTEMPTS=3` then quarantine with a synthetic `verifier_error` result
  (`docs/PIPELINE_SPEC.md:285-294`).
- **membench landing:** the leg loop shared by the three grid drivers
  (`membench/harbor/bundle_grid.py` + `scripts/run_grid_3arm*.py`). Every leg
  persists a verdict record (scored | crashed | aborted | quarantined) with a
  failure class; a leg that errors N times is quarantined with a synthetic
  zero-reward record and the grid continues. `HeadlessAgentError`
  (`membench/runner/headless_agent.py:56`) already fails loud per-leg; the
  missing piece is the persisted classification and the bounded-retry
  quarantine so one wedged bundle can't stall or silently vanish from a grid.
- **Effort:** M.
- **Failure mode prevented:** silent leg drops and unbounded retry. Incident:
  the Fable-era `-eacq` run dirs had to be **discarded wholesale as "mixed
  provenance indefensible"** — with per-leg verdict records the salvageable
  legs would have been identifiable. The graded judge's own
  `GRADED_JUDGE_MAX_ATTEMPTS` retry cap is the in-repo precedent for the same
  discipline one level down.

### P2 — Post-run leg-sanity flag audit (the complement of mem-xe2p)

- **CSB source:** `validate_task_run.py:92-190` flags — CRITICAL
  `agent_never_ran`, `task_crashed`; WARNING `suspiciously_fast` (<10s
  non-crash), `barely_tried` (reward<0.1 with <3 tool calls),
  `no_output_tokens` (<50); auto-invoked by the postrun pipeline
  (`lib/csb/postrun.py:243-299`) including rate-limit quarantine
  (`classify_rate_limited:115-134`: reward 0 + wall clock <30s → archived);
  CRITICAL flags → nonzero exit (I-8). Convention: "agent finishing <2s =
  never ran" (`docs/conventions/validation-scoring.md`).
- **membench landing:** new `membench/grading/postrun_flags.py` (pure
  predicate over persisted run records: duration, output tokens, tool-call
  count, reward), auto-invoked at the end of all three drivers, verdict as
  `summary["postrun_flags"]`, CRITICAL → nonzero driver exit. The pre-run
  gate asserts the mechanism *can* fire; this asserts each leg *actually ran*.
  Together they close both halves of the invalid-run class Stephanie ruled on.
- **Effort:** M.
- **Failure mode prevented:** never-ran legs counted as data. Incident: the
  headless-auth failure class — `claude -p` aborting at `/login` when
  `CLAUDE_CODE_OAUTH_TOKEN` is unset produces near-instant empty runs; today
  that surfaces only if someone reads trajectories. Under P2 it is a CRITICAL
  flag and the grid exits nonzero.

### P3 — Token-death runtime watchdog

- **CSB source:** `_RuntimeWatchdog` (`lib/csb/harness_runner.py:258-349`) —
  sliding window over recent per-task statuses; >50% invalid in the last 10 →
  probe account tokens; any expired → SIGTERM the batch, exit 2.
- **membench landing:** the driver leg loop. Cheap version: after each leg,
  feed P1's verdict into a sliding window; on threshold, probe the OAuth
  credential (same source as `mem-harbor-headless-auth`:
  `~/.claude-homes/accountN/.claude/.credentials.json`) and abort the batch
  with a classified error instead of burning the remaining legs.
- **Effort:** M (depends on P1's verdict records).
- **Failure mode prevented:** a mid-grid credential/pool death converting the
  tail of a grid into invalid legs. Incident: the 2026-07-04 Fable-wall pool
  outage — runs started under a dying pool were unusable and the mixed dirs
  were discarded; a watchdog bounds the damage to the window size.

### P4 — Launch-time run manifest

- **CSB source:** `write_run_manifest` (`lib/csb/harness_runner.py:356-423`) —
  planned tasks, agent, model, config, parallelism, dry-run flag written to
  the runs dir *before dispatch*; experiment `config_hash` sha256
  (`lib/runner/manifest.py:97-129`); non-manifest run archiver
  (`scripts/maintenance/archive_non_manifest_runs.py`).
- **membench landing:** shared helper used by all three drivers, written
  before the first leg: planned bundles/arms/reps, model + CLI pins, store
  path + `SCHEMA_VERSION`, mem code commit, judge config mode (post-mem-9ld4),
  any gate override reasons. Today `summary["pins"]`/`["mechanism_fires"]`/
  `["validity_gates"]` are assembled at *summary* time — a grid that dies
  mid-flight leaves a runs dir with no machine-readable statement of what it
  was.
- **Effort:** S.
- **Failure mode prevented:** orphaned/undocumented run dirs whose provenance
  must be reverse-engineered. Incident: same `-eacq` discard — "mixed
  provenance indefensible" is precisely the absence of launch-time manifests
  distinguishing pre-wall from post-wall legs.

### P5 — Claim gate: stamp verification before results are citable

- **CSB source:** publication gate
  (`scripts/maintenance/publication_gate.py:142-199`) — every result must
  carry `oracle_spec_sha`/`recipe_spec_sha`/`pre_reg_sha` matching the sha256
  of the committed spec files; stale stamp or `eval_broken=true` → exit 1
  (stamps from `scripts/migration_eval/pre_reg.py`).
- **membench landing:** a standalone `scripts/verify_grid_claim.py` run
  against a results dir before its numbers are cited in any ADR/mail: asserts
  the P4 manifest exists and matches, `pins` green, `mechanism_fires` fired or
  override-logged, `validity_gates` pass, postrun flags carry no CRITICAL,
  judge config mode matches the current clean-judge contract. Read-only; no
  change to how anything is scored.
- **Effort:** M (S once P1/P2/P4 exist).
- **Failure mode prevented:** citing structurally-invalid numbers. Incidents:
  **grid-ce and every graded number to date were contaminated** by the
  bare-host judge (mem-eacq) and were cited until the variance pilot exposed
  it; the zhy00 oracle run was invalidated after the fact. A claim gate that
  checks the judge-config stamp would have refused both at citation time.

### P6 — Verifier-discrimination audit (beyond gold-passes/empty-fails)

- **CSB source:** verifier audit framework
  (`scripts/evaluation/verifier_audit.py:302-457`) — run each verifier against
  known-good *and known-bad* solutions; FP/FN rates with Wilson 95% CIs; flag
  FN>0.10.
- **membench landing:** extend the `validity_gate` admission run
  (`membench/grading/validity_gate.py:46`) with known-bad candidates beyond
  the empty diff — e.g. another bundle's gold diff (the `shuffled_condition`
  trick applied to candidates), asserting the oracle *fails* them; report
  per-pool discrimination stats alongside the existing two-point check.
- **Effort:** M.
- **Failure mode prevented:** test-insensitive oracles admitted as sound.
  Incident: this is a documented, recurring hole — `score_direct`/
  `repro_passed` came back **flat-zero degenerate (SD=0, oracle
  test-insensitivity)** in the mem-eacq variance pilot, and the mem-58io N=8
  anchor ceiling root-caused to sequential-replay drift **plus oracle
  test-insensitivity**. Gold-vs-empty passes such oracles; a wrong-gold probe
  catches them at admission.

### P7 — Judge calibration against a frozen anchor set

- **CSB source:** cross-model judge calibration — Cohen's κ per category,
  self-preference measured by position-swap averaging, `uncalibrated` flag
  (`observatory/calibrate.py:186-309`); gold-anchor correlation with
  `eval_broken` tripwire at Phi<0.7 point / CI-low<0.5
  (`scripts/migration_eval/gold_anchor.py`); frozen curator calibration set
  (`calibration/curator_calibration/`).
- **membench landing:** a frozen, labeled anchor set of (bundle, candidate,
  expected-band) fixtures + a `grading/` calibration check that the graded
  judge stays within band; the existing `divergence_flagged`
  (`membench/grading/graded.py:446`) is the runtime tripwire, this is the
  offline instrument test. Precedent in-repo: the confabulation gate already
  refuses to promote flag→void **until a frozen κ set clears FPR≤0.05/κ≥0.6**
  (`membench/grading/safety_gates.py:35-36,78-91`) — P7 extends that exact
  discipline to the rubric judge.
- **Effort:** L (needs a labeled set; everything else is mechanical).
- **Failure mode prevented:** an uncalibrated/biased judge carrying weight.
  Incident: judge within-task SD 0.0917 (>0.05) with the contamination
  confound — the mem-eacq doctrine is "judge needs variance reduction or
  demotion to secondary." Calibration is the instrument-QA half; the
  demotion question is ADR lock #5 territory → §5.

### P8 — In-repo QA runbook (skill-encoded launch procedure)

- **CSB source:** QA procedure shipped as repo skills —
  `skills/run/SKILL.md:16-42` (5-param launch confirmation, account preflight,
  paired policy), `skills/audit/SKILL.md`,
  `.claude/skills/compass-validation-scoring/SKILL.md` (validator sha256
  discipline, dual-score fallback order, quarantine), backed by the AGENTS.md
  failure-mode ledger (`AGENTS.md:9-39`).
- **membench landing:** one checked-in runbook (`memory-bench/docs/` or a
  repo skill) encoding the grid-launch preflight that today lives only in
  agent memory: build-store cwd requirement (`city.toml` missing → 0 traces
  resolved, exit 0), OAuth credential sourcing, pin expectations, override
  policy, results-dir hygiene. Where a check can be structural instead
  (e.g. `--with-traces` erroring when `city.toml` is absent), file it as code
  follow-up rather than prose.
- **Effort:** S.
- **Failure mode prevented:** re-learning environment gotchas per session.
  Incident: the build-store cwd trap has bitten repeatedly (silent 0/N trace
  resolution) and survives only as agent-memory notes; CSB's answer is to
  make the procedure a repo artifact.

## 4. Not portable / deliberately skipped

- **MCP-specific flags** (`mcp_never_used`, `deepsearch_unused`) — already
  generalized by mem-xe2p's parameterized gate; new mechanisms pass their own
  covariate to `enforce_mechanism_fires` rather than porting per-flag checks.
- **Paired baseline/MCP lane policy + pairing validator** — membench's
  ITT-primary paired CI covers the same failure mode by construction.
- **Per-task Docker image smokes** (`smoke_test_tasks.py`,
  `smoke_artifact_verifier.py`) — membench replays in worktrees at exact
  `base_commit` (`repro_live.py`), not per-task images; env recon is
  deliberately approximate (D17). The equivalent assurance comes from the
  validity gate actually executing the oracle.
- **Training-data contamination split** (`contamination.py:69-121`,
  pre/post-cutoff) — membench's corpus is private rig work; repo-creation vs
  model-cutoff bucketing has no signal here. Revisit only if public-repo
  tasks enter the pool.
- **IRT difficulty calibration** (`calibration/curator_calibration`,
  `docs/ORG_CALIBRATION.md`) — needs curator labels and a pool far larger
  than current N (mem-eacq MDE floors: N4k3/N8k1 minimum for the one clean
  metric). Premature; P7's anchor set is the right first step.
- **Compile-gate insertion** (`add_compile_gates.py`) — targets
  structural-only verifiers; membench oracles are test-execution based.
- **Snapshot publishing verifier** (`verify_snapshot.py`) — no publishing
  pipeline; the claim-side need is P5.
- **Interactive launch confirmation** (`skills/run/SKILL.md:16-26`) — grid
  launches here are operator-driven with explicit flags; the credit-burn
  guard that matters is P3 + the existing paid-run gating (D16). Folded into
  P8's runbook rather than an interactive gate.

## 5. Flags for mem-pl (eval-design territory — not specced here)

- **Judge-channel demotion vs variance reduction** — P7 gives the instrument
  QA, but whether `judge_score` stays a headline-capable channel is ADR lock
  #5 (mem-eacq doctrine, mem-do8r.1 lock package). Decision, not a port.
- **What the P7 anchor labels mean** — choosing the human/gold acceptance
  criterion for judge calibration defines part of the scoring contract;
  needs a mem-pl ruling before the labeled set is built.
- **CSB's pre-registration discipline** (`pre_reg.py` hypotheses stamped
  before runs) — porting the *stamp mechanics* is P5; adopting
  pre-registered hypotheses for membench grids is an eval-methodology
  decision, surfaced here without a recommendation.

## Suggested sequencing

P4 (S, unblocks P5) → P1 → P2 (closes the invalid-run class end-to-end) →
P3 (rides P1) → P5 (claim gate over all of it) → P6 → P8 → P7 (gated on the
§5 labeling ruling).
