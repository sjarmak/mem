# mem-lvp.24: §4.4 outcome-lift (validity wall, two substrates, offline quality-proxy)

**Status:** writeup (fork c, 2026-07-07). §4.4 was originally scoped as a real 3-arm
outcome-lift *run*; that run is substrate-walled (below). The stated §4.4 result is now
three things this line of work actually produced: (a) the diagnosis of the wall, (b) two
substrates constructed to get around it, and (c) an offline, deterministic quality-proxy
result on the second substrate. The literal real-agent `ours`-vs-`builtin` verdict remains
a separate, optional, paid run under **mem-rk41**; nothing here claims it.

## What §4.4 set out to measure

Outcome-lift = pass-rate delta per memory arm (`none` / `ours` / `builtin`) on *executable*
tasks, scored by the pass/fail oracle (the headline) and, alongside it, the §12.6
action-impact behavioral axes (`membench/metrics/action_impact_run.py`, the validated
sub-finding). The instrument itself works: the lvp.8-fixture staged run (mem-lvp.19)
showed memory changes *behavior*. It could not measure outcome-lift because lvp.8 has no
executable environment. Pointing the instrument at a real executable substrate is where
the wall appears.

## (a) The diagnosis: three independent substrate walls

Real-corpus outcome-lift is not measurable on the substrate we have, for three reasons
that stack. None is a defect in the memory arms; all three are properties of the corpus.

