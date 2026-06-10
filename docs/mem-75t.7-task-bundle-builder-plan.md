# mem-75t.7 — Task-Bundle Builder: Implementation Plan

**Status:** PLAN (for review) · **Parent epic:** mem-75t (trace substrate) · **Feeds:** mem-apg (grid inputs; oracle_context = oracle rung)

Turn the durable trace substrate into evaluable **task bundles** (`issue → trace → output → oracle_context`), porting codeprobe's mining methodology from a **merged-PR source** to a **bead-epic source**. Our corpus is bead-based and has **no PR linkage**, so the plan routes around it.

---

## 0. Why now (context)

The mem-apg.3.1 base-rate go/no-go (run 2026-06-10, 5 real gascity none-rung agent runs) returned **INSUFFICIENT_POWER with a NO_GO signal**: `recurrence_rate = 0.0` — the zero-memory agent resolved the held trace_error in all 3 path-reached beads. The **trace-error recurrence oracle lacks dynamic range** on this corpus. mem-75t.7's **gold-diff + dual-verifier** oracle is a stronger, recurrence-independent signal, and the bead's own stated convergence is: *the `oracle_context` IS the ablation 'oracle' rung; the bundle IS the grid's eval object.* So this is not just connective tissue — it is the **replacement oracle** for the axis that just came back weak.

---

## 1. The central design decision (decide before P0)

**Where does the gold diff (`output` leg) come from?**

Codeprobe's gold output is the **merged-PR diff** (`git diff sha^..sha`). mem lacks this:
- **No PR linkage** (mem-apg.5, structurally unrecoverable: ~1/5977 records carry an `external_ref`).
- **No produced/head commit.** mem-75t.3 captured only the **base** commit (session start), by the no-HEAD-leak invariant; `commit_sha` is 0/6557.

**Two options:**

| Option | Gold diff = | Pros | Cons |
|---|---|---|---|
| **A — trace-derived (RECOMMENDED)** | reconstruct from the transcript's `Edit`/`Write`/`MultiEdit` tool calls (`old_string`/`new_string`/`content`) | no PR, no head-commit dependency; the trace *is* the output record; reuses today's `_FILE_TOOLS`/`derive_files_touched` harbor harvest code | fidelity bounded by what the transcript recorded; non-file mutations (shell `mv`, codegen) not captured |
| **B — git base..head** | `git diff <base_commit>..<head_commit>` | exact, codeprobe-shaped | needs head-commit capture (not recorded; leak-risky to derive — the whole reason mem-75t.3 stopped at base) |

**Recommendation: Option A (trace-derived).** It is the port delta that makes mem-75t.7 viable on a PR-less corpus, and the base commit (mem-75t.3) is still used to check out the repo for oracle-context curation. **This is an eval-design fork — Stephanie's call (§7).**

---

## 2. The bundle (target schema) and what supplies each leg

Mirrors codeprobe `models/task.py` (`Task` + `TaskMetadata` + `TaskVerification`), bead-shaped.

| Leg | mem source | State |
|---|---|---|
| **issue** (`issue_title`/`issue_body`) | bead title/body (`work_records`), leak-guarded via existing `assert_no_outcome_leak` | ✅ have |
| **trace** | resolved transcript via `record_agents.trace_ref` / `trace_path` | ✅ have (874 transcripts, mem-75t.1) |
| **output** (gold diff) | trace-derived diff (Option A) | ⚠️ new — P0 keystone |
| **oracle_context** (`CuratedOracle`, `oracle_answer`, `oracle_tiers`) | grep/AST/(SG) consensus over repo@`base_commit` | 🟢 PR-free port — P2 |
| **verification** (dual) | `score_direct` (gold-diff/test) + `score_artifact` (file-list F1) | port — P4 |
| **`oracle_backends_consensus`** | union of backends contributing a kept oracle file (anti-tautology provenance) | port — P2 |

---

## 3. Prerequisites (before P0)

1. **Merge mem-75t.3** — it is DONE but **orphaned** on `feat/mem-75t.3-git-provenance` (commit `33bc857`). Until merged, `repo`/`base_commit` are not in main's store; its `SCHEMA_VERSION 1→2` bump requires a store rebuild (`mem build-store`).
2. **mem-75t.1 trace ingest** (in_progress) — largely landed (874 transcripts; 41 trace_error beads). Error-extractor widening (mem-75t.6: go/pytest/cargo/mypy/ruff/gradle) is already in the git log. Sufficient to start.

---

## 4. Phase decomposition (child beads)

