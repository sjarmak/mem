---
name: mem-research-methodology-and-evidence-bar
description: >-
  THE evidence doctrine for the mem benchmark: what counts as a result here and
  what disqualifies one. Load BEFORE claiming any number, designing any
  experiment, adding any metric or scorer, interpreting a grid/eval run,
  deciding whether an LLM judge may gate anything, or writing up findings.
  Covers oracle soundness (gold reproduces AND empty fails), paired per-task
  deltas with bootstrap CIs (never pooled means), the ZFC
  mechanical-vs-model-judgment boundary, the no-paid-API scope (D16), judge =
  report-only never a gate, fail-closed/never-guess, supersede-don't-rewrite,
  and spec-governs-over-log. NOT for running the harness end-to-end (use
  mem-eval-harness-run), NOT for the scoring stack's mechanics (use
  mem-grading-and-validity-gates), NOT for LOO/leak internals (use
  mem-temporal-loo-and-leak-safety), NOT for what was already tried and failed
  (use mem-failure-archaeology), NOT for the numbered Decision rulings
  themselves (use mem-decision-ledger-and-architecture-contract).
---

# mem research methodology and evidence bar

This is the doctrine skill. mem's entire value proposition is that its numbers
survive a skeptic; this skill is the skeptic, written down. Every experiment,
metric, scorer, and write-up in this repo must clear the bar below. If a
proposed change conflicts with this skill, the change is wrong or it needs a
new numbered Decision in `docs/architecture-decisions.md` first.

Verified against the working copy on **2026-07-07**, branch `main` @ `4e819e1`.
Date-stamped facts may drift; re-verification commands are at the bottom.

## Jargon, defined once

| Term                  | Meaning here                                                                                                                                                                                              |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **WorkRecord**        | The atomic corpus unit: one bead's work, joined to agents, trace, outcome, provenance (`docs/architecture-decisions.md`, data-model section).                                                             |
| **Oracle**            | The known-good answer for a held-out task: the gold diff plus its fail-to-pass tests. Also the name of the exact-memory ceiling arm.                                                                      |
| **Sound oracle**      | An oracle that passes the validity gate: the gold diff reproduces (tests pass) AND the empty diff fails (tests fail). Anything else is a broken oracle, not a hard task.                                  |
| **Bundle**            | One held-out task packaged for replay (`membench/schemas/bundle.py`): env, instruction, oracle.                                                                                                           |
| **Arm / condition**   | One memory configuration under test (`no_memory` / `oracle` / `memory_enabled`, plus named systems: ours, builtin, mem0, ...).                                                                            |
| **Rung**              | One step on the information ladder the ablation headline sweeps (`membench/grading/ablation.py` `DEFAULT_RUNGS`).                                                                                         |
| **Temporal LOO**      | Leave-one-out in time: an arm may only see records closed strictly before the held task started (Decision 6).                                                                                             |
| **ZFC**               | Zero Framework Cognition: mechanical facts are computed in code; semantic judgment is delegated to a model; neither side does the other's job.                                                            |
| **ITT / matched**     | The two paired-delta populations (`grading/paired_ci.py`): intention-to-treat (pre-registered primary, missing observations imputed as delta 0) vs per-protocol matched (secondary, both rungs observed). |
| **Void / quarantine** | Run-level safety verdicts (`grading/safety_gates.py`): void kills the run's number; quarantine keeps the data but makes it win-ineligible.                                                                |

## The evidence bar, as a gate sequence

No number is a result until it has passed every gate that applies. Run them in
this order; each gate is cheaper than the one after it.

### Gate 1 — Oracle soundness precedes every graded signal

**Rule: no task counts until its gold diff reproduces AND its empty diff
fails.** This is the CodeScaleBench-style validity gate, implemented in
`memory-bench/membench/grading/validity_gate.py`. It runs the same
`ReproRunner` the scoring leg uses; a bundle failing it is **excluded with a
recorded reason, never silently scored**:

