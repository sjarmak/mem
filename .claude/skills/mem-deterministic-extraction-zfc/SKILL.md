---
name: mem-deterministic-extraction-zfc
description: The ZFC boundary in mem's TypeScript store half — the deterministic parse layer (src/parse/ runner classification, format-anchored file:line error extractors, the failure-signature model, recurrence math) and the hard line to src/distill/, the only model-invoking module in src/. Load before editing anything in src/parse/, adding an error extractor or runner rule, changing failure signatures, touching src/distill/, or when deciding whether a new capability belongs in code or in a model call. NOT for retrieval exclusions or temporal leave-one-out — use mem-temporal-loo-and-leak-safety. NOT for trace resolution / store rebuilds — use mem-ingest-and-provenance and the ingest-trace-substrate skill. NOT for the Python grading stack — use mem-grading-and-validity-gates.
---

# Deterministic extraction and the ZFC boundary

Verified against `sjarmak/mem`, branch `main`, HEAD `4e819e1`, 2026-07-07. All
paths are repo-relative; run commands from the repo root.

**ZFC (Zero Framework Cognition)** is this project's rule for where reasoning
lives: _mechanical signal is computed in code; semantic judgment is delegated
to a model._ No keyword heuristics, no meaning-detection regexes, no
hardcoded semantic scoring in the deterministic layer. The rule is stated in
`ARCHITECTURE.md` ("Constraints that shape every choice" → "ZFC boundary")
and in the repo's `CLAUDE.md` invariants ("Deterministic signal is mechanical,
never model judgment"). This skill maps exactly where that line runs in
`src/` and how to work on either side of it without crossing it.

Why it is load-bearing here, not just style: the deterministic layer produces
the **failure signatures** that the store persists, that failure-triggered
retrieval keys on (Decision 8), and that the Python eval harness scores
against. If a keyword heuristic sneaks in, the "deterministic" half of the
benchmark's reward becomes an unauditable judgment call and the eval's
validity story collapses. Reviewers have rejected exactly this before: the
engram ancestor's keyword _memory-tier_ classifier was deliberately **not
ported** because it is a ZFC violation (`src/parse/runners.ts`, header
comment).

## When NOT to use this skill

| You are working on                                                  | Use instead                                                                             |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `closedBefore`, convoy/PR/branch/supersedes exclusions, leak safety | `mem-temporal-loo-and-leak-safety`                                                      |
| Resolving transcripts, `--with-traces`, store rebuild mechanics     | `mem-ingest-and-provenance` + existing `ingest-trace-substrate` skill                   |
| Schema columns, append-only tables, `mem rebuild`                   | `mem-store-schema-and-rebuild`                                                          |
| Python scorers, ablation curve, validity gates                      | `mem-grading-and-validity-gates`                                                        |
| Why these rules exist (Decision ledger)                             | `mem-decision-ledger-and-architecture-contract`                                         |
| Whether a number is publishable                                     | `mem-research-methodology-and-evidence-bar` (publication freeze `mem-0rrf` is in force) |

## The map: which side of the line each module is on

All five parse-layer files are in `src/parse/` (line counts as of 2026-07-07):

| File                  | Lines | Role                                                              | Side of the line |
| --------------------- | ----- | ----------------------------------------------------------------- | ---------------- |
| `runners.ts`          | 46    | Is this Bash command a build/test/lint run, and which runner?     | Mechanical       |
| `error-extractors.ts` | 344   | Tool output → structured `file:line` `TraceError`s (8 extractors) | Mechanical       |
| `recurrence.ts`       | 217   | Failure signature + cross-task recurrence confidence              | Mechanical       |
| `trace-parse.ts`      | 415   | Transcript JSONL → executions, errors, run metadata, pr-links     | Mechanical       |
| `index.ts`            | 25    | Public exports                                                    | —                |

And the model side:

