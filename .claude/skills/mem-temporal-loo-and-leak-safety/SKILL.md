---
name: mem-temporal-loo-and-leak-safety
description: >
  The temporal leave-one-out (LOO) eval-validity invariant of mem — the one
  mechanism that, if weakened, invalidates every benchmark number. Load this
  before touching ANY of: src/store/reader.ts (closedBefore), src/store/timestamp.ts
  (toIsoUtc), src/retrieve/exclusions.ts or retrieval.ts (sibling/supersedes
  exclusions), memory-bench/membench/validity.py (canonical_ts, loo_bounded,
  assert_no_leak), or memory-bench/membench/grading/leak_guard.py (outcome-label
  scan); before adding a memory arm that ingests WorkRecords; when debugging a
  LeakageError or OutcomeLeakError; when a timestamp/format change touches
  lifecycle columns; or when reviewing the TS↔Python parity contract. Also covers progressive-disclosure
  retrieval (mem retrieve --format index|details|full). NOT for running the eval (mem-eval-harness-run), grading
  gates/ablation/judge doctrine (mem-grading-and-validity-gates), the
  deterministic-parse ZFC boundary (mem-deterministic-extraction-zfc), or store
  schema/rebuild (mem-store-schema-and-rebuild).
---

# Temporal LOO and leak safety

The invariant: **when the benchmark evaluates a held-out work item `B`, no
memory arm may see anything that did not exist — or that is the same work as
`B` — at the moment `B` started.** mem's whole thesis ("does memory of past
work improve future work?") is only measurable if the "past" an arm retrieves
from cannot contain `B`'s own answer. This skill is the runbook for that
boundary: where it is enforced in both halves of the repo, why every piece is
shaped the way it is, how to verify it, and how to change it without silently
breaking every number the project reports.

Jargon, defined once:

- **Temporal leave-one-out (LOO)** — Decision 6 (`docs/architecture-decisions.md`):
  when evaluating work `B`, the retrievable set is only WorkRecords **closed
  strictly before `B.started`**, minus records that are "the same work dodging
  the timestamp filter" (self, convoy siblings, supersedes chain, PR/branch
  sharers, epic siblings).
- **Leakage** — any path by which `B`'s outcome (its fix, its PR, its commit,
  its own trace errors' resolution) reaches the agent being evaluated. Two
  distinct paths exist and each has its own guard: the **retrieval path**
  (an arm ingests/returns a record it should not see) and the
  **task-construction path** (an outcome label lands in agent-readable task
  files).
- **Arm** — one memory system under test (`none`, `oracle`, `ours`, `builtin`,
  third-party adapters). Arms implement only retrieve/write; the harness owns
  the corpus and the boundary (Decision 11, `memory_systems/base.py`).
- **Parity contract** — the LOO semantics are implemented twice, once per
  language half, and the two implementations must agree exactly. Marked in
  source with "parity contract, change both".

## 1. The map — one invariant, two halves, four layers

| Layer                | What it blocks                         | TypeScript (store half, `src/`)                                                                                      | Python (eval half, `memory-bench/membench/`)                                                    |
| -------------------- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Temporal cut         | Future records                         | `store/reader.ts` `queryRecords` `closedBefore` (strict `closed_at IS NOT NULL AND closed_at < ?`)                   | `validity.py` `_is_eligible` (`ref.closed is not None and canonical_ts(ref.closed) < boundary`) |
| Canonical timestamps | Mixed-format lexicographic misordering | `store/timestamp.ts` `toIsoUtc` (writer projects with it; reader canonicalizes the filter param)                     | `validity.py` `canonical_ts`                                                                    |
| Same-work exclusions | `B` itself sneaking in past the clock  | `retrieve/exclusions.ts` `isSibling` + `store/reader.ts` `supersedesClosure` + self-check in `retrieve/retrieval.ts` | `validity.py` `is_sibling` + `supersedes_closure` + self-check in `_is_eligible`                |
| Outcome-label scan   | Answers in agent-readable task text    | (no TS side — task construction is harness-owned)                                                                    | `grading/leak_guard.py` `assert_no_outcome_leak` / `find_outcome_leaks`                         |

