---
name: mem-orientation
description: >
  Zero-context orientation for the mem repo: what the project is (agentic
  memory benchmarked on a real multi-agent orchestration corpus), the
  TypeScript-store / Python-harness split, the
  ingest→parse→store→retrieve→distill→benchmark pipeline, which directories
  are source vs stale agent-session residue, and the fastest reading route
  through the docs of record. Load this FIRST in any fresh session on this
  repo (sjarmak/mem, the Gas City mem rig), before touching code, when asked
  "what is mem", "how is this repo organized", "where do I start", or
  when a path like src/, memory-bench/, or a root mem-* directory is
  confusing. NOT for running the eval (use mem-eval-harness-run), building or
  rebuilding the store (use mem-store-schema-and-rebuild), ingest mechanics
  (use mem-ingest-and-provenance), why a design decision was made (use
  mem-decision-ledger-and-architecture-contract), or what was already tried
  and failed (use mem-failure-archaeology).
---

# mem-orientation

The map before you touch anything. Read this top to bottom once (~10 minutes),
run the checklist at the end, then load the sibling skill that matches your
actual task.

## What mem is

`mem` is a **research benchmark**, not a product. A multi-agent orchestrator
running across eighteen project rigs left behind a large audit trail: 6,691
work records and 874 resolved agent-session transcripts (corpus figures as
stated in `README.md`, 2026-07-07). `mem` turns that audit into a queryable
**work-audit graph**, then benchmarks whether retained, parsed, retrieved
memory **measurably improves future agent work** (success rate, iterations,
cost) and which retention/retrieval strategies win.

**The thesis:** work records beat session prose as a memory corpus. Every
record carries a lifecycle label (created/started/closed) and a full trace of
how it got there, so the labels come from work that actually happened, not
synthetic tasks.

**The central deliverable:** a benchmark whose numbers _survive a skeptic_.
Concretely that means oracle soundness (a task counts only if its gold diff
reproduces AND an empty diff fails) plus paired per-task deltas with bootstrap
confidence intervals that exclude zero. Everything load-bearing in this repo
hangs off eval validity.

## Jargon, defined once

| Term                   | Meaning here                                                                                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **work record / bead** | One work item from the orchestrator's work-item store (the "bead spine"); the atomic unit, keyed by `work_id`                                                                 |
| **WorkRecord**         | The JSON join of a work item + its lifecycle, agents, trace, outcome, signal, and links (see `README.md` §Data model)                                                         |
| **trace**              | A session transcript (JSONL): tool calls, outputs, errors                                                                                                                     |
| **store / sidecar**    | The generated SQLite+FTS5 database at `.mem/store.db` (gitignored; rebuild it, never hand-edit it)                                                                            |
| **oracle**             | The ground-truth label a replayed task is graded against; "sound" = gold diff reproduces and empty diff fails                                                                 |
| **arm**                | One memory system under test in the eval harness (none, oracle, ours, builtin, mem0, ...)                                                                                     |
| **temporal LOO**       | Temporal leave-one-out: retrieval sees only records closed strictly before the target started, with sibling exclusions; THE eval-validity invariant                           |
| **Harbor**             | The containerized execution substrate that replays held-out tasks (harbor-framework/harbor)                                                                                   |
| **ZFC boundary**       | Zero Framework Cognition: mechanical signal is parsed in code (`src/parse/`), semantic judgment is delegated to a model (`src/distill/`, semantic annotation); never mix them |
| **distilled lesson**   | A model-produced, append-only, citation-carrying summary of a prior resolution; what the `ours` arm injects                                                                   |
| **rig**                | One project the orchestrator manages; each rig contributes records to the corpus                                                                                              |

## The two halves

One repo, two independent projects, two CI jobs (`.github/workflows/ci.yml`).
There is no `make`.

|             | Store builder                                      | Eval harness                                                                                       |
| ----------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Language    | TypeScript (Node >= 18)                            | Python >= 3.12                                                                                     |
| Path        | `src/` (+ `bin/`, `tests/`)                        | `memory-bench/` (package `membench`)                                                               |
| Install     | `npm ci`                                           | `cd memory-bench && pip install -e ".[dev]"`                                                       |
| Build       | `npm run build` (tsc → `dist/`)                    | n/a                                                                                                |
| Gate        | `npm run check` (tsc + eslint + prettier + vitest) | `ruff check membench tests && black --check membench tests && mypy --strict membench && pytest -q` |
| Tests       | 43 `*.test.ts` files under `tests/`                | 157 test files under `memory-bench/tests/`                                                         |
| Entry point | `./bin/mem` (CLI)                                  | `python3 -m membench.cli`                                                                          |
| Produces    | `.mem/store.db` (schema v11)                       | run reports, graded numbers                                                                        |

