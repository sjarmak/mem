---
name: mem-grading-and-validity-gates
description: >
  How a memory-bench run becomes a defensible number: the ablation
  score-vs-information curve (the headline), floor_lift / ceiling_gap /
  saturation readouts and InsufficientLadderError, the CSB oracle-soundness
  validity_gate (gold reproduces AND empty fails), run-level safety_gates
  (wrongful_destruction voids; confabulation flags-only pending a frozen
  κ-set), the D10 precision / injected-volume guard, and the scorer stack
  (deterministic trace scorer, dual verifier, LLM judges, merged-diff) with
  which signals are deterministic vs report-only. Load when scoring a grid,
  interpreting curve readouts, adding or changing a gate or scorer, debugging
  a refused/void/quarantined run, or deciding whether a number is reportable.
  NOT for running the harness end-to-end — use mem-eval-harness-run. NOT for
  temporal LOO / leak exclusions — use mem-temporal-loo-and-leak-safety. NOT
  for the arms under test — use mem-competitive-arms. NOT for why the real
  corpus nulled — use mem-failure-archaeology.
---

# mem grading and validity gates

Everything here lives in `memory-bench/membench/grading/` (Python ≥3.12,
`mypy --strict`, tested against deterministic stubs — no Docker, network, or
paid API in CI). This skill teaches how a completed run turns into a number
that survives a skeptic, and which gate refuses, voids, or quarantines it
when it should not.

Verified against the repo on 2026-07-07, branch `main`, HEAD `4e819e1`.

**Standing constraint on every number in this file and every number you
produce:** real-corpus / headline numbers are held under the `mem-0rrf`
publication freeze. Nothing below is publishable; numbers appear only with
their validity caveats. Releasing any headline is a Stephanie decision
(`mem-1fl8`). PROVISIONAL pending Stephanie (discovery Q4): the freeze is
stated here conservatively as covering ALL headline/real-corpus numbers.

## When NOT to use this skill

| You want to…                                                                         | Use instead                                 |
| ------------------------------------------------------------------------------------ | ------------------------------------------- |
| Run bundles through Harbor end-to-end                                                | `mem-eval-harness-run`                      |
| Understand `closedBefore` / sibling exclusions / `leak_guard`                        | `mem-temporal-loo-and-leak-safety`          |
| Add or debug a memory arm (`ours`, `builtin`, mem0, …)                               | `mem-competitive-arms`                      |
| Know why replay/linkage/gh-outcome fixes were already rejected                       | `mem-failure-archaeology`                   |
| The evidence bar itself (paired deltas, CI excludes zero, oracle soundness doctrine) | `mem-research-methodology-and-evidence-bar` |
| Author synthetic tasks that pass the necessity gate                                  | `mem-synthetic-world-generator`             |
| Attack the oracle-validity wall                                                      | `mem-oracle-validity-wall-campaign`         |

## Jargon (defined once)

| Term                         | Meaning here                                                                                                                                                                                                                                                               |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **rung**                     | One point on the information ladder: WHICH memory the agent may access on a fresh run (`none`, `vector-rag`, `ours`, `builtin`, `ours+builtin`, `oracle`). Never an outcome value — the ladder is label-free by construction.                                              |
| **bundle**                   | One held-out task packaged for replay (`membench.schemas.bundle.TaskBundle`): issue text, gold diff, oracle context, LOO exclusions.                                                                                                                                       |
| **oracle soundness**         | The CSB invariant: the bundle's gold diff must reproduce (pass its tests) AND the empty diff must fail. A bundle failing either has a broken oracle and is excluded, never scored.                                                                                         |
| **CSB**                      | CodeScaleBench — the external benchmark whose validity-gate discipline this repo ports.                                                                                                                                                                                    |
| **floor_lift / ceiling_gap** | `ours − none` mean reward / `oracle − ours` mean reward. The two readouts a partial ladder supports.                                                                                                                                                                       |
| **ITT / matched**            | The two delta populations. `itt` (intention-to-treat, pre-registered PRIMARY): every admitted task contributes; a task missing one rung contributes delta 0. `matched` (SECONDARY): only tasks observed on both rungs — mechanism-conditional, shifts as the corpus grows. |
| **void / quarantine / flag** | A **void** run's numbers are discarded outright. A **quarantined** run is win-ineligible but its data is retained. A **flag** marks for review without voiding. Authority is per-gate and earned, never bundled.                                                           |
| **κ-set**                    | A frozen, on-disk judge-calibration set (human labels vs judge verdicts). Clearing FPR ≤ 0.05 AND κ ≥ 0.6 is what promotes confabulation from flag to void. None exists yet (2026-07-07).                                                                                  |
| **ZFC**                      | Zero Framework Cognition: mechanical signal computed in code; semantic judgment delegated to a model; no keyword/threshold scoring of meaning in code.                                                                                                                     |
| **D6 / D10 / D16 / D17 …**   | Numbered rulings in `docs/architecture-decisions.md`. See `mem-decision-ledger-and-architecture-contract`.                                                                                                                                                                 |

