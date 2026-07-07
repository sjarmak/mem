---
name: mem-decision-ledger-and-architecture-contract
description: The mem architecture contract — all 24 numbered Decisions from docs/architecture-decisions.md distilled to their rulings, the five load-bearing invariants (work-audit graph as source of truth, temporal leave-one-out, append-only non-regenerable tables, direct-to-main landed oracle, synthetic-records-are-WorkRecords), the known weak points stated plainly, and what must not change without a Decision. Load BEFORE proposing any design change, re-opening a settled question ("why don't we just use PR outcomes / rewrite lessons / add a synthetic loader / return more context"), touching eval validity, or citing a headline number. NOT for running the eval (use mem-eval-harness-run), NOT for schema/rebuild mechanics (use mem-store-schema-and-rebuild), NOT for the chronicle of failed investigations and dead ends (use mem-failure-archaeology), NOT for the evidence bar and experiment discipline (use mem-research-methodology-and-evidence-bar).
---

# mem — Decision ledger and architecture contract

This skill is the distilled ruling on every settled design question in `mem`,
plus the invariants that make the benchmark's numbers defensible. Its job is to
stop you from re-litigating a decision Stephanie already made, and from
weakening an invariant whose whole purpose is invisible until it fails.

**Who you are assumed to be:** a zero-context engineer or model about to change
something in `/home/ds/projects/mem` (paths below are repo-relative; the repo
root is wherever you cloned `sjarmak/mem`).

## When to use this skill / when not to

| Situation                                                                        | Use                                                                              |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| "Why is the system shaped this way?" / "Can I change X?"                         | **This skill**                                                                   |
| About to propose a design that touches retrieval, eval, schema, arms, or oracles | **This skill first**, then the sibling for mechanics                             |
| What is mem, where is everything                                                 | `mem-orientation`                                                                |
| What counts as evidence; how to run an experiment                                | `mem-research-methodology-and-evidence-bar`                                      |
| "Has this been tried before?" — investigations, nulls, reverts                   | `mem-failure-archaeology`                                                        |
| Bump the schema, rebuild the store                                               | `mem-store-schema-and-rebuild`                                                   |
| Ingest mechanics, provenance, trace resolution                                   | `mem-ingest-and-provenance`                                                      |
| The ZFC parse/distill boundary in code                                           | `mem-deterministic-extraction-zfc`                                               |
| LOO enforcement mechanics in both languages                                      | `mem-temporal-loo-and-leak-safety`                                               |
| Run the harness, arms, grading                                                   | `mem-eval-harness-run`, `mem-competitive-arms`, `mem-grading-and-validity-gates` |

## Jargon (defined once)

| Term                   | Meaning here                                                                                                                                               |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **WorkRecord**         | The atomic unit: one bead's full audit join (lifecycle, agents, trace, outcome, provenance, signal, links). Schema: `src/schemas/workrecord.ts`.           |
| **bead / rig / spine** | A work item / a project repo under the orchestrator / the dolt bead store that holds all work items.                                                       |
| **sidecar**            | The gitignored SQLite+FTS5 store `.mem/store.db` built from the spine — a rebuildable projection, except for three tables (Invariant 3).                   |
| **temporal LOO**       | Temporal leave-one-out: when evaluating bead B, retrieval sees only records closed strictly before `B.started`, minus B's siblings.                        |
| **oracle**             | The ground-truth label for a replayed task (originally merged-PR/CI; now the git-native `landed` fact, D18), never shown to the agent.                     |
| **arm / condition**    | A memory system under test behind the uniform interface (`memory-bench/membench/memory_systems/`); conditions = `no_memory` / `oracle` / `memory_enabled`. |
| **ZFC**                | Zero Framework Cognition: mechanical signal is computed in code; semantic judgment is delegated to a model; never keyword-heuristics-in-code.              |
| **the spec**           | `.gc/memory-eval-harness-spec.md`. **Where the decision ledger and the spec conflict, the spec governs** (ledger header + Decisions 11–17).                |
| **the ledger**         | `docs/architecture-decisions.md` — chronological; entries are superseded in place, never rewritten.                                                        |