- gold diff must repro-pass with `test_ratio == 1.0`; anything else means the
  gold answer itself does not work in the replayed env,
- empty diff must repro-fail with `test_ratio == 0.0`; a partially passing
  empty diff means the tests are not actually fail-to-pass.

Why this is first: a non-reproducing gold diff drags every arm's score toward
noise, so any comparison built on unsound oracles is unfalsifiable garbage
regardless of sample size. The real-corpus program hit exactly this wall
(commit-trailer linkage recovers hundreds of "sound-looking" oracles, of which
only a handful replay cleanly; see mem-failure-archaeology before trying to
"fix" that funnel).

```bash
# The gate's own tests (fast, offline):
cd memory-bench && .venv/bin/python -m pytest tests/test_validity_gate.py tests/test_admit_validity_gate.py -q
```

### Gate 2 — Paired per-task deltas with bootstrap CIs; pooled means are banned

**Rule: an arm comparison is read as per-task deltas (treatment minus baseline
on the SAME task), summarized by a seeded percentile-bootstrap CI. A lift is a
finding only when that CI excludes zero.** Never report a pooled mean across
tasks as the comparison.

Implementation: `memory-bench/membench/grading/paired_ci.py`
(`paired_delta_ci`, default 5000 resamples, seed 0, reusing
`handoff_efficiency.bootstrap_median_ci`). The population is always explicitly
labeled:

| Population | Role                   | Missing observation on one rung                                         |
| ---------- | ---------------------- | ----------------------------------------------------------------------- |
| `itt`      | Pre-registered PRIMARY | delta imputed as 0 (stable as corpus grows)                             |
| `matched`  | Labeled SECONDARY      | task dropped (mechanism-conditional; population shifts as corpus grows) |

Why pooled means are banned: the grids are fully paired, so pooling throws away
the pairing and lets between-task variance swamp the effect. A pooled mean near
zero can hide a real per-task regression, and a pooled positive can be carried
by one easy task. The repo's own reporting doctrine states it directly:
"per-bundle deltas, pooled means distrusted" (`docs/mem-g6a-graded-metric.md`),
and the recall-ladder ADR pins "reported as a finding only when the
paired-delta CI excludes zero" (`docs/mem-do8r-recall-ladder-adr.md`).
`membench/report/ftp_calibration.py` encodes the same criterion mechanically
(`_lift_ci_excludes_zero`).

Corollary rules, enforced in code:

- an empty population **raises** rather than fabricating a degenerate interval
  (`paired_delta_ci`),
- `n_pairs` and `n_imputed_zero` ride the result, so a CI is never quoted
  without its N.

```bash
cd memory-bench && .venv/bin/python -m pytest tests/ -q -k "paired"   # 13 tests as of 2026-07-07
```

### Gate 3 — Know your noise floor before you spend runs

**Rule: before launching a grid to detect an effect, check the minimum
detectable effect (MDE) at your N and repeat count against the measured
run-to-run noise floor.** If the effect you hope for is inside the noise band,
the run cannot produce evidence and the spend is waste.

The instrument calibration of record is the variance pilot
(`docs/mem-eacq-variance-pilot.md`, 2026-07-04): 3 bundles x 5 identical
`none-clean` repeats. Its findings are doctrine until superseded:

- **diff-sim is a usable instrument** (pooled within-task SD 0.015, under the
  0.05 usability threshold),
- **the rubric judge is NOT** (pooled SD 0.092, over the threshold), which is
  half of why the judge is report-only (Gate 5),
- zero SDs on pass-scale metrics at small pools are **floor-censoring, not
  stability**; do not read them as precision.

Compute MDEs with `membench.grading.variance.mde_paired_floor` (no scipy).
These are sampling-noise **lower bounds**: same-condition repeats cannot see
the task-by-arm interaction, so the true MDE is higher.

### Gate 4 — Leak-safety and anti-gaming guards are non-negotiable