## Module map — `memory-bench/membench/grading/`

21 modules (verified 2026-07-07). One home per concept:

| Module               | What it is                                                                                                                                                                  | Deterministic?                         |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `base.py`            | `OutcomeSource` / `Feasibility` — the uniform "can this source grade this task?" contract                                                                                   | yes                                    |
| `ablation.py`        | `AblationSource` + the rung vocabulary (`DEFAULT_RUNGS`, `COMBINATION_RUNGS`, `COMBINATION_BASE_RUNG`)                                                                      | yes                                    |
| `curve.py`           | `build_curve`, `floor_lift`/`ceiling_gap` (+ `_ci` variants), `saturation_point`, `min_useful_combo`, `InsufficientLadderError`                                             | yes (seeded bootstrap)                 |
| `trace_score.py`     | Deterministic half of the per-rung reward: failure-signature recurrence, `path_reached`, `combined_reward`, `RewardRecord`                                                  | yes                                    |
| `judge.py`           | Semantic half: `OssLlmJudge` (local endpoint only; paid hosts refused), `StubJudge`, `Rubric`, `Calibration`                                                                | model (report contract in code)        |
| `graded.py`          | S3 graded diff-quality judge: blinded view, coarse 3-point + evidence, N-round median, divergence flag                                                                      | model; side signal, never a gate       |
| `dual_verifier.py`   | Dual scorer: direct leg (test repro primary, diff-sim fallback) + comprehension leg (tier-weighted F1); `ReproRunner` seam                                                  | yes (test verdict delegated to runner) |
| `probe_direct.py`    | Diff-similarity scoring + `extract_efficiency` (tokens/turns/tool calls)                                                                                                    | yes                                    |
| `merged_diff.py`     | `MergedDiffSource` feasibility (merged PR + commit_sha + rig→repo map)                                                                                                      | yes                                    |
| `validity_gate.py`   | The CSB oracle-soundness gate (see below)                                                                                                                                   | yes                                    |
| `safety_gates.py`    | Run-level `wrongful_destruction` + `confabulation` block (see below)                                                                                                        | destruction yes; confabulation judged  |
| `coverage.py`        | **Oracle-SOURCE coverage probe**: which source can grade each record; precedence merged-diff → ablation. NOT the D10 precision guard (naming trap, see below)               | yes                                    |
| `paired_ci.py`       | Shared paired-delta + seeded percentile-bootstrap CI; `itt`/`matched` populations                                                                                           | yes                                    |
| `base_rate.py`       | C1.3 go/no-go: does the held-out error even recur at the `none` rung? Protects the headline from a vacuous curve                                                            | yes                                    |
| `mechanism_gate.py`  | Pre-run "mechanism fires" smoke (mem-xe2p): refuse a grid whose mechanism never engages (the 69-leg null lesson)                                                            | yes                                    |
| `provenance_gate.py` | M7 reversibility gate: every consolidated item's cited `source_trace_id` must dereference to a live row                                                                     | yes                                    |
| `retrieval_leg.py`   | M1/M2 white-box retrieval-correctness leg; must declare its scoring target; `None` = not measured                                                                           | yes                                    |
| `leak_guard.py`      | Outcome-label leak guard on task construction (high-entropy identifiers in agent-readable text) — owned by `mem-temporal-loo-and-leak-safety`                               | yes                                    |
| `variance.py`        | Within-task repeat variance + paired-MDE noise floor (mem-eacq)                                                                                                             | yes                                    |
| `ftp_difficulty.py`  | Gold-file-count difficulty banding for ftp anchors (mem-on3f)                                                                                                               | yes                                    |
| `__init__.py`        | Public surface. Note: `dual_verifier` and `graded` are NOT re-exported (import-chain reasons) — import them as `membench.grading.dual_verifier` / `membench.grading.graded` | —                                      |