## Document precedence (memorize this)

1. `.gc/memory-eval-harness-spec.md` — authoritative eval contract.
2. `docs/architecture-decisions.md` — the why, chronological, supersede-in-place.
3. `ARCHITECTURE.md` — synthesized current state.
4. `architecture/exports/orient.md` — mechanically regenerated map; **can lag the
   code** (as of 2026-07-07 it says "schema v8" while `src/store/schema.ts:28`
   says `SCHEMA_VERSION = 11` — trust the code, then the ledger).

Nothing in this skill overrides those documents; this is a distillation with
verified pointers, not a new authority.

---

## Part 1 — The 24 Decisions, distilled to rulings

Source: `docs/architecture-decisions.md` (read it in full before proposing a
change to anything it covers). Column "Do NOT" is the newcomer trap each ruling
fences off. Status notes verified against the working tree 2026-07-07.

| D#  | Ruling (distilled)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Do NOT                                                                                                                            |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Benchmark = **outcome lift** (headline) + retrieval precision as an instrument toward it.                                                                                                                                                                                                                                                                                                                                                                                                                                         | Treat retrieval precision alone as the result.                                                                                    |
| 2   | First milestone = the **work-audit graph builder** (useful as an audit tool before any memory exists).                                                                                                                                                                                                                                                                                                                                                                                                                            | —                                                                                                                                 |
| 3   | Store = **bead spine + sidecar** for trace-derived signal (sidecar became SQLite+FTS5).                                                                                                                                                                                                                                                                                                                                                                                                                                           | Move source-of-truth into the sidecar.                                                                                            |
| 4   | Retrieval v1 = **structured/keyword** over the graph; embeddings only if structured underperforms; OSS/self-hosted only.                                                                                                                                                                                                                                                                                                                                                                                                          | Reach for embeddings/vector DB as a default.                                                                                      |
| 5   | Eval task source = **replay closed historical beads first**; live-shadow later.                                                                                                                                                                                                                                                                                                                                                                                                                                                   | —                                                                                                                                 |
| 6   | Eval contract = **temporal LOO** (closed strictly before `B.started`) + explicit exclusion of convoy siblings, supersedes-chain, PR/branch sharers + a duplicate audit. The original outcome is the oracle, **never a label the agent sees**.                                                                                                                                                                                                                                                                                     | Weaken any exclusion; let the agent see the label.                                                                                |
| 7   | **Dual-track, report both**: strict/headline = cross-rig only; realistic/secondary = same-rig temporal-LOO + duplicate audit (`retrieval_scope` knob). Same-rig ≫ cross-rig lift is a finding, not a flaw.                                                                                                                                                                                                                                                                                                                        | Report only the flattering track.                                                                                                 |
| 8   | Retrieval trigger v1 = **failure-triggered** on the deterministic failure signature (normalized `file:line` + error-class). Structured fields filter; message keywords are a weak tiebreaker only.                                                                                                                                                                                                                                                                                                                                | Add semantic/keyword triggering to v1; claim it prevents first failures (it cuts iterations-to-green).                            |
| 9   | Payload = **distilled lesson + citation** (`bead_id` + `commit_sha`), extracted **once at ingest, append-only, never iteratively rewritten**. Never inject the raw prior trace.                                                                                                                                                                                                                                                                                                                                                   | Rewrite/"improve" stored lessons; inject raw traces; over-compress to atomic one-liners.                                          |
| 10  | **Precision/injected-volume guard is mandatory on every lift run** (returning the whole store gets recall 1.0 — the canonical gaming attack).                                                                                                                                                                                                                                                                                                                                                                                     | Report a lift number without the guard.                                                                                           |
| 11  | Competitive arms run behind **one uniform interface**; the **harness owns the LOO-bounded ingest set**; arms never read the store; per-arm token+latency overhead is reported, not hidden. Arms map to spec conditions; Harbor is the execution substrate.                                                                                                                                                                                                                                                                        | Let an arm query the store directly or curate its own ingest set.                                                                 |
| 12  | **Versioned 5-axis telemetry** per run (task-perf, token, latency, privacy, interruption) as OTel GenAI spans (primary) + ATIF (derived); privacy/interruption measured but not acted on in v1.                                                                                                                                                                                                                                                                                                                                   | Drop an axis because v1 doesn't act on it.                                                                                        |
| 13  | Flat four-type memory taxonomy **SUPERSEDED** by the spec's two-level model: representation {filesystem/vector/kg} × `candidate_memory.type` {episodic/semantic/procedural/preference/entity/relationship/failure_pattern}.                                                                                                                                                                                                                                                                                                       | Cite the old four-type list.                                                                                                      |
| 14  | Controller = **6-stage loop exposed as an MCP server** (retrieve/write/reflect); writes append-only; **replay writes go to a per-run scratch store, never the LOO-bounded corpus**. Status: `#planned` (orient.md), not built.                                                                                                                                                                                                                                                                                                    | Build controller stages ad hoc outside this frame; write replay output into the corpus.                                           |
| 15  | NVIDIA-stack posture: **adopt** verified self-hostable pieces (NAT arm w/ local backends, OTel+ATIF, G1–G4 vocabulary, NeMo-Evaluator/RULER); **avoid** GPU/NIM/paid-gated components; **contribute** the orchestration benchmark + privacy/interruption axes.                                                                                                                                                                                                                                                                    | Wire NAT's Mem0/Zep-cloud defaults (not no-paid-API-clean).                                                                       |
| 16  | **No-paid-API is scoped to the memory stack only** (backends/embeddings/extractor/judge). The agent-under-test (Claude via flat-rate OAuth), Harbor, and Docker are NOT paid infra and NOT an escalation trigger. Ledger says verbatim: **"Do not re-litigate this."** Also: eval object = multi-session sequences; roadmap re-baselined on the spec's 5 phases.                                                                                                                                                                  | Escalate "can we afford to run the agent" as a paid-API question.                                                                 |
| 17  | **Headline = the ablation score-vs-information curve**, not merged-PR/CI outcome-lift — the real corpus lacks the bead→PR→commit linkage at scale (~1 external_ref in ~6,000). Per-rung reward is a deterministic trace-error check + a separate OSS judge `rubric_score`. Merged-diff oracle = opportunistic validation only. _(Amended by D18.)_                                                                                                                                                                                | Re-attempt the gh/PR outcome re-ingest — the source data is absent, not unwired.                                                  |
| 18  | The sparse-linkage wall is a **direct-to-main workflow property** (364/364 integration branches = `main`): the merged-PR oracle is _inapplicable_, not broken. Added the **git-native landed oracle** (`src/ingest/landed.ts`): branch tip at session close → `base..end` range → `landed_state` ∈ {landed, reverted, abandoned, empty-window, ambiguous-window, unresolved}. Base-commit resolution lifted 359 → 5,644 records; deterministic attribution is concurrency-gated (see Weak point 2). Headline unchanged.           | Guess attribution for ambiguous windows; treat the landed oracle as the headline.                                                 |
| 19  | **Synthetic records ARE WorkRecords** — one firewall, one reader, one temporal-LOO path; the only synthetic-specific field is the origin marker (`origin="synthetic"`, projected via `validity.WorkRef.origin`; see `memory-bench/membench/generators/synthetic_corpus.py`). A parallel synthetic loader was **rejected**: two code paths = two places for a leak to hide.                                                                                                                                                        | Build any synthetic-only load/eval path.                                                                                          |
| 20  | **OpenRath** (arXiv 2606.19409) = a mem-owned **projecting read-model over `memory_events`** measured by ΔR; it is NOT an orchestration/control layer.                                                                                                                                                                                                                                                                                                                                                                            | Adopt OpenRath as a control plane.                                                                                                |
| 21  | NeMo dense embedder = a plain **baseline arm** (`nemo-embed`, cosine top-k behind the `SemanticMemoryClient` seam), explicitly NOT an `ours` upgrade; agentic NeMo loop + ColBERT rerank deferred; default model is the permissive `llama-nemotron-embed-1b-v2` (the NC model is non-commercial). _(Ledger says "branch-ready, not yet merged" — since landed on main: `memory_systems/nemo_embed_system.py` @ a7f502d.)_                                                                                                         | Fold dense retrieval into `ours`.                                                                                                 |
| 22  | Forward-capture firewall = **post-close value re-scan** (`rescan_closed_work`, `memory-bench/membench/forward_capture.py`) on the LIVE write path (the structural-only filter was inert). `builtin` = the agent's **native memory as a no-store arm** behind the uniform interface. _(Ledger says branch-ready — since landed: `memory_systems/builtin_system.py` @ b9c8b89.)_                                                                                                                                                    | Rely on a structural filter alone for live-path leak prevention.                                                                  |
| 23  | The `ours` arm is **relabeled `ours-oracle-triggered`** — its query comes from the held record's OWN stored trace errors, baked pre-run (`trigger: "oracle"` rides conditions/provenance/summaries). A separable control **`ours-issue-trigger`** forms its query WITHOUT trace errors (`mem retrieve <work_id> --no-trace-query`, `src/cli/commands/retrieve.ts`). H3 relaxed-signature overlap is a persisted per-payload **covariate** (`signature-overlap.json`), not a hard guard (the oracle payload keeps its hard guard). | Report the arm as plain "failure-triggered `ours`"; recompute held signatures in Python (they are canonical TS-computed strings). |
| 24  | Ftp-shape calibration is **SCOPED to the blueprint track** (2 blueprints, the 2 memory-dependent shapes of 6). The external anchor = a real, manifest-frozen adaptation of BIG-bench `list_functions` (32 subtasks, frozen at upstream commit `092b196c…`, leak-rejecting loader). The NeMo enterprise-workflow track references no `FtpShape`; its lift is **uncalibrated against real ftp shapes** until a shape-bearing linkage is designed.                                                                                   | Claim shape calibration for the enterprise-workflow track; hand-write "anchor" rows whose rule text appears in the episodes.      |

