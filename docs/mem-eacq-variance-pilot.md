# mem-eacq variance pilot: the eval's run-to-run noise floor

Three admitted bundles ran five identical times each under `none-clean` on
2026-07-04, scored with the same graded instrument as the n9 headline. The
result: diff-sim is a usable instrument (pooled within-task SD 0.015, under the
0.05 threshold), the rubric judge is not (SD 0.092, over it), and the pass-scale
metrics have no dynamic range on this pool at all (identically zero across every
run that carries them). The n9 matched-set diff-sim delta (+0.046) sits
marginally above the sampling-noise floor at that pool size; the judge delta
(+0.038) is far inside the noise band and stays inside it even at five repeats
on an eight-task pool. The artifact of record is
`.mem/grid-eacq2/variance-pilot.json`.

## Setup

- 3 bundles x `none-clean` x 5 repeats = 15 fresh agent runs, strictly
  sequential (probe worktree sweeps on the shared rig clone are prefix-global;
  concurrent reps would corrupt harvests).
- Agent pinned to `claude-sonnet-4-6` / CLI `2.1.173` via harbor
  (`assert_run_pins` raises on drift); judge `claude-sonnet-4-6`, 3 rounds,
  median-of-rounds. All pins echoed into the report's `pins` block.
- Every rep in a fresh rep-scoped dir: runs under `.mem/probe-eacq2/rep{1..5}`,
  scored results under `.mem/grid-eacq2/rep{1..5}`. No legacy-cache reuse. The
  six runs from the earlier Fable-era launch (`.mem/probe-eacq` /
  `.mem/grid-eacq`) were discarded per the mem-pl clean-re-run directive, never
  mixed; those dirs remain untouched as forensics.
- Network: this was the first live exercise of the mem-yeoz allowlist default.
  All 15 produced task envs carried `network_mode = "allowlist"` in `task.toml`
  (verified before launch), and no run failed on a blocked host.
- Driver: `scripts/run_variance_pilot.py` at branch `mem-eacq-variance-pilot`
  (launch commit `2cd7944`; scoring completed under `cc36b84`, see run log).

## Bundle selection

| bundle | why |
|---|---|
| `gascity-dashboard-2a7lh` | CSB-valid admitted bundle from the grid-ce pool; carries the full oracle leg (repro + test_ratio). |
| `gascity-dashboard-4lf62` | CSB-valid, and a member of the n9 matched set (retrieval fired for it in the n9 `ours` arm), so the SD covers the population the headline reads. |
| `codeprobe-3l6tb` | Real 6-file src+test change with a live judge/diff-sim task. Replaced the originally planned `km0wj` after adversarial plan review: km0wj's anchor is pinned near 1.0 by its broken oracle, which would bias SD toward 0 and hand the judge a degenerate task. |

The pass-scale SD (repro/test_ratio) therefore comes from the two CSB-valid
dashboard bundles; 3l6tb carries no oracle leg.

## Run log

15 of 15 runs completed; none were substituted.

- Launch 2026-07-04 11:45 UTC. Reps 1-4 (12 runs) plus rep5 `2a7lh` and
  `4lf62` ran clean.
- Rep5 `codeprobe-3l6tb` errored with `api_error_status=429` (session limit,
  reset 14:30 UTC). The batch aborted without writing a result file; the run
  re-executed fresh on resume at 14:34 UTC. One infra retry, recorded here.
- The scoring pass then crashed on a `RubricParseError`: the host `claude -p`
  judge intermittently returned a code-review `{findings, level}` object
  instead of the rubric schema. Fixed in `cc36b84` (retry unparseable draws up
  to 4 attempts, fail loud if all are unparseable; a parseable reply scores
  exactly as before, so judge identity and grid-ce comparability are
  unchanged). Scoring completed on the 15:20 UTC resume. The underlying
  host-config contamination is a separate validity finding, surfaced to mem-pl.

## Per-run results

