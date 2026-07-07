---
name: mem-git-and-dispatch-workflow
description: >-
  How change is gated and shipped in the mem rig: the direct-to-main no-PR
  push model and its pre-authorization boundary, the mem-0rrf publication
  freeze on headline/real-corpus numbers, HALT-branch-ready change classes
  that stop for Stephanie's sign-off, branch/worktree/commit conventions,
  the recurring mol-focus-review finalize-gap wedge and its recovery runbook,
  and the .gc-reports audit cadence. Load when deciding whether a change may
  be pushed, merged, or must halt branch-ready; when a number is asking to be
  published; when a bead convoy is wedged; or when reading rig process state.
  NOT for building or running the code — use mem-build-test-env; NOT for what
  counts as scientific evidence — use mem-research-methodology-and-evidence-bar;
  NOT for the settled research negatives — use mem-failure-archaeology; NOT for
  design invariants — use mem-decision-ledger-and-architecture-contract.
---

# mem — Git and dispatch workflow

How change moves in this repo: who may push what, which numbers are frozen,
which change classes must halt for a human decision, and how the agent-fleet
dispatch machinery around the repo fails and recovers.

> **INTERNAL-ORCHESTRATION NOTICE.** Sections tagged `[internal-orchestration]`
> describe the Gas City agent-fleet machinery (beads/`bd`, `gc` dispatch,
> `mol-*` formulas, `.gc-reports/`) that exists only on the maintainer's
> operator install. This is the ONLY skill in this library that documents that
> machinery. If you are reading a public clone of this repo with no `bd`/`gc`
> on PATH, the tagged sections are context, not runbook; everything untagged
> applies everywhere.

## When NOT to use this skill

| You actually want                                       | Use instead                                                 |
| ------------------------------------------------------- | ----------------------------------------------------------- |
| Build both halves, run CI gates locally                 | `mem-build-test-env`                                        |
| The evidence bar (oracle soundness, paired deltas, CIs) | `mem-research-methodology-and-evidence-bar`                 |
| Why an experiment is fenced off as a settled negative   | `mem-failure-archaeology`                                   |
| The Decision ledger and load-bearing invariants         | `mem-decision-ledger-and-architecture-contract`             |
| Rebuilding the store / ingest mechanics                 | `mem-store-schema-and-rebuild`, `mem-ingest-and-provenance` |
| Running the eval end-to-end                             | `mem-eval-harness-run`                                      |

## Jargon (defined once)

| Term                                      | Meaning here                                                                                                                                          |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **rig**                                   | This repo as operated by the Gas City agent fleet: a project with a bead work-queue (`.beads/`, bead prefix `mem-`) and worker agents.                |
| **bead**                                  | One work item in the dolt-backed queue, id like `mem-x5d3`. Read with `bd show <id>` (read-only).                                                     |
| **branch-ready**                          | Work complete on its own branch, tests included, NOT merged to `main`. The stopping state for gated changes.                                          |
| **HALT-branch-ready**                     | Standing instruction on gated work: stop at branch-ready and wait for Stephanie's sign-off before merge/push.                                         |
| **publication freeze / publication-held** | Standing hold on releasing result numbers (see §4). Numbers go to the maintainer per-action; they are never pushed or published.                      |
| **mol-focus-review**                      | `[internal-orchestration]` A fleet formula ("molecule") that wraps a review convoy around a work bead. Its finalize step is the recurring wedge (§6). |
| **PL / mayor**                            | `[internal-orchestration]` Fleet roles: the rig's project-lead agent and the city-level coordinator agent.                                            |
| **rollup bead**                           | `[internal-orchestration]` The PL's periodic self-audit heartbeat bead, label `rollup`, title `Rollup(mem): …`.                                       |

## 1. The shape of change here (verified 2026-07-07)

- **Solo maintainer + agent fleet.** `main` authorship is 391 commits `sjarmak`
  - 3 `Stephanie Jarmak` (394 total). Workers do the typing; the maintainer
    owns every gate.