**Rule of engagement:** if your proposal contradicts a ruling above, you are not
"fixing" — you are re-opening a Decision. That requires Stephanie's sign-off and
a new superseding entry in the ledger (supersede in place; never rewrite an old
entry). Ledger header: "Entries below are preserved as written; supersede in
place, do not rewrite history."

---

## Part 2 — The five load-bearing invariants

These are the invariants named in the repo's own AGENTS.md, deepened here with
verified anchors. Each one, if weakened, silently invalidates work downstream —
usually every published-able number at once.

### Invariant 1 — The work-audit graph is the source of truth

- The SQLite+FTS5 sidecar `.mem/store.db` (`src/cli/store.ts`), schema version
  `SCHEMA_VERSION = 11` (`src/store/schema.ts:28`, verified 2026-07-07).
- Every projected column is rebuilt from the `work_records.record` JSON on
  upsert. **Never write projections directly** — they are derived data.
- The store is a rebuildable projection of the bead spine + traces… except for
  Invariant 3's three tables.

```bash
# Verify the schema version the code enforces (openStore throws on mismatch):
grep -n "SCHEMA_VERSION" src/store/schema.ts
```

Mechanics of bumping/rebuilding: `mem-store-schema-and-rebuild`.

### Invariant 2 — Temporal leave-one-out is load-bearing for eval validity

