# Is "bead closed" a true outcome label? (mem-z14cd)

**Status:** measurement complete, exploratory. Reports a rate and a fork; does
not redefine the outcome label — that call is Stephanie's.

mem's premise is that Gas City exhaust is a benchmark rather than a log, because
every work unit carries a verifiable outcome label. Quoting the brief: _"Because
every record carries a real, verifiable outcome, the city's exhaust is a labeled
benchmark, not just a log."_ This measures whether the load-bearing half of that
claim — `closed` means the work landed — survives contact with the corpus.

## Headline

Across the three rigs with enough surviving branches to support a rate, **54–81%
of closed beads whose branch still exists have no content on the integration
branch.** Pooled: **61.5%, 95% CI [55.3%, 67.4%]** (152 of 247 decided).

The rate is real and it is not a mem-only artifact — the generalization question
the bead was filed to answer. mem is **not** the worst case; gpk is.

**But `not landed` is not the same as `falsely closed`, and the gap between them
is the actual finding.** Read the decomposition below before quoting any number
here. The pooled 61.5% is a _content-landing_ rate over a 2.2% sample frame, not
a false-close rate, and it must never be reported as "62% of closes are lies."

## Snapshot (the corpus moves under measurement)

The corpus changed _during_ this work: mem's `main` advanced from `fbc3674` to
`0a568a2` mid-session, and `0a568a2` is the remediation of one of the two
hand-verified gold cases. A rate here is only reproducible against pinned
anchors, so every run records them.

|                |                                                                           |
| -------------- | ------------------------------------------------------------------------- |
| Store snapshot | `/home/ds/projects/mem/.mem/store.db`                                     |
| Run            | `2026-07-15T09:35:40Z`                                                    |
| Reproduce      | `node scripts/measure-false-close.mjs`                                    |
| Full output    | `verify/false-close.<date>.json` (per-branch verdicts + per-ref evidence) |

Pinned integration tips for the powered rigs:

| rig     | `main`     | authoritative remote     |
| ------- | ---------- | ------------------------ |
| gascity | `3cb8d2d4` | `origin/main` `3cb8d2d4` |
| mem     | `0a568a2`  | `origin/main` `76378f8a` |
| gpk     | `e7bd25d4` | `origin/main` `f9b70a5d` |

## Method

For every **closed** bead that still has a branch naming it, decide whether its
change is present on an integration branch, by **content** rather than by ref.

Ref-level tests cannot answer this. `branch merged` is false for a squash-merge;
`git cherry` reports rebased-but-landed commits as unlanded; a stranded branch
and a squash-merged one are indistinguishable by ancestry alone. So the test is
**patch-id equivalence** — git's fingerprint over a diff's content, invariant to
sha, parent, author, date, and message.

The verdict ladder, strongest evidence first (`src/ingest/landedContent.ts`):

| verdict             | meaning                                                                             |
| ------------------- | ----------------------------------------------------------------------------------- |
| `landed-direct`     | branch tip is an ancestor of integration                                            |
| `landed-equivalent` | every content-bearing commit has a patch-id twin (rebase / cherry-pick)             |
| `landed-squashed`   | the branch's _combined_ diff has a patch-id twin in one commit                      |
| `partial`           | some commits landed, some did not                                                   |
| `absent`            | no twin at any granularity                                                          |
| `undecidable`       | git could not be asked — **excluded from the denominator, never counted as absent** |

Three choices carry the result:

- **Bead↔branch join by exact segment-aligned token intersection**, never a
  prefix rule (`src/ingest/falseClose.ts`). No prefix rule survives this corpus:
  gascity's beads are `gc-*` but its branches are `bd-gc-*`; website's are
  `sjai-*`; EnterpriseBench mixes case. Precision comes from intersecting
  candidates against the rig's known closed-id set — across all rigs, **no branch
  matched more than one closed bead**, so the join adds no ambiguity.
- **Both integration refs are consulted, strongest verdict wins.** Neither ref is
  universally right: mem's local `main` is **53 commits ahead** of its remote
  (finalize lands locally, workers never push), while gpk's is **14 behind**
  upstream and CodeScaleBench's **1196 behind**. Measuring against one ref would
  manufacture false closes out of a ref selection. Taking the strongest verdict
  makes the rate a **lower bound** — a ref-selection error can only understate the
  problem, never inflate it.