Enforcement call sites on the Python side (the harness _owns_ the boundary; no
arm may touch the raw store directly):

- `membench/replay.py` — `loo_bounded(corpus, query)` builds the only ingest
  set an arm gets; `assert_no_leak(result.payloads.keys(), corpus, query)`
  re-audits every arm's output.
- `membench/compare/retrieval_compare.py` — same pair for the retrieval-quality
  comparisons; deliberately keeps unknown ids in the output so they surface as
  "unknown id" leaks rather than being silently dropped.
- `membench/bundle/assemble.py` (line ~411) — `assert_no_outcome_leak` on
  assembled task text; `membench/forward_capture.py` re-uses the same guard
  (`IDENTIFYING_KEYS` is its single source of truth).

The `ours` arm (`memory_systems/ours_system.py`) does not reimplement
retrieval: it shells `mem retrieve --json`, so the TS exclusions apply to what
it returns — and the harness _still_ re-checks its output with
`assert_no_leak`. Belt and suspenders, by design.

## 2. Layer by layer — what each rule is and why it is exactly that

### 2.1 The temporal cut is strict and null-safe

- **Strict `<`, never `<=`**: a record closed at exactly `B.started` is
  excluded. Pinned by `test_strict_temporal_cut_excludes_boundary_equal`
  (`memory-bench/tests/test_validity.py`).
- **Null-safe**: a record with no `closed` timestamp is _never_ eligible
  (`closed_at IS NOT NULL AND ...`). An open record's content can still change,
  so it cannot be "memory as it existed".
- **The boundary is `B.started`, falling back to `B.created`** when the work
  never recorded a start (`queryFromRecord` in `retrieve/retrieval.ts`;
  `query_from_record` in `validity.py`). `created` is earlier than any possible
  start, so the fallback is _strictly leak-safe_ (it can only under-include,
  never over-include). A record with neither raises — no boundary, no eval.
- Failure direction everywhere is **fail closed**: over-exclusion costs a
  little recall; under-exclusion invalidates the benchmark.

### 2.2 Canonical timestamps (mem-0rrf.15) — why a string compare is dangerous

The D6 cut is a **lexicographic TEXT comparison** (`closed_at < ?` in SQLite;
`str < str` in Python). That is only a chronological comparison when every
value shares one format. The corpus does not: dolt emits space-separated
zoneless timestamps (`2026-06-07 02:19:05`), the synthetic generators
(Decision 19) emit ISO `T`/`Z`. Since `' ' < 'T'`, a mixed-format record that
closed **after** the boundary can pass the strict filter silently — **in the
leak direction**.

The fix is one canonicalizer per half, mirrored byte-for-byte:

- TS `toIsoUtc` (`src/store/timestamp.ts`): the **writer** canonicalizes every
  projected lifecycle column (`writer.ts` projects `created`/`started`/`closed`
  through it) and the **reader** canonicalizes its `closedBefore` parameter, so
  the stored comparison is format-free.
- Python `canonical_ts` (`membench/validity.py`): canonicalizes both sides of
  the cut in `loo_bounded`.

Shared acceptance grammar (both regexes): `YYYY-MM-DD` + `T` or space +
`HH:MM[:SS[.fff]]` + optional zone (`Z`, `±HH:MM`, `±HHMM`). Rules with their
reasons:

| Rule                                                            | Why                                                                                                                                                                                                                                                                      |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Zoneless values are UTC                                         | The city convention; `ingest/provenance` pins the same shapes for git                                                                                                                                                                                                    |
| Date-only values rejected                                       | A boundary is an instant, not a day to guess midnight for                                                                                                                                                                                                                |
| Malformed input **throws/raises**                               | A bad producer timestamp must fail loudly at ingest, never silently reorder the boundary                                                                                                                                                                                 |
| Calendar overflow rejected on the TS side by a round-trip check | JS `Date.parse` ROLLS Feb 30 → Mar 2; Python `fromisoformat` raises. Guessing would diverge the parity contract, so TS re-derives the day and rejects mismatches (`tests/store.test.ts` "rejects calendar-invalid days instead of rolling them (parity with Python...)") |
| Output shape `YYYY-MM-DDTHH:MM:SS.sssZ`                         | One shape in, one shape stored, string compare is safe                                                                                                                                                                                                                   |

A schema consequence: canonical lifecycle projections forced the store schema
bump to v11 (`SCHEMA_VERSION = 11`, `src/store/schema.ts:28`) — there is no
in-place migration, so pre-canonical stores must be rebuilt. Rebuild mechanics
belong to mem-store-schema-and-rebuild.

### 2.3 Same-work exclusions — "the same work dodging the timestamp filter"

Three exclusion families, applied after the temporal cut:

1. **Self** — `record.work_id !== query.work_id`. Caller-owned in both halves.
2. **Supersedes closure** — `supersedesClosure` (TS: recursive CTE over
   `record_links` where `kind = 'supersedes'`) / `supersedes_closure` (Python:
   BFS over `WorkRef.supersedes` edges). The traversal is **undirected and
   transitive**: ancestors AND descendants are "the same work" for LOO. A
   superseded predecessor of `B` contains `B`'s problem; a successor contains
   its answer.
3. **Sibling test** — `isSibling` (TS `retrieve/exclusions.ts`) / `is_sibling`
   (Python `validity.py`). A record is `B`'s sibling when it shares `B`'s:
   - `convoy_id` (dispatched together),
   - `pr` (`record.outcome.pr` — same change),
   - `external_ref` (same branch),
   - **TS only, as of 2026-07-07** — the epic-parent axis (mem-qgdz): shared
     `record.links.parent`, the record _is_ `B`'s epic parent, or the record is
     a _child_ of `B`. See §3 for the parity gap.

   **The NULL-safety rule** (both halves): each comparison only fires when the
   **query side** names a value — absence never matches absence. Without this,
   every record with no PR would be a "sibling" of every query with no PR and
   the corpus would collapse. Pinned by `test_sibling_test_is_null_safe`.

The sibling keys are **ingest-derived data** (`record.links`, populated by the
dolt dependencies-table read, mem-qgdz), never re-parsed at retrieval time.
Historical incident behind the epic axis: before mem-qgdz (commit `54ec166`,
2026-07-03), ingest read only issues+labels, `record.links` stayed empty
corpus-wide, and the convoy/supersedes exclusions were **inert** — the commit
message records a confirmed leak (`mem-lvp.1`, closed 12:26, retrievable by its
epic sibling `mem-lvp.12`, started 14:15). Lesson: an exclusion whose key
column is empty passes every test that constructs its own fixtures.
`checkRecordLinks` (`src/cli/commands/build-store.ts`) now throws on a
full-corpus build with zero `record_links` (warns on single `--rig` builds).

### 2.4 Harness ownership and the double-check (Decision 11)

The arm interface (`memory_systems/base.py`) is deliberately thin: an arm gets
a `RetrievalRequest` and has **no discretion over the boundary**. The harness:

1. builds the corpus (`work_ref_from_record` over the TS export — one reader
   for real AND synthetic records, Decision 19: two code paths hide leaks);
2. computes `loo_bounded(corpus, query)` — the only door to the corpus;
3. hands the arm only that set (or, for `ours`, the store handle + boundary);
4. re-checks the arm's _output_ with `assert_no_leak` — any returned work_id
   outside the LOO set, **including ids the harness cannot account for at
   all** ("unknown id" is also a leak), raises `LeakageError` and fails the
   run. A leak is a validity bug that must fail the run, never be silently
   filtered away.

### 2.5 The outcome-label leak guard (task-construction path)