Counts verified 2026-07-07. Deeper build/env detail, including optional-dep
skip semantics: sibling **mem-build-test-env**.

**Day-one trap #1:** `./bin/mem` executes `../dist/main.js`, not `src/`.
Edit TypeScript without `npm run build` and the CLI silently runs stale
compiled code. Build first, always. (`node dist/main.js` alone does nothing;
the entrypoint is `./bin/mem`.)

## The pipeline

Ingest → parse → store → retrieve → distill → benchmark. The first five
stages are the TypeScript half; the last is the Python half.

| Stage     | Module                   | What it does                                                                                                                                                                                                                                         |
| --------- | ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ingest    | `src/ingest/`            | Pure IO readers: bead spine, trace resolve (session id → JSONL), git provenance (session-start `base_commit`), git-native `landed`/commit-linkage oracle, rig→repo map                                                                               |
| Parse     | `src/parse/`             | Two extractors, strictly separated (the ZFC boundary): deterministic (runner matching + format-anchored `file:line` error extraction, no keyword heuristics) and semantic (model, once per record, append-only)                                      |
| Store     | `src/store/`             | The work-audit graph: SQLite+FTS5, `SCHEMA_VERSION = 11` (`src/store/schema.ts:28`), projections rebuilt from `work_records.record` JSON; three append-only non-regenerable tables (`lessons`, `memory_events`, producer-source `provenance_events`) |
| Retrieve  | `src/retrieve/`          | Failure-triggered retrieval v1 (keys on normalized `file:line` + error class) with temporal-LOO exclusions enforced at read time; progressive disclosure (`index` → `details --pick` → `full`)                                                       |
| Distill   | `src/distill/`           | The only model-invoking module in the store half: prior resolutions → retrievable lessons with citations                                                                                                                                             |
| Benchmark | `memory-bench/membench/` | Replays held-out tasks under `no_memory` / `oracle_memory` / `memory_enabled` on Harbor; competitive arms behind one uniform interface; grading gates + metrics                                                                                      |

CLI surface (verified via `./bin/mem --help`, 2026-07-07): `build-store`,
`ingest-traces`, `rebuild`, `coverage`, `query`, `retrieve`,
`distill-lessons`, `lessons`, `link-outcomes`, `provenance`, `signature`,
`search-errors`, `extract-errors`, `memory-event`, plus the
`export-/import-` pairs for the three append-only tables.

Harness CLI (verified in `memory-bench/membench/cli.py`): `run-sequence`,
`gen-tasks`, `replay`, `curate-ftp`. How to actually run them: sibling
**mem-eval-harness-run**.

**Day-one trap #2:** `mem build-store --with-traces` resolves transcripts by
shelling `gc session logs`, which loads `city.toml` from the **current
working directory**. Run from the wrong directory and it exits 0 with zero
traces resolved, no error. Operational precondition: run full rebuilds from
the gas-city checkout (`/home/ds/gas-city` on the machine this rig lives on)
with an absolute `--store` path. [PROVISIONAL pending Stephanie, Q1: this
gas-city-cwd precondition is stated as an operational precondition, not a
load-bearing code path; on a clone without `gc`, spine-only (flagless) builds
still work.] Full ingest mechanics: sibling **mem-ingest-and-provenance** and
the existing in-repo `/ingest-trace-substrate` skill.

## Where the numbers stand (2026-07-07 — read the caveats)

All headline and real-corpus numbers are held under the **publication freeze
(bead `mem-0rrf`)**. None of the figures below is publishable; they are
internal orientation only, and releasing any of them is a Stephanie decision.
(The one release fork put to her so far, bead `mem-1fl8`, was **resolved
2026-06-18: kill the write-up call** — findings held in beads/docs; any
future release is a fresh call of hers.) [PROVISIONAL pending Stephanie, Q4:
freeze scope stated conservatively as covering all headline/real-corpus
numbers.]

- **Real corpus:** signal-poor and largely non-replayable by construction.
  The corpus is direct-to-main (no PR/CI workflow to link), so a merged-PR/CI
  oracle is inapplicable (Decisions 17/18). The recovered-oracle 3-arm graded
  eval is a **diagnosed-ceiling null**: `ours` +0.000, `builtin` +0.125, with
  only 8 of 407 commit-linkage-recovered oracles scorable. N is bound by
  replay/oracle fidelity, not by method. This is a settled, diagnosed result,
  not an open bug; do not burn a session re-fighting it (sibling
  **mem-failure-archaeology** chronicles the wall and every fenced-off dead
  end).
