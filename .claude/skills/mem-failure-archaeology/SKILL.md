---
name: mem-failure-archaeology
description: >
  The chronicle of mem's settled negatives — investigations that ended in a
  diagnosed dead end — so no session re-fights them. Load BEFORE proposing any
  fix to oracle linkage, bundle replay, the replay engine (replay.py), the
  ours arm on the codeprobe corpus, gh/PR outcome re-ingest, LLM-judge gating,
  or before citing any pre-2026-07 headline number. Covers: the
  oracle-validity wall (8 sound of 407), the replay-engine null (mem-7q6e),
  the ours-substrate data wall (mem-bxhh3), the dead merged-PR/CI oracle
  (Decisions 17/18), the bare-host judge contamination (mem-eacq), and the
  zhy00 network-fetch contamination (mem-hp9o). NOT for running the forward
  campaign on the oracle wall — use mem-oracle-validity-wall-campaign. NOT for
  the decision rulings themselves — use
  mem-decision-ledger-and-architecture-contract. NOT for how the validity
  gates work mechanically — use mem-grading-and-validity-gates.
---

# mem-failure-archaeology — settled negatives, so you do not re-fight them

Date-stamped 2026-07-07. Repo: the mem rig (TypeScript store builder in
`src/`, Python eval harness in `memory-bench/`). This skill is a read-first
gate: if the work you are about to start appears in the table below, STOP and
read that entry before writing any code or spending any tokens. Every entry
here was bought with real sessions and, in some cases, real agent-run spend.

## When to use / when not

**Use when:**

- You are about to "fix" bundle replay, grow N, recover broken oracles, wire
  PR/CI outcomes, patch `memory-bench/membench/bundle/replay.py`, or run the
  `ours` arm on a new corpus.
- A number from a doc dated before 2026-07-05 is about to be cited — check the
  contamination entries first.
- An Explore agent or a plan proposes something that sounds like "just rebase
  the edits forward" or "just re-ingest the GitHub outcomes."

**Do NOT use when:**

- You want the forward plan of attack on the oracle wall (ranked levers,
  gates, expected observations) → `mem-oracle-validity-wall-campaign`
  (PROVISIONAL pending Stephanie Q2: that skill is the single home of the
  solution menu; this one records only what is settled-dead).
- You want the numbered Decision rulings and invariants →
  `mem-decision-ledger-and-architecture-contract`.
- You want gate mechanics (validity_gate, safety_gates, precision guard) →
  `mem-grading-and-validity-gates`.
- You want the evidence bar for NEW results → `mem-research-methodology-and-evidence-bar`.

## Jargon (defined once)

| Term         | Meaning here                                                                                                                                                                             |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| oracle       | The ground-truth check for a replayed task: the gold diff's tests must pass with the gold diff applied (gold reproduces) AND fail on an empty diff (fail-to-pass).                       |
| sound oracle | An oracle that passed the two-stage pre-admission gate: scope guard (mem-1eph `fanout_scope_guard`) + CodeScaleBench `validity_gate` (`memory-bench/membench/grading/validity_gate.py`). |
| bundle       | A materialized replay task (`TaskBundle` JSON): base commit + replayed session edits + gold diff + oracle tests.                                                                         |
| replay       | Reconstructing the session's edits from its transcript onto `base_commit` (`memory-bench/membench/bundle/replay.py`).                                                                    |
| arm          | A memory condition under test: `none-clean` / `ours` / `builtin` / `oracle` / third-party adapters.                                                                                      |
| `ours`       | mem's failure-triggered retrieval arm: query from the held task's stored `trace.errors`, payload = distilled lessons (D8/D9).                                                            |
| LOO          | Temporal leave-one-out: retrieval sees only records closed strictly before the target started.                                                                                           |
| headline     | The publishable metric: the ablation score-vs-information curve (D17/D18).                                                                                                               |

## The chronicle at a glance