`validity.py` guards retrieval. It does not cover task construction, where an
outcome label can reach the agent through `instruction.md` or verifier markers.
`grading/leak_guard.py` closes that path mechanically:

- `IDENTIFYING_KEYS = ("pr", "commit_sha", "base_commit")` — the high-entropy,
  answer-revealing identifiers, and the **single source of truth** shared by
  the forward-capture scan and the relevance-judge anti-circularity scan so
  the guards cannot drift.
- `assert_no_outcome_leak(agent_readable, labels)` — case-insensitive
  substring scan over a string or a filename→content mapping; raises
  `OutcomeLeakError` listing every offender. Errs toward over-catching: a
  false positive fails loudly, a false negative lets a leak through.
- **Deliberately excluded** from the scan (do not "fix" this): the low-entropy
  enum states `pr_state`/`ci` (a task may legitimately say "pass" or
  "merged") and `repo` (legitimate task context — the agent must know where
  the work lives). Enum/context leakage is prevented structurally by the
  design/task-manifest split, not by substring scanning.

Both error types subclass `AssertionError` on purpose: they are invariant
violations, not recoverable conditions.

## 3. The parity contract — and its one known open gap

Every symbol below is marked in source with a "parity contract, change both"
comment (or mirrors one). If you touch a row's left cell you MUST touch its
right cell in the same change, with tests on both sides in the same commit.

| Semantics                                | TypeScript                                        | Python                                       |
| ---------------------------------------- | ------------------------------------------------- | -------------------------------------------- |
| Timestamp canonicalization               | `store/timestamp.ts` `toIsoUtc`                   | `validity.py` `canonical_ts`                 |
| Strict null-safe temporal cut            | `store/reader.ts` `queryRecords` (`closedBefore`) | `validity.py` `_is_eligible` / `loo_bounded` |
| Undirected supersedes closure            | `store/reader.ts` `supersedesClosure`             | `validity.py` `supersedes_closure`           |
| Sibling test                             | `retrieve/exclusions.ts` `isSibling`              | `validity.py` `is_sibling`                   |
| Replay boundary (`started ?? created`)   | `retrieve/retrieval.ts` `queryFromRecord`         | `validity.py` `query_from_record`            |
| Relaxed failure key (covariate, not LOO) | `retrieval.ts` `relaxedSignature`                 | `grading/trace_score.py` `relaxed_signature` |

**KNOWN OPEN PARITY GAP (verified 2026-07-07 at HEAD `4e819e1`, branch
`main`):** commit `54ec166` (mem-qgdz, 2026-07-03) added the **epic-parent
sibling arms** to the TS side only — `isSibling` gained shared-parent /
is-parent / is-child, `LinksSchema` gained `parent`, schema v10→v11. The
commit touched **no `memory-bench/` file**: Python `is_sibling` still tests
only convoy/pr/`external_ref`, and `WorkRef`/`QueryWork` carry no `parent`
field (verify: `grep -n parent memory-bench/membench/validity.py` → no
matches). Consequences, stated precisely:

- Direction of failure is **leak-permissive on the Python side**: the
  harness-owned `loo_bounded` set can include `B`'s epic siblings, so a
  non-`ours` arm could ingest them and `assert_no_leak` would not flag it.
- The `ours` arm is unaffected in what it _returns_ (it delegates to
  `mem retrieve`, where the TS exclusion applies), and the re-check cannot
  false-fail on it (the TS-filtered set is a subset of the more permissive
  Python set).
- Do **not** silently patch this while working on something else. Closing it
  changes the LOO set, which changes eval-validity-bearing behavior:
  HALT-branch-ready — Stephanie sign-off, tests ship with the fix on BOTH
  sides, and the fix must update `WorkRef`, `QueryWork`,
  `work_ref_from_record`, `query_from_record`, and `is_sibling` together
  [PROVISIONAL pending Stephanie — discovery Q4 gating scope].