The single cross-cutting invariant, enforced in both languages — with one
documented parity gap (below). Retrieval for a target work item only ever
sees records **closed strictly before the target started**, minus records
that are "the same work dodging the timestamp filter."

The authoritative file:line enforcement-point map lives in
**mem-temporal-loo-and-leak-safety §1** — consult it there rather than a
copy here. In brief: TS side = strict temporal cut (`src/store/reader.ts`
`closedBefore`), canonical UTC timestamps (`src/store/timestamp.ts`),
sibling exclusion (`src/retrieve/exclusions.ts` `isSibling`),
supersedes-chain closure (`supersedesClosure`); Python mirror =
`memory-bench/membench/validity.py` (`canonical_ts`, `is_sibling`) +
`memory-bench/membench/grading/leak_guard.py`.

**Known parity gap (open as of 2026-07-07):** TS `isSibling` excludes epic
parent-child; the Python mirror's `is_sibling` (`validity.py`) tests only
convoy / PR / `external_ref` — zero `parent` references. Do NOT read this
ledger as claiming full two-language enforcement; the gap and its status
are documented in mem-temporal-loo-and-leak-safety §3.

**Weakening ANY of these leaks the answer into the eval context.** That is the
one thing this benchmark exists to prevent; a leak is not a degraded result, it
is an invalid one. Full mechanics: `mem-temporal-loo-and-leak-safety`.