| Module                     | What invokes the model                                                                                                                                                     | What stays plumbing                                                                                                                            |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/distill/distiller.ts` | `claudeRunner()` — `spawnSync('claude', ['-p', prompt, '--model', …, '--allowedTools', ''])`, headless Claude Code on the OAuth subscription (no-paid-API, Decisions 4/16) | Candidate selection (mechanical predicates), evidence resolution + char-budget truncation, prompt assembly, Zod validation of the model's JSON |

**`src/distill/` is the only module in `src/` that invokes a model.**
Verified 2026-07-07: `spawnSync` appears only in `distiller.ts`; the other
`child_process` sites (`src/ingest/beads.ts`, `outcomes.ts`, `provenance.ts`,
`trace-resolve.ts`) shell mechanical IO tools (`bd`, `gh`, `git`, `gc`), never
a model. The CLI wiring is `src/cli/commands/distill-lessons.ts`, which
accepts an injectable `DistillRunner` so tests never spawn a real model.

One judgment lives outside `src/` entirely: **task-type residue
classification**. `src/ingest/task-type.ts` types records by three sources —
`formula` and `structural` are exact mechanical grammars in TypeScript;
`model` is a **lookup** of a pre-built artifact (`.mem/task-types.json`)
produced offline by `memory-bench/scripts/classify_task_types.py` (headless
`claude -p`, Haiku tier, closed 13-label taxonomy). The TS side validates
labels against `MODEL_TASK_TAXONOMY` and throws on anything outside it;
mechanical rules always take precedence over the artifact. So the precise
statement is: _in `src/`, model judgment enters at exactly two points — the
distiller's runtime call, and the task-type artifact lookup — and everything
else is code._

## The failure-signature model

The **failure signature** is the canonical identity of a build/test/lint
error, used everywhere: `tool:file:line:error_class` (Decision 8,
rig-agnostic by construction).

Built in `src/parse/recurrence.ts` from three pieces:

1. **`normalizePath(file)`** — forward slashes, strip leading `./`. Nothing
   more. An absolute path stays absolute, so the same file printed absolute
   in one trace and relative in another does NOT merge — deliberate:
   cross-rig transfer is handled by retrieval _scope_ (Decision 7), not by
   collapsing signatures.
2. **`errorClass(error)`** — the stable class token, lifted per-tool from the
   message via `CLASS_BY_TOOL` (tsc `TS2345`, eslint trailing `(rule-id)`,
   mypy `[code]`, ruff leading `F401`, cargo `E0382`, pytest exception type).
   Tool-gating is deliberate: a tool-blind rule once wrongly lifted `int`
   from a go message. Tools with no code (go, gradle) — or a message missing
   its code — fall through to `classFallback`: lowercase, digits masked to
   `#`, whitespace collapsed, capped at 80 chars.
3. **`failureSignature(error)`** = `tool:normalizePath(file):line:errorClass(msg)`.

