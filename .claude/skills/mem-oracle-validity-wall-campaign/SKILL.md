---
name: mem-oracle-validity-wall-campaign
description: >-
  The executable, decision-gated campaign on mem's hardest live problem: the
  signal-poor real corpus behind the oracle-validity wall (407
  linkage-recovered oracles, ~8 scorable; real 3-arm eval flat). Load when
  asked to grow the sound-oracle pool, "fix the null result", make
  ours-vs-builtin separable, run/extend the tool-requiring substrate
  (mem-rk41), the recall ladder (mem-do8r), base-SHA capture (mem-75t
  Phase 2), or forward-capture (mem-31kz), or when deciding which lever on the
  wall to work next. Gives numbered phases with expected observations at every
  gate (linked → replayable → sound → discriminating), the ranked solution
  menu as decision-gated branches, and the fenced wrong paths. NOT for the
  history of how the wall was diagnosed — use mem-failure-archaeology. NOT for
  how the validity gates score mechanically — use
  mem-grading-and-validity-gates. NOT for authoring synthetic worlds — use
  mem-synthetic-world-generator. NOT for running the harness generally — use
  mem-eval-harness-run. NOT for evidence doctrine — use
  mem-research-methodology-and-evidence-bar.
---

# mem-oracle-validity-wall-campaign — the forward campaign on the signal-poor real corpus

Date-stamped **2026-07-07**. Verified against the working copy at branch
`main` @ `0b780c6` (local main is 6 commits ahead of `origin/main` @
`49e9698`; the delta is the mem-on3f + mem-31vl deliverables, push held —
the checkout moved from `4e819e1` to `0b780c6` DURING authoring, this is an
active rig). Volatile facts below carry their date; re-verification commands
are at the bottom.

This is the one skill that moves the project forward instead of describing
it. Everything here is measured by two numbers and nothing else: the
**sound-oracle count** (tasks whose gold diff reproduces AND whose empty diff
fails) and the **paired per-task lift CI** (a bootstrap confidence interval
that excludes zero). A branch of this campaign succeeds when one of those
numbers moves. "Looks better" is not an observation; never judge a gate by
eye.

## 0. Vocabulary (defined once)