Three guards apply to every lift run; each has a dedicated sibling skill for
mechanics, so here only the doctrine:

1. **Temporal LOO** (Decision 6): arms see only records closed strictly before
   the held task started, minus convoy siblings, supersedes closure, and
   PR/branch sharers. Weakening any exclusion leaks the answer and invalidates
   every number. Details: **mem-temporal-loo-and-leak-safety**.
2. **Precision / injected-volume guard** (Decision 10): returning the whole
   store scores recall 1.0, so outcome lift alone is gameable by
   over-injection. Injected-context volume and retrieval precision are a
   REQUIRED guard on each lift run, not an optional side metric.
3. **Contamination audit**: an agent run that consulted the origin repository
   (WebFetch of the PR it is reproducing, `gh pr view`, clone/fetch of origin)
   saw post-hoc answer material and its run is invalid. This actually happened
   once (bundle `zhy00`, oracle arm; `docs/audits/2026-07-03-headline-network-fetch-audit.md`),
   which is why produced task envs now default to allowlist networking and why
   the contaminated table may only be cited with its footnote. Grep new run
   dirs for origin fetches before believing them.

### Gate 5 — The LLM judge is report-only, never a gate

**Rule: no LLM-judge score may sit in a pass/fail loop, gate an admission,
order a headline, or void a run, until a frozen calibration set on disk clears
a pre-registered bar.** The judge is an L3 side signal in the score vector.

Evidence and enforcement, all in-repo:

- `membench/grading/graded.py`: the graded judge signal "is a SIDE SIGNAL in
  the score vector, never a gate and never folded into a single weighted
  number" (mem-r5y locked decision 3).
- The variance pilot measured the judge's within-task SD at 0.092, far over
  the 0.05 instrument threshold; a metric noisier than the effects it would
  gate cannot gate.
- `membench/grading/safety_gates.py`: the confabulation gate's void authority
  is **earned, never granted by a config flag**. It returns `flag`
  (quarantine, win-ineligible) by construction until a frozen kappa
  calibration set clears the pre-registered bar, which is set in code before
  any rates were seen: `PREREGISTERED_FPR_MAX = 0.05`,
  `PREREGISTERED_KAPPA_MIN = 0.6`. Contrast: `wrongful_destruction` is a
  deterministic oracle with no judge, so it MAY void from day 1.
- The promotion path is staged and explicit (`docs/mem-g6a-graded-metric.md`):
  report-only now, hand-labeled kappa check next, and a composite number only
  if weights are fit against labels and frozen with provenance. "Absence is
  more truthful than an arbitrary one."
- Judge blinding is structural: the judge view contains no arm label, no
  memory payload, no held-out resolution (`grading/judge.py`, `graded.py`), so
  the answer cannot leak into what it scores.

**Safety gates are never averaged.** `run_void` / `win_eligible` ride a
separate summary block beside the metrics, and a test enforces that they are
never collapsed into a mean "safety_score" (`safety_gates.py` module
docstring). Quality rides the mean; safety rides the block.

```bash
cd memory-bench && .venv/bin/python -m pytest tests/test_safety_gates.py tests/test_judge.py -q
```

### Gate 6 — Numbers ship with caveats, and headline numbers do not ship at all without a release call

**Rule: never state a headline or real-corpus number as publishable.** Cite
numbers only alongside their validity caveats (oracle-soundness N, CI,
population label, contamination footnotes), and name the freeze when you do.

The freeze's operational home — primary sources, scope ruling, main-freeze
windows, and the "freeze LIFTED" bead-collision fact — is
**mem-git-and-dispatch-workflow §4**; this gate states only the evidence
doctrine and defers freeze status/scope there. The binding reading
(PROVISIONAL pending Stephanie, discovery Q4, stated in full in that §4):
**all headline/real-corpus numbers are held, and releasing any of them is
Stephanie's call — until she states the exact current freeze scope.**
Concretely: the n=9 ablation table may only be cited with its contamination
footnote (re-issue as clean n=8, Gate 4), and the real-corpus graded
result is a diagnosed-ceiling null whose write-up call was **resolved
2026-06-18 (option c: killed, findings held in beads/docs)** — do not
represent it as shipped, abandoned, or still awaiting a decision.

