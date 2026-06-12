# mem-g6a — Graded quality metric design (EB/CSB-informed)

Design only — no wiring lands until Stephanie signs off. Scoped 2026-06-12 against
the mem-apg.3 grid results and the grading code in EnterpriseBench and
CodeScaleBench. The recommendation is at the bottom; everything above it is the
evidence and the options.

## 1. The problem the binary metric cannot see

The quality guard in the mem-apg.3 grid is gold-test reproduction: apply the
agent's harvested diff to a fresh checkout, apply the gold test files on top, run
them, exit code 0 = pass (`membench/harbor/repro_live.py:99-193`, scored to
1.0/0.0 in `membench/grading/dual_verifier.py:219`). The grid outcome
(`docs/mem-apg.3-grid.md`): **repro pass none 1/9, oracle 1/9 — the same single
bundle (e9y0d), Δ = 0 on every pair.** The instrument is honest but has almost no
dynamic range between rungs on this pool: 8 of 9 bundles score 0–0 and contribute
nothing to the quality axis, so the headline degrades to "quality flat, efficiency
clear" no matter what the memory system does in the failing-but-improving region.

That failing region is exactly where a memory intervention should first show up:
an agent that gets *closer* — right files, right shape, one missed edge case —
scores identically to an agent that did nothing. Binary pass/fail is the right
*anchor* (ungameable, SWE-bench-shaped) and the wrong *only* signal.

Constraints fixed by the bead:

1. Gold-test repro stays as the floor/anchor — never replaced, never diluted.
2. New signals are additive layers, each independently reported.
3. ZFC: semantic judgment is delegated to a model; everything in membench code is
   mechanical arithmetic over model or test outputs.

## 2. What membench already has (reuse surface)

| Piece | Where | State |
|---|---|---|
| Binary gold-test repro | `repro_live.py:99-193` (`LiveReproRunner.run`), scored in `dual_verifier.py:219` (`score_direct`) | live, the anchor |
| Diff similarity (file-F1 × hunk-Jaccard) | `probe_direct.py:123` (`score_probe_direct`) | live, but only as *fallback* when repro errors |
| Artifact comprehension (tier-weighted oracle F1) | `dual_verifier.py:120` (`score_artifact`) | live, separate leg |
| Composition policy (`direct`/`min`/`mean`/`weighted`) | `dual_verifier.py:304-326` | live, per-bundle `scoring_policy` |
| Judge protocol + rubric + self-hosted impl | `judge.py:86` (`Rubric`), `:165` (`StubJudge`), `:207` (`OssLlmJudge`) | built (mem-apg.3b), **not wired into grid scoring** |
| Deterministic+rubric blend | `trace_score.py` (`combined_reward`, det_weight=0.5) | built for trace scoring, pattern reusable |
| Per-bundle paired deltas + gap stats | `probe_gate.py:599` (`paired_deltas`), `:657` (`metric_gap_stats`), `bundle_grid.py:204/:362` (`summarize_grid`, `summarize_grid_3arm`) | live; any new metric added to a result dict is paired automatically |

Two consequences. First, most of the graded metric is *promotion and wiring*, not
new machinery. Second, the reporting layer already does the right thing (per-bundle
paired deltas, never pooled means alone — the mem-75t.7.6 lesson), so a new signal
only has to land in the per-run result dict to be reported correctly.

## 3. Prior art: what EB and CSB actually did

### EnterpriseBench (`/home/ds/projects/EnterpriseBench`)

- **Partial credit is the default.** Every task has 2–5 weighted checkpoints
  (weights sum to 1.0); each verifier emits a continuous score in [0,1]; the task
  score is the weighted sum (`lib/eb_verify/scoring.py:44-52`, `:194-202`). The
  documented driver (`docs/LEGACY_CSB_ASSESSMENT.md`): single monolithic binary
  checkpoints on a 90.5%-hard pool produced all-zero/all-one outcomes with no
  stratification — the exact pathology our 8-of-9 zero-zero pairs reproduce.
- **The LLM judge is a ceiling, not a bonus:** `final = min(grep_score,
  judge_score)` (`lib/eb_verify/runner.py:340-347`). The judge can only take away
  points from the mechanical signal, never add — gaming the judge gains nothing.
- **Judge anti-gaming** (`lib/eb_verify/judge/prompts.py:9-55`): coarse 3-point
  scale (0/0.5/1.0); answers must cite code-specific evidence (paths, symbol
  names) or score low; structured JSON with quoted evidence + confidence;
  temperature 0; a different/cheaper model family than the agent
  (`judge/engine.py:20-34`); grep-vs-judge divergence > 0.3 flagged for manual
  review (`scripts/analysis/rescore_with_judge.py:156-166`).
- **Distribution stats kept visible:** min/max/stdev of checkpoint scores ride
  along in diagnostics so two equal means with different shapes stay
  distinguishable (`scoring.py:177-191`).

### CodeScaleBench (`/home/ds/projects/CodeScaleBench`)