`classFallback` is the one **documented ZFC exception** in this layer: a
deliberate _similarity-merge_ threshold (calibrated mechanical transform),
not a semantic judgment. The Python scorer
(`memory-bench/membench/grading/trace_score.py`) claims the same exception
for its `relaxed_signature` (drops the line, basenames the file — the avoid
axis must not key on the original agent's exact line). Cite these two when
you need the precedent for what "allowed threshold" means.

### One definition, everywhere

`failureSignature` / `errorClass` / `normalizePath` are **imported, never
reimplemented**, by every consumer:

- `src/store/writer.ts` — persists them into the `trace_errors` columns.
- `src/retrieve/retrieval.ts` — D8 match tiers (exact signature → same
  `tool:error_class` → FTS message), plus a line-invariant
  `tool:basename:error_class` class key.
- `src/cli/commands/extract-errors.ts` — the fresh-run extraction path;
  its rows are byte-identical to the persisted held-out rows.

The Python side never recomputes signatures from raw output; it reads the
persisted store columns (`trace_score.py` `TraceErrorRef.from_mapping`). If
you change signature construction you have changed the join key between the
store, retrieval, and the eval harness — that touches eval validity and is
HALT-branch-ready (Stephanie sign-off; PROVISIONAL pending Stephanie, Q4).

### Recurrence math (engram's `reflect`, ported)

`computeRecurrence` groups errors by signature across traces:
`confidence = unique error-bearing traces with this signature / total
error-bearing traces`, capped at 1. The denominator is _error-bearing_ traces
(≥1 parsed error), an explicit adaptation of engram's failed-trace
denominator. Ranking is fully deterministic: confidence, then frequency, then
signature lexicographic. The 0.5 cutoff engram baked in is retrieval-time
_policy_ and lives in the caller (`minConfidence`, default 0). Do not move
policy into the math.

## How the deterministic parse actually flows

`parseTranscript` (`src/parse/trace-parse.ts`) reads Claude Code transcript
JSONL (string or streamed line iterable — `readLines` streams multi-GB files
without loading them):

1. Assistant `tool_use` blocks named `Bash` are held pending by id; the
   matching `user` `tool_result` pairs by `tool_use_id`.
2. `matchRunner(command)` (`src/parse/runners.ts`) gates: only recognized
   build/test/lint runners become `Execution` records — `ls`, `git status`,
   `cat` never pollute the signal. `RUNNER_RULES` is ordered, **first match
   wins**: specific tools (`tsc`) before generic wrappers (`npm run …`). For
   a wrapper the runner name is the wrapper (`npm`); underlying tools surface
   as each error's `tool`.
3. `extractErrors(output)` strips ANSI SGR color, runs **all 8 extractors**
   over the combined stdout+stderr, and de-duplicates by `errorKey`. Safe on
   wrapper output (e.g. `npm run check` fanning out to tsc+eslint+vitest)
   because every extractor is anchored on its toolchain's file extension plus
   a distinct format token — there is a polyglot cross-match test.
4. **`status: 'fail'` when `tool_result.is_error` is true OR parsed errors
   exist.** The second clause is load-bearing: agents pervasively pipe output
   (`npm run check 2>&1 | tail`), and the pipe makes the shell report the
   pager's exit code, masking the failure. Parsed errors recover it.
5. Run-level metadata (`TraceRun`) is folded mechanically: tokens summed,
   `model`/`harness_version`/`outcome` last-write-wins from assistant
   entries; `outcome` is the final `stop_reason` **verbatim — a transcript
   field, NOT a pass/fail oracle**.
6. Unparseable JSONL lines are skipped (append-only logs may end mid-write);
   a reaped transcript (ENOENT) leaves parse fields absent, preserving the
   "not yet parsed" vs "parsed, found nothing" distinction.

Extractor coverage as of 2026-07-07 (8 extractors in `EXTRACTORS`): tsc
(both emitted shapes), eslint stylish (header/detail state machine), go
build/vet, mypy, ruff, cargo/rustc (two-line header→`-->` location state
machine with a one-line adjacency window), pytest short-summary
(`FAILED`/`ERROR` lines), gradle (javac + kotlinc). Runner recognition is
broader than extraction — go/cargo/gradle/make/pnpm etc. all produce
pass/fail `Execution`s; only the formats above yield structured errors.

## Accepted limits — do NOT "fix" these with heuristics

Each of these is documented in-source as intentional. Adding a heuristic to
paper over one is the canonical ZFC violation in this repo.

| Observation                                                             | Why it stays                                                                                                                                          |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| A failure that is both exit-code-masked AND unparseable reads as `pass` | "Indistinguishable from success by deterministic means; an accepted limit, not something to paper over with keyword sniffing" (`parseTranscript` doc) |
| pytest errors carry `line: 0`                                           | pytest summaries have no line; the avoid axis is line-invariant by design                                                                             |
| Absolute vs relative paths of the same file don't merge                 | Cross-rig transfer belongs to retrieval scope (D7), not signature collapse; repo-relativizing needs the trace `cwd` and is explicitly deferred        |
| go/gradle recur poorly on identifier-bearing messages                   | Codeless-toolchain degraded mode of `classFallback`, stated in-source                                                                                 |
| An orphan eslint detail line (no file header yet) is dropped            | "Dropped rather than guessed at"                                                                                                                      |
| A cargo header with no adjacent `-->` location is dropped               | Adjacency window prevents cross-tool pairing; a stray `error:` line must not reach forward                                                            |
| Malformed/malicious `pr-link` entries are skipped                       | "Skipped, never coerced into a partial link"                                                                                                          |

## Decision table: code or model?

Before adding capability anywhere in the store half, classify it:

| Capability                                                               | Verdict                                          | Where                                                                        |
| ------------------------------------------------------------------------ | ------------------------------------------------ | ---------------------------------------------------------------------------- |
| Recognize a new tool's output format (exact `file:line` grammar)         | Code                                             | New extractor in `error-extractors.ts`                                       |
| Recognize a new build/test runner command                                | Code                                             | New `RUNNER_RULES` entry                                                     |
| Exit-code / structured-field projection (tokens, turns, timestamps)      | Code                                             | `trace-parse.ts` fold                                                        |
| Dedup, normalization, deterministic ranking, capped fallbacks            | Code                                             | Documented as ZFC-clean transforms                                           |
| "Does this output look like a failure?" beyond exit code + parsed errors | **Model or nothing**                             | Never a keyword sniff in `src/parse/`                                        |
| Root-cause of a failure; what resolved it; the reusable lesson           | Model                                            | `src/distill/` (prompt carries the evidence; JSON contract validated by Zod) |
| Task-type of a free-form bead title                                      | Model, offline                                   | `memory-bench/scripts/classify_task_types.py` → artifact → TS lookup         |
| Severity/importance/quality scoring of a lesson or trace                 | Model, and currently out of scope for this layer | See `mem-grading-and-validity-gates` for what the harness scores             |

If your change makes the parse layer's output depend on what text _means_
rather than what format it _matches_, it belongs on the model side or should
not exist.

## Runbook: add an error extractor

1. Get real output samples of the tool's diagnostic format (from a trace or
   by running the tool). Identify the anchor: **file extension + one distinct
   format token** (e.g. mypy is `.py`/`.pyi` immediately followed by `:` plus
   the `error:`/`warning:` keyword — the sole discriminator from javac's
   identically-shaped line; keep anchors that strict).