Run `scripts/check-loo-parity.sh` (this skill) to re-derive the axis matrix
mechanically; it reports this gap as `PARITY GAP` until closed and will flag
any new drift the same way.

## 4. Progressive-disclosure retrieval (P2.5)

Retrieval-v1 output can be projected into three layers
(`src/retrieve/disclosure.ts`) so the **agent, not the pipeline, chooses
hydration depth** — the Decision-10 precision/injected-volume guard made
concrete (returning the whole store scores recall 1.0; the canonical gaming
attack):

- **L1 index** (`--format index`): per-item title, match tier, citation URI
  (`mem://lesson/<work_id>[/<commit_sha>]`), and `token_cost` — the estimated
  cost (1 token ≈ 4 chars, `estimateTokens`) of hydrating that item's L2 row,
  plus `token_cost_total` for "inject everything".
- **L2 details** (`--format details [--pick a,b]`): the full retrieved items
  for the picked work_ids (all when `--pick` omitted). An unknown pick throws.
- **L3 source**: not a payload — the `mem://record/<work_id>` URI; recover via
  `mem query <work_id>`.

Projection is pure: D10 ranking order is preserved, no store access, and the
precision-guard flags (`total_matched`, `near_duplicate_top`, `fts_truncated`)
survive into every layer so truncation is never silent. Retrieval is
deterministic (every store query has an explicit ORDER BY; ranking is the
fixed tuple tier → matched-signature count → matched-class count → FTS
position → work_id), so an index call followed by a details call sees the same
ranking.

Copy-pasteable, from the repo root (build first — `./bin/mem` runs `dist/`,
not `src/`):

```bash
npm run build
# L1: see what a failure-triggered query would cost before injecting anything
./bin/mem retrieve <work_id> --scope cross-rig --format index
# L2: hydrate two picked items
./bin/mem retrieve <work_id> --scope cross-rig --format details --pick mem-abc,mem-def
# issue-text trigger control (no stored trace errors in the query; mem-tnyo)
./bin/mem retrieve <work_id> --no-trace-query --scope same-rig --format full --json
```

`--scope` is required (`cross-rig` = strict/headline track, `same-rig` =
realistic/secondary — Decision 7). Replay mode's default `trace` trigger uses
the held record's OWN stored errors — an **oracle trigger** (information a
fresh agent does not have before failing; Decision 23 relabeled the arm
`ours-oracle-triggered` for this reason). `--no-trace-query` is the separable
issue-text control. Boundary and exclusions are identical under both triggers.

## 5. Runbook

### Verify the invariant is intact (read-only, ~1 min, from the repo root)

```bash
# 1. Mechanical parity/axis matrix (this skill's diagnostic)
.claude/skills/mem-temporal-loo-and-leak-safety/scripts/check-loo-parity.sh
# 2. TS-side pinned semantics (timestamps, exclusions, disclosure)
npx vitest --run tests/store.test.ts tests/retrieve.test.ts tests/retrieve.disclosure.test.ts
# 3. Python-side pinned semantics (use memory-bench's venv, or pip install -e ".[dev]")
cd memory-bench && .venv/bin/python -m pytest tests/test_validity.py tests/test_outcome_leak_guard.py -q
```

### Change anything in the LOO surface (checklist)

1. **STOP first**: anything touching temporal LOO / leak-safety is
   HALT-branch-ready — Stephanie sign-off before it lands [PROVISIONAL pending
   Stephanie — discovery Q4].
2. Locate the symbol in the §3 parity table. If it has a right-hand cell,
   your change has two halves. One commit, both halves, tests for both.
3. Preserve the failure direction: strict cut, null-safe comparisons, throw on
   malformed input, fail the run on any leak. Never convert a raise into a
   silent filter.
4. If you touch the timestamp grammar: keep the two regexes accepting the same
   language, keep the output shape identical, and re-run the mixed-format
   tests (`test_mixed_format_*` in `test_validity.py`; the mem-0rrf.15
   describe block in `tests/store.test.ts`).