- **Seven scoring families** (`docs/SCORING_SEMANTICS.md:1-160`); the relevant
  ones here: *test-ratio* (fraction of test cases passing), *similarity*
  (file/line recall-precision blends, e.g. PyTorch `0.35·file_recall +
  0.45·line_recall + 0.20·line_precision`), and *hybrid* (`0.6·verifier +
  0.4·rubric`, `SCORING_SEMANTICS.md:269-297`).
- **Judge done carefully** (`scripts/csb_metrics/judge/`): cross-model judge
  (GPT-4o judging Claude agents) to avoid same-family bias; preamble stripping so
  the judge never sees harness/condition context (`run_judge.py:352-408`);
  optional multi-round majority vote with median tie-break
  (`judge/engine.py:252-299`); **kappa calibration as a gate** — Cohen's/Fleiss'
  kappa computed against reference labels, κ < 0.4 = uncalibrated, do not trust
  (`judge/agreement.py`).
- **Validity gate before any scoring:** the gold answer must score 1.0 and the
  empty answer 0.0 on every task, or the oracle itself is broken
  (`docs/ORG_CALIBRATION.md:75-98`).
- **Documented limitation we inherit:** diff similarity penalizes
  functionally-equivalent but structurally-different fixes
  (`SCORING_SEMANTICS.md:69`) — fine as a bounded sub-signal, wrong as a gate.

## 4. Candidate signals

**S0 — gold-test repro (anchor, unchanged).** Binary, ungameable, already live.
Everything below exists to add resolution *underneath* it, in the fail region.

**S1 — test-subset partial credit (mechanical).** Today
`LiveReproRunner.run` passes all gold test paths to one runner invocation per
workspace and gates on exit code, short-circuiting on first workspace failure
(`repro_live.py:181-190`). Graded variant: invoke per gold-test *file* (no
short-circuit), score = passed_files / total_files.
- Pros: same trust class as the anchor (real tests, real checkout); zero new
  semantics; CSB's test-ratio family.