- **`undecidable` is reported per cause, never folded into `absent`.** An
  unanswerable case is not a negative one. Only 3 of 250 were undecidable in this
  run (all `range-too-large`, on CodeScaleBench). That cause has since been
  removed rather than reduced — see [the CodeScaleBench addendum](#addendum-codescalebench-decided-2026-07-15t2233z-mem-jz93m),
  which decides all 3. `range-too-large` no longer exists as an
  `UndecidableCause`, so a reader grepping the code for it will not find it.

## Per-rig results

| rig               | closed    | joined  | decided | not landed | rate      | 95% CI             | coverage |
| ----------------- | --------- | ------- | ------- | ---------- | --------- | ------------------ | -------- |
| **gpk**           | 1567      | 53      | 53      | 43         | **81.1%** | [68.6%, 89.4%]     | 3.4%     |
| **mem**           | 1649      | 94      | 94      | 60         | **63.8%** | [53.8%, 72.8%]     | 5.7%     |
| **gascity**       | 4150      | 74      | 74      | 40         | **54.1%** | [42.8%, 64.9%]     | 1.8%     |
| EnterpriseBench   | 1509      | 13      | 13      | 3          | 23.1%     | [8.2%, 50.3%]      | 0.9%     |
| codeprobe         | 233       | 4       | 4       | 3          | —         | —                  | 1.7%     |
| gascity_dashboard | 1280      | 4       | 4       | 2          | —         | —                  | 0.3%     |
| scix_experiments  | 418       | 4       | 4       | 1          | —         | —                  | 1.0%     |
| website           | 47        | 1       | 1       | 0          | —         | —                  | 2.1%     |
| CodeScaleBench ‡  | 128       | 3       | 0       | 0          | —         | —                  | 2.3%     |
| **POOLED**        | **11192** | **250** | **247** | **152**    | **61.5%** | **[55.3%, 67.4%]** | **2.2%** |

‡ `decided=0` is this run's result, not a standing property of the rig: its 3
branches were undecidable only because the checker buffered patch text through
Node. A [later re-run](#addendum-codescalebench-decided-2026-07-15t2233z-mem-jz93m)
decides all 3. The row is left as-measured because this table is one pinned run.

Rigs below 20 decided branches are shown but **must not be read as rates** — the
Wilson interval at that n spans ±20 points or worse. Rigs with zero joined
branches (migration_evals, code_intel_digest, tom_swe, …) contribute nothing;
`gc` is skipped as multi-repo.

Only **three rigs** carry a readable rate. The bead asked whether mem's rate
transfers: it does, and mem sits in the middle of the three.

## The finding: `not landed` ≠ `falsely closed`

The mechanical checker proves content absence. It cannot prove a _false close_,
because a false close requires the bead to have **claimed** a landing. Hand
inspection of mem's 60 not-landed beads splits them three ways, and only the
first is a false close:

1. **Claimed and absent — a true false close.** The close note asserts completed,
   shipped work that `main` does not have. Both hand-verified gold cases
   (`mem-zfeys`, `mem-cv06b`) are this. _Neither appears in the numbers above_:
   both were **reopened** after being caught, so they are `open` in this snapshot
   and correctly fall out of the closed population. The rate above rests on 152
   **other** cases.
2. **Disclosed non-landing — an honest close.** `mem-6bsd` closed with:
   `"BRANCH LEFT FOR USER: branch=mem-6bsd-ci-rollup-wiring commit=07b48db … NOT
yet merged into main"`, and explained why (it stacks on the unmerged
   `mem-lt6u`). That is a _truthful_ close, written by the `/focus` Phase 5
   disclosure gate. Counting it as a false close would be the measurement lying,
   not the agent.
3. **No landing obligation at all.** The deliverable was never a main-landing
   change: `mem-75t.12` is a feasibility report closing **BLOCKED**, `mem-31xp` a
   spike, `mem-aju5` an explicitly **HELD** draft. Their branches are docs-only.

Measured on mem's 60 not-landed beads:

|                                                                   | n   |
| ----------------------------------------------------------------- | --- |
| close note **disclosed** the non-landing (honest)                 | 5   |
| close note **silent** — and **most carry no close reason at all** | 55  |
| branch is **docs/markdown only** (no landing obligation)          | 13  |
| branch **touches code**                                           | 47  |

Neither existing field separates these. `task_type` is derived from the bead's
title, not the branch's content, so it misfires: `mem-75t.12` is typed `bugfix`
while its branch is a docs-only feasibility report.

**The decisive structural fact: mem's store never ingests the close reason.** The
record JSON carries `lifecycle.status = "closed"` and no justification text. The
disclosure that distinguishes case 2 from case 1 exists in the bead store
(`bd`'s `close_reason`) and is **dropped at ingest**. mem cannot currently tell an
honest "left for user" close from a false "shipped it" close, because it does not
read the field that says which one it is.

And the majority of closes make no claim at all — they are bare status flips with
an empty close reason. So the premise "the outcome label is self-reported by the
agent that did the work" is _optimistic_: most of the time nothing is reported.

## Recommendation

**`bead closed` alone is not a sound landing label.** Not primarily because
agents lie — the verified-lie cases are rare and both got caught and reopened —
but because the closed population **mixes work with a landing obligation and work
with none**, and the substrate records neither the obligation nor, in most cases,
any claim at all.

On the fork the bead posed:

- **Option (a) — join closure to a landed-commit check before treating it as a
  positive label — is actively harmful, and this measurement is the evidence.**
  It would mark every legitimately-closed spike, feasibility report, held draft,
  and research run as a negative. On mem's frame that is at minimum the 13
  docs-only cases plus an unknown share of the 47 code-touching research
  branches. It converts truthful closes into false negatives.
- **Option (b) — carry a label-confidence field — is the sound one.** Record the
  content verdict (`landed-direct` … `absent`, `undecidable`) plus the ref it was
  decided against, and let downstream eval weight or exclude. `undecidable` must
  stay distinct from `absent`.
- **A third option the bead did not list, and the cheapest real fix: ingest
  `close_reason`.** It already exists in the bead store and already carries the
  `/focus` disclosure literal. Ingesting it costs one field and turns cases 1 and
  2 — a lie and an honest hand-off — from indistinguishable into separable.

Recommended next step, if the rate is judged material: **(b) + ingest
`close_reason`**, then re-measure with obligation held out. Not proposed here.

## Limits — read before quoting

- **Coverage is 2.2%.** Only closed beads with a _surviving_ branch are visible.
  This is a sampling frame, not the corpus. Surviving branches are not a random
  sample of closed beads, and the frame is not quantified. **The rate does not
  generalize to the corpus and must never be reported as a corpus-wide rate.**
- **Survival is not a pure strand marker** — merged branches do survive here
  (34/111 in mem are merged-and-surviving), so the frame is not simply "the
  stranded ones."
- **Patch-id is evidence of landing, not proof of absence.** Work
  re-implemented by hand in a different shape reads `absent`. This inflates the
  numerator by an unmeasured amount.
- **The squash step can err the other way.** `landed-squashed` is granted when
  the branch's _combined_ diff patch-id equals a single integration commit's, and
  it is checked before `partial` on purpose. A coincidental collision on a
  trivial diff (a one-line fix, a version bump) would mark a genuinely absent or
  partial branch as landed, biasing the rate **down**. A patch-id is a SHA-1 over
  the diff, so this is very unlikely for any non-trivial change — but the corpus
  contains trivial commits, and the bias is real even if small. Named here for
  symmetry with the `absent` bias above; the two push in opposite directions.
- **Stranded-and-deleted branches are pruned** and invisible, biasing the frame
  the other way. **This is not theoretical — it happened during this work.**
  `work/mem-ljp8b` was a closed bead with a `partial` landing (1 of 2 commits
  present) and it appears in the run above. Between that run and a verification
  re-run hours later, an external process deleted the branch and its worktree.
  The bead is still closed and still in the store; its branch, and with it the
  unlanded commit, is simply gone — so it silently left the sampling frame
  (mem: 94 → 93 joined). The pruning bias is active, it removes exactly the cases
  of interest, and it means **this rate understates by an amount that grows with
  the delay between the work and the measurement.**
- **gpk's upstream is not consulted** (its `RIG_REPOS` slug names `origin` as
  authoritative, while `upstream/main` is 14 ahead). Measured sensitivity:
  consulting it rescues **2 of 43**, moving gpk 81.1% → 77.4%. Ref selection is
  not what drives the result.
- **CodeScaleBench was unmeasured in this run** — 3 branches, all
  `range-too-large` (its local `main` trails upstream by 1196 commits, so the
  range diff exceeds even V8's string cap). Recorded as a coverage hole, not
  counted. **This limit is now resolved** (mem-jz93m): the cause was the
  checker's own memory ceiling rather than anything about the rig, and the
  [addendum below](#addendum-codescalebench-decided-2026-07-15t2233z-mem-jz93m)
  decides all 3.
- **The decomposition is mem-only** (n=60). `bd` resolves only the local rig's
  beads, so per-rig close-reason analysis would need per-rig dolt access. The
  _mechanical_ rate is cross-rig; the _interpretation_ is measured on one rig and
  is the part most in need of replication.

## Provenance

- Checker: `src/ingest/landedContent.ts` (pure, 22 tests)
- Join + aggregation: `src/ingest/falseClose.ts` (pure, 58 tests)
- Runner (IO only): `scripts/measure-false-close.mjs`

Two join defects were found by the tests and fixed before this run; both had
silently zeroed real rigs. Whole-compound token matching (inherited from
`commitLinkage`, correct for commit _messages_ where whitespace delimits an id)
joined 4 of gascity's 112 branches, because branch names embed the id with no
delimiter. Case-sensitive lookup joined **0 of EnterpriseBench's 1509** closed
beads. After the fix EnterpriseBench joins 13 — independently matching the 13
predicted by hand during recon.

## Addendum: CodeScaleBench decided (2026-07-15T22:33Z, mem-jz93m)

The run above left one coverage hole: CodeScaleBench's 3 branches were
`range-too-large`. That cause has since been **removed rather than reduced**, so
this addendum closes the hole.

**It was never a fact about the rig.** The checker read a range's full patch text
into a JS string and piped it straight back into `git patch-id` unread — 574.5 MB
buffered per mem run to produce 2.0 MB of ids. CodeScaleBench's range ran past
V8's ~512 MB string cap, so the checker could not answer. The fix (mem-jz93m)
keeps the patch text in the kernel: `git log -p … | git patch-id --stable`, with
only ~82 bytes/commit crossing back into Node. Measured during the re-run below:
node RSS held **flat at 85.7 MB** while the `git log` stage alone reached
**854.7 MB** — the old failure, reproduced on the producer side, where it no
longer matters. `range-too-large` is gone from `UndecidableCause`, along with the
`isOutputTooLarge` / `ENOBUFS` / `ERR_STRING_TOO_LONG` handling that served it.

|                |                                                              |
| -------------- | ------------------------------------------------------------ |
| Store snapshot | `/home/ds/projects/mem/.mem/store.db` (unchanged)            |
| Run            | `2026-07-15T22:33:57Z`                                       |
| Reproduce      | `node scripts/measure-false-close.mjs --rigs CodeScaleBench` |
| Pinned tips    | `main` `90c5c11b` · `origin/main` `0d5c8804`                 |
| Wall clock     | 1m27s                                                        |

| rig            | closed | joined | decided       | not landed | rate          | coverage |
| -------------- | ------ | ------ | ------------- | ---------- | ------------- | -------- |
| CodeScaleBench | 128    | 3      | **3** (was 0) | 3          | — (3 decided) | 2.3%     |

All 3 are `absent`, each a 1-commit branch decided against local `main`:
`co-7ac-postrun-epilogue`, `co-7ow-harness-retry-policy`,
`feature/co-tuu-token-rollups`. Zero undecidable, no causes remaining.

**Read this as coverage, not as a rate.** 3 decided is far below the 20-branch
power floor, so the 100% is not a rate and its Wilson interval [43.8%, 100.0%]
spans most of the range. What the addendum establishes is that the rig is
_measurable_, not what its rate is.

**The pooled row above is deliberately NOT restated.** Splicing these 3 verdicts
into it would mix two runs decided against different integration tips at
different times, and §Snapshot's whole point is that a rate is reproducible only
against pinned anchors — the spliced table would answer to no single command. For
the record, the arithmetic is small and the direction is _away_ from
overstatement: 152/247 → 155/250 moves 61.5% to 62.0%, inside the published CI
of [55.3%, 67.4%]. It does not touch the finding.

Two things this addendum does **not** claim:

- **Not a speedup.** The re-run still took 1m27s; git-side generation time is
  unchanged. The ceiling removed is memory, not time.
- **Not immune to the pruning bias.** CodeScaleBench's frame happened to be
  identical across the two runs (128 closed, 3 joined, 2.3% coverage), so nothing
  was pruned here between them. That is a fact about these two runs, not a
  refutation of the bias documented in §Limits.