5. If you add a sibling axis: it must be NULL-safe (query side names a value),
   ingest-derived (never re-parsed at retrieval), added to BOTH `isSibling`
   and `is_sibling`, carried by `queryFromRecord` AND
   `query_from_record`/`WorkRef`/`work_ref_from_record`, and covered by the
   parity script's axis list (edit the script's `AXES` line).
6. If you touch what an arm may see: the harness must still own the boundary
   (no arm reads the raw store), and `assert_no_leak` must still audit the
   output.
7. Re-run all three verification steps above.

### Debug a `LeakageError` / `OutcomeLeakError`

| Symptom                                                                   | Likely cause                                                                                                                                                   | Discriminating check                                                                                                    |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `LOO leakage (not in LOO set ...)` naming ids you expected to be eligible | Arm returned ids the harness's corpus lacks ("unknown id" is a leak too)                                                                                       | Are the offender ids in the corpus export the harness loaded?                                                           |
| Same, ids ARE in the corpus                                               | The record really is future/sibling/chained relative to `B` — the guard is working                                                                             | `./bin/mem query <offender-id> --json`: compare `lifecycle.closed` vs `B.started`; check shared convoy/pr/branch/parent |
| `not a recognizable lifecycle timestamp`                                  | A producer emitted a malformed/date-only/calendar-invalid timestamp                                                                                            | Fix the producer or its ingest mapping — do NOT loosen the grammar; the throw is the guard                              |
| `outcome label leaked into agent-readable text`                           | A pr/commit_sha/base_commit value appears in task files                                                                                                        | The exception lists `(where, label)`; scrub the assembly path, not the guard                                            |
| Retrieval returns nothing where you expected hits                         | Over-exclusion is the _safe_ direction; often an empty `record_links` (ingest ran without the dependencies read) or `closedBefore` given a pre-canonical store | `./bin/mem coverage`; check `record_links` count; rebuild if schema < 11                                                |

## 6. When NOT to use this skill

- Running the three-condition eval, Harbor, or the runners → **mem-eval-harness-run**.
- Ablation curve, oracle-soundness `validity_gate`, safety gates, judge
  doctrine (report-only, never pass/fail) → **mem-grading-and-validity-gates**.
- Adding/modifying a memory arm's retrieval logic (interface, adapters, fakes)
  → **mem-competitive-arms** (this skill only constrains what arms may see).
- The parse-layer ZFC boundary (deterministic extractors vs model) →
  **mem-deterministic-extraction-zfc**.
- Schema bumps, rebuild round-trips, append-only tables →
  **mem-store-schema-and-rebuild**.
- Why these decisions exist / what needs a new Decision →
  **mem-decision-ledger-and-architecture-contract**.
- Ingest coverage, trace resolution, provenance → **mem-ingest-and-provenance**
  (and the existing `ingest-trace-substrate` skill).

## Provenance and maintenance

Authored 2026-07-07 against branch `main`, HEAD `4e819e1`
(retiring-distinguished-fellow campaign; sources: the files named above, commit
`54ec166` (mem-qgdz), `docs/architecture-decisions.md` Decisions 6–11, 19, 23).
Facts most likely to drift, with one-line re-verification:

```bash
# From the repo root:
git log --oneline -1 -- src/store/timestamp.ts src/retrieve/exclusions.ts memory-bench/membench/validity.py memory-bench/membench/grading/leak_guard.py  # any commit newer than 54ec166/4e819e1 → re-verify §2–3
.claude/skills/mem-temporal-loo-and-leak-safety/scripts/check-loo-parity.sh   # axis matrix + the §3 parity gap (PASS once closed)
grep -n "SCHEMA_VERSION = " src/store/schema.ts                                # v11 as of 2026-07-07
grep -n "IDENTIFYING_KEYS" memory-bench/membench/grading/leak_guard.py         # (pr, commit_sha, base_commit)
./bin/mem retrieve --help 2>&1 | head -3                                       # CLI flag surface (build dist first)
```
