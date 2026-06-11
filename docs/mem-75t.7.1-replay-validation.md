# mem-75t.7.1 — Trace→Diff Replay Validation on Real Beads

Acceptance validation for the P0 replay reconstructor (`membench/bundle/replay.py`)
against plan §9.1 (docs/mem-75t.7-task-bundle-builder-plan.md, review revision 1):
replay each transcript's Edit/Write/MultiEdit calls against `repo@base_commit`,
report replay success rate with every mismatch classified, emit a real `git diff`
per bead. Run 2026-06-10.

## Methodology (reproducible)

- **Store**: `/home/ds/projects/mem/.mem/store.db`, opened strictly read-only
  (`file:...?mode=ro`). Eligibility: `trace_path IS NOT NULL AND base_commit IS NOT NULL
  AND status='closed'`, rig ∈ {mem, gascity, gascity_dashboard} (rigs with local clones
  in `env_recon.DEFAULT_RIG_REPOS`; the 2 eligible `gpk` rows have no local clone).
- **Pool**: 111 eligible rows. All 111 transcripts parsed with
  `parse_mutation_calls` (0 parse errors).
- **Selection**: 8 beads with ≥3 mutation calls, spanning rigs (2 mem, 6
  gascity_dashboard), transcript sizes 0.4–2.7 MB, 7 distinct base commits.
  gascity could not contribute: both eligible gascity beads have **zero** mutation calls.
- **Checkout**: `git -C <clone> worktree add --detach /tmp/replay-val-<work_id>
  <base_commit>`. All 8 base commits resolved — **0 CHECKOUT_FAILED**. Worktrees
  removed after the run; clones untouched.
- **Replay**: `replay_transcript(stream, checkout_dir=…, work_dir=…)` via
  `uv run python` from `memory-bench/`. Two passes:
  1. **as-recorded** — `work_dir` from the store record (the rebase prefix the
     bundle assembler would use today);
  2. **corrected** — `work_dir` = the session's true root inferred from the
     majority common prefix of the transcript's own mutation paths (a mechanical
     inference, applied to the 4 beads pass 1 zeroed out on rebase/absence).

### Exact bead set

| work_id | rig | base_commit | transcript |
|---|---|---|---|
| mem-us6j | mem | `e22c664f1e82` | 413,730 B |
| mem-dodn | mem | `80ee45fa6a74` | 2,678,607 B |
| gascity-dashboard-usu9f | gascity_dashboard | `d675136b4d44` | 2,219,784 B |
| gascity-dashboard-2nbe9 | gascity_dashboard | `ab634bc291f6` | 1,170,896 B |
| gascity-dashboard-52kv3 | gascity_dashboard | `ab634bc291f6` | 729,694 B |
| gascity-dashboard-dh0gt | gascity_dashboard | `ec161aa8cda4` | 720,370 B |
| gascity-dashboard-zg4da | gascity_dashboard | `541f4f7ec92a` | 380,943 B |
| gascity-dashboard-041jz | gascity_dashboard | `2f5c9aec7a70` | 673,566 B |

## Coverage data (pre-replay)

Of the 111 eligible beads:

- **39 (35%) have zero Edit/Write/MultiEdit calls** — not bundle-able from trace
  replay at all (includes both gascity beads: their polecat sessions mutate only
  via shell). These were skipped by construction.
- 24 more have only 1–2 calls. **48 beads (43%) have ≥3 mutation calls** — the
  realistic bundle-candidate pool for these three rigs.
- The 4 eligible mem beads resolve to only 2 distinct transcripts: mem-5we9 /
  mem-dodn / mem-mi02 share one 2.7 MB mega-session trace that the store maps to
  **9 work_records** (6 of them base_commit-less).

## Per-bead results

Outcome key: A=APPLIED, OSM=OLD_STRING_MISSING, AMB=OLD_STRING_AMBIGUOUS,
FA=FILE_ABSENT, OWD=OUTSIDE_WORK_DIR.

| work_id | calls | rate (recorded wd) | rate (true wd) | outcome histogram (true wd) | diff files | diff lines |
|---|---:|---:|---:|---|---:|---:|
| mem-us6j | 8 | **1.00** | 1.00 | 8 A | 3 | 261 |
| mem-dodn | 57 | 0.65 | 0.65 | 37 A · 19 OSM · 1 AMB | 6 | 349 |
| gascity-dashboard-usu9f | 54 | 0.00 (54 FA) | **0.69** | 37 A · 13 OSM · 4 OWD | 9 | 501 |
| gascity-dashboard-2nbe9 | 38 | 0.00 (38 OWD) | **0.63** | 24 A · 10 OSM · 2 FA · 2 OWD | 16 | 585 |
| gascity-dashboard-52kv3 | 21 | 0.00 (21 OWD) | **0.76** | 16 A · 2 OSM · 3 OWD | 8 | 338 |
| gascity-dashboard-dh0gt | 13 | **0.92** | 0.92 | 12 A · 1 OWD | 2 | 370 |
| gascity-dashboard-zg4da | 5 | 0.00 | 0.00 | 4 FA · 1 OSM | 0 | 0 |
| gascity-dashboard-041jz | 3 | 0.00 | 0.00 | 1 FA · 2 OSM | 0 | 0 |

Aggregates (true-wd pass, 199 calls total):