- **Headline metric:** the ablation score-vs-information curve (the agent is
  its own control across an information ladder). A recall-ladder redesign
  exists as a branch-ready ADR (`docs/mem-do8r-recall-ladder-adr.md`) with
  locks awaiting Stephanie; it is NOT the current design. [PROVISIONAL
  pending Stephanie, Q3: this skill teaches current reality; nothing future
  is canonized.]
- **Synthetic-world track:** the live frontier and the first measurable
  lift: cross-task continuity 0.062 (isolated stores) → 0.188 (shared
  store), plus live Confusion/Staleness retrieval-quality metrics. Whether a
  synthetic lift generalizes to real city work is an open validity question
  (`mem-bxhh`). Same freeze caveat applies.

## Layout: source vs residue

The repo root is cluttered. Know what is real before you search or read.

**Source (tracked by git):**

```
src/            TypeScript store builder (the first five pipeline stages)
bin/mem         CLI entrypoint (runs dist/ — build first)
tests/          TS tests (vitest)
memory-bench/   Python eval harness (package: membench)
docs/           Decision ledger + ~40 mem-*.md status/gate/null docs + docs/audits/
architecture/   LikeC4 model + exports/orient.md (regenerated daily)
scripts/        Node/shell utilities (ingest cron doc, freeze, verification)
research/       GPU SFT/RL reranker track — parked, not on the CI path
paper/          LaTeX draft — gitignored from the public repo, under the mem-0rrf freeze
infra/, freeze/, verify/, hooks/   supporting artifacts
```

**Generated / local-only (gitignored):** `.mem/` (the store sidecar plus
historical `store-v*.db` snapshots), `.gc/` (internal briefs + the governing
spec), `.gc-reports/` (weekly audits), `.beads/` (live work queue; never run
`bd dolt start|stop|status` here), `.claude/` and `.codex/` (session-local
agent tooling; only `ingest-trace-substrate/SKILL.md` is tracked as an
exception), `dist/`, `node_modules/`. **Packaging caveat for this skill
library itself:** `.gitignore:53` ignores `.claude/` wholesale, so the 15
`mem-*` skills (this one included) are currently untracked and do NOT ship
with a clone — as delivered they exist only on this machine. Shipping them
requires either force-adding each `SKILL.md` (the ingest-trace-substrate
precedent) or a `.gitignore` exception for `.claude/skills/`; that is a
Stephanie packaging decision, unmade as of 2026-07-07.

**Residue to IGNORE — never source:** the repo root holds **60 `mem-*/`
directories** (count verified 2026-07-07, e.g. `mem-04nx/`,
`mem-124m-load-context-and-understand-assignment/`). Every one sampled
contains ONLY session-local `.claude/` and `.gc/` dot-folders, tens of KB, no
code. They are stale agent-session working directories. Never read, cite, or
edit anything under them. The real in-flight branch work lives in git
branches (118 local branches, verified 2026-07-07) checked out as **sibling**
worktrees outside this tree (`~/projects/mem-*` on this machine; `git
worktree list` shows them). [PROVISIONAL pending Stephanie, Q5: residue dirs
and in-flight branches are treated as parked/stale, never declared dead.]

**Search discipline that follows:** scope every search to real source, or the
residue dirs and `.mem/` will pollute results.

```bash
git grep -n "pattern"                      # tracked files only — the safe default
grep -rn "pattern" src/ memory-bench/membench/ docs/   # explicit scoping also fine
```

Never eyeball a truncated root listing; count instead (`ls -d mem-*/ | wc -l`).

## Fastest reading route

In order. Skip nothing on a first pass; each stop is short.