2. Add the extractor to `src/parse/error-extractors.ts`: implement
   `ErrorExtractor` (`tool` + pure `extract(output): TraceError[]`), register
   it in `EXTRACTORS`. Multi-line formats (cargo, eslint) use a small state
   machine with an explicit adjacency rule — copy that pattern.
3. If the tool has a stable diagnostic code, add a `CLASS_BY_TOOL` entry in
   `src/parse/recurrence.ts` (tool-gated regex lifting the code). No code →
   omit the entry; `classFallback` is the intended degraded mode.
4. If the _runner command_ is new, add a `RUNNER_RULES` entry in
   `runners.ts` — ordered, specific before generic wrappers; no `g` flag on
   the regex (they are used with `.test()`).
5. Tests ship in the same commit: real-format fixtures in
   `tests/parse.trace.test.ts` (or alongside the existing extractor tests),
   **including a cross-match case** proving your pattern does not fire on the
   other tools' sample outputs (the existing polyglot cross-match test is the
   template), and a recurrence-class case in `tests/parse.recurrence.test.ts`
   if you added a `CLASS_BY_TOOL` entry.
6. Verify:

   ```bash
   npm run build                      # bin/mem runs dist/, not src/ — always build first
   npx vitest run tests/parse.trace.test.ts tests/parse.recurrence.test.ts tests/cli-extract-errors.test.ts
   printf 'src/x.ts(12,5): error TS2345: boom\n' | ./bin/mem extract-errors --json
   ```