## 1. The headline: the ablation score-vs-information curve

### Why this is the headline

The real corpus is direct-to-main, so a merged-PR/CI outcome oracle is
inapplicable by construction (D17/D18 — do not re-litigate; see
`mem-failure-archaeology`). The ablation family needs **no ground-truth
label**: the same agent replays the same task at each rung of an information
ladder, and the curve of reward vs information is read off. The agent is its
own control, so the design is env- and label-independent and fully paired.

### The ladder (`ablation.py`)

```python
DEFAULT_RUNGS = ("none", "vector-rag", "ours", "builtin", "ours+builtin", "oracle")
COMBINATION_RUNGS = frozenset({"builtin", "ours+builtin"})   # the combination axis
COMBINATION_BASE_RUNG = "ours"                               # what combination layers onto
```

Runnability is a separate concept (`harbor/memory_inject.py`):
`RUNNABLE_RUNGS = ("none", "vector-rag", "ours", "oracle")`;
`DEFERRED_RUNGS = ("builtin", "ours+builtin")` are owned by mem-whi — the
grid driver SKIPS them without error (`DeferredRungError` is caught, the
rung is simply not scored).

### Per-run reward (`trace_score.py` + `judge.py`)

One `RewardRecord` per `(work_id, rung, repeat_idx)`. Its reward composes up
to two terms (`combined_reward`, default `det_weight=0.5`):

1. **Deterministic term** — did the held-out task's known failure class
   recur in the fresh run?
   - Recurrence keys on the **relaxed signature** `tool:basename:error_class`
     (line dropped, path basenamed) so a shifted line is still the same
     failure. The full `tool:file:line:error_class` signature is kept
     verbatim for `exact_recurrence` reporting only.
   - **`path_reached` gate**: a run that never touched the file the held
     error lives in scores `None` (not applicable), NOT a free 1.0. This
     kills the "did nothing → trivially avoided the error" confound; the
     no-op then falls to the judge term.
   - Fresh-run errors MUST come from the canonical TS extractor
     (`src/parse/error-extractors.ts`) so signatures are byte-identical to
     the held-out side. The extractor is injected — CI needs no TS build.
2. **`rubric_score`** — the semantic-completion term from an LLM judge, in
   [0, 1], `None` until the judge runs. `run_grid` always returns it as
   `None`; the judge is a separate post-harvest step.

Composition rules: both present → weighted; one present → that one; neither
→ 0.0. A judge-only reward is legitimate (a genuine different-path solve).

### Building the curve (`curve.py`, `build_curve`)

- **Repeats collapse within task first** (k repeats are correlated, not
  independent); each task contributes one mean to its rung's sample.
- Per-rung mean carries a **Student-t CI** clamped to [0, 1] (exact
  integer-df CDF, scipy-free; the old normal-z interval was
  anti-conservative at held-out-set sizes).
- An explicit `rungs=` argument must be a duplicate-free subsequence of
  `DEFAULT_RUNGS` in canonical order — anything else raises (a reordered or
  duplicated ladder flips the order-sensitive readouts into artifacts).
- Empty input, or a `rungs=` filter matching nothing, raises. No silent
  empty curve.

### The readouts — and what refuses