### P0 — Trace→diff reconstructor `[mem-75t.7.1]` — KEYSTONE
Reconstruct the gold diff from a transcript's file-mutation tool calls.
- **In:** a resolved transcript (Claude Code `.jsonl` / stream-json).
- **Out:** a structured per-file diff (path, hunks/old→new), the `output` leg.
- **Reuse:** today's harbor harvest (`membench/harbor/harbor_exec.py` `_FILE_TOOLS`, `derive_files_touched`, `project_claude_stream`) already extracts `files_written` from `Edit`/`Write`/`MultiEdit`; extend to capture edit **content**.
- **Risk:** highest unknown — fidelity of trace-reconstructed diffs. **Build + validate FIRST on ~5 mem/gascity-dashboard beads** against the actual repo state where possible.
- **Acceptance:** for ≥5 real beads, produce a diff whose changed-file set matches `files_written`, with non-empty hunks; document any mutation classes the trace misses.
- **Deps:** prerequisites (merged provenance, traces).

### P1 — Bundle schema + assembler `[mem-75t.7.2]`
- Define the membench bundle dataclasses mirroring `models/task.py` (frozen, leak-guarded).
- Assemble: `issue` (bead, leak-checked) + `trace_ref` + `output` (P0 diff) + placeholder `oracle_context` (filled by P2).
- **Reuse:** `assert_no_outcome_leak`, `validity.query_from_record` (LOO boundary).
- **Acceptance:** emit a valid bundle for the P0 validation beads; leak guard fires on a planted outcome value.
- **Deps:** P0.

### P2 — Oracle curation (consensus + curator) `[mem-75t.7.3]` — highest-value port
- Port codeprobe `consensus.py` (backends: grep + AST; Sourcegraph optional) + `oracle_curator.py`:
  - **Tier-1 `required`:** file reported by ≥ `min_backends` (default 2) — arithmetic, no LLM.
  - **Tier-2 `supplementary`:** single-backend → Haiku keep/reject (bounded snippet, strict JSON); reject → quarantined with rationale (never silently dropped).
  - Emit `CuratedOracle` (per-file backend provenance) + `oracle_backends_consensus`.
- **AST caveat:** codeprobe ships Python/Go resolvers. mem's real rigs are **Go (gascity) + TS (gascity_dashboard, mem)** — Go ports directly; **TS needs an AST backend or grep+SG-only to start** (fork §7).
- **ZFC:** Tier-2 keep/reject and scoping are model-delegated; consensus arithmetic + structural validation are mechanism.
- **Acceptance:** curate oracle_context for the validation beads; consensus quarantine fires below the F1 threshold; `oracle_backends_consensus` populated.
- **Deps:** P1 (needs repo@base_commit checkout + candidate symbols/files, which the bundle/trace supply).

### P3 — SELECT / assess `[mem-75t.7.4]`
- Port codeprobe assess rubric, **replacing merge-history signals** (`list_merged_prs`, merge-commit counts) with bead-count / commit-count per repo. Score benchmarking potential; pick self-contained units of work.
- **ZFC:** the model picks well-scoped tasks; mechanism gathers signals.
- **Acceptance:** rank a rig's beads by benchmarking potential with a transparent rubric; top-N are demonstrably self-contained.
- **Deps:** P1 (operates over assembled candidates).

### P4 — Dual-verifier scoring `[mem-75t.7.5]`
- Port the two **independent** legs (both always run, both sub-scores preserved):
  - **(a) direct:** gold-diff / test reproduction (`score_direct`).
  - **(b) comprehension:** F1 of the agent's identified files/symbols vs the oracle file list (`score_artifact`), with `oracle_tiers` weighted F1.
- `scoring_policy` ∈ {min, mean, weighted}; default `automated_score = score_direct`; graceful degradation when a leg's input is missing.
- **Acceptance:** score a completed agent run on a bundle, emitting both sub-scores; missing-artifact path yields artifact=0.0 with the direct leg intact.
- **Deps:** P1 (bundle), P2 (oracle answer).

---

## 5. Coverage reality (how many bundles)
- **Trace-derived diff (P0):** bounded by resolved transcripts that did file edits (~874) — far larger than the 41-bead trace_error set.
- **Oracle-context (P2, needs a repo checkout):** bounded by `work_dir`-bearing records (~458/6557) → realistically **gascity, gascity_dashboard, mem, EnterpriseBench, codeprobe**. Scope the first batch there.

---

## 6. Sequencing
```
prereqs (merge mem-75t.3 + rebuild store)
   └─ P0 trace→diff  ──┐
                       ├─ P1 bundle schema ──┬─ P2 oracle curation ──┐
                       │                     ├─ P3 select/assess     ├─ P4 dual-verifier
                       │                     └─────────────────────────┘
```
P0 is the gate: validate trace-diff fidelity before investing in P1–P4.

---