7. Regenerate the extract-errors fixture if the CLI's output shape moved:
   `scripts/regen-extract-errors-fixture.mjs` exists for that.

Signature-affecting changes (anything in `errorClass`, `normalizePath`,
`failureSignature`, or an extractor's `message` construction that feeds
them): the persisted `trace_errors` rows in an existing store were computed
with the OLD definition, and the Python scorer joins against them. That is a
store-rebuild plus an eval-validity question — route through
`mem-store-schema-and-rebuild` and treat as HALT-branch-ready (PROVISIONAL
pending Stephanie, Q4).

## Runbook: work on the distiller without crossing the line

- Everything you may freely change in `src/distill/distiller.ts` is plumbing:
  `selectCandidates` (closed records with ≥1 trace error, minus
  already-lessoned unless `force`), `resolveResolutionEvidence` (landed diff
  head-truncated at 6,000 chars, else transcript tail tail-truncated —
  truncation always explicit in the prompt), `buildDistillPrompt`,
  `parseDistilledPayload` (tolerates a markdown fence, nothing else; missing
  fields = per-record failure, never a guess).
- What the lesson IS belongs to the model. Do not pre-classify, pre-score, or
  post-edit lesson content in code; structural (Zod) validation only.
- `lastFailureIndex` looks like a heuristic but is not: it exact-substring
  matches _already-extracted_ error messages (raw + JSON-escaped forms) to
  find the transcript slice where the fix happened. Matching known evidence
  is mechanical; inferring new evidence is not.
- Per-record model failures are collected in `DistillOutcome.failures` and
  surfaced, never swallowed; one flaky generation must not discard a batch.
- Lessons are **append-only** (Decision 9) — the default candidate filter
  skips already-lessoned records precisely because a re-run would stack
  near-duplicates. Never rewrite; supersede.
- **Do not run `mem distill-lessons` casually.** Each candidate is a real
  headless-Claude call on the shared OAuth subscription. Distill/judge spend
  is gated (PROVISIONAL pending Stephanie, Q4: treat as requiring sign-off).
  For code changes, use the injectable `DistillRunner` seam — the tests
  (`tests/distill.test.ts`) never spawn a model.

## Diagnostics

```bash
# Extract structured errors from any raw tool output (stdin or --file).
# Rows are field-for-field identical to the store's trace_errors projection.
npm run build && printf 'app.py:3:1: F401 unused import\n' | ./bin/mem extract-errors --json

# Parse-layer test suites (62 tests across the two parse suites, 2026-07-07):
npx vitest run tests/parse.trace.test.ts tests/parse.recurrence.test.ts

# Boundary check — model invocation confined to src/distill/, parse layer clean:
bash .claude/skills/mem-deterministic-extraction-zfc/scripts/check-zfc-boundary.sh
```

`mem extract-errors` takes **raw combined output, NOT transcript JSONL** (it
wraps `extractErrors`, not `parseTranscript`). It is the fresh-run extraction
path the Harbor runner shells to in the ablation grid.

## Provenance and maintenance

Authored 2026-07-07 against branch `main`, HEAD `4e819e1` (checkout was on
`main`). Re-verify before trusting drift-prone facts:

```bash
git -C . rev-parse --short HEAD                                   # compare against 4e819e1
ls src/parse/                                                     # still 5 files?
grep -c "ErrorExtractor = {" src/parse/error-extractors.ts        # extractor registrations (8 as of 2026-07-07; count tracks EXTRACTORS)
grep -rn "spawnSync" src --include='*.ts' | grep -v test          # still only src/distill/distiller.ts
grep -n "MODEL_TASK_TAXONOMY" src/ingest/task-type.ts             # closed taxonomy still enforced (13 labels 2026-07-07)
grep -n "failureSignature" src/store/writer.ts src/retrieve/retrieval.ts src/cli/commands/extract-errors.ts  # one-definition rule holds
npx vitest run tests/parse.trace.test.ts tests/parse.recurrence.test.ts
```