| #   | Settled negative                           | Symptom                                                                  | Root cause                                                                                                                            | Status (2026-07-07)                                                                                                                            |
| --- | ------------------------------------------ | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Oracle-validity wall                       | 407 linkage-recovered oracles, only ~8 scorable                          | `base_commit` is a timestamp-approximate main-tip, not the session's true per-worktree base; funnel collapses at assembly + soundness | SETTLED negative; three independent N-lift attempts confirm. Forward levers live in mem-oracle-validity-wall-campaign                          |
| 2   | Replay-engine "recovery" (mem-7q6e)        | 4 bundles look recoverable by patching `replay.py`                       | Both proposed levers fabricate gold diffs; engine is correctly failing closed                                                         | NULL. N=9 is the native ceiling. Do not patch `replay.py` to force anchors                                                                     |
| 3   | `ours` on the codeprobe corpus (mem-bxhh3) | `ours` retrieval 0/6 anchors, arm provably inert                         | Corpus never trace-resolved/lesson-distilled before the ~6-week JSONL pruning window closed; substrate cannot be regenerated          | HALTED before spend. Data wall, not wiring — positive control proves the wiring                                                                |
| 4   | Merged-PR/CI outcome oracle (D17/D18)      | ~1 external ref in ~6,000 records; gh re-ingest looks "unwired"          | Corpus is direct-to-main: there are no PRs to link. Absent workflow, not missing wiring                                               | INAPPLICABLE by construction. Replaced by the git-native `landed` oracle. Do not re-attempt the gh re-ingest                                   |
| 5   | Bare-host LLM-judge numbers (mem-eacq)     | Judge deltas cited as signal; judge intermittently returned wrong schema | Host-config contamination + within-task SD 0.0917 > 0.05 noise bar — the judge instrument cannot confirm effects of the observed size | Pre-isolation judge numbers are contaminated. Judge is report-only, never in the pass/fail loop                                                |
| 6   | zhy00 network fetch (mem-hp9o audit)       | One mem-apg.4 headline run WebFetched the origin PR it was reproducing   | WebFetch vector was open in the task image; "ship PR #N" task titles invite origin consultation                                       | mem-apg.4 n=9 table must carry the footnote / be re-issued as clean n=8; mem-n9 CERTIFIED CLEAN; runs now default `network_mode = "allowlist"` |