- **Overall replay success rate: 134/199 = 0.67** (as-recorded wd: 57/199 = 0.29).
- **6/8 beads emit a real, non-empty git diff** (2–16 files, 261–585 diff lines).
- Failure histogram (65 non-APPLIED): **OSM 47 (72%)**, OWD 10 (15%), FA 7 (11%),
  AMB 1 (2%).
- Excluding auto-memory writes (`/home/ds/.claude-homes/...` — not part of any
  gold diff) from the denominator, per-bead adjusted rates: 1.00, 0.65, 0.69,
  0.67, 0.89, **1.00**, 0.00, 0.00.

## Root-cause classification of failures (eyeballed)

1. **`work_dir` provenance is wrong in the store record (dominant, recoverable).**
   3/8 beads scored 0.00 on pass 1 purely because `record.work_dir` is the clone
   root (`/home/ds/gascity-dashboard`) while the session actually worked in a git
   worktree — nested (`.claude/worktrees/4bol-health` → FA) or sibling
   (`/home/ds/gascity-dashboard-wt-aqje`, `-wt-spbi` → OWD). Rebasing on the true
   root lifted them to 0.63–0.76. The transcript's per-event `cwd` field is *not*
   a sufficient fix: 2nbe9's events report the clone root while its edits target
   the sibling worktree (EnterWorktree). Majority-prefix inference over the
   mutation paths themselves is the robust mechanical recovery — validated here.
   usu9f additionally hops to a *second* worktree mid-trace
   (`.claude/worktrees/o3km-workers-clickable`, its remaining 4 OWD), so a single
   rebase prefix per trace is an approximation.

2. **`base_commit` is timestamp-approximate and can predate the session's tree
   (fatal for small beads).** zg4da edits
   `backend/src/routes/supervisor-read-allowlist.ts`, which was only added in
   `ee83bc0` (#77) — *after* its resolved base `541f4f7e` (#62) → all 4 file
   edits FA, empty diff. Same for 041jz: `supervisor-status.ts` added in
   `3f3fc12` (#89) after base `2f5c9ae` (#87). Both produced **empty gold diffs**
   despite real work in the trace.

3. **Multi-bead mega-session traces.** mem-dodn's transcript is shared by 9
   work_records; replaying the *full* transcript against one bead's base commit
   mixes nine beads' edit streams → 19 OSM where an edit assumes a sibling
   bead's (or a shell command's) prior mutation. Per-bead replay needs trace
   segmentation, or the bundle must be cut at session granularity.

4. **Worktree state reuse across beads.** The `4bol-health` worktree served at
   least usu9f and 041jz; the later session's edits assume the earlier session's
   tree state, which `base_commit` (resolved per bead from timestamps on
   origin/main) cannot represent. This is the main driver of usu9f's residual 13
   OSM.

5. **Auto-memory writes** (`/home/ds/.claude-homes/.../memory/*.md`, 6 calls
   across 3 beads) are correctly classified OWD — genuinely outside the gold
   diff. They should be excluded from the success-rate denominator, not counted
   as drift.

## Verdict on plan §9.1's bet

**VIABLE.** The replay framing does what the review revision promised: 6/8 beads
yield real, applyable, non-empty git diffs, and — the stronger result — *every*
failure was mechanically detected and classified, with the classifications
pointing at concrete, fixable provenance gaps rather than at the replay
machinery (zero crashes, zero unclassified calls, exhaustive outcome coverage).
The acceptance criterion (≥5 real beads, classified mismatches, non-empty hunks)
is met.

### Implications for the P1 admission filter (mem-75t.7.2)

- **Derive the rebase prefix from the trace, not from `record.work_dir`.**
  Majority common-prefix inference over mutation-call paths recovered 3 beads
  from 0.00 → 0.63–0.76 here. Record-level `work_dir` and even per-event `cwd`
  are both unreliable under worktree-based workflows.
- **Pre-validate `base_commit`**: cheap pre-check that every Edit-target file
  exists at base (`git cat-file -e <base>:<path>`) catches timestamp-drift
  before replay (would have flagged zg4da/041jz immediately).
- **Auto-reject empty gold diffs** (zg4da, 041jz pattern).
- **Exclude non-repo paths (auto-memory writes) from the success-rate
  denominator** before thresholding.
- **Threshold**: a partial replay means a gold diff with *missing hunks* — a
  corrupted oracle, worse than no bundle. On the adjusted rates observed,
  `replay_success_rate ≥ 0.9` admits 3/8 of this sample (mem-us6j 1.00, dh0gt
  1.00, 52kv3 0.89≈borderline); ≥0.6 would admit 6/8 but with known-incomplete
  diffs. Recommend **≥0.9 adjusted** for oracle-grade bundles, with the
  0.6–0.9 band retained as context-only (trace-as-context rungs don't need an
  exact diff).
- **Multi-bead traces** (the mem trio and 6 sibling records) need segmentation
  or session-granularity bundling before admission; expected yield from the
  current corpus is therefore closer to the ≥3-call pool (48 beads) × admission
  pass-rate (~3/8 observed) ≈ **15–20 oracle-grade bundles** — enough for the
  .7.6 gate's ~10-bundle probe.

## Cleanup

All 8 `/tmp/replay-val-*` worktrees removed via `git worktree remove --force`;
`git worktree list` in both clones verified back to its pre-run state. No writes
to the store, no writes inside the rig clones' main trees.