**1. Oracle validity is thin.** Of ~407 recovered/computable oracles, only **8** survive
the soundness gate as grid-ready (mem-qarg wave-2, `docs/mem-qarg-oracle-repair-wave2.md`,
up from 3 in mem-1eph's first re-carve). Sound oracle coverage is ≈11.7% of sessions and
≈1.6% of work_records (`docs/prd-task-agent-outcome-linkage.md`), single-rig-dominated
(gascity_dashboard). A pass/fail headline needs a sound oracle per task; there are 8.

**2. The executable pool saturates.** The mem-f2vi Option-A valid re-run (66/66 legs,
memory delivery confirmed on every leg after the native-injection fix, LOO-valid) is a
clean **null**: `ours − none-clean` is **0/6** on binary repro, and `ours` is
flat-to-behind `builtin` on the continuous signals, while costing more (+38 turns, +28
tool-calls). The null is not a verdict on memory. On this pool, difficulty tracks
gold-file-count almost monotonically: ≤3 gold files are solved by every arm, ≥5 fail every
arm, and 4/6 tasks saturate all-pass or all-fail regardless of arm. There is no medium
band in which recalled memory could flip an outcome. (This supersedes the earlier
`.mem/grid-optionA` grid, which was confounded by the 0/50-consumed injection bug; do not
cite those numbers. See `mem-f2vi-optionA-valid-null-substrate-scope`.)

**3. The executable corpus is small.** The behavioral fail-to-pass corpus is **32 anchors**
across two external Python rigs (`scix_experiments` 25 + `codeprobe` 7;
`memory-bench/data/ftp-oracle/README.md`). The node and config rigs
(`gascity_dashboard`, `gpk`) yield zero because the curator is pytest-only. Executability
is not a bundle key; it is ftp-oracle-corpus membership plus a rig repro runner. So a
difficulty-spread executable pool is a curation problem, not a cheap re-run.

## (b) The two constructed substrates

Two substrates were built to attack walls #2 and #3 directly. Both are landed on local main.

### (b1) mem-on3f: cross-rig, difficulty-banded executable pool

Attacks the small-N + no-medium-band walls by pooling anchors across rigs and banding them
by difficulty so a grid can report per-band separation instead of one saturated pooled
number.

- `scripts/materialize_ftp_anchors.py` generalizes the codeprobe-only materializer to
  `--rig codeprobe|scix_experiments`.
- `membench/grading/ftp_difficulty.py` `band_pool()` tertile-buckets a bundle pool by
  gold-file-count into easy/medium/hard, with deterministic `(gold_file_count, work_id)`
  ordering. ZFC: deterministic math over a structural signal, not a semantic difficulty
  judgment.
- `scripts/report_ftp_pool_difficulty.py` emits the free (Docker-free, JSON-only) pool
  distribution report.
- `scripts/run_grid_3arm_ftp.py` `load_ftp_bundles` is generalized to span multiple rig
  bundle dirs.

Payoff: the banded cross-rig pool is **8/8/8** (easy/medium/hard) and is materially less
saturated than the single-rig Option-A pool, which had no populated medium band (rollup
mem-lq2w). Commits `78ee5ca` + `4e819e1`. The *paid* graded 3-arm run over this pool is
gated; that is the decision point in mem-lq2w (run the cheap real-rig grid, or go straight
to the tool-requiring build). This writeup does not run it.

### (b2) mem-31vl: tool-requiring shape (recalled content becomes load-bearing)

Attacks the root cause of the saturation wall. In both prior substrates memory was an
*optional text recall*, so a good retriever and a naive one land on the same reward and
`ours`-vs-`builtin` cannot separate. mem-31vl makes recalled content load-bearing by
routing it through a tool argument.

- `membench/schemas/sequence.py`: `ExpectedAction{tool, arg_values, forbidden_values}` and
  `OutcomeCheck.requires_action` (its own forbidden set).
- `membench/metrics/scorers.py` `outcome_check_passes`: a per-call clause (a single call
  must carry all `arg_values` and no `forbidden_value`, matched over arg values by
  word-boundary; backward-compatible with `tool_calls=()`).
- `membench/generators/enterprise_workflow.py` `materialize_world/project(...,
  tool_requiring=True)` (`GENERATOR_VERSION = "enterprise-workflow.v3"`): the goal must call
  `apply_config` with the *current*, supersession-aware value; stale values move off the
  text answer onto the action's forbidden set so the action is the sole reward-bearing
  channel.

The architect caught the critical trap here: the `ScriptedAgent` fuses text-answer and
tool-arg into one recalled string, so reusing the text `forbidden_values` for the tool
clause is inert. The fix makes the action the sole reward channel and adds a C1 isolation
test (clean text + stale tool arg must score False).

## (c) The offline quality-proxy result

On the tool-requiring shape, a quality retriever and a naive one separate cleanly, offline,
with no model and no Docker.

`membench/generators/retrieval_discrimination_gate.py` reads the **goal-step** reward (not
the diluted whole-sequence mean) for two arms:

- `filesystem` (id-exact, surfaces the current version only) applies the current value and
  passes the goal: reward **1.0**.
- `lexical` (token top-k, surfaces the superseded versions too) rides a stale value into
  the tool argument and fails: reward **0.0**.

Delta 1.0 > EPSILON (0.05, `membench/report/comparison.py`), and the separation holds
across seeds {5, 11, 23, 42} (`tests/test_retrieval_discrimination_gate.py`). The same e2e
proves necessity (`memory > none`) and discrimination (`quality > naive`) together. Commits
`30733d2` (shape) + `ee046d2` (gate + e2e) + `0b780c6` (per-call fix); full suite green,
architect + code/security/python reviewers passed.

This is a deterministic **proxy** for `ours`-vs-`builtin`, not the verdict itself. It
establishes that the shape *can* discriminate retrieval quality, which is the precondition
for ever spending on the real comparison.

## What this establishes, and what it does not

**Establishes.** (1) Real-corpus outcome-lift is substrate-walled, with the three walls
quantified above. (2) A cross-rig, difficulty-banded executable pool exists and is less
saturated (8/8/8) than the pool the earlier null was measured on. (3) A tool-requiring
task shape exists on which a quality retriever strictly beats a naive one (1.0 vs 0.0),
proven offline.

**Does not establish.** The literal real-agent `ours`-vs-`builtin` pass-rate verdict. Real
`ours` is replay-only (`memory_systems/ours_system.py`: `retrieve` needs a resolved
`work_id` + `scope` and shells out to `mem retrieve`), and real `builtin` is off-store
native memory, which equals `none` under the deterministic `ScriptedAgent`. So the literal
verdict is inherently a paid Harbor / `claude -p` run. It remains open under epic
**mem-rk41** (needs a synthetic→TaskBundle bridge or the Harbor path). mem-ovi synthetic
realism thresholds stay deferred until that substrate direction is set.

## Reproduce (all free)

```bash
# (b1) cross-rig pool distribution (Docker-free, JSON only).
# Materialize each rig's bundles first, then report the banded distribution.
cd memory-bench
uv run python scripts/materialize_ftp_anchors.py --rig codeprobe
uv run python scripts/materialize_ftp_anchors.py --rig scix_experiments
uv run python scripts/report_ftp_pool_difficulty.py \
    --bundles-dir .mem/bundles-codeprobe .mem/bundles-scix_experiments

# (c) offline quality-proxy (no model, no Docker).
uv run pytest tests/test_retrieval_discrimination_gate.py -q
```

## Provenance

- Diagnosis: `mem-f2vi-optionA-valid-null-substrate-scope`,
  `docs/mem-qarg-oracle-repair-wave2.md`, `docs/mem-1eph-oracle-soundness-gate.md`,
  `docs/prd-task-agent-outcome-linkage.md`, `memory-bench/data/ftp-oracle/README.md`.
- Substrate (b1) mem-on3f: `78ee5ca`, `4e819e1`. Paid graded run gated → mem-lq2w /
  mem-lvp.26.
- Substrate (b2) + proxy (c) mem-31vl: `30733d2`, `ee046d2`, `0b780c6`.
- Follow-up (literal verdict, paid): epic mem-rk41.