| #   | Read                             | What it gives you                                                                                                                                                     |
| --- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `README.md`                      | The thesis, the direct-to-main caveat, the pipeline, the data model, store-building commands, both invocation traps                                                   |
| 2   | `ARCHITECTURE.md`                | System design: WorkRecord, retrieval, the planned memory controller, the eval harness, the constraints and targeted failure modes                                     |
| 3   | `docs/architecture-decisions.md` | The Decision ledger: 24 numbered rulings (verified 2026-07-07). What must not be re-litigated. Distilled in sibling **mem-decision-ledger-and-architecture-contract** |
| 4   | `architecture/exports/orient.md` | Mechanically derived subsystem map with delivery state (#built/#evolving/#planned/#research) and exact source paths; regenerated daily                                |
| 5   | `memory-bench/README.md`         | The three conditions, the three eval modes, the arms, the metrics, the run commands, the no-paid-API boundary                                                         |

Then, only as your task demands: `.gc/memory-eval-harness-spec.md` (the
governing spec — where the log and the spec conflict, the spec governs;
local-only, gitignored) and the settled-negative `docs/mem-*.md` docs (via
sibling **mem-failure-archaeology**).

**Doc-drift warning (verified 2026-07-07):** prose schema-version mentions
lag reality. `README.md` says "schema v6", `architecture/exports/orient.md`
says "schema v8"; the authoritative value is the code:
`src/store/schema.ts:28` → `SCHEMA_VERSION = 11`. When any doc and the code
disagree on a number, trust the code and note the drift. Same class of drift:
`README.md` points the nightly ingest cadence at `.gc/cron/`, which does not
exist; the cadence doc actually lives at
`scripts/ingest-trace-substrate.cron.md` (verified 2026-07-07).

## First 15 minutes: orientation checklist

All read-only. Run from the repo root.

```bash
# 1. Where am I?
git branch --show-current && git rev-parse --short HEAD

# 2. One-shot orientation diagnostic (script shipped with this skill)
bash .claude/skills/mem-orientation/scripts/orient-check.sh

# 3. Is the TS half built? (required before any ./bin/mem call)
ls dist/main.js 2>/dev/null || echo "NOT BUILT — run: npm ci && npm run build"

# 4. Does a store exist, and what does it hold?
ls -la .mem/store.db 2>/dev/null
./bin/mem coverage --store .mem/store.db        # read-only coverage report (needs dist/)

# 5. What does the CLI expose?
./bin/mem --help

# 6. Read the route (order matters)
#    README.md -> ARCHITECTURE.md -> docs/architecture-decisions.md
#    -> architecture/exports/orient.md -> memory-bench/README.md
```

Do NOT, in your first session: run a benchmark, call anything that spends
model tokens (`mem distill-lessons`, judge paths), rebuild the store, or
touch `.beads/`. Those all have owning siblings with the gates spelled out.

## When NOT to use this skill

This skill is the map only. Route onward:

| Your task                                                                 | Load                                              |
| ------------------------------------------------------------------------- | ------------------------------------------------- |
| What counts as evidence; the validity doctrine                            | **mem-research-methodology-and-evidence-bar**     |
| "Has X been tried?" / any fix to linkage, replay, outcome ingest          | **mem-failure-archaeology**                       |
| Why the system is shaped this way; the 24 Decisions; invariants           | **mem-decision-ledger-and-architecture-contract** |
| Schema details, version bump, `mem rebuild` round-trip                    | **mem-store-schema-and-rebuild**                  |
| Building the store, traces, provenance, coverage axes                     | **mem-ingest-and-provenance**                     |
| The parse layer and the ZFC mechanical/model line                         | **mem-deterministic-extraction-zfc**              |
| Temporal LOO, exclusions, leak-safety in both halves                      | **mem-temporal-loo-and-leak-safety**              |
| Running the eval end-to-end on Harbor                                     | **mem-eval-harness-run**                          |
| Memory arms; adding or debugging an arm                                   | **mem-competitive-arms**                          |
| Grading, gates, the ablation curve, what makes a number defensible        | **mem-grading-and-validity-gates**                |
| Synthetic worlds, NeMo generator, necessity gate                          | **mem-synthetic-world-generator**                 |
| Recreating either environment; CI parity; gate failures                   | **mem-build-test-env**                            |
| Branches, pushes, freeze scope, dispatch process (internal orchestration) | **mem-git-and-dispatch-workflow**                 |
| Attacking the oracle-validity wall (the hardest live problem)             | **mem-oracle-validity-wall-campaign**             |
| The recurring trace-substrate ingest specifically                         | in-repo `/ingest-trace-substrate` (pre-existing)  |

## Provenance and maintenance

Authored 2026-07-07 against `/home/ds/projects/mem`, branch `main`, HEAD
`4e819e1` (checkout was on `main` at authoring time). Every command, path,
count, and version above was verified against that checkout on that date.
Corpus sizes (6,691 / 874) and eval figures are quoted from `README.md` and
the project's own docs as of that commit, all under the `mem-0rrf` freeze.

Re-verify volatile facts before trusting them:

```bash
git -C /path/to/mem rev-parse --short HEAD                     # has the repo moved past 4e819e1?
grep -n "SCHEMA_VERSION" src/store/schema.ts                   # schema version (was 11)
ls -d mem-*/ 2>/dev/null | wc -l                               # root residue-dir count (was 60)
git for-each-ref refs/heads --format='x' | wc -l               # local branch count (was 118)
./bin/mem --help                                               # CLI command surface
grep -cE '^[0-9]+\. \*\*' docs/architecture-decisions.md      # numbered Decisions in the ledger (was 24)
find tests -name '*.test.ts' | wc -l                           # TS test files (was 43)
find memory-bench/tests -name 'test_*.py' -o -name '*_test.py' | wc -l           # Py test files (was 157)
bash .claude/skills/mem-orientation/scripts/orient-check.sh    # all of the above in one pass
```