- **No PR ceremony.** The repo's own docs state it:
  `docs/prd-openrath-incorporation.md` line 4 — "Solo-dev project — land
  direct to main, no PR ceremony. Result numbers HELD under
  publication-freeze." There is no PR/CI-merge oracle in this repo's own
  history either (Decisions 17/18 in `docs/architecture-decisions.md`).
- **Work lands from bead branches.** Branch naming: `mem-<bead>-<slug>`
  (e.g. `mem-0rrf.8-diff-sha-sanitizer`). Integration is squash-style single
  commits for most work, plus explicit merge commits titled
  `merge: mem-<bead> <summary>` (66 merge commits on `main` as of
  2026-07-07, per `git rev-list --count --merges HEAD` — always count with
  `rev-list --count`, never by eyeballing a piped listing, which truncates).
  The real history — dead ends, review threads, reverted attempts — lives in
  worker branches and bead threads, NOT in `main`'s squashed log. 118 local
  branches exist, 75 not merged to `main` (2026-07-07; drifts daily).
- **Commit subjects are conventional.** `type(scope): subject (mem-XXXX)` —
  observed types feat/fix/docs/refactor/test/chore/style/perf; dominant
  scopes `bench`, `memory-bench`, `ingest`, `grading`, `store`, `cli`,
  `parse`, `distill`. 189 of the 394 `main` subjects carry a bead id in
  parentheses; put the driving bead id in yours. No attribution trailers
  (none in recent history; keep it that way).
- **Worktrees, not shared-root editing.** Fleet work runs in per-bead git
  worktrees at `../mem-<bead>` (87 registered worktrees on the operator
  machine, 2026-07-07). Do not run more than one working session in the
  shared root `/home/ds/projects/mem` — see §6 prevention rules.
- **Quality gates before "done"** (both halves; details in
  `mem-build-test-env`): TypeScript `npm run check`; Python (`memory-bench/`)
  `ruff check` + `black --check` + `mypy --strict` + `pytest`. Tests ship in
  the same commit as the change they cover.

## 2. Push authorization matrix

`[internal-orchestration]` The pre-authorization below is Stephanie's standing
ruling of 2026-06-19 (recorded in the operator workspace's standing rules); the
in-repo corroboration is the "land direct to main, no PR ceremony" line in
`docs/prd-openrath-incorporation.md`. When in doubt, treat an action as
per-action gated.

| Action                                                                                         | Gate                                                                                                                                                                |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Push branch-ready worker **code** (rig code, tests, docs) direct to `origin/main`              | **Pre-authorized** (Stephanie 2026-06-19) — no per-action approval needed, PROVIDED the change is not in a HALT class (§5) and no main-freeze window is active (§4) |
| Push a worker branch (no merge to main)                                                        | Pre-authorized (it is how branch-ready work waits)                                                                                                                  |
| Push **data / results / comparison numbers** (any artifact whose content is a measured number) | **Per-action approval, always** — explicitly carved out of the 2026-06-19 pre-authorization                                                                         |
| Publish / release / cite a headline or real-corpus number anywhere                             | **Frozen** — see §4. Maintainer release call, never an agent action                                                                                                 |
| Merge a change in a HALT class (§5)                                                            | **Stephanie sign-off first**; stop at branch-ready                                                                                                                  |
| Force-push to a shared ref; delete a shared branch                                             | Per-action approval, always                                                                                                                                         |
| Any external artifact (upstream PR, issue comment, release, external comms)                    | Per-action approval, always                                                                                                                                         |

Never route around a gate by relabeling the artifact (e.g. committing a
results table as "docs"). The gate follows the content, not the file type:
`docs/prd-openrath-incorporation.md` risk #16 mandates every emitted artifact
be classified DIAGNOSTIC or RESULT, default HELD when unclassified.

## 3. Decide-before-you-push checklist

Run through this before any merge/push to `main`:

1. Does the diff touch a HALT class (§5 table)? → stop at branch-ready,
   surface for sign-off.
2. Does the artifact contain a measured number (lift, pass-rate, token/cost
   delta, N, CI)? → publication-held (§4): number goes to the maintainer
   per-action; the artifact does not push.
3. Is a main-freeze window active (§4, last bullet)? Check the newest
   `.gc-reports/audit-*.md` and open rollup beads before merging ANYTHING.
4. Gates green in the touched half (`npm run check` / the four Python gates)?
5. Tests in the same commit as the fix?
6. Commit subject `type(scope): … (mem-<bead>)`?

If all six pass and no gate applies, direct-to-main is the normal path.

## 4. The publication freeze (mem-0rrf)

**What it is.** A standing hold on releasing result numbers, referred to
across the repo as "the mem-0rrf publication freeze". **This section is the
single operational home for the freeze** (mem-research-methodology-and-
evidence-bar Gate 6 states the evidence doctrine and defers status/scope
here). Primary sources, verified 2026-07-07:

- Bead `mem-0rrf` (P1, BLOCKED as of 2026-07-02): the path-a warm-vs-cold
  A/B run spec states "Results PUBLICATION-HELD -> numbers to mayor
  per-action, NOT pushed."
- `docs/mem-do8r-recall-ladder-adr.md`: "the tree stays under the mem-0rrf
  publication freeze."
- `docs/mem-72sj-gate0-nonflat-probe.md`: "Numbers HELD (publication
  freeze), branch-ready, not pushed."
