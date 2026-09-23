# Pre-registration: the beads capture endpoint

Registered 2026-09-21, before the first capture turn is fired. The three-arm registration
(`docs/prereg-beads-three-arm.md`) stands unchanged except for the one amendment named in its
own §1; this document registers a different question and is not a revision of it.

## 1. The question

**Does a coding agent, told it has beads and asked to record something, actually call `bd`?**

Not whether beads helps. Whether it is reached at all. Four pilots have now returned the same
answer for the shipped text: the beads arm makes **zero tool calls** in its establish leg while
stating in prose that it has recorded the facts. The most recent, with the stream kept
(`results/beads-three-arm-20260921/repilot2.json`, 6 beads cells): *"Acknowledged — noting these
established facts for this session... I have these recorded and ready to act on."* Nothing was
recorded.

A memory system that is never called cannot be evaluated on what it remembers, so capture is the
gate every downstream question sits behind. This registration makes it the measured endpoint and
fixes everything about how it is measured, so that the beads build is the only thing that varies
between turns.

## 2. Subject under test

The **beads (`bd`) memory system**, as a prototype: memory beads have not shipped, so a turn names
a commit on `sjarmak/beads`, not an installed binary. A fire pins it with
`--bd-ref <remote> <sha>` (`membench/runner/bd_ref.py`), which fetches that commit, builds it with
the project's own Makefile, and records the commit and the binary's sha256 in the artifact's
resume identity. A grid cannot straddle two builds, and an artifact cannot name a build its cells
did not run.

Admissible arms, unchanged from the three-arm registration:

| role | arm | what it is |
|---|---|---|
| treatment | `beads` | the pinned build, reached through a shim that pins every call to the cell's own store |
| floor | `none` | no durable store; `bd` resolves to a stub that exits 127 |
| comparator | `builtin` | the agent's own native memory (`MEMORY.md` plus topic files) |

## 3. Endpoints

Two, both per arm, both read from the **establish leg only**.

- **`reached`** — the leg made at least one call to its memory facility. For `beads`, a `bd`
  invocation on the observed argv. For `builtin`, a write to a native-memory path.
- **`engaged`** — the facility accepted a write that stores the established content. For `beads`,
  a receipt row stating the token. For `builtin`, a native-memory file whose content states it.

`reached` without `engaged` is the interesting middle: the agent tried and the tool refused it, or
it called a verb that reads where it meant to write. `bd remember <bare-token>` is a known such
case and is a READ or a refusal, never a write.

**Reported as a point estimate with a 95% confidence interval. No threshold, and no hypothesis
test.** There is no prior worth testing against: the shipped text's capture rate is zero across
four pilots, and the question a turn asks is how far a candidate moves it, not whether it clears
a line drawn in advance.

## 4. What is fixed for the life of the flywheel

Changing any of these ends the series and starts a new one.

**The establish instruction**, verbatim, the same for every arm:

> You are picking up work in this session. The current state of the system is below. Acknowledge
> it; a separate session in this project will ask you to act on it. Record what you have been told
> above, so a later session can recall it.

**The shared protocol** — corpus, tool allowlists, model, timeout, sandbox policy — as
`SharedProtocol` holds it, and `assert_arms_comparable` refuses a registry where the arms differ
in anything but the memory they have.

**The floor's capability paragraph** (35 words):

> ## Durable storage
>
> This session has no durable store. Nothing written during it is carried to a later session: the
> working directory is emptied between sessions, and no facility outside it is available to you.

**The comparator's capability paragraph** (37 words):

> ## Durable storage
>
> You have your own memory. It lives under your configuration directory, as a `MEMORY.md` index
> and the topic files it links, and its contents are available to you in later sessions in this
> project.

**The task sample.** Eight of the 32 `work_id`s in `fixtures/worlds-tool-jev32`, drawn once with
`random.Random(20260921).sample(sorted(ids), 8)` and written down here so the draw cannot be
repeated until it flatters a candidate:

```
world-seed1-task0   world-seed4-task0   world-seed8-task0   world-seed10-task0
world-seed13-task0  world-seed14-task0  world-seed22-task0  world-seed29-task0
```

**The variant.** `necessary` only. The capture question is about a leg that is asked to record,
and the unnecessary variants are not.

**The Claude Code runtime**, pinned at **2.1.278**. A bump is allowed and is not free: it requires
a bridge re-measurement of the floor and comparator on the new runtime before any candidate is
compared across the boundary.

**`versioned-history.enabled` is OFF**, unless a turn's declared variable is that setting.

## 5. What varies between turns

**The beads build, and nothing else.** One turn = one candidate = one `(remote, sha)`.

## 6. Sample per turn

- **Candidate (treatment):** 8 tasks x 2 repeats = **16 sessions**.
- **Floor and comparator:** measured **once per (runtime version) and reused** across every
  candidate on that runtime. They do not depend on the beads build, so re-buying them per turn
  would spend on a constant.
- **Baseline (treatment at the turn's base commit):** measured **once per (bd base sha, runtime
  version)** and reused the same way.

## 7. Validity conditions

A cell is **UNMEASURED**, not a zero, when any of these holds. An unmeasured cell is excluded and
counted; a run whose unmeasured cells form a consecutive streak is a broken rig and halts.

- **Any memory reach on the floor.** It is graded on not having one.
- **Any native-memory reach on a non-comparator arm.** A treatment cell that satisfies the leg out
  of the agent's own memory has not exercised beads. This is a realized failure mode, not a
  hypothetical: an earlier grid scored zero because the agent read the native path. A foreign
  command harness additionally runs with a fresh `HOME` and `XDG_CONFIG_HOME` per cell, seeded
  only with explicitly supplied login material, so its runtime-native files cannot leak across
  cells or write into the operator's home.
- **`bd` resolving to anything but the cell's own shim** on the assembled child PATH, for any arm.
  On the floor that is a store it should not have; on the treatment arm it is a different store
  and an unpinned build.
- **A store that `bd init` did not claim.** An ancestor workspace capturing the mint makes every
  call in the cell reach somebody else's store.
- **A bd schema migration in the stream.** It moves the store out from under the pinned build the
  artifact is identified by.

## 8. Analysis

Per turn, per arm: `reached` and `engaged` counts over the 16 candidate sessions, each as a
proportion with a 95% interval, alongside the reused floor, comparator and baseline. The
comparison that matters is candidate against its own baseline; the comparator says what the same
agent does when the facility is its own, and the floor says what the leg looks like with nothing.

No pooling across turns, and no pooling across runtime versions.