Name collision warning: **`zhy00` appears in two distinct entries.** As a
bundle it is one of the `old_string_missing` replay rejects (#2); as a run
(`zhy00.oracle` in the mem-apg.4 probe set) it is the network-fetch
contamination (#6). Do not conflate them.

---

## 1. The oracle-validity wall — 8 sound of 407

**Symptom.** Commit-message linkage (`src/ingest/commitLinkage.ts` +
`scripts/validate-linked-bundles.mjs`) recovered 407 sound
work→landed-commit oracles on the real corpus (README §"Where the eval
stands", citing mem-wanz), a 26×-larger substrate than the earlier N=9
binary-oracle pool. But only **8 of 407** oracles are scorable end-to-end —
the other ~98% fail somewhere in the funnel. The recovered-oracle 3-arm
graded eval on that N shows no capability lift: `ours` **+0.000**, `builtin`
+0.125. Both numbers are held under the **mem-0rrf publication freeze** and
carry the caveat "N is bound by replay/oracle fidelity, not by method"
(PROVISIONAL pending Stephanie Q4: freeze scope stated conservatively as all
headline/real-corpus numbers).

**The funnel, measured (mem-qarg wave 2, docs/mem-qarg-oracle-repair-wave2.md, 2026-06-16):**

| stage                               | count                                           |
| ----------------------------------- | ----------------------------------------------- |
| ranked native candidates            | 113 (105 dashboard / 4 mem / 2 gascity / 2 gpk) |
| non-zero mutation signal            | 73                                              |
| assembled into an admissible bundle | 23                                              |
| scope-admitted (stage 1)            | 18                                              |
| oracle-sound + grid-ready (stage 2) | **8** (all dashboard)                           |

The binding constraint is bundle **assembly**, not the soundness gate: ~83 of
105 dashboard candidates are typed-rejected upstream (`base_predates_tree`
16, `low_replay_fidelity` 10, `empty_output` 10, `shared_trace` 8,
`dirty_trace_tail` 5, `no_rig_clone` 1).

**Root cause** (docs/mem-7q6e-replay-engine-null.md): the stored
`base_commit` is a timestamp-approximate main-tip, not the session's true
per-worktree base SHA, so legitimately-applied session edits have no anchor
at replay time. Fixing it is trace-substrate/corpus work (per-worktree
base-SHA capture, the mem-75t lineage — "substantially larger than a harness
patch"), not an eval-harness patch.

**Evidence.** Three independent N-lift attempts, all converging:

1. mem-1eph (docs/mem-1eph-oracle-soundness-gate.md): 10 materialized
   bundles → 3 sound after the two-stage gate.
2. mem-qarg (docs/mem-qarg-oracle-repair-wave2.md): full ranked pool → 8
   sound; every stage-2 reject diagnosed as a corpus/decomposition limit,
   not a repairable test fixture.
3. mem-7q6e (docs/mem-7q6e-replay-engine-null.md): the +4 "recoverable"
   bundles proven unsound (entry #2 below).

**Status.** SETTLED negative. The wall is replay fidelity, not corpus size
and not method. Anyone proposing "just find more candidates" or "loosen the
gate" is re-fighting this. The ranked forward levers (mem-75t base-SHA
capture, mem-rk41 tool-requiring substrate, mem-do8r synthetic recall
ladder, mem-31kz forward capture) are the campaign skill's territory —
status parked/live per the bead store, not dead (PROVISIONAL pending
Stephanie Q5: parked-not-dead framing).

## 2. The replay-engine null — mem-7q6e

**Symptom.** After mem-qarg, 4 dashboard bundles (`zhy00`, `8n3to`,
`ytvbs`, `e9y0d`) looked recoverable by two `replay.py` levers, which would
have lifted N from 9 to 13. An Explore-agent hypothesis scoped a bead to
implement them.

**Root cause — both levers are UNSOUND** (docs/mem-7q6e-replay-engine-null.md,
2026-06-16; no change was made to `memory-bench/membench/bundle/replay.py`):

- **Lever A (sequential-edit rebasing; zhy00/8n3to/ytvbs):** the failing
  calls are middle edits whose anchors exist in neither the base commit nor
  any prior edit's output — the agent edited against a file state the
  timestamp-approximate `base_commit` does not contain. Decisive
  reconstruction of `8n3to` `frontend/src/attention/registry.ts` (base
  `b16f1e36ea`, 9 edits): calls #5 and #8 anchor nowhere; #2 is real edit
  overlap. There is no correct substitution for a missing anchor; forcing
  one **invents an unanchored hunk** — a fabricated gold diff. Note:
  replay-rate ≠ soundness (zhy00 replays at 0.952 and is still broken).
- **Lever B (multi-workspace work_dir inference; e9y0d):** the session
  edited the same files at two filesystem roots (main clone + a sibling git
  worktree). Merging roots requires asserting they share one logical repo
  state and that cross-root edits compose in transcript order — an
  unverifiable topology assumption. Not mechanically sound.

**Evidence.** The doc's per-call anchor-reconstruction table; the real
bundles at `.mem/bundles-qarg/gascity-dashboard-{zhy00,8n3to,ytvbs,e9y0d}.json`
(gitignored data; may not exist in a fresh clone).

**Status.** NULL. **N=9 is the native ceiling** reachable by harness/replay
work (the mem `RigTestConfig` was the only sound growth lever, +1). The
replay engine is behaving correctly by failing these bundles closed. Do not
patch `replay.py` to "recover" bundles; the fix is upstream base-state
fidelity (entry #1). Latent trap recorded there: a heavily cross-root
session can look perfect at `adjusted_replay_success_rate` (it excludes
`OUTSIDE_WORK_DIR`) while its gold diff is incomplete — the soundness gate
is what catches it.

## 3. The ours-substrate data wall — mem-bxhh3

**Symptom.** Running the `ours` arm on the 6 curated codeprobe fail-to-pass
bundles: retrieval coverage **0/6 anchors** with a non-empty lesson-bearing
payload. A paid graded run would have compared `none` vs `builtin` vs a
provably empty `ours`.

**Root cause** (docs/mem-bxhh3-ours-substrate-data-wall.md, 2026-06-23): a
non-empty `ours` payload requires simultaneously (a) the anchor carries real
`trace.errors`, (b) a corpus record carries matching `trace.errors` in its
record JSON, (c) that record carries a lesson, (d) it closed strictly before
the anchor and is not LOO-excluded. The codeprobe corpus was never
trace-resolved / lesson-distilled at scale before Claude Code's ~6-week
JSONL pruning window closed, so the failure-signature substrate does not
exist **and cannot be regenerated** for these commits. Only 3 codeprobe
records anywhere on disk carry both trace_errors and a lesson, and their
failure domains (ruff lint, collection-time import errors) are disjoint from
the anchors' behavioral-assertion failures.

**Evidence.** `memory-bench/scripts/bxhh3_ours_substrate_probe.py` built the
maximally generous substrate and ran real retrieval, all free: 0/6. The
**positive control** proves the wiring: a probe query mirroring
`codeprobe-v0q4x`'s ruff `I001` signature retrieves that record with its
lesson (`trigger=1 matched=1 items=1 lessons=1 match=signature`). The zero
is substrate barrenness, not a wiring bug.

**Status.** HALTED before any spend, per directive. This is an ingest/data
wall. Do not re-run `ours` on the codeprobe ftp anchors; the doc's
recommendation is that this corpus's productive use is calibration data for
synthetic-task design, not a real `ours` eval anchor.

## 4. The dead gh/PR outcome re-ingest — Decisions 17/18

**Symptom.** Across 5,977 closed records: ~14 have a PR number, ~7 a
commit_sha, exactly **1** an `external_ref`. The merged-PR/CI outcome-lift
headline looks "structurally uncomputable," which tempts a re-ingest of
GitHub outcomes ("the schema is wired, the data must just be missing").

**Root cause** (docs/architecture-decisions.md, Decisions 17 and 18): the
corpus is **direct-to-main** — every record that recorded an integration
branch recorded `main` (364/364 in the store measured at D18). There is no
PR workflow to link. The sparse external-ref count measures an **absent
workflow, not an unrecoverable chain**. More wiring cannot create data that
was never produced.

**Evidence.** D17 (Stephanie, 2026-06-08, bead mem-apg.5) explicitly rules:
"do not re-attempt the gh re-ingest; the source data is absent, not
unwired." D18 (Stephanie, 2026-06-17) re-diagnoses from first principles and
replaces it with the git-native `landed` oracle (`src/ingest/landed.ts`):
dating the named branch tip at session close lifted base-commit resolution
359 → 5,644 records (≈79% of the corpus). The residual limit is in-repo
session concurrency (overlapping windows → `ambiguous-window`, never
guessed); commit-trailer linkage (`src/ingest/commitLinkage.ts`,
docs/mem-outcome-linkage-lever-status.md) sidesteps the window for records
whose landing commits carry the `(work-id)` trailer.

**Status.** INAPPLICABLE by construction, per two standing Decisions. The
headline stays the ablation score-vs-information curve
(env- and label-independent). Do not re-litigate; a change here requires a
new numbered Decision (route via
mem-decision-ledger-and-architecture-contract).

## 5. Judge contamination — the bare-host judge (mem-eacq)

**Symptom.** Every graded number to date, including the grid-ce numbers, was
scored with a host `claude -p` judge that intermittently returned a
code-review `{findings, level}` object instead of the rubric schema — and
those numbers were being cited.

**Root cause.** Host-config contamination of the judge process (the judge
ran on the bare host, inheriting non-eval configuration), exposed by the
mem-eacq variance pilot (docs/mem-eacq-variance-pilot.md, run 2026-07-04).
Independently, the same pilot measured the judge's within-task SD at
**0.0917 — above the 0.05 kill-shot bar** — while diff-sim pooled to 0.0145.
Every judge delta narrated to that point (e.g. the n9 +0.038) sits far
inside the noise band: the minimum detectable effect for the judge at N=4,
k=1 is 0.270, a factor of 7 above the observed delta, and still 0.067 at
N=8, k=5.

**Evidence.** docs/mem-eacq-variance-pilot.md (per-run table, SD table, MDE
tables); docs/csb-validity-port-map.md ("grid-ce and every graded number to
date were contaminated by the bare-host judge (mem-eacq) and were cited
until the variance pilot exposed it"). The scoring crash fix (retry
unparseable draws, fail loud) landed in `cc36b84` with tests; the
contamination itself is the standing validity finding.

**Status.** Doctrine, now enforced in code and design: the LLM judge is
**report-only, never in the pass/fail loop**. `memory-bench/membench/grading/safety_gates.py`
promotes confabulation from `flag` to `void` ONLY when a frozen
κ-calibration set on disk clears the pre-registered FPR≤5% / κ≥0.6 bar — no
κ set exists yet, so authority is `flag`. The recall-ladder ADR
(docs/mem-do8r-recall-ladder-adr.md) carries the judge as "an L3 report-only
column, never the loop's reward." Do not cite any pre-isolation judge
number, and do not put a judge score in a gate. A dedicated judge-isolation
capacity fix needs a real headless OAuth token — a Stephanie credential
call (bead mem-a0cf), still open as of 2026-07-07.

## 6. The zhy00 network-fetch contamination — mem-hp9o

**Symptom.** Retroactive audit question: did any agent run behind the n=8/n=9
headlines consult the rig's origin repository?

**Root cause and finding**
(docs/audits/2026-07-03-headline-network-fetch-audit.md): one run —
`gascity-dashboard-zhy00`, **oracle arm**, in the mem-apg.4 ablation
headline — ToolSearch-loaded WebFetch and successfully fetched
`github.com/gastownhall/gascity-dashboard/pull/91` and `/pull/91/files`: the
very PR the bundle's gold answer derives from. The run exceeded its arm's
information budget with post-hoc answer material: **invalid**. Three further
runs _attempted_ `gh pr view` and got nothing (`gh` not installed in the
image) — valid, but proof that "ship PR #N" task titles actively invite
origin consultation. 135 stream/session files, 10,298 tool_use blocks
scanned; zero URLs in any Bash command.

**Blast radius.** mem-apg.4 (n=9): one of 18 headline runs invalid;
recomputed over the 8 clean pairs the reward span moves −0.007 → −0.020
(qualitative read unchanged: flat-to-negative reward, headline lives on the
efficiency axis). The published n=9 table **must be footnoted or re-issued
as clean n=8** — never cite it bare. **mem-n9 graded headline: CERTIFIED
CLEAN** (all 20 probe-n8L runs), as are the gate probes. All these numbers
remain under the mem-0rrf publication freeze regardless (PROVISIONAL pending
Stephanie Q4 on exact freeze scope).

**Status.** Closed as an audit finding with the vector fixed forward: real
runs now default `network_mode = "allowlist"` (agent hosts + package
registries only — `memory-bench/membench/harbor/task_env.py`,
`probe_gate.py`; first exercised live in the mem-eacq pilot, 15/15 runs
carried it). The `gh` vector is closed by the image. When auditing a new
run set, the audit doc's method section is the template.

---

## Do-not-retry checklist

Before starting work, check the plan against this list. A "yes" means read
the entry above and, if you still believe the negative is wrong, bring NEW
evidence and route through change control — never silently retry.

- [ ] Patching `memory-bench/membench/bundle/replay.py` to recover broken
      bundles or force missing anchors? → #2. NULL; fabricates gold diffs.
- [ ] Growing N by loosening the two-stage admission gate or hand-repairing
      stage-2 rejects? → #1. Rejects are corpus limits, not fixtures.
- [ ] Running the `ours` arm on the codeprobe ftp corpus? → #3. Substrate
      is absent and non-regenerable; HALTED before spend.
- [ ] Re-ingesting gh/PR/CI outcomes to build an outcome-lift headline on
      the real corpus? → #4. Inapplicable by construction (D17/D18).
- [ ] Citing a judge score as a gate, or citing any graded number scored
      before judge isolation? → #5. Judge is report-only; pre-isolation
      numbers contaminated.
- [ ] Citing the mem-apg.4 n=9 table without the contamination footnote? →
      #6. One oracle run WebFetched its own answer; use clean n=8.
- [ ] Citing ANY real-corpus headline number as publishable? → The mem-0rrf
      publication freeze is in force (2026-07-07); numbers appear only with
      validity caveats and the freeze named (PROVISIONAL pending Stephanie
      Q4 on exact scope).

## What is settled-dead vs parked (do not confuse the two)

PROVISIONAL pending Stephanie Q5 — parked items are fenced, not dead:

| Territory                                                       | Classification                                                                                                                                                                      |
| --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| replay.py bundle "recovery" levers A/B                          | **Dead** (unsound, #2)                                                                                                                                                              |
| gh/PR outcome re-ingest on the real corpus                      | **Dead** (inapplicable, #4)                                                                                                                                                         |
| `ours` on the codeprobe ftp anchors                             | **Dead** as an eval anchor; corpus reusable as synthetic-calibration data (#3)                                                                                                      |
| Pre-isolation judge numbers                                     | **Dead** as citations (#5)                                                                                                                                                          |
| Per-worktree base-SHA capture (mem-75t lineage)                 | **Parked/live** — the diagnosed real fix for #1; check bead state                                                                                                                   |
| Forward-capture (mem-31kz), tool-requiring substrate (mem-rk41) | **Parked/live** — campaign levers; check bead state                                                                                                                                 |
| Recall ladder (mem-do8r)                                        | Branch-ready, 6 locks awaiting Stephanie (ADR "Stephanie's locks", 6 numbered items) — descriptive only, not canon (PROVISIONAL pending Stephanie Q3)                               |
| Real-corpus null release call (mem-1fl8)                        | **Resolved 2026-06-18 (Stephanie, option c): kill the write-up call** — no release of the sound-tier null; findings stay captured in beads/docs; the fork re-opens only on her call |

## Helper script

`scripts/check-archaeology-sources.sh` (in this skill's directory) — a
read-only diagnostic that verifies every source document and code path this
chronicle cites still exists and still carries its key verdict strings.
Run it from the repo root when you suspect drift:

```bash
bash .claude/skills/mem-failure-archaeology/scripts/check-archaeology-sources.sh
```

## Provenance and maintenance

Authored 2026-07-07 against branch `main` @ `4e819e1`
(`git -C /path/to/mem branch --show-current && git rev-parse --short HEAD`).
Every claim was verified against the working tree this session by direct
read of the cited docs and code. Volatile facts (bead states mem-a0cf /
mem-1fl8 / mem-0rrf, freeze scope, "N=9 native ceiling") are as of
2026-07-07; provisional markers cite the Phase-1 discovery Q-numbers and
are revisable by Stephanie's real answers.

One-line re-verification commands (run from the repo root):

```bash
# The six source documents still exist:
ls docs/mem-7q6e-replay-engine-null.md docs/mem-bxhh3-ours-substrate-data-wall.md \
   docs/mem-qarg-oracle-repair-wave2.md docs/mem-1eph-oracle-soundness-gate.md \
   docs/mem-eacq-variance-pilot.md docs/audits/2026-07-03-headline-network-fetch-audit.md
# The 8-of-407 / +0.000 / +0.125 figures and the freeze framing (README of record):
grep -n "8 of 407" README.md && grep -n "mem-1fl8" README.md
# D17's "do not re-attempt the gh re-ingest" ruling still stands:
grep -n "do not re-attempt the gh re-ingest" docs/architecture-decisions.md
# Judge authority is still flag-until-κ (report-only doctrine in code):
grep -n "confabulation_authority" memory-bench/membench/grading/safety_gates.py
# Allowlist networking is still the default:
grep -rn '"allowlist"' memory-bench/membench/harbor/probe_gate.py | head -3
# Judge-contamination citation chain:
grep -n "bare-host judge" docs/csb-validity-port-map.md
# Full drift check:
bash .claude/skills/mem-failure-archaeology/scripts/check-archaeology-sources.sh
```