- `docs/mem-eacq-variance-pilot.md`: "All numbers in this document are
  publication-held pending mem-pl review."
- `docs/prd-openrath-incorporation.md`: "Result numbers HELD under
  publication-freeze."

**Scope — PROVISIONAL pending Stephanie (discovery Q4).** State it
conservatively: the freeze covers **all headline and real-corpus numbers**
(lift numbers, pass-rates, cost deltas, the ablation-curve headline, the
sound-tier N=8 null, warm-vs-cold results). The exact boundary — whether
operational diagnostics such as agreement rates are inside it — is an open
question the repo itself flags (`docs/prd-openrath-incorporation.md` Open
Decision 5) and is Stephanie's to confirm. Until she rules: default HELD.

**A known collision fact (verify before relying on it):** the bead record
also shows a 2026-07-03 ruling relayed as "freeze LIFTED" for the
recall-ladder-vs-warm-cold priority collision (bead `mem-5o3b` close reason,
`mem-4omu`). That relay and the HELD framing in the docs above coexist in
the repo; the conservative reading (all headline/real-corpus numbers HELD)
is binding until Stephanie states the exact current scope.

**Operational rules under the freeze:**

- A number may appear in a branch-held doc WITH its validity caveats and the
  freeze named (the pattern every `docs/mem-*.md` result doc follows). It may
  not be pushed to `main` as a result, cited externally, or presented as
  publishable.
- The existing real-corpus headline is a **diagnosed-ceiling null** (`ours`
  +0.000, `builtin` +0.125, N=8 of 407 scorable). Its release fork was
  **resolved 2026-06-18 (Stephanie, bead `mem-1fl8` NOTES): option (c), kill
  the write-up call** — no release, findings stay captured in beads/docs for
  when reporting time comes; the fork re-opens only on her call. Do not
  represent the result as shipped, abandoned, or still awaiting a decision.
- `[internal-orchestration]` **Main-freeze windows.** While a
  publication-held experiment is measuring the live tree, the PL can freeze
  `main` entirely so mid-experiment merges don't contaminate the A/B.
  Dated example: `.gc-reports/audit-2026-07-02.md` — "Code frozen on `main`
  since 2026-07-01 for the publication-held warm-vs-cold A/B"; hygiene
  findings (`mem-6bsd`, `mem-rhlv`) were filed and deliberately HELD, not
  dispatched, because they touched the measured path. Merges resumed by
  2026-07-03. A freeze window is declared in the audits/rollups, not in git —
  read the newest audit before merging.

## 5. HALT-branch-ready change classes

**PROVISIONAL pending Stephanie (discovery Q4) — conservative list.** Any
change in these classes stops at branch-ready (tests included, unmerged) and
waits for Stephanie's sign-off:

| Change class                                                                                                               | Why gated                                                                    | Where the doctrine lives           |
| -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ---------------------------------- |
| Temporal LOO / leak-safety (TS `closedBefore`, `src/retrieve/exclusions.ts`, Python `leak_guard`)                          | Weakening any exclusion structurally leaks answers; invalidates every number | `mem-temporal-loo-and-leak-safety` |
| Oracle soundness / validity gate (gold-reproduces AND empty-fails)                                                         | No task counts without it; it is the evidence bar itself                     | `mem-grading-and-validity-gates`   |
| The ablation headline metric or its curve code                                                                             | It IS the headline; changing it is an eval-design fork                       | `mem-grading-and-validity-gates`   |
| Anything producing or altering a publishable number                                                                        | Publication freeze, §4                                                       | this skill                         |
| Repo-map / repin changes                                                                                                   | Q4 provisional item — treat as gated until Stephanie scopes it               | this skill (PROVISIONAL)           |
| Distill / judge spend (model-invoking runs; the judge-token capacity item is a Stephanie credential call, bead `mem-a0cf`) | Spend + judge-isolation doctrine                                             | `mem-grading-and-validity-gates`   |

The pattern is live practice, not invention — verified audit language:
`.gc-reports/audit-2026-06-29.md` bakes "HALT branch-ready, yield number
publication-held" into a dispatched bead; `.gc-reports/audit-2026-07-02.md`
dispatches work "branch-held, numbers publication-held, HALT branch-ready";
`docs/mem-do8r-recall-ladder-adr.md` is the canonical artifact shape — a full
design, branch-ready, "Every DECISION below is Stephanie's to accept or
redirect."

**What HALT-branch-ready means operationally:** finish the work on its
branch; ship the tests with it; run the gates green; do NOT merge or push to
`main`; write the result doc with caveats + freeze named; surface the
decision (fleet: via the PL/mayor ledger; human session: tell Stephanie
directly). Then stop.

## 6. `[internal-orchestration]` The mol-focus-review finalize-gap wedge

The rig's recurring operational failure (fired 3x on 2026-07-06 alone:
rollups `mem-0q8f`, `mem-sxx4`, `mem-nkvx`; again 2026-07-07: `mem-0890`).

**Signature.** A `mol-focus-review` convoy finishes its real work — review
PASSED, commit landed, work bead closed — but the molecule's "Finalize
workflow" step is routed to `core.control-dispatcher`, which never fires it.
The root bead (title `mol-focus-review`) wedges IN_PROGRESS forever: a
"finalize-gap zombie". No work is lost; only scaffolding is stuck.

**Root cause** (bead `mem-cvn3`, mayor-owned, open since 2026-06-19): the
dispatch path lacks (1) an idempotency guard — one open wrapper per target
(the `ltte.5` incident spawned THREE concurrent wrappers racing in the same
checkout) — and (2) a per-target worktree pin.

**Detect** (read-only):

```bash
cd /home/ds/projects/mem
bd list --title-contains "Finalize workflow" --status open,in_progress --no-pager
bd list --title-contains "mol-focus-review" --status in_progress --no-pager
bd list --label rollup --status open --no-pager -n 10   # PL wedge reports
```

**Recover** (the verified pattern from `mem-0q8f`/`mem-fcds` close reasons):

1. Identify the underlying work bead (the wrapper's target, named in the
   convoy) and VERIFY the deliverable landed: review verdict PASS, commit
   sha present on `main` or branch-ready in its worktree, work bead CLOSED.
2. Only then force-close the scaffolding beads (the `Finalize workflow` step
   and the `mol-focus-review` root), citing the landed commit sha in the
   close reason. Never close scaffolding before verifying the commit — the
   wedge and "work actually lost" look identical from bead state alone.
3. If the deliverable did NOT land, it is not a finalize-gap wedge; treat it
   as a real stall and diagnose (see the newest `.gc-reports` audit for the
   current dispatch state).

**Prevent / route around:**

- Until `mem-cvn3` lands its guards: never run more than one working session
  in the shared root `/home/ds/projects/mem`; focus-review work for bead B
  belongs in B's worktree `/home/ds/projects/mem-B` (both rules from
  `mem-cvn3`).
- Before attaching a `mol-focus-review` wrapper to target T, check no OPEN
  wrapper for T already exists (the missing idempotency guard, done by hand).