```bash
grep -n "closedBefore" src/store/reader.ts
grep -n "isSibling" src/retrieve/exclusions.ts
ls memory-bench/membench/grading/leak_guard.py memory-bench/membench/validity.py
```

### Invariant 3 — Three tables are append-only and non-regenerable

`lessons` (`src/store/schema.ts:246`), `provenance_events` (`schema.ts:269`),
and `memory_events` (`schema.ts:299`). All three:

- **Deliberately have NO foreign key to `work_records`** — lesson citations are
  snapshotted at append time, never joined live (guards against later record
  churn changing what a lesson "cited").
- **Cannot be regenerated from the spine.** A rebuild reproduces every other
  table; these three hold history that exists nowhere else.
- **There is no in-place schema migration.** `openStore` throws on any
  `user_version ≠ 11`; a version bump means `mem rebuild`, which round-trips all
  three mechanically (export-all → fresh build → import-all). Manual pairs
  (`export/import-lessons`, `export/import-memory-events`,
  `export/import-provenance-events`) exist for surgical use — verified in
  `./bin/mem help` output 2026-07-07.

Never rewrite a lesson (Decision 9); supersede by appending. Mechanics:
`mem-store-schema-and-rebuild`.

```bash
grep -n "Append-only" src/store/schema.ts
./bin/mem help | grep -E "rebuild|export-|import-"   # build dist/ first
```

### Invariant 4 — Direct-to-main corpus → the landed oracle (never guess)

The real corpus has essentially no PRs to link (D17/D18). Consequences that must
hold:

- `provenance.base_commit` is an **approximation**: newest commit on the named
  `base_branch` at/before `started_at` (`history_state: commit-by-date`). It is
  resolved **only when a base branch was recorded**; an absent base branch is
  terminal `unresolved`. Resolving from the work_dir's HEAD is forbidden — that
  walks the agent's own feature branch, a train/test leak.
- The outcome fact for direct-to-main work is `landed_state` from
  `src/ingest/landed.ts` ∈ {landed, reverted, abandoned, empty-window,
  ambiguous-window, unresolved}. Ambiguous windows (concurrent sessions on one
  branch) stay ambiguous — **never guessed** (author/SHA attribution is scoped
  future work).
- `None`/`unresolved` always means "honestly not measured," never a fabricated
  zero. Fail-closed behavior here is intentional, not a bug.

Mechanics: `mem-ingest-and-provenance`.

### Invariant 5 — Synthetic records ARE WorkRecords (one path)