## 7. Eval-design forks — Stephanie's call (surface, do not improvise)
1. **Gold-diff source** (§1): trace-derived (recommended) vs git base..head (needs head-commit capture).
2. **Bundle schema specifics** — which provenance fields are first-class (likely the home for mem-3ab provenance capture).
3. **Oracle backends for TS rigs** — port a TS AST backend, or start grep+Sourcegraph-only.
4. **Bundle batch scope** — which rigs/beads for the first evaluable set.

---

## 8. Reuse boundaries (what's a port vs new)
- **New:** P0 trace→diff reconstructor (keystone); the bead-shaped bundle schema (P1).
- **Direct port (codeprobe):** consensus/curator (P2), assess rubric minus merge-signals (P3), dual-verifier (P4). These already operate on `(commit, repo, changed_files)` — the only structural swap is the candidate stream (bead+commit instead of merged-PR) and the diff range (trace-diff instead of `sha^..sha`).
- **Reuse (mem):** `assert_no_outcome_leak`, `validity.query_from_record`, today's harbor harvest (`_FILE_TOOLS`/`derive_files_touched`/`project_claude_stream`), mem-75t.3 provenance (`repo`/`base_commit`), mem-75t.1 trace_ref.

**codeprobe references:** `src/codeprobe/models/task.py`, `mining/{oracle_curator,consensus,curator,confidence,cross_validate,extractor}.py`, `assess/heuristics.py`, `prd_dual_verifier_mining.md`.

---

## 9. Review revisions (2026-06-10 — ACCEPTED, supersedes conflicting text above)

1. **P0 = replay, not reconstruction** (`mem-75t.7.1`, bumped P1). Check out `repo@base_commit` (reuse `env_recon`), replay the transcript's Edit/Write/MultiEdit calls **in order** against the checkout, then `git diff` is the gold diff. Real applyable diffs (near-Option-B exactness for the file-edit class); every `old_string` mismatch is a *detected* drift, so fidelity failure classes fall out mechanically. **Acceptance replaced** — the original (changed-file set vs `files_written`) was circular: both sides derive from the same tool calls. New: replay success rate with classified mismatches, on ≥5 beads with checkoutable base commits.
2. **New GATE `mem-75t.7.6` (P1) blocks P2 and full P4.** A new oracle does not guarantee dynamic range — the exact failure that killed the recurrence oracle. Thin slice (P0 + minimal P1 bundles + a **probe-grade** `score_direct` living in the gate) runs none-rung vs a *cheap* upper-bound rung (gold-diff file list / trace-as-context — no curation needed) on ~10 admitted bundles. Measurable gap ⇒ GO for the .3/.5 ports; none ≈ oracle ⇒ NO-GO, revisit admission/SELECT first.
3. **Admission filter in P1** (`mem-75t.7.2`): trace-derived gold diff = "what the agent did," not "the correct fix" (no merged-PR/CI signal in this corpus). Admit only: bead closed **and** clean trace tail (no unresolved `trace_errors` in the final segment). Bundle also carries env-recon fields (`repo`, `base_commit`, `base_image`) — a self-contained *runnable* eval object — and stores its **LOO-excluded record IDs** (own + sibling traces) so the exclusion is a mechanical invariant, not a convention.
4. **P3 SELECT unhooked from P1** (`mem-75t.7.4`, bumped P1): ranks candidates from bead/trace/repo metadata, needs no bundles; runs **parallel to P0** and picks the P0 validation beads + first batch. Rubric gains **env-reconstructable** (via `env_recon.py` checkout-ability).
5. **P4 `score_direct` primary = gold-test reproduction** (apply candidate diff, run gold tests — fail-to-pass), diff-similarity as fallback. **Efficiency axis** (tokens/turns/tool-calls from the trace) recorded as first-class sub-scores — the metric most likely to retain dynamic range if success-rate saturates.
6. **P2 forks resolved**: TS rigs start grep+Sourcegraph-only (Tier-2 quarantine rate decides if a TS AST backend is ever needed); Tier-2 keep/reject routes through the OAuth Claude runtime, not paid API.

**Prerequisites resolved 2026-06-10:** `mem-75t.3` (33bc857) was already in `main`; store rebuilt at SCHEMA_VERSION 3 with `--with-traces --with-provenance` from the gas-city cwd (gc session resolution needs `city.toml`) — `repo` 482, `base_commit` 133 populated (previously 0).

**Revised sequencing:**
```
P0 .1 replay reconstructor ──┐                          ┌─ P2 .3 oracle curation
P3 .4 SELECT/assess ─────────┼─ P1 .2 bundles+admission ┼─ GATE .6 go/no-go ──┤
                             │  (probe scorer in .6)    └─ P4 .5 dual-verifier (full)
```