- **Direct-dispatch around the wedge class:** for work that does not need the
  review-convoy scaffolding, sling the bead directly to a worker instead of
  attaching the molecule — the finalize step that wedges then never exists.
  The wedge is scaffolding-only, so this trades automated review packaging
  for dispatch reliability; keep review itself (the gates in §3) either way.

## 7. `[internal-orchestration]` .gc-reports audit cadence

`.gc-reports/audit-YYYY-MM-DD.md` are PL-synthesized DEEP_AUDIT reports from
read-only audit agents. Cadence (verified against the 8 files present,
2026-06-15 → 2026-07-03): weekly, PLUS stall-triggered runs whenever the rig
has backlog with nothing moving ~24h (the 07-02 and 07-03 files are
stall-triggered and open with the WHY diagnosis).

Use them as the rig's process ground truth: current freeze windows (§4),
held findings, dispatch blocks, and gate state ("Gates verified GREEN this
run") all live there. Read the newest one before planning or merging work:

```bash
ls /home/ds/projects/mem/.gc-reports/ | sort | tail -1
```

## 8. Repo hygiene facts (verified 2026-07-07)

- The repo root contains **60 `mem-*/` directories holding only agent
  session state** (`.claude/`, `.gc/` — no source). They are leftovers from
  fleet sessions, not worktrees and not source. Ignore them; never commit
  them; do not "clean them up" as a side effect of other work (an active rig
  may have live sessions).
- Real worktrees are siblings: `/home/ds/projects/mem-<bead>` (87 registered
  via `git worktree list`).
- 75 local branches are unmerged (2026-07-07). Per discovery Q5 (PROVISIONAL pending
  Stephanie): treat unmerged branches, the `research/` reranker track, and
  the `#planned` controller/OpenRath work as **parked, not dead** — check
  bead + branch state before building on or deleting anything.
- `.beads/` is the live dolt-server-backed queue. Standard rig rule: never
  run `bd dolt start|stop|status` here — it kills the shared city server.
- `.mem/store.db` is gitignored and rebuildable; it is never a git artifact.

## 9. Helper script

`scripts/rig-process-state.sh` — read-only one-shot: branch/HEAD, unmerged
branch count, newest audit, open rollups, wedge detection. Safe to run any
time; degrades gracefully when `bd` is absent (public clone).

```bash
bash /home/ds/projects/mem/.claude/skills/mem-git-and-dispatch-workflow/scripts/rig-process-state.sh
```

## Provenance and maintenance

Authored 2026-07-07 against `/home/ds/projects/mem`, branch `main`, HEAD
`4e819e1` (checkout was on `main`). Facts marked PROVISIONAL depend on
discovery Q4 (freeze scope + HALT list) and Q5 (parked-not-dead); the
internal-orchestration scoping follows discovery Q1. Volatile counts (60/87/
75/394/66, freeze-window state, wedge beads) are 2026-07-07 snapshots.

Re-verify before trusting:

```bash
git -C /home/ds/projects/mem branch --show-current && git -C /home/ds/projects/mem rev-parse --short HEAD
git -C /home/ds/projects/mem rev-list --count HEAD                     # main commit count (was 394)
git -C /home/ds/projects/mem rev-list --count --merges HEAD            # merge commits (was 66)
git -C /home/ds/projects/mem branch --no-merged main | wc -l           # unmerged branches (was 75)
git -C /home/ds/projects/mem worktree list | wc -l                     # worktrees (was 87)
ls -d /home/ds/projects/mem/mem-*/ | wc -l                             # root session-state dirs (was 60)
ls /home/ds/projects/mem/.gc-reports/ | sort | tail -1                 # newest audit (was audit-2026-07-03.md)
sed -n '4p' /home/ds/projects/mem/docs/prd-openrath-incorporation.md   # direct-to-main + freeze line
grep -n "publication freeze" /home/ds/projects/mem/docs/mem-do8r-recall-ladder-adr.md
bd show mem-0rrf | head -5; bd show mem-cvn3 | head -5                 # freeze bead + wedge root cause (operator install only)
```