Decision 19. The synthetic-world track emits records the existing ingest/store
accepts; the only synthetic-specific field is the origin marker
(`origin="synthetic"`, projected first-class by `validity.WorkRef.origin`;
`memory-bench/membench/generators/synthetic_corpus.py`). Synthetic records flow
through the **same** firewall, the **same** reader, and the **same** temporal-LOO
path as real records; the synthetic outcome sentinel is routed into a
firewall-scanned key so existing guards apply unchanged. Every validity gate
already written applies to synthetic data with zero forks.

If you find yourself writing `if synthetic: ...` in a load/eval path, stop —
that is the rejected two-code-paths design. Mechanics:
`mem-synthetic-world-generator`.

### Adjacent invariants (owned by siblings, listed for completeness)

- **Deterministic signal is mechanical, never model judgment** (the ZFC
  boundary): build/test/lint outcomes parsed by runner matching
  (`src/parse/runners.ts`) + format-anchored extractors
  (`src/parse/error-extractors.ts`); the model is reserved for semantic
  annotation and `src/distill/`. → `mem-deterministic-extraction-zfc`.
- **Trace resolution depends on the working directory.** `--with-traces` shells
  `gc session logs`, which needs a `city.toml` in the cwd; from the wrong
  directory it exits 0 with zero traces (silent). This is an **operational
  precondition of the ingest step**, not a code-path this contract depends on
  (PROVISIONAL pending Stephanie, discovery Q1). → `mem-ingest-and-provenance`
  and the checked-in `ingest-trace-substrate` skill.
- **CLI contract:** entrypoint `./bin/mem` runs `dist/` (build first);
  `--json` emits `{apiVersion, cmd, ok, data?, errors?}`
  (`src/schemas/envelope.ts`). → `mem-build-test-env`.

---

## Part 3 — Known weak points, stated plainly

These are open, acknowledged weaknesses. Do not paper over them, do not
"discover" them as new bugs, and do not cite numbers that depend on them without
the caveat.

1. **`base_commit` is a timestamp-approximate main-tip, not the session's true
   per-worktree base SHA.** This is the diagnosed root cause of the
   replay-engine null (`docs/mem-7q6e-replay-engine-null.md`): legitimately
   applied session edits have no anchor at replay time, so replay fails closed.
   The real fix is per-worktree base-SHA capture (the mem-75t lineage —
   trace-substrate work, substantially larger than a harness patch). History and
   fenced-off wrong paths: `mem-failure-archaeology`; the executable campaign:
   `mem-oracle-validity-wall-campaign`.
2. **Landed attribution is concurrency-gated.** Of 5,031 landed candidates on
   `store-v6p-lessons`, 4,910 have ambiguous time-windows (overlapping sessions
   on one branch); the deterministically attributed landed set was **31** at
   D18 time. The ~2,836-record gap is recoverable only by author/SHA attribution
   (scoped future work, never guessed).
3. **The real corpus is signal-poor, and the real-corpus result is a
   diagnosed-ceiling null.** Only ~8 of 407 commit-linkage-recovered oracles
   survive replay + the two-stage validity gate; on that set the graded 3-arm
   result is `ours` +0.000 vs `builtin` +0.125. Caveats that must always ride
   these numbers: N is bound by replay/oracle fidelity (weak point 1), not by
   method; and **all headline/real-corpus numbers are held under the `mem-0rrf`
   publication freeze** (PROVISIONAL pending Stephanie on the freeze's exact
   scope, discovery Q4) — nothing here is publishable as-is.
4. **The first measurable lift is synthetic-track only** (cross-task continuity
   0.062 isolated → 0.188 shared store), and per Decision 24 the
   enterprise-workflow track is **uncalibrated against real ftp shapes**. Same
   freeze and caveats apply; synthetic↔real generalization is an open question
   (mem-bxhh), not an assumption.