| Readout                                                   | Needs                                                                                                | Behavior when unmet                                                                                                                                      |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `floor_lift` (`ours − none`)                              | both rungs present                                                                                   | `None` — surfaced, never guessed                                                                                                                         |
| `ceiling_gap` (`oracle − ours`)                           | both rungs present                                                                                   | `None`                                                                                                                                                   |
| `floor_lift_ci` / `ceiling_gap_ci` / `paired_delta(a, b)` | per-task data for both rungs                                                                         | `None`; otherwise a `PairedDeltaCI` (paired median delta, seeded percentile bootstrap, `n_resamples=5000`, labeled population, `n_imputed_zero` counted) |
| `saturation_point` / `min_useful_combo`                   | the FULL ladder: ≥4 rungs AND a combination rung (`builtin`/`ours+builtin`) AND the `ours` base rung | raises **`InsufficientLadderError`**                                                                                                                     |

The full-ladder gate is an **axis gate, not a raw count** (mem-do8r.2 /
Codex F1): a `(none, vector-rag, builtin, oracle)` ladder REFUSES despite
having four rungs, because saturation / min-useful-combo measure the
COMBINATION axis, and a recall sweep is a different axis — reading a
combination number over a recall ladder is a category error. Tolerance for
"stopped improving": `DEFAULT_SATURATION_TOL = 0.05` on the [0, 1] reward
scale (a documented calibrated-threshold ZFC exception), with a 1e-9
epsilon against IEEE-754 rounding.

**If you hit `InsufficientLadderError`: this is the gate working.** Do not
lower `MIN_LADDER_FOR_SATURATION`, do not synthesize rungs. Report
`floor_lift`/`ceiling_gap` (the readouts your ladder supports) or run the
missing rungs (builtin rungs are gated on mem-whi).

### Never pool means

Both delta populations are explicit (`itt` primary, `matched` secondary)
and every reported delta rides a bootstrap CI. A pooled mean can hide a
real per-task regression. A rung ordering is a finding only when the
paired-delta CI excludes zero. This is the standing gate instruction
(mem-75t.7.6); the evidence bar lives in
`mem-research-methodology-and-evidence-bar`.

### The grid driver (`harbor/grid.py`, `run_grid`)

```
record → per-rung task dirs → inject rung memory → agent run per rung
       → harvest RunTrace → score_run → RewardRecord(work_id, rung, repeat_idx)
```

- Default rungs are the live 3-rung subset `("none", "ours", "oracle")`.
- `validate_rungs` fails on a typo'd rung BEFORE any task dir or agent
  spend.
- Two caller preconditions `run_grid` does NOT enforce: (a) `ours_payloads`
  / `vector_payloads` must already be D6 LOO-bounded by the caller —
  `run_grid` injects verbatim; (b) `rubric_score` is always `None` on
  return — run the judge yourself before `combined_reward` if you want the
  semantic term.
- `StubRunner` (deterministic, no Docker/network) drives the whole pipeline
  in tests; `HarborRunner` shells `harbor run` on the Claude OAuth
  subscription (not a paid API, D16).

Execution scripts (in `memory-bench/scripts/`, argparse `--help` on each):
`run_grid.py` (none/ours/oracle grid), `run_grid_3arm_graded.py` (the
graded 3-arm clean-room grid), `build_headline_report.py` (aggregates
`.mem/grid/summary.json` into the headline doc). Running them is
`mem-eval-harness-run` territory.

### Where the headline actually stands (2026-07-07, all under the mem-0rrf freeze)

- `docs/mem-apg.4-ablation-headline.md`: n=9 bundles, rungs `none < oracle`
  only. One oracle run (`zhy00`) WebFetched the PR it was reproducing and
  is invalid (`docs/audits/2026-07-03-headline-network-fetch-audit.md`);
  the clean read is n=8 and the curve is flat. Never cite the n=9 table
  without that footnote. Real replay runs now default to allowlist
  networking.
- `docs/mem-apg.9-graded-3arm-grid.md`: the graded 3-arm grid on the
  native pool is a NULL — the validity gate admitted only 2 of 5
  candidates. N is bound by oracle/replay fidelity, not by method
  (settled; see `mem-failure-archaeology` and
  `mem-oracle-validity-wall-campaign`).

## 2. The oracle-soundness `validity_gate` (`validity_gate.py`)