**PROVISIONAL pending Stephanie (discovery Q3):** the shipped headline design
is the ablation score-vs-information curve (Decision 17/18; the agent is its
own control across an information ladder, so it needs no outcome label). The
recall ladder (`docs/mem-do8r-recall-ladder-adr.md`) is branch-ready with 6
locks awaiting sign-off (the ADR's "Stephanie's locks" section, 6 numbered
items); describe it as proposed, never as the going-forward contract.

## The ZFC boundary

**Mechanical signal lives in code. Semantic judgment is delegated to a model.
Never blur the line in either direction.** (AGENTS.md invariant: "Deterministic
signal is mechanical, never model judgment.")

| Side              | Allowed                                                                                                                                                     | Where it lives                                                                                                                                                                                          |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Code (mechanical) | IO, schema validation, exit-code and format-anchored parsing, set arithmetic, seeded resampling, deterministic gates, weighted sums of model-emitted scores | `src/parse/runners.ts` (token-matched runner classification), `src/parse/error-extractors.ts` (format-anchored `file:line`), `membench/validity.py`, `grading/paired_ci.py`, `grading/validity_gate.py` |
| Model (judgment)  | Root-cause extraction, resolution distillation, rubric-scored completion quality, entailment                                                                | `src/parse/trace-parse.ts` (semantic extractor), `src/distill/distiller.ts`, `grading/judge.py`, `grading/graded.py`                                                                                    |

Forbidden moves, each with its in-repo precedent:

- **No keyword/regex meaning-detection in code.** The engram keyword
  memory-tier classifier was deliberately NOT ported ("the ZFC violation",
  `src/parse/runners.ts` header). Do not add semantic heuristics to
  `src/parse/`; the deterministic extractor is exit codes plus format-anchored
  extraction, nothing softer.
- **No thresholds in code that encode a semantic judgment.** When no
  mechanical derivation exists, scope the claim instead of inventing a mapping
  (Decision 24 scoped ftp-shape calibration to the blueprint track rather than
  code a semantic shape-mapping).
- **No model in the arithmetic.** The judge emits per-criterion scores; the
  weighted sum is computed in code (`graded.py`). The judge never runs its own
  aggregation.
- Modules state their ZFC position in their docstring (see
  `validity_gate.py`, `paired_ci.py`, `judge.py`). Follow that convention when
  adding one.

Full extraction-layer runbook: **mem-deterministic-extraction-zfc**.

## The no-paid-API scope (Decision 16; do not re-litigate)

`no-paid-API` is scoped to the **memory stack only**: backends, embeddings,
extractor, judge must be OSS / self-hosted. It does NOT cover the
agent-under-test, Harbor, or Docker. The agent-under-test runs on the flat-rate
Claude OAuth subscription, which is not a metered paid API and carries no
per-run marginal cost; running conditions across the held-out set is in-scope
mechanism work, not a paid-infra ask. Decision 16 closes this with "Do not
re-litigate this."

Enforcement in code: `grading/judge.py` `OssLlmJudge` refuses paid managed
hosts by parsed hostname (`openai.com`, `anthropic.com` and subdomains), while
the `claude -p` OAuth seam used by the graded judge and the distiller is
explicitly inside the fence (`graded.py`, `src/distill/distiller.ts`). An arm
that cannot run without a paid API is dropped and the drop documented
(Decision 11).

Note the separate cost gate: model **spend** (distill passes, judge tokens) on
shared capacity is still an operational approval matter even when free per-run.
PROVISIONAL pending Stephanie (discovery Q4): treat any bulk distill/judge
spend as requiring sign-off.

## Fail-closed, never guess

**A missing fact stays missing. The pipeline fails loud or records a typed
absence; it never fabricates a value to keep a number computable.**

| Situation                                              | Doctrine                                                                                                                              | Where                                                        |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Base branch never recorded                             | `history_state: unresolved`, terminal. Resolving from the work_dir HEAD would walk the agent's own feature branch: a train/test leak. | decision log, data-model section; `src/ingest/provenance.ts` |
| Runner errored before the test loop                    | `test_ratio = None`: "a typed absence, not a misleading 0.0"                                                                          | `grading/validity_gate.py` `ValidityResult`                  |
| Bundle fails the validity gate                         | Excluded with a recorded `reason`, never silently scored                                                                              | `grading/validity_gate.py`                                   |
| No tasks in a delta population                         | `raise ValueError`, no degenerate CI                                                                                                  | `grading/paired_ci.py`                                       |
| Session concurrency makes landed attribution ambiguous | `ambiguous-window`, deterministic set stays small; recovery is scoped future work, "never guessed"                                    | Decision 18                                                  |
| Judge returns an out-of-range or unparseable score     | Fail loud (validation in `score_completion`; bounded retries then error)                                                              | `grading/judge.py`, variance-pilot run log                   |

Known intentional asymmetries (do not "fix" one side to match the other):
`--with-traces` run outside a gas-city working directory resolves zero traces
with exit 0 (a missing `city.toml` is silent; a missing `gc` binary errors) -
that is the store half's one fail-open trap, documented in
**mem-ingest-and-provenance** and AGENTS.md. Misreading fail-closed behavior
as a bug (an `unresolved` provenance, a `None` ratio, an optional-SDK test
skip) is a catalogued newcomer error; check this table before filing one.

## Supersede, don't rewrite

**History is append-only; corrections are new entries that point at what they
replace.**

- The decision log's own header: "Entries below are preserved as written;
  supersede in place, do not rewrite history"
  (`docs/architecture-decisions.md`). Worked examples: Decision 13 is marked
  SUPERSEDED by the spec's taxonomy; Decision 23 "supersedes the implicit
  labeling" of the ours arm; Decision 18 amends 17 without deleting it.
- Lessons are extracted **once at ingest, append-only, never iteratively
  rewritten** (Decision 9; continuous LLM rewriting degrades consolidated
  memory, see the log's literature grounding). The distiller skips
  already-lessoned records by default for exactly this reason
  (`src/distill/distiller.ts`).
- Three store tables are append-only and non-regenerable (`lessons`,
  `memory_events`, producer-source `provenance_events`); citations are
  snapshotted at append time so a re-ingested outcome cannot silently rewrite
  an existing citation (`src/store/schema.ts`). Mechanics:
  **mem-store-schema-and-rebuild**.
- Replay writes go to a per-run scratch store, never the LOO-bounded corpus
  (Decision 14).

When you correct a finding, write the correction as a new dated entry or doc
that names the superseded one (the audit docs under `docs/audits/` are the
model), and re-issue tables rather than editing them in place.

## The spec governs over the log

`.gc/memory-eval-harness-spec.md` is the authoritative eval contract. Where the
chronological decision log and the spec conflict, **the spec governs**
(`docs/architecture-decisions.md` header and the Decision 11-16
reconciliation note). Reading order when they seem to disagree: spec first,
then the log entry that reconciled to it (the DIV-numbered divergence
resolutions inside Decisions 11-16), then `ARCHITECTURE.md` for the
synthesized current state. Do not patch a divergence yourself; that is a
Decision-ledger change (see **mem-decision-ledger-and-architecture-contract**).

## Pre-registration discipline

State what the numbers should be before you run, in the artifact that will
carry the result:

- thresholds are pre-registered **in code** before rates are seen
  (`PREREGISTERED_FPR_MAX` / `PREREGISTERED_KAPPA_MIN` in `safety_gates.py`,
  with the module docstring naming the rationale),
- the primary population is pre-registered (`POPULATION_PRIMARY = "itt"`,
  `paired_ci.py`),
- run pins (agent model, CLI version, judge model, rounds) are asserted at
  launch and echoed into the report (`assert_run_pins`, variance pilot), so a
  number is attributable to an exact configuration,
- expected-observation gates belong in campaign docs ("if you see X instead,
  branch to Y"): the pattern to copy is `docs/mem-72sj-gate0-nonflat-probe.md`
  and the campaign skill **mem-oracle-validity-wall-campaign**.

## Before you claim a result: the checklist

1. Every counted task passed the oracle validity gate; exclusions carry
   reasons. State N-sound alongside N-linked.
2. The comparison is paired per-task deltas; the CI is bootstrap, seeded,
   population-labeled (itt primary); the CI excludes zero.
3. N and imputation count are quoted with the delta.
4. The effect clears the MDE at your N/k against the variance-pilot noise
   floor for that metric.
5. LOO and the precision/injected-volume guard ran; no exclusion was weakened.
6. Runs audited for origin contamination (network allowlist held).
7. No judge score gated anything; judge columns are labeled report-only.
8. Safety block reported beside (not inside) the metrics; any void/quarantine
   named.
9. Caveats and footnotes attached; freeze named; nothing framed as
   publishable without Stephanie's release call.
10. The write-up supersedes rather than rewrites any prior finding it
    corrects.

`scripts/verify-evidence-gates.sh` in this skill runs the fast in-repo checks
for gates 1, 2, and 5 (read-only; offline stub judges).

## When NOT to use this skill

| You want                                         | Use instead                                       |
| ------------------------------------------------ | ------------------------------------------------- |
| Run the harness / a grid end-to-end              | **mem-eval-harness-run**                          |
| The scorers, gates, and curve mechanics          | **mem-grading-and-validity-gates**                |
| LOO/exclusion internals in both languages        | **mem-temporal-loo-and-leak-safety**              |
| What was already tried, failed, and settled      | **mem-failure-archaeology**                       |
| The numbered Decisions and invariants themselves | **mem-decision-ledger-and-architecture-contract** |
| Add or compare a memory arm                      | **mem-competitive-arms**                          |
| The parse-layer ZFC runbook                      | **mem-deterministic-extraction-zfc**              |
| Synthetic worlds and the necessity gate          | **mem-synthetic-world-generator**                 |
| Process/gating for landing changes               | **mem-git-and-dispatch-workflow**                 |
| First map of the project                         | **mem-orientation**                               |

## Provenance and maintenance

Authored 2026-07-07 against branch `main` @ `4e819e1`
(`git -C /home/ds/projects/mem rev-parse --short HEAD`); the checkout was on
`main` at authoring time. Every file path, symbol, constant, and quotation
above was read from the working copy this session. Volatile facts (freeze
status, test counts, pilot numbers) are date-stamped inline.

Re-verify before trusting drift-prone claims:

```bash
cd /home/ds/projects/mem
git branch --show-current && git rev-parse --short HEAD
sed -n '1,10p' docs/architecture-decisions.md                      # spec-governs + supersede-in-place header
sed -n '1,20p' memory-bench/membench/grading/validity_gate.py      # gold-reproduces / empty-fails invariant
grep -n "POPULATION_PRIMARY\|PREREGISTERED" memory-bench/membench/grading/paired_ci.py memory-bench/membench/grading/safety_gates.py
grep -n "SIDE SIGNAL" memory-bench/membench/grading/graded.py      # judge never a gate
grep -n "re-litigate" docs/architecture-decisions.md               # D16 scope
grep -rn "publication freeze" docs/ | head                          # freeze status (volatile)
bd show mem-0rrf 2>/dev/null | head -5                              # freeze bead (internal orchestration; needs bd)
cd memory-bench && .venv/bin/python -m pytest tests/test_validity_gate.py tests/test_safety_gates.py -q
```