5. **The `ours` arm's trigger information is baked in pre-run** (D23): the query
   comes from the held record's own stored trace errors. It is honestly labeled
   (`ours-oracle-triggered`) and the `ours-issue-trigger` control measures the
   trigger-information contribution, but any `ours` number you read carries this
   construct.
6. **Zero-links guard asymmetry:** `checkRecordLinks`
   (`src/cli/commands/build-store.ts:118`, verified 2026-07-07) throws on a
   full-corpus build with zero `record_links` but only **warns** on a single
   `--rig` build — a broken dependency ingest on a `--rig` build does not fail
   loudly.
7. **The LLM judge is L3, report-only.** It sits outside the pass/fail loop and
   must clear a frozen κ-set before any flag→void promotion; pre-isolation judge
   numbers were contaminated (mem-eacq incident — `mem-failure-archaeology`).
8. **Documentation lag is real.** The ledger's D21/D22 status lines
   ("branch-ready, not yet merged") are stale — both landed on main (verified
   via `git log` on the arm files, 2026-07-07). `orient.md` says "schema v8" vs
   code v11. When the ledger, orient.md, and the code disagree: code, then
   spec, then ledger.

---

## Part 4 — What must not change without a Decision

Conservative gating list (PROVISIONAL pending Stephanie, discovery Q4). Treat
each as HALT-branch-ready — prepare the change on a branch, ship tests with it,
and stop for Stephanie's sign-off; do not merge on your own judgment:

- Anything touching **temporal LOO / leak-safety** (Invariant 2's table, either
  language).
- **Oracle soundness**: the validity gate (gold diff must reproduce AND empty
  diff must fail), the landed-oracle semantics, admission gates.
- The **ablation headline** definition or any change to how a headline number is
  computed.
- Any **publishable number** or external claim (the `mem-0rrf` freeze is in
  force; release is a Stephanie call — the one fork put to her, `mem-1fl8`,
  was resolved 2026-06-18 as "kill the write-up call"; it re-opens only on
  her say-so).
- **Schema changes** to the three append-only tables, or any migration story
  other than `mem rebuild`.
- Reversing or bypassing any numbered Decision (that IS a new Decision).
- Any **distill/judge spend** beyond established patterns (judge-token capacity
  is a known block, `mem-a0cf`).

Changes outside this list follow normal repo gates (CI green in both languages;
see `mem-build-test-env` and `mem-git-and-dispatch-workflow`).

## Part 5 — Fast contract check

Run the bundled read-only diagnostic to confirm the contract's anchors still
hold in your checkout (drift here means this skill needs re-verification, not
that the code is wrong):

```bash
bash .claude/skills/mem-decision-ledger-and-architecture-contract/scripts/verify-contract.sh
```

It checks: schema version constant, strict `closedBefore`, sibling exclusions,
supersedes closure, the three append-only tables, the Python LOO mirror, the
landed-state enum, the synthetic one-path marker, the D23 `--no-trace-query`
control, and the ledger's decision count. All checks are greps — nothing is
executed or mutated.

---

## Provenance and maintenance

- Authored 2026-07-07 against `sjarmak/mem` working copy at branch `main`,
  HEAD `4e819e1`. Every path, line number, command, and status claim above was
  verified against that tree on that date. Volatile facts are date-stamped
  inline.
- Numbered-Decision count re-verify:
  `grep -cE "^[0-9]+\. \*\*" docs/architecture-decisions.md` (expect 24; if
  higher, this skill is missing rulings — read the new entries).
- Anchor re-verify (one line): `bash .claude/skills/mem-decision-ledger-and-architecture-contract/scripts/verify-contract.sh`
- Pin re-verify: `git rev-parse --short HEAD` (drift from `4e819e1` is expected
  and fine; re-run the contract check after large merges).
- PROVISIONAL markers in this skill hang on discovery questions Q1 (gas-city cwd
  as operational precondition) and Q4 (exact freeze scope + the HALT gating
  list); update them when Stephanie answers.