- Cons: resolution capped by test-file count — bundles whose gold diff carries one
  test file gain nothing (granularity 1–50 in CSB's experience); ~k× test runtime
  (mitigated by the existing worktree cache, `repro_live.py:197`); per-test-*case*
  resolution would need per-workspace JSON reporters (vitest `--reporter=json`,
  pytest `--json-report`) — real but rig-specific plumbing.
- Verdict: **include.** Cheapest honest resolution gain; per-case parsing deferred
  until per-file proves too coarse on real data.

**S2 — bounded diff similarity (mechanical, already built).** Promote
`score_probe_direct` (`probe_direct.py:123`) from error-fallback to
always-computed side signal on every run.
- Pros: free (the harvested candidate diff and gold diff are already in hand);
  continuous; was the discriminative instrument in the 7.6 gate run.
- Cons: the CSB-documented equivalent-fix penalty; partially gameable by
  surface-imitating the gold diff (an agent that copies file names scores file-F1
  without correctness).
- Verdict: **include as diagnostic with a small bounded weight, never a gate.**

**S3 — guarded LLM judge (semantic).** Rubric-scored judgment of the harvested
diff against the bundle's issue text and gold diff. All semantic judgment lives in
the model (ZFC); membench code only validates the JSON shape and does arithmetic.
Controls, composed from EB + CSB:

| Control | Source |
|---|---|
| Judge sees only: issue text, candidate diff, gold diff. No condition/arm label, no memory payload, no harness preamble, no token counts. | CSB preamble-strip + blinding |
| Coarse per-criterion scale (0 / 0.5 / 1.0), structured JSON with quoted evidence | EB 3-point + evidence requirement |
| Temperature 0; pinned judge model + version recorded in the result | EB/CSB |
| 3-round majority vote per criterion, median tie-break | CSB `judge/engine.py:252-299` |
| Mechanical-vs-judge divergence > 0.3 flagged in the summary | EB rescore comparator |
| κ-calibration gate vs hand labels before the judge score enters any headline (κ ≥ 0.4 on a labeled slice of existing grid transcripts; we have 20+ cached runs to label) | CSB `judge/agreement.py` |
| Judge contributes via `min()` ceiling or a bounded additive term — never the sole source of a point | EB `runner.py:340-347` |

Judge backend: `OssLlmJudge` (`judge.py:207`, self-hosted OpenAI-compatible
endpoint) is the D4/D16-compliant default. A Claude-in-Harbor OAuth run is a
cost-free alternative backend but is same-family with the agent under test —
EB's bias warning applies; if used, the κ-calibration gate is mandatory, not
advisory. **Judge model choice is a sign-off item.**

**S4 — artifact comprehension F1 (`score_artifact`).** Already reported. Stays a
*separate axis* (comprehension, not solution quality); not folded into the
composite.

Signals considered and rejected:
- **Lint/build pass as a quality term** — measures the repo's toolchain, not the
  task; gold diffs already presuppose a building tree.
- **Transcript-derived "effort" signals** (turns, tool mix) in the quality
  composite — they are the *efficiency* axis; mixing them into quality would let
  a cheaper failure outscore a costlier near-pass.
- **Per-criterion learned weights** — no labeled data to fit them; revisit after
  the calibration slice exists.

## 5. Composite options

**Option A — anchored weighted sum.**
`Q = 1.0 if repro_pass else λ·(w1·test_ratio + w2·diff_sim + w3·judge)` with
λ ≈ 0.8 capping the fail region below any true pass.
*Pros:* one headline number; monotone in every signal; pass strictly dominates.
*Cons:* w1/w2/w3 are unfounded today — picking them before calibration data
exists is weight-gaming ourselves; ZFC-adjacent smell (hardcoded thresholds for a
semantic blend).

**Option B — EB-style min-gating.**
`Q = min(mechanical, judge)` where mechanical = anchored blend of S1+S2.
*Pros:* judge strictly anti-gaming (can only subtract).
*Cons:* discards the judge's upside in exactly our common case — when mechanical
signals are near 0 but the diff is semantically close, `min` stays ~0 and the
flatness problem survives. Built for EB's "verify claimed evidence" shape, not
ours.

**Option C — score vector, no composite (yet).**
Report `(repro_pass, test_ratio, diff_sim, judge, artifact_f1)` per run; the
existing pairing machinery (`paired_deltas` omits absent metrics, never imputes)
yields per-signal paired deltas per bundle; bundles are *ordered* lexicographically
(repro pass first, then test_ratio, then the bounded graded tail) when a ranking is
needed.
*Pros:* most honest; no invented weights; matches the house reporting doctrine
(per-bundle deltas, pooled means distrusted); every signal's calibration is
visible separately.
*Cons:* no single headline number; lexicographic ordering can over-trust
test_ratio granularity on 1-test-file bundles.

### Recommended: C now, A later — staged.

1. **Phase 1 (next grid run):** compute the vector. S1 per-test-file ratio, S2
   always-on diff-sim, S4 unchanged. S3 runs but is **report-only** (excluded from
   any ordering) until calibrated. Headline remains anchored on repro; the new
   resolution shows up as per-signal paired deltas in the fail region.
2. **Phase 2 (after κ-gate passes):** hand-label ~15–20 cached grid runs
   (pass/near/far), compute judge κ; if κ ≥ 0.4, admit the judge into the vector
   proper; flag divergence > 0.3 per run.
3. **Phase 3 (only if a single number is demanded for mem-apg.4 aggregation):**
   fit Option A's weights against the Phase-2 labels and freeze them with
   provenance (weights + label set + κ recorded in the summary). Until then the
   composite simply does not exist — absence is more honest than an arbitrary one.

Validity gates precede all phases, per CSB: per bundle, the gold diff itself must
score `repro_pass=True, test_ratio=1.0` and the empty diff
`repro_pass=False, test_ratio=0.0`, else the bundle's oracle is broken and it is
excluded (this also catches the km0wj-style null-repro bundles mechanically).

## 6. ZFC boundary

- Mechanical, in membench code: test execution and ratios (S1), diff F1/Jaccard
  (S2), oracle F1 (S4), JSON-shape validation of judge output, majority-vote and
  κ arithmetic, pairing/aggregation. All deterministic math over artifacts.
- Semantic, delegated to a model: every quality judgment in S3 (the rubric
  criteria themselves). No keyword/regex semantic detection anywhere in the layer.
- Documented exception (per the ZFC carve-outs): diff similarity is a *calibrated
  mechanical comparison*, not a semantic judgment — it is bounded, never gating,
  and its known equivalent-fix bias is carried in the report text.

## 7. Integration sketch (for sizing, not for building now)

- `ReproOutcome` gains `tests_passed: int, tests_total: int` (per-file loop in
  `LiveReproRunner.run`, no short-circuit) → `DirectScore` carries `test_ratio`.
- `score_run` (`dual_verifier.py:320`) computes S2 unconditionally into a
  `diff_sim` field (today's fallback path keeps its role for repro *errors*).
- New `membench/grading/graded.py`: judge-call orchestration (blinding envelope,
  vote loop, divergence flag) reusing `judge.py` as-is; result dict gains
  `judge_score`, `judge_confidence`, `judge_divergence`.
- `summarize_grid*` needs zero changes for deltas (new keys pair automatically via
  `paired_deltas`); one addition: the validity-gate report block.
- Out of scope here: per-test-case reporters, learned weights, any headline change
  to mem-apg.4 before Phase 3.

## 8. Sign-off items for Stephanie

1. Judge backend: self-hosted OSS endpoint (D4/D16-clean) vs Claude-in-Harbor
   (free, same-family bias, κ-gate mandatory)?
2. Is per-test-FILE granularity acceptable for Phase 1, deferring per-test-case
   reporters?
3. Phase 3 trigger: does mem-apg.4 need a single composite number, or do
   per-signal paired deltas suffice for the headline?
4. Hand-labeling budget: ~15–20 cached runs labeled pass/near/far for the κ gate.