diff_sim / judge / test_ratio / repro from the scored grid results, in rep
order. 3l6tb has no oracle leg (repro and test_ratio absent by construction).

| bundle | rep | diff_sim | judge | test_ratio | repro | turns | out tokens |
|---|---|---|---|---|---|---|---|
| 2a7lh | 1 | 0.0311 | 0.50 | 0.0 | 0 | 107 | 2933 |
| 2a7lh | 2 | 0.0261 | 0.50 | 0.0 | 0 | 160 | 3023 |
| 2a7lh | 3 | 0.0309 | 0.50 | 0.0 | 0 | 127 | 3363 |
| 2a7lh | 4 | 0.0374 | 0.35 | 0.0 | 0 | 100 | 2336 |
| 2a7lh | 5 | 0.0231 | 0.50 | 0.0 | 0 | 134 | 3668 |
| 4lf62 | 1 | 0.0352 | 0.50 | 0.0 | 0 | 183 | 6622 |
| 4lf62 | 2 | 0.0880 | 0.50 | 0.0 | 0 | 145 | 4676 |
| 4lf62 | 3 | 0.0912 | 0.50 | 0.0 | 0 | 206 | 6471 |
| 4lf62 | 4 | 0.0485 | 0.50 | 0.2 | 0 | 163 | 4491 |
| 4lf62 | 5 | 0.0587 | 0.60 | 0.2 | 0 | 168 | 5312 |
| 3l6tb | 1 | 0.0 | 0.10 | — | — | 64 | 1830 |
| 3l6tb | 2 | 0.0 | 0.35 | — | — | 106 | 2985 |
| 3l6tb | 3 | 0.0 | 0.35 | — | — | 90 | 2481 |
| 3l6tb | 4 | 0.0 | 0.35 | — | — | 83 | 1957 |
| 3l6tb | 5 | 0.0 | 0.10 | — | — | 86 | 2857 |

Flag: `codeprobe-3l6tb` diff_sim is degenerate, 0.0 in all five repeats (the
candidate diff never overlapped the gold hunks). It contributes zero variance
and deflates the pooled diff_sim SD. Excluding it, the two dashboard bundles
pool to diff_sim SD 0.0178, still comfortably under the threshold, so the
threshold read below survives the flag.

## Within-task SD

Sample SD per bundle, pooled as sqrt(mean per-bundle variance). Threshold: the
kill-shot criterion from the bead is pooled SD > ~0.05 on the reward-scale
metrics.

| metric | 2a7lh | 4lf62 | 3l6tb | pooled | > 0.05? |
|---|---|---|---|---|---|
| diff_sim | 0.0055 | 0.0246 | 0.0 | **0.0145** | no |
| judge_score | 0.0671 | 0.0447 | 0.1369 | **0.0917** | **yes** |
| test_ratio | 0.0 | 0.1095 | — | **0.0775** | **yes** |
| repro_passed | 0.0 | 0.0 | — | 0.0 | no (degenerate) |
| score_direct | 0.0 | 0.0 | 0.0 | 0.0 | no (degenerate) |
| turns | 23.8 | 22.9 | 15.1 | 20.9 | n/a |
| tool_calls | 18.8 | 21.2 | 10.8 | 17.5 | n/a |
| output_tokens | 501 | 992 | 519 | 708 | n/a |
| input_tokens | 63 | 1416 | 15 | 819 | n/a |

The zero SDs on repro/score_direct are floor-censoring, not stability: every
one of the runs carrying those metrics scored exactly 0, consistent with the
n9 finding that no single agent run lands these features. They carry no
information about arm separation at this pool.

## Minimum detectable effect (paired, alpha 0.05, power 0.80)

`(t_{0.975,N-1} + t_{0.80,N-1}) * sqrt(2/k) * sd_within / sqrt(N)` over N tasks
and k repeats per (task, arm) cell, via `membench.grading.variance.
mde_paired_floor` (t-quantiles from the mem-lp24 helper, no scipy). These are
**sampling-noise lower bounds**: same-condition repeats cannot see the
task-x-arm interaction, which inflates the true MDE above every number here.
N=2/3/5 are from the report; N=4 is the n9 matched-set size, N=8 the current
admitted pool, N=20 a plausible expanded pool (computed with the same helper).