Runs BEFORE any candidate is scored, per bundle:

1. Apply the **gold diff as the candidate** → must reproduce
   (`repro_pass=True`; where per-file counts ran, `test_ratio == 1.0`).
2. Apply the **empty diff** → must fail (`repro_pass=False`;
   `test_ratio == 0.0`).

A bundle failing either has a broken oracle (a non-reproducing gold diff,
or a gold test that passes without the fix) and is **excluded from the
graded comparison, never silently scored** — an unsound oracle drags every
arm toward noise. `ValidityResult.reason` names the first breach;
`ValidityResult.valid` is the conjunction. Test ratios are `None` when the
runner never reached the test loop — a typed absence, not a 0.0.

The gate runs the SAME `ReproRunner` the direct scoring leg uses (the test
verdict is delegated; the module only interprets two outcomes — ZFC).

Grid reports carry the gate as a `validity_gates` block
(`harbor/bundle_grid.py::_validity_block`): `checked` / `valid` /
`invalid` (work_ids) / `evidence` (full readouts). **`checked == 0` means
no gate ran (no runner wired) — distinct from all-valid.** Treat a
`checked == 0` report as ungated, not clean.

Related but distinct admission machinery: `tests/test_admit_validity_gate.py`
covers gate-at-admission wiring; the two-stage pre-admission funnel
(mem-1eph scope guard + this gate) is chronicled in `mem-failure-archaeology`.

## 3. Run-level `safety_gates` (`safety_gates.py`)

Two frontier failure modes a consolidation arm can hide. They ride a
summary block ALONGSIDE the metrics — a test enforces that they are never
averaged into `metrics()` (the mem-75t.7.6 "laundering" failure this
structure exists to prevent). Voiding authority is per-gate and earned:

| Gate                   | Mechanism                                                                                                                                                               | Authority today (2026-07-07)                                                                                                                                                                                                                                                                      |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wrongful_destruction` | Deterministic synthetic-disposition oracle, no judge: a KEEP/HELD record absent from the final store with no re-derivable tombstone. `record_ids` names every casualty. | `count >= 1` **VOIDS the run**, day 1. Tombstoned-but-recoverable destruction is a correctness finding, not a void.                                                                                                                                                                               |
| `confabulation`        | Entailment-judged, so it cannot void on an uncalibrated judge.                                                                                                          | **FLAG-and-QUARANTINE only** (win-ineligible, data retained). Promotes to void ONLY when a frozen κ-calibration set on disk clears the pre-registered bar. **No κ-set exists yet ⇒ `confabulation_authority` returns `"flag"` by construction** (the B-2 hard pin, enforced in code and by test). |

The bar is pre-registered IN CODE (it cannot be set after seeing the
rates): `PREREGISTERED_FPR_MAX = 0.05`, `PREREGISTERED_KAPPA_MIN = 0.6` —
FPR is the operative criterion, κ the necessary-but-insufficient
companion. Both constants ride on every `ConfabulationGate` as provenance.
`compare/relevance_calibration.py` reuses the SAME constants for its judge
calibration — do not fork a second bar.

`SafetyGates.run_void` trips on wrongful destruction or on calibrated
confabulation; `win_eligible` is false whenever void OR quarantined.
Quality rides the mean; safety rides this block. Never "simplify" the
counters into an averaged safety_score.

`report/retention_schedule.py` reuses `compute_safety_gates` (no new void
authority may be minted elsewhere — mem-frontier M7).

## 4. The D10 precision / injected-volume guard

**The gaming attack:** returning the whole store gets recall 1.0 and can
pass answer-quality evals. Decision 10 therefore makes injected-context
volume + retrieval precision a REQUIRED guard on every lift run, not an
optional side metric.

Where it lives (a naming trap — three different things):

| Thing                                                              | Location                                                                                                                                                                                                                                               | Role                                |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------- |
| Retrieval precision/recall/MRR/nDCG (+ distractor and stale rates) | `metrics/scorers.py::score_retrieval` → `schemas/metrics.py::RetrievalMetrics` (spec §12.3)                                                                                                                                                            | The precision half of the D10 guard |
| Injected-context volume                                            | `injected_context_chars` — computed in `replay.py` / `compare/retrieval_compare.py` (`sum(len(v) for v in result.payloads.values())`), surfaced as an OTel span attr (`telemetry/otel_spans.py`) and as `token_budget_chars` in `report/arm_vector.py` | The volume half of the D10 guard    |
| `grading/coverage.py`                                              | The oracle-SOURCE coverage probe (which OutcomeSource can grade each record; precedence merged-diff → ablation; unmapped rigs surface as a loud CONFIG-GAP, never reclassified)                                                                        | NOT the D10 guard, despite the name |

`grading/retrieval_leg.py` (M1/M2) is the white-box retrieval-correctness
leg for the headline grid: it reuses `score_retrieval` against the
gold-relevant set (declared sources minus `loo_excluded_work_ids`) and is
reported SEPARATELY from answer-correctness — never folded into a
composite. Rules that must survive any refactor:

- A retrieval-bearing result MUST declare its scoring target
  (`raw` / `source` / `canonical`).
- An empty relevant set scores **`None` — "not measured" — never a
  fabricated 0.0**.

## 5. The scorer stack — deterministic vs report-only

| Scorer                              | Module                                              | Signal                                                                                                                                         | Status                                                                                                                                                                     |
| ----------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Trace-error recurrence              | `trace_score.py`                                    | deterministic term of the per-rung reward                                                                                                      | **Deterministic; in the reward**                                                                                                                                           |
| Gold-test reproduction (direct leg) | `dual_verifier.py::score_direct`                    | binary anchor; `test_ratio` = S1 per-test-file partial credit underneath it                                                                    | **Deterministic anchor — the untouchable pass/fail floor**                                                                                                                 |
| Diff similarity                     | `probe_direct.py`, surfaced as `DualScore.diff_sim` | S2, always-on bounded structural similarity                                                                                                    | Deterministic **side signal, never a gate** (known equivalent-fix bias, carried in the report)                                                                             |
| Comprehension leg                   | `dual_verifier.py::score_artifact`                  | F1 of identified files vs curated oracle; tier-weighted recall (required 2.0 / supplementary 1.0 / context 0.5; untiered defaults to required) | Deterministic; composed per `scoring_policy`                                                                                                                               |
| Completion judge                    | `judge.py::OssLlmJudge`                             | `rubric_score`, the semantic half of the per-rung reward                                                                                       | Model; enters `combined_reward` as a reward term. Local OSS endpoint ONLY — paid hosts (`openai.com`, `anthropic.com` and subdomains) are refused at construction (D4/D16) |
| Graded S3 diff-quality judge        | `graded.py`                                         | rubric verdict on candidate diff vs issue + gold diff                                                                                          | Model; **side signal on the score vector, never a gate, never folded into one weighted number** (mem-r5y locked decision 3)                                                |
| Merged-diff oracle                  | `merged_diff.py`                                    | the locked headline oracle where constructible                                                                                                 | Deterministic feasibility; on this corpus effectively inapplicable (D17/D18)                                                                                               |

Dual-scorer contract (`dual_verifier.score_run`): both legs ALWAYS run;
a missing leg input degrades that leg only (no artifact → artifact 0.0;
no oracle → artifact `None`, unscoreable, never a blaming 0.0). Direct-leg
fallback to diff-sim records `repro_error` — WHY it fell back is never
hidden. `PASS_THRESHOLD = 0.5` is shared with the grid pass rule.
`compose_automated_score` policies: `direct` (default), `min`, `mean`,
`weighted` (weights must sum to 1.0). Immutable: the input bundle is never
mutated; a new bundle comes back with `verification` populated.

Graded-judge anti-gaming stack (`graded.py`), each control load-bearing:

- **Blinded view** (`build_graded_view`): exactly (issue text, candidate
  diff, gold diff). No arm/condition label, no memory payload, no token
  counts, no harness preamble.
- **Coarse 3-point scale + evidence**: per-criterion scores in
  {0, 0.5, 1.0}; evidence-free criteria score 0. Off-grid in-range scores
  are snapped (`_snap_to_coarse`); out-of-range raises. The weighted sum is
  computed in code (ZFC).
- **N-round median** (`DEFAULT_JUDGE_ROUNDS = 3`): the variance control
  standing in for temperature 0, which the `claude -p` seam does not expose.
- **Divergence flag** (`GRADED_DIVERGENCE_THRESHOLD = 0.3`): judge vs
  mechanical reference (diff-sim) disagreement flags the run for review.
- **Pinned model** (`DEFAULT_GRADED_JUDGE_MODEL = "claude-sonnet-4-6"`,
  env-overridable) recorded on every judgment;
  `GRADED_PROMPT_VERSION = "v1"` invalidates caches on bump.
- **Isolation is mandatory** (`judge_config.py`, mem-9ld4): every headless
  `claude -p` callsite goes through `run_isolated_claude` with an isolated
  config dir. A judge inheriting the host `CLAUDE_CONFIG_DIR` gets hijacked
  into code-review mode by reviewable diffs — this contaminated every
  pre-isolation graded number (mem-eacq; Stephanie ruled ISOLATE +
  RE-SCORE). **Never cite a pre-isolation judge number; never wire a new
  `claude -p` callsite outside this seam.**

Judge doctrine, stated precisely: the judge is a reported/graded SIGNAL —
it may contribute a reward term (`rubric_score`) or a side signal (S3) but
it is never a pass/fail GATE and it never voids a run (the only path to
judge-earned void authority is the frozen κ-set in §3). Calibration for
the graded instrument is out-of-band (Opus + Codex review, replacing a
hand-labeled κ gate for that instrument); the judge never grades against
itself.

## 6. Supporting pre-run gates (spend protection)

Run these BEFORE burning agent legs; each has refused a real class of
wasted grid:

- **`base_rate.py`** (C1.3): if the held-out `trace_error` rarely recurs
  at the `none` rung, the curve cannot show lift — go/no-go before the
  grid.
- **`mechanism_gate.py`** (mem-xe2p): the mechanism under test must FIRE
  (covariate > 0 on ≥1 anchor) in a cheap smoke. The 69-leg Option-A grid
  (mem-lvp.24) nulled on a mechanism that never engaged once; this gate
  would have refused it for the cost of one scan.
- **`provenance_gate.py`** (M7): every consolidated item's cited
  `source_trace_id` must dereference to a live row — re-derivability is
  proven, not claimed.
- **`variance.py`** (mem-eacq): within-task repeat spread + the paired-MDE
  noise floor — know whether your N can even detect the effect you claim.

## 7. The recall ladder — described, NOT canonized

> PROVISIONAL pending Stephanie (discovery Q3): the shipped headline is the
> ablation curve above. The recall ladder is a branch-ready PROPOSED design
> awaiting Stephanie's locks. Do not build on it as the going-forward eval,
> and do not present its design choices as settled.

`docs/mem-do8r-recall-ladder-adr.md` (DESIGN-DRAFT, 2026-07-03) adopts the
Sakana recall ladder: fix the model, vary ONLY the recall policy across
`none` / `vector-rag` / ranked-ledger (`ours`) / `oracle`. Its "Stephanie's
locks" section carries **6 numbered accept/redirect items** (verified
2026-07-07; the campaign discovery report describes them as 7 — count them
yourself in the ADR): rung-2 arm choice, the distill pass to make the
ledger rung non-empty, the two-track task pool, the lift definition
(paired pass-rate + true-cost co-primary), 3-vs-5 repeats, and the
headline metric wording.

What HAS already landed on main (resolved, safe to rely on):

- The `vector-rag` rung sits in `DEFAULT_RUNGS` between `none` and `ours`
  and in `RUNNABLE_RUNGS` (mem-do8r.2).
- The combination-axis guard in `curve._require_full_ladder` exists
  precisely so a future recall sweep cannot mis-read combination
  readouts (§1).
- `scripts/recall_ladder_smoke.py`: wires and scores all four rungs on a
  tiny fixture pool with a `StubRunner` (no Docker/network/paid API), then
  **HALTs** — a plumbing proof, not an eval.

Nothing else from the ADR is implemented. Any real ladder RUN is a run
under the `mem-0rrf` freeze and a Stephanie call.

## 8. Runbook: verify the gate stack locally

```bash
cd /home/ds/projects/mem/memory-bench
# use the project venv (system python has no membench):
.venv/bin/python -m pytest tests/test_curve.py tests/test_validity_gate.py \
  tests/test_safety_gates.py tests/test_trace_score.py \
  tests/test_grading_coverage.py tests/test_dual_verifier.py \
  tests/test_graded.py tests/test_judge.py -q