| Term         | Meaning here                                                                                                                                                                       |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| oracle       | The ground-truth pass/fail check for a held-out task (usually: the task's gold tests)                                                                                              |
| sound oracle | Oracle passing the CSB validity gate: gold diff reproduces (tests go green) AND empty diff fails (tests stay red). `memory-bench/membench/grading/validity_gate.py:46`             |
| bundle       | A materialized held-out task: issue text + base commit + gold diff + env (`TaskBundle`)                                                                                            |
| replay       | Re-applying a session's recorded edits onto its `base_commit` to reconstruct the gold diff (`membench/bundle/replay.py`)                                                           |
| the wall     | The funnel collapse: thousands of linked records → hundreds replayable → single-digit sound → zero discriminating                                                                  |
| arm          | A memory condition under test: `none-clean` / `ours` (our store) / `builtin` (agent-native memory) / `oracle` (injected truth)                                                     |
| LOO          | Temporal leave-one-out: retrieval sees only records closed strictly before the target started                                                                                      |
| ftp          | fail-to-pass: a task whose tests are red before the fix and green after                                                                                                            |
| lift         | Paired per-task delta of an arm vs its baseline, with a seeded bootstrap CI (`membench/grading/curve.py` `floor_lift_ci` / `ceiling_gap_ci` / `paired_delta`)                      |
| the freeze   | The standing hold on all headline/real-corpus numbers (fleet shorthand: "the mem-0rrf publication freeze"). Numbers in this skill appear with caveats only and are NOT publishable |

## 1. Before you start (hard prerequisites)

1. **Read `mem-failure-archaeology` first.** Most "obvious fixes" to this
   problem are settled negatives bought with real spend. Section 6 below
   fences the ones that specifically ambush this campaign.
2. **The wall is a diagnosed result, not an open bug.** Root cause
   (`docs/mem-7q6e-replay-engine-null.md`): the stored `base_commit` is a
   timestamp-approximate main-tip, not the session's true per-worktree base
   SHA, so legitimately-applied session edits have no anchor at replay time.
   The replay engine fails these bundles closed, **correctly**. Three
   independent N-lift attempts (mem-1eph N=3 → mem-qarg N=8 → mem-7q6e
   proving the "+4 recoverable" bundles unsound) confirmed it.
3. **Freeze + sign-off gates.** Any paid agent run on shared accounts, any
   distill/judge spend, anything touching temporal LOO / oracle soundness /
   the headline metric, and any release of a number requires Stephanie's
   explicit go. PROVISIONAL pending Stephanie (discovery Q4): treat the
   freeze as covering ALL headline/real-corpus numbers until she states a
   narrower scope.
4. **Environment.** Both halves built and green first — see
   `mem-build-test-env`. The free legs of this campaign need Docker (for the
   validity gate's containerized pytest/vitest) but no API key and no paid
   token.

## 2. State of the wall (verified 2026-07-07 — all numbers under the freeze)

The funnel, stage by stage, with where each number comes from:

| Stage                                                                  | Count                                                                         | Source (re-verify there)                                                                                                |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Bead-spine work records                                                | 6,691                                                                         | `README.md`; store rebuild reports                                                                                      |
| Time-window `landed` oracle, sound                                     | 31 (0.4%)                                                                     | `docs/mem-outcome-linkage-lever-status.md`                                                                              |
| Commit-trailer linkage: sound work→landed-commit oracles               | 407 (mem-wanz); 356 replayable bundles / 285 test-bearing measured 2026-06-18 | `README.md` §"Where the eval stands"; `docs/mem-outcome-linkage-lever-status.md`; `scripts/validate-linked-bundles.mjs` |
| Assembled into admissible bundles (native ranked pool, 113 candidates) | 23                                                                            | `docs/mem-qarg-oracle-repair-wave2.md`                                                                                  |
| Scope-admitted (stage-1 fanout guard)                                  | 18                                                                            | same                                                                                                                    |
| **Oracle-sound, grid-ready (stage-2 validity gate)**                   | **8** (all gascity_dashboard)                                                 | same; `docs/mem-1eph-oracle-soundness-gate.md`                                                                          |
| Native replay ceiling by harness work alone                            | N=9                                                                           | `docs/mem-7q6e-replay-engine-null.md`                                                                                   |
| Discriminating (any arm's lift CI clears zero on real work)            | **0**                                                                         | `docs/mem-72sj-gate0-nonflat-probe.md` (Gate-0 FLAT, Stephanie accepted the honest-null 2026-06-21)                     |

The graded 3-arm result on the sound pool: `ours` **+0.000**, `builtin`
**+0.125** (one bundle, and that pass is not memory-attributable — `ours`
retrieval was empty on it), N=8 of 407. The binding constraint upstream of
soundness is bundle **assembly** (~83 of 105 dashboard candidates
typed-rejected: `base_predates_tree`, `low_replay_fidelity`, `empty_output`,
`shared_trace`, `dirty_trace_tail`), and the binding constraint on assembly
is base-commit fidelity. N is bound by replay/oracle fidelity, **not by
method and not by corpus size**.

Two newer facts that reframe the campaign (2026-07-06/07, mem-rk41):

- **Even sound real anchors saturate.** The Option-A re-run (6 codeprobe ftp
  anchors × 3 reps, memory delivery fixed and verified) is a valid NULL:
  4/6 tasks are all-pass or all-fail regardless of arm. No medium band =
  nothing for memory to flip.
- **The synthetic substrate saturates the other way** (mem-ons4 / mem-pjh8.1:
  memory-vs-none separates, ours-vs-builtin does not — lift 1.0 at N=8 means
  the pool stopped discriminating).

So the campaign's live question is no longer just "grow sound N" — it is
"produce a task pool with a **medium band** where the _source and quality_
of memory is load-bearing."

## 3. Phase 1 — measure the funnel yourself (free, ~minutes)

Never work from remembered numbers; the pools drift and live in gitignored
`.mem/` dirs (often in a sibling worktree's `.mem/`, not this checkout's).

```bash
# From the repo root: git pin + every bundle pool / manifest / store on disk
python3 .claude/skills/mem-oracle-validity-wall-campaign/scripts/funnel_status.py

# Linked → replayable (free, node): counts per rig from commit-trailer linkage
node scripts/validate-linked-bundles.mjs --rigs gascity_dashboard,mem,scix_experiments,codeprobe,gpk

# Replayable → sound on the ftp pool (free — Docker pytest only, no agent):
cd memory-bench
uv run python scripts/run_grid_3arm_ftp.py --validity-only
uv run python scripts/run_grid_3arm_ftp.py --dry-run   # construct + leak-check tasks, no Docker
```

To re-materialize the native bundle pool from a store (the mem-qarg
procedure — use an **isolated** `--bundles-dir`, never overwrite the shared
live pool):

```bash
cd memory-bench
PYTHONPATH=. python scripts/assemble_batch.py --limit 113 \
  --bundles-dir ../.mem/bundles-<yourtag> --report-out ../.mem/assemble-<yourtag>.json
PYTHONPATH=. python scripts/admit_batch_guarded.py --write \
  --bundles-dir ../.mem/bundles-<yourtag> \
  --manifest ../.mem/grid-ready-pool-<yourtag>.json \
  --report-out ../.mem/admit-<yourtag>.json
```

**Expected observations (gate):**

- Cross-rig ftp pool after mem-on3f (2026-07-07): 35 anchors materialize
  (6 codeprobe + 29 scix_experiments), **24/35 admit** through
  `--validity-only`, tertile-banded **8/8/8 easy/medium/hard**
  (`membench/grading/ftp_difficulty.py`).
- Native pool: ~23 assembled, ~8 oracle-sound.
- **If you see materially MORE sound bundles than the recorded counts →
  suspect the gate before celebrating.** A broken gate (empty diff passing,
  Docker image drift, tests skipped) inflates soundness silently. Re-run one
  known-broken bundle (e.g. km0wj: empty diff historically scored
  test_ratio 0.6) and confirm it still REJECTS.
- **If you see far fewer → environment**, usually Docker unavailable, rig
  clone missing (`no_rig_clone`), or you are in a worktree without the
  `.mem/` data. Fix the environment; do not "repair" bundles.

## 4. Phase 2 — choose a branch (the ranked solution menu)

PROVISIONAL pending Stephanie (discovery Q2): the wall is the campaign
spine and these four levers are its ranked branches; revisable if she
elevates a single lever. Ranking below is by liveness as of 2026-07-07.

| Pick this branch when                                         | Branch                               | Bead                                                                                                      | State (2026-07-07)                                                  |
| ------------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| You need ours-vs-builtin separable NOW on executable tasks    | **A. Tool-requiring substrate**      | mem-rk41 (OPEN, P1)                                                                                       | Most live; two children delivered today, paid grid awaits Stephanie |
| You need the headline metric pinned (recall-policy curve)     | **B. Synthetic recall ladder**       | mem-do8r (pilot CLOSED; design = mem-do8r.1 ADR)                                                          | ADR branch-ready, locks await Stephanie; free smoke exists          |
| You need real sound N to grow past 9                          | **C. Per-worktree base-SHA capture** | mem-75t lineage (epic CLOSED Phase-1; eval-validity items = Phase-2, tracked in mem-gqul decompose notes) | The root-cause fix; largest investment                              |
| You need a real corpus that is discriminating by construction | **D. Forward-capture**               | mem-31kz (CLOSED, mechanism landed)                                                                       | Branch-ready; activation is a config step; pays off slowly          |

### Branch A — tool-requiring substrate (mem-rk41): make ours-vs-builtin separable

Design target (Stephanie pivot, 2026-07-06): task shapes where success
REQUIRES using recalled prior-session content in a specific **tool action**,
so the source and quality of memory are load-bearing rather than optional
text recall.

Delivered under it (2026-07-07 — check merge state before building on
either):

- **mem-on3f (CLOSED):** cross-rig difficulty-banded executable ftp pool.
  `materialize_ftp_anchors.py --rig {codeprobe,scix_experiments}`,
  `report_ftp_pool_difficulty.py --bundles-dir <dirs...>`, and
  `run_grid_3arm_ftp.py --bundles-dir` now takes multiple dirs. Landed on
  **local** main (78ee5ca + 4e819e1); `origin/main` still at 49e9698.
- **mem-31vl (CLOSED):** the tool-requiring task shape itself — opt-in
  `tool_requiring` enterprise-workflow goal (recalled value must appear IN
  the tool argument; text channel cleared;
  `GENERATOR_VERSION = "enterprise-workflow.v3"`), plus a deterministic
  `membench/generators/retrieval_discrimination_gate.py` (filesystem/id-exact
  must beat naive lexical top-k — a model-free PROXY for ours-vs-builtin).
  Fast-forwarded onto **local** main @ `0b780c6` on 2026-07-07; `origin/main`
  is still behind (push held). Verify the push state before citing it as
  landed; do not re-implement.

Numbered steps with gates:

1. Verify the pool: Phase-1 commands above. Gate: 24 admitted, 8/8/8 bands.
2. Free discrimination check: the mem-31vl e2e proxy proves necessity
   (memory > none) AND discrimination (quality-retrieval > naive, 1.0 vs 0.0
   across 4 seeds) with no model
   (`memory-bench/tests/` — the mem-31vl e2e suite; full suite was 2125
   passed / 9 skipped at delivery). Gate: both hold on your seeds; if
   discrimination collapses to ties, the shape regressed — stop.
3. **HALT — Stephanie's go required:** the paid graded 3-arm grid over the
   banded pool (up to ~216 agent legs on the shared `claude -p` pool,
   multi-hour). The prepared command lives in the mem-on3f bead body; the
   driver is `run_grid_3arm_ftp.py` (paid legs need
   `CLAUDE_CODE_OAUTH_TOKEN`).
4. Read the result the only valid way: per-band paired per-task deltas with
   bootstrap CIs. Expected shapes: (i) medium band shows an ours-vs-builtin
   CI clearing zero → the substrate works, scale it; (ii) still flat across
   bands → a documented, substrate-valid null; the acceptance criterion of
   mem-rk41 explicitly allows this outcome — write it up as such, do NOT
   torture the pool.

### Branch B — synthetic recall ladder (mem-do8r)

PROVISIONAL pending Stephanie (discovery Q3): teach current reality, canonize
nothing. The shipped headline instrument is the ablation
score-vs-information curve (`mem-grading-and-validity-gates`); the ladder
(none / vector-RAG / ranked-ledger / oracle, model fixed, ONLY the recall arm
varies) is the proposed going-forward design in
`docs/mem-do8r-recall-ladder-adr.md`, whose "Stephanie's locks" section (6
numbered items: rung-2 arm, ledger distill pass, two-track task pool, lift
definition, repeats, headline framing) awaits her ruling. Do not implement a
lock she has not accepted.

What you can run free today:

```bash
cd memory-bench
uv run python scripts/recall_ladder_smoke.py   # all 4 rungs, StubRunner, no Docker/network/paid API
```

Gates from the ADR (all pre-locked constraints, not suggestions): every task
pool entry passes the memory-necessity gate
(`membench/generators/memory_necessity_gate.py` — oracle must beat no-memory
by > epsilon or the task is rejected); synthetic and real tracks are NEVER
pooled; the rung-3 ledger requires a distill pass (the eval store's `lessons`
table was 0 on 2026-07-03 — an empty ledger silently degenerates rung 3 to
rung 1); judge stays out of the pass/fail loop; lift = paired per-task
pass-rate delta + true-cost co-primary, promoted only when the CI excludes
zero.

### Branch C — per-worktree base-SHA capture (mem-75t Phase 2)

The root-cause fix: capture the exact base SHA each session's worktree
actually sat on, at claim time, so replay anchors hold. This is
trace-substrate/corpus work, "substantially larger than a harness patch"
(mem-7q6e). Phase-1 of the epic (durable trace ingest, all rigs) is CLOSED;
the eval-validity items (gold-diff validation, admit-gate re-run) were
explicitly carved out as Phase-2 / manual-Stephanie scope (see the mem-gqul
decompose notes via `bd show mem-75t`).

Success is measured ONE way: re-run Phase 1 of this skill on a
post-capture corpus window and watch the assembly-reject taxonomy. The
fix is working when `base_predates_tree` + `low_replay_fidelity` rejects
shrink and the sound count rises past the N=9 ceiling **without any change
to the validity gate**. If sound N rises after a gate change instead, you
have measured the gate, not the corpus.

Fence: this branch only pays off on **future** sessions. It cannot rescue
the existing corpus — the historical JSONL transcripts needed to
re-anchor old sessions are gone (~6-week rolling window; see
`mem-ingest-and-provenance` and the fence in §6.2).

### Branch D — forward-capture (mem-31kz)

The only route that can manufacture a **discriminating real** corpus: the
city's exhaust today is agents doing coding, not agents USING memory, so a
memory-lift signal cannot be reconstructed post-hoc — only captured at
execution time. Mechanism is landed (schema v8→v9 `memory_events`, strict
allow-list, capture hook, round-trip export/import —
`docs/mem-31kz-forward-capture.md`); activating the PostToolUse hook
fleet-wide is a config step and cross-system capture is mayor-owned. Note the
hook activation as documented is an operational precondition tied to this
install's fleet (PROVISIONAL per discovery Q1: fleet machinery lives in
`mem-git-and-dispatch-workflow`, marked internal-orchestration).

Gate for this branch: `memory_events` row counts rising across real sessions
(`mem query` over the store, or `mem export-memory-events`), then — and only
then — the Gate-0 non-flat question becomes re-computable on captured data
(`scripts/gate0_nonflat_probe.py --summary <grid summary>`). Expected
observation today: the `used` retrieval-causality edge is empty by
construction until capture runs in production.

## 5. The synthetic↔real generalization question (mem-bxhh)

The synthetic track holds the project's only measurable lift (cross-task
continuity 0.062 isolated → 0.188 shared store — synthetic-only, under the
freeze). Whether that transfers to real work is formally OPEN with a recorded
NO-GO on the first attempt (mem-bxhh.5, 2026-06-18): the real N=8 anchor is
statistically flat AND its only discriminating arms have no synthetic
counterpart, so the synthetic↔real rank-correlation was **uncomputable** —
the PRD's named honest-null exit.

What would make it computable (this is the checklist, not a hope):

1. A non-flat real anchor — Branch A's medium band or Branch D's captured
   corpus are the two candidate producers.
2. A shared arm set run on BOTH substrates (the bxhh.5 blocker was
   ours=replay-only vs builtin=paid-Harbor-only).
3. Rank correlation across substrates with its own CI, reported as its own
   result — never "synthetic lift, therefore real lift".

Until all three hold, the synthetic lift is a synthetic-only result. Say so
in every write-up that cites it.

## 6. Fenced wrong paths (each one already cost a session or worse)

Full stories in `mem-failure-archaeology`; this is the campaign-facing list.
Do NOT:

1. **Patch `replay.py` to "recover" broken bundles.** Settled NULL
   (mem-7q6e): the missing anchors exist in neither the base commit nor any
   prior edit's output; every proposed lever fabricates gold diffs. N=9 is
   the native ceiling. The fix is upstream (Branch C), not in the engine.
2. **Re-run the `ours` arm on the codeprobe corpus.** Provably inert 0/6
   (mem-bxhh3): those sessions were never trace-resolved before the ~6-week
   JSONL pruning window closed; the failure-signature substrate cannot be
   regenerated. The positive control proved the wiring sound — it is the
   substrate that is barren. Codeprobe ftp anchors are calibration data and
   validity-gate fodder, not `ours` eval anchors.
3. **Re-ingest gh/PR/CI outcomes as the headline oracle.** The corpus is
   direct-to-main (~1 external ref in 6,000 records); Decisions 17/18
   replaced that oracle with the git-native `landed` one. The data is
   absent, not unwired.
4. **Merge multi-root sessions to rescue bundles** (e.g. e9y0d). A worktree
   and its main clone are separate trees on possibly different commits;
   asserting they compose is an unverifiable topology assumption.
5. **Grow "sound" N by touching the gate.** Any edit to
   `validity_gate.py`, the LOO exclusions, or the leak guard to admit more
   bundles invalidates every number downstream. Gate changes are
   HALT-branch-ready, Stephanie sign-off, tests in the same commit.
6. **Report pooled means or eyeball a grid.** The mem-75t.7.6 gate showed a
   pooled ±0.02 hiding a real −0.09 regression and a +0.17 win. Paired
   per-task deltas + bootstrap CI, always (`curve.py paired_delta`).
7. **Let the LLM judge gate anything, or cite pre-isolation judge numbers.**
   Judge is L3 report-only (bare-host contamination, mem-eacq). Also never
   cite the apg.4 n=9 table without its network-fetch footnote (clean n=8,
   `docs/audits/2026-07-03-headline-network-fetch-audit.md`).
8. **Re-probe Gate-0 on the dashboard rig expecting non-flat.** Measured
   FLAT and decided (option (a), Stephanie 2026-06-21); the confirmatory
   fresh grid (option (b)) was explicitly declined as low-information. What
   was left re-openable is option (c): wiring pytest `RIG_TEST_CONFIGS` for
   scix/codeprobe breadth — and mem-on3f has since materially delivered
   that pool.
9. **Beware `adjusted_replay_success_rate`.** It excludes `OUTSIDE_WORK_DIR`
   calls, so a heavily cross-root session can look "perfect" while its gold
   diff is incomplete (zhy00 replays at 0.952 and is still broken). Replay
   rate is not soundness; only the validity gate is.

## 7. The release-vs-invest fork (mem-1fl8) and how results exit this campaign

The fork — release the real-corpus result as a diagnosed-ceiling negative
vs. keep holding and invest in fidelity — was put to Stephanie and
**resolved 2026-06-18 as option (c): kill the write-up call entirely.** Not
time for headlines; still exploratory development. Findings (real-eval
+0.000 at N=8; synthetic continuity lift; construct-validity NO-GO) stay
captured in beads/docs for when reporting time comes.

Operationally for you:

- Every number this campaign produces is **held** under the freeze. Results
  go into a `docs/mem-*.md` status doc and the bead thread, branch-ready,
  numbers-to-Stephanie per-action — never pushed as a headline, never in a
  README claim, never external.
- The fork re-opens only on her call, and the trigger evidence would be: a
  lift CI clearing zero on a substrate-valid pool (Branch A/B), or sound N
  materially past the ceiling (Branch C/D). If your work produces either,
  surface it as a DECISION item; do not draft the release.
- PROVISIONAL pending Stephanie (discovery Q4): the exact freeze scope
  (all numbers vs. real-corpus headline only) needs her confirmation; act
  on the conservative reading.

Parked-not-dead (PROVISIONAL per discovery Q5): the `research/` reranker
track, the `#planned` 6-stage controller + OpenRath read-model, and the
in-flight `mem-*` branches (104 local branches match `mem-`, 69 of them
unmerged into main, as of 2026-07-07 — `git branch | grep -c mem-`) are
parked, not dead — check bead

- branch state before building on or fencing off any of them.

## 8. When NOT to use this skill

- Understanding WHY the wall exists / what was already tried → `mem-failure-archaeology`.
- The Decision rulings themselves (D6/D8/D17/D18/D19/D23…) → `mem-decision-ledger-and-architecture-contract`.
- Validity-gate / ablation-curve / safety-gate mechanics → `mem-grading-and-validity-gates`.
- Running the harness or an arm outside this campaign → `mem-eval-harness-run`, `mem-competitive-arms`.
- Authoring or freezing synthetic worlds → `mem-synthetic-world-generator`.
- LOO / leak internals → `mem-temporal-loo-and-leak-safety`.
- Rebuilding stores, ingest, trace coverage → `mem-store-schema-and-rebuild`, `mem-ingest-and-provenance`, `ingest-trace-substrate`.
- Push/merge/dispatch process and the fleet machinery → `mem-git-and-dispatch-workflow`.

## Provenance and maintenance

Authored 2026-07-07 against branch `main` @ `0b780c6` (checkout on main;
local main = origin/main `49e9698` + 6 held commits: mem-on3f + mem-31vl,
push held). The checkout advanced from `4e819e1` to `0b780c6` while this
skill was being written — workers are live on this rig, so treat every pin
here as a snapshot. Bead states (mem-rk41 OPEN; mem-on3f/mem-31vl CLOSED
today) and the origin push state are the most volatile facts — re-check
them first.

```bash
# Pin drift
git -C . branch --show-current && git rev-parse --short HEAD origin/main
# Funnel + pool state (read-only)
python3 .claude/skills/mem-oracle-validity-wall-campaign/scripts/funnel_status.py
# Live lever states (fleet bead store; internal-orchestration)
bd show mem-rk41 | head -30 && bd show mem-31vl | head -8
# mem-31vl merge state: v3 on main yet?
grep -n "GENERATOR_VERSION" memory-bench/membench/generators/enterprise_workflow.py
git branch -a | grep 31vl
# Key files still where this skill says
ls memory-bench/scripts/{run_grid_3arm_ftp.py,materialize_ftp_anchors.py,report_ftp_pool_difficulty.py,recall_ladder_smoke.py,gate0_nonflat_probe.py} scripts/validate-linked-bundles.mjs
grep -n "def validity_gate" memory-bench/membench/grading/validity_gate.py
grep -n "floor_lift_ci\|ceiling_gap_ci" memory-bench/membench/grading/curve.py | head -3
# Freeze framing + headline caveats of record
grep -n "mem-1fl8" README.md && grep -n "publication freeze" docs/mem-72sj-gate0-nonflat-probe.md
```