diff_sim (pooled SD 0.0145):

| N | k=1 | k=3 | k=5 |
|---|---|---|---|
| 2 | 0.205 | 0.118 | 0.092 |
| 3 | 0.064 | 0.037 | 0.028 |
| 4 | 0.043 | 0.025 | 0.019 |
| 5 | 0.034 | 0.020 | 0.015 |
| 8 | 0.024 | 0.014 | 0.011 |
| 20 | 0.014 | 0.008 | 0.006 |

judge_score (pooled SD 0.0917):

| N | k=1 | k=3 | k=5 |
|---|---|---|---|
| 2 | 1.292 | 0.746 | 0.578 |
| 3 | 0.402 | 0.232 | 0.180 |
| 4 | 0.270 | 0.156 | 0.121 |
| 5 | 0.216 | 0.125 | 0.096 |
| 8 | 0.150 | 0.086 | 0.067 |
| 20 | 0.086 | 0.049 | 0.038 |

test_ratio (pooled SD 0.0775, two-bundle estimate):

| N | k=1 | k=3 | k=5 |
|---|---|---|---|
| 4 | 0.228 | 0.132 | 0.102 |
| 8 | 0.126 | 0.073 | 0.056 |
| 20 | 0.072 | 0.042 | 0.032 |

Efficiency legs, N=8: output_tokens MDE 1154 (k=1) / 516 (k=5); turns 34.1
(k=1) / 15.3 (k=5).

## Verdict: is the n9 delta above the noise floor?

The n9 graded headline (docs/mem-n9-graded-headline.md) reported, on the
four-bundle matched set at k=1: ours vs none-clean **+0.046 diff-sim** and
**+0.038 judge**.

- **diff-sim: marginally above the floor.** +0.046 against a 0.043
  sampling-noise lower bound at N=4, k=1. It clears the bound by 7%, and the
  bound excludes the task-x-arm interaction, so the honest read is "consistent
  with a real effect, not yet distinguishable from noise with any margin." The
  full-eight dilution (+0.023) sits exactly at its N=8, k=1 floor (0.024):
  indistinguishable.
- **judge: far below the floor.** +0.038 against a 0.270 lower bound at N=4,
  k=1, a factor of 7 short. Even at five repeats on the full eight-task pool
  the judge floor is 0.067, still above the observed delta. Every judge delta
  narrated so far is noise-indistinguishable, exactly the kill-shot condition
  the bead named (SD 0.092 > 0.05).
- **pass metrics: no instrument.** repro_passed and score_direct are
  identically zero across all carrying runs; test_ratio SD (0.0775) exceeds
  its own headroom on this pool. Arm separation claims on the pass scale need
  a pool where the metrics move at all.

Sizing forward from the diff-sim floor: a +0.046-scale effect needs N=4 at
k=3, or N=8 at k=1, as a bare minimum; a +0.02-scale effect (the full-pool
dilution) needs roughly N=20 at k=1 or N=8 at k=3. The judge instrument
cannot confirm effects of the observed size at any feasible pool; it needs
either variance reduction (better rubric anchoring, more rounds) or
retirement to a secondary signal.

## Consequences already wired

- `run_grid_3arm_graded.py` now takes `--repeats` with a hard floor of 3 for
  anything called a headline (rep1 resumes legacy dirs, deterministic stages
  run once, scrub per rep; summary gains a `repeats` block with
  repeats-collapsed per-metric stats and delta-of-means). Landed in `2cd7944`
  with tests.
- Wrong-schema judge draws retry instead of aborting the batch (`cc36b84`,
  with tests). The host-config contamination that produces them is tracked
  separately.

All numbers in this document are publication-held pending mem-pl review.