# 198 tests collected as of 2026-07-07 @ 4e819e1
```

From a fresh clone: `pip install -e ".[dev]"` first (see
`mem-build-test-env`). All grading tests run on stubs — no Docker, no
network, no model.

Smoke a curve in-process (deterministic, no spend):

```python
from membench.grading import RewardComponents, RewardRecord, build_curve
from membench.grading.curve import InsufficientLadderError, saturation_point

recs = [
    RewardRecord("t1", "none", 0, RewardComponents(True, False)),
    RewardRecord("t1", "ours", 0, RewardComponents(True, True)),
    RewardRecord("t1", "oracle", 0, RewardComponents(True, True)),
]
curve = build_curve(recs)
print(curve.floor_lift, curve.ceiling_gap)   # 1.0 0.0
try:
    saturation_point(curve)                  # 3 rungs, no combination axis
except InsufficientLadderError as e:
    print("refused, as designed:", e)
```

Diagnostic helper shipped with this skill (read-only):

```bash
.claude/skills/mem-grading-and-validity-gates/scripts/check_grading_stack.sh
```

It verifies the module inventory, the pinned constants (rungs, tolerance,
pre-registered FPR/κ bar, pass threshold, divergence threshold), and that
the grading tests still collect. If it reports drift, re-read the changed
module before trusting this skill's numbers.

## Provenance and maintenance

Authored 2026-07-07 against `/home/ds/projects/mem`, branch **`main`**,
HEAD **`4e819e1`**. Every path, constant, behavior, and doc claim above was
verified read-only against that checkout. Volatile facts (rung sets,
deferred rungs, the no-κ-set status, freeze status, test counts, the
recall-ladder lock list) are date-stamped 2026-07-07.

Re-verify before trusting after drift:

```bash
git -C /home/ds/projects/mem log --oneline -1                      # has HEAD moved past 4e819e1?
ls /home/ds/projects/mem/memory-bench/membench/grading/            # module inventory (21 files)
grep -n "DEFAULT_RUNGS" /home/ds/projects/mem/memory-bench/membench/grading/ablation.py
grep -n "RUNNABLE_RUNGS\|DEFERRED_RUNGS" /home/ds/projects/mem/memory-bench/membench/harbor/memory_inject.py
grep -n "PREREGISTERED_FPR_MAX\|PREREGISTERED_KAPPA_MIN" /home/ds/projects/mem/memory-bench/membench/grading/safety_gates.py
grep -n "DEFAULT_SATURATION_TOL\|MIN_LADDER_FOR_SATURATION" /home/ds/projects/mem/memory-bench/membench/grading/curve.py
grep -n "PASS_THRESHOLD" /home/ds/projects/mem/memory-bench/membench/grading/dual_verifier.py
grep -n "GRADED_DIVERGENCE_THRESHOLD\|DEFAULT_GRADED_JUDGE_MODEL" /home/ds/projects/mem/memory-bench/membench/grading/graded.py
grep -n "Status" /home/ds/projects/mem/docs/mem-do8r-recall-ladder-adr.md  # ladder still DESIGN-DRAFT?
grep -rn "0rrf" /home/ds/projects/mem/docs/ | head -3               # freeze still referenced?
```

If the recall-ladder locks land, §7 must be rewritten (and possibly §1's
headline framing). If a frozen κ-set appears on disk, §3's
"flag by construction" claim expires. If `checked/valid` semantics in
`_validity_block` change, §2's `checked == 0` warning must be re-verified.
