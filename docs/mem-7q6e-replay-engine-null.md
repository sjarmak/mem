# mem-7q6e — replay-engine recovery is UNSOUND (null)

**Verdict:** the two replay-engine "levers" scoped for +4 native bundles (→N=13) do
**not** survive contact with the real bundle data. Neither is a mechanically sound
recovery; both would **fabricate gold diffs** and corrupt eval validity. No change was
made to `membench/bundle/replay.py`. Native sound-oracle yield stays at **N=9** (the
mem `RigTestConfig`, mem-tz48 lever 2). The replay engine is behaving **correctly** by
failing these bundles closed.

This confirms the original mem-qarg wave-2 triage ("all 10 stage-2 rejects are
corpus/decomposition limits, NOT repairable test fixtures") and **overturns** the
Explore-agent hypothesis that scoped this bead.

## Method

Read the real materialized bundles (`.mem/bundles-qarg/gascity-dashboard-{zhy00,8n3to,
ytvbs,e9y0d}.json`); each carries the full `ReplayResult` (per-call outcomes + the gold
`file_diffs`). For Lever A, reconstructed the failing file from its actual `base_commit`
through the applied edits and located each missing anchor. Trace payloads resolved from
`trace_ref` and parsed with `membench.bundle.replay.parse_mutation_calls`.

## Lever A — sequential-edit rebasing (zhy00, 8n3to, ytvbs): UNSOUND

Hypothesis was: `OLD_STRING_MISSING` drops hunks because sequential edits don't rebase
onto `base_commit`; "rebase remaining edits forward through prior transforms" recovers
them.

Reality: the failing calls are **middle edits flanked by APPLIED edits on the same file
at the same root**. The file state is coherent, the engine already carries state
forward across calls and already runs later independent edits after a failure (no
`break` in `replay_calls`). Decisive reconstruction of `8n3to` /
`frontend/src/attention/registry.ts` (base `b16f1e36ea`), 9 edits in order:

| call | anchor in current state | anchor in base file | substring of any prior `new_string` | classification |
|---|---|---|---|---|
| #2 | 0 | **1** | no | base anchor **consumed by an overlapping earlier edit** |
| #5 | 0 | **0** | no | anchor exists **nowhere** (base ≠ session base) |
| #8 | 0 | **0** | no | anchor exists **nowhere** (base ≠ session base) |

None is "producible from an earlier replayed edit," which is what an engine ordering bug
would look like. #5/#8 reference text that is in neither the base commit nor any prior
edit's output; the agent edited against a file state the timestamp-approximate
`base_commit` does not contain. #2 is real edit overlap. **There is no correct text
substitution for a missing anchor; forcing one invents an unanchored hunk.** Replay-rate
≠ soundness, exactly as the qarg doc noted (zhy00 is 0.952 and still broken).

## Lever B — multi-workspace work_dir inference (e9y0d): UNSOUND

Hypothesis was: npm-workspace truncation, with work_dir rooted at `frontend/` dropping
`backend/`+`shared/` edits.

Reality: not npm workspaces. The session edited the **same files at two filesystem
roots**: the main clone `/home/ds/gascity-dashboard/` (4 calls) **and** a sibling git
worktree `/home/ds/gascity-dashboard-jkkc/` (10 calls). `effective_work_dir` correctly
picked the majority root (`-jkkc`); the 4 main-clone edits (e.g. `backend/test/
sessionId.test.ts` #0/#1, `shared/src/runs/session-link.test.ts` #2/#3) became
`OUTSIDE_WORK_DIR`. Those same files were **also** edited under `-jkkc` and applied (#4,
#5/#6), so they appear in the gold diff, but missing the main-clone hunks.

"Recovering" requires asserting the two roots are the **same logical repo state** and
that cross-root edits **compose in transcript order**. A git worktree and its main
clone are separate working trees that can sit on different branches/commits. That is an
**unverifiable topology assumption**; getting it wrong produces an incoherent gold diff.
Picking the other single root is strictly worse (drops 10 vs 4). No single work_dir
captures both, and merging roots is not mechanically sound.

## What this means for N

- **N = 9 is the native ceiling** reachable by harness/replay work. The mem
  `RigTestConfig` (+1) was the only sound growth lever of the three.
- The 4 "replay-recoverable" bundles are **not** recoverable in the replay engine. The
  real blocker is upstream: `base_commit` is a timestamp-approximate main-tip that does
  **not** match the session's true per-worktree base state, so legitimately-applied
  session edits have no anchor at replay time. Closing that gap is **trace-substrate /
  corpus work** (capturing the exact per-worktree base SHA each session ran against,
  mem-75t lineage), not a `replay.py` patch. It is substantially larger.
- Decision impact (mem-tz48): the "wait for N=13" premise is **void**; N=13 was
  predicated on this fix. The live choices are now: (a) run the 3-arm graded headline at
  **N=9** (still 4.5× apg.9's N=2, every oracle provably sound), (b) invest in corpus
  base-state fidelity (large, mem-75t), or (c) reconsider the scix/CSB arms. Stephanie's
  call.

## Minor latent observation (not fixed — out of scope, caught downstream)

A heavily cross-root session looks "perfect" at `adjusted_replay_success_rate` (it
**excludes** `OUTSIDE_WORK_DIR`), masking an incomplete gold diff. This is benign today
because the oracle-soundness gate (live gold-test repro) catches it and rejects the
bundle. A "multi-root session detected" diagnostic in assembly would aid triage but is
not required for correctness.
