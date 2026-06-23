# Risk-Annotated PRD: TASK → AGENT → OUTCOME Linkage Dataset

**For:** agentic-memory benchmark (`/home/ds/projects/mem`) — LOO eval of whether retained memory improves future agent work.
**Question:** How do we construct a maximally-complete, SOUND dataset linking TASK → AGENT → OUTCOME from the existing substrate, plus external methods worth adopting?
**Status:** Research complete (diverge → converge → premortem). Decision-ready. Several headline numbers were FALSIFIED by the premortem and are flagged inline.

---

## 1. Decision

**Build the linkage store MAXIMALLY (corpus-wide, tiered); compute the eval HEADLINE only on the sound tier, behind hard, mechanically-enforced gates.** Coverage (maximalist) and integrity (skeptic) are reconciled by separating the *store* (maximal, all tiers) from the *scored test set* (Tier-S floor). The eval harness must be physically incapable of emitting a pooled cross-tier number.

This is a **provenance-graph problem (PROV-O + SZZ-style linking), not a SWE-bench**. No public agent benchmark binds a real agent *trace* to its outcome; that linkage must be reconstructed from the software-provenance literature.

---

## 2. Verified substrate (measured; VERIFIED unless noted)

| Fact | Value | Verdict |
|------|-------|---------|
| work_records / record_agents / trace_runs | 7159 / 6200 / 3231 | VERIFIED |
| distinct session_uuids in store | 1008 | VERIFIED |
| `external_ref`/`pr`/`commit_sha`/`ci` populated | ~0% | VERIFIED (ingest artifact) |
| `base_commit` populated | 78.8% (5644) — TIMESTAMP-APPROXIMATE | VERIFIED |
| `landed_commit` (sound tier) | 0.4% (31) | VERIFIED |
| `trace_runs.session_uuid` | 100% (3231/3231) | VERIFIED — clean spine |
| `trace_path` populated / exist on disk | 45.9% / 98.4% of sampled | VERIFIED |
| session_uuid fan-out → multiple work_ids | 557/1008; inverse work_id→session = 0 | VERIFIED |
| rig=gascity records / prefix | 2799 (39.1%) / 100% `gc-` | VERIFIED |
| dashboard merged PRs (gastownhall/gascity-dashboard) | 118 | VERIFIED |
| gascity checkout topology | `/home/ds/gascity-main` is a **linked worktree** of `/home/ds/gascity` (shared object store); origin = **gastownhall/gascity** (map slug correct); `bd-gc-*` branches live as worktrees under `/home/ds/gascity-worktrees/` | VERIFIED this session |

**FALSIFIED claims that earlier drafts carried (do not propagate):**
- ❌ "gascity-main has no .git" AND its "correction" (both checkouts independent; origin=azanar fork) — BOTH WRONG. Truth: worktree alias, one object store, origin=gastownhall.
- ❌ "345 bd-gc-* branches" — actual **78** local `bd-gc-*` heads (345 = all-prefix total).
- ❌ "266/983 sessions = 27% live branch ref" — **NOT reproducible** via any substrate-derivable join; the only derivable join (store `gc-` token → live `bd-gc-*` ref) yields **72/2799 ≈ 2.6%**. There is no `gitBranch` column in the store; the 27% came from transcript-side parsing that has no validated path to a work_id yet. **The perishable-yield headline is UNVALIDATED.**
- ❌ "gc-/ga- namespace split" — **no `ga-` records exist** in the corpus. The number (39% gc-) is right; the disjointness is vs `dr-`/`co-`/dashboard `gascity-`, not `ga-`.
- ⚠️ "36-75 fully replayable" (T0 floor) — **UNVERIFIED**: depends entirely on un-run `gh` CI enrichment; store has 0% ci/pr/commit_sha.
- ⚠️ "~13% sound ceiling" — denominator-sensitive: 11.7% of *sessions*, **1.6% of work_records**. State as "% of sessions."

---

## 3. Architecture: tiered join-key ladder (attempt highest-precision first)

Tiers: **T0** replayable / **T1** CI-PR verified / **T2** commit-attribution / **T3** trace-attribution.

| # | Join key | Bridges | Tier | P | R | Fallback |
|---|----------|---------|------|---|---|----------|
| 1 | pr-link transcript event → PR# | transcript→GitHub | T1/T2 | .98 | 14% sessions (64 net-new) | #2 |
| 2 | dashboard merged-PR/CI oracle (PR#→merge SHA→CI→replay) | PR→commit→CI | T0/T1 | .99 | 118 PRs (⚠️ replayable count UNVERIFIED) | #3 |
| 3 | branch-slug + rig worktree → merge-base base + tip landed | transcript↔local git | T1/T2 | .95 | ⚠️ claimed 27%, MEASURED ~2.6% — must re-measure | #6 |
| 4 | branch-slug → bead (anchor `(...)`, FULL slug) | transcript↔work_record | T3 | .90 | branch recall high; slug-collision risk | #5 |
| 5 | commit-subject bead-slug (SZZ blame, PyDriller-SZZ) | git commit↔work_record | T2 | .85 | 2-21%/rig | #7 |
| 6 | base_commit by session-start date | session time↔history | T2 | .70 | 78.8% (approx) | #9 |
| 7 | PR-body/title → bead (LLM rerank, 2nd gate) | PR↔work_record | T2 | .76 P@1 | linked-PR subset (54% base unlinked) | #5 |
| 8 | CI-run → commit (Actions rollup) | CI↔merge SHA | T1 enricher | .99 | dashboard only | (enrich) |
| 9 | Dolt actor+ts → session (interactions.jsonl) | Dolt↔session | T3 disambig | mem 215/298 | local-only, no DoltHub remote | (terminal) |
| 10 | session_uuid ↔ trace_runs | store↔transcript | T3 spine | 1.0 | 100% | (terminal spine) |

---

## 4. Schema delta (PROV-O names on existing SQLite; NO triplestore)

Target file `/home/ds/projects/mem/src/store/schema.ts` (bump `SCHEMA_VERSION` 7→8; `links` after `record_links` at ~line 122).

- **New `links` table:** `(id, work_id, session_uuid, relation[wasGeneratedBy|wasAssociatedWith|used|wasInformedBy], entity_ref, entity_kind, key_type, tier, confidence, provenance, suspect, created_at)` + indexes; unique on `(work_id, entity_ref, relation, key_type)`. Reuses the `record_agents.sources/suspect` corroboration convention (`+`-joined provenance, `suspect=1` for contradicted edges).
- **`work_records += link_tier, link_source`** (projections; best/lowest tier reached).
- **Why separate from `record_links`:** that table is intra-corpus `dep|supersedes` only and has no confidence/tier column — widening its CHECK conflates two reasons-to-change (SRP).
- **`wasInformedBy`** = the memory edge (run B informed by memory from run A) — the relation the eval measures.

---

## 5. Build sequence (PERISHABLE-FIRST; assertions fail the build, never silent-pass)

**Step −1 (DAY 0, before all else) — FREEZE PERISHABLE EVIDENCE.** `git for-each-ref --format='%(refname) %(objectname) %(committerdate:iso)'` dump + `git bundle` of all `refs/heads/bd-*` and `gc-*` per rig, to durable storage. Same day: dump dashboard PR#→merge-SHA→CI-conclusion to durable storage (PRs decay too — squash+delete, token rotation). **Decouples merge-base computation from live-ref decay.** *Rationale: premortem modeled 266→~111 over 12 weeks at observed decay; harvest is a backup, and backups run first.*

**Step 0 (BLOCKING) — per-rig checkout+remote verification TABLE** (not a one-line finding). One row per rig: `checkout_path | git-common-dir | is_worktree | origin_url | authoritative_remote | base_ref | base_ref_resolves | n_session_branches_present`. Rule: `authoritative_remote` = the remote whose `<remote>/main` is an ancestor of the session-branch merge-base (`git merge-base --is-ancestor`), tie-broken by map slug. **FAIL CLOSED if two mapped rigs share a `git-common-dir` (worktree aliasing — gascity/gascity-main DO) or any `base_ref_resolves=N`.** Resolve cwd→rig via `git-common-dir`, never the path string.

1. **session_uuid spine** — assert `wasAssociatedWith` for all trace_runs (100%). T3 floor.
2. **RE-MEASURE the live-ref join** (do NOT trust 27%): build the validated branch-slug↔work_id resolver, then `for-each-ref`+`merge-base` over the bundle from Step −1. Report the REAL live-ref %. Each write gated by `git merge-base --is-ancestor <base_sha> <authoritative>/main` → else DROP with reason `base_not_on_authoritative_integration_branch`.
3. **pr-link event ingest** (incl. 64 net-new).
4. **dashboard merged-PR/CI oracle** → T0/T1 (gate auth + rate budget, see risks).
5. **branch-slug + commit-msg SZZ blame** (FULL anchored slug).
6. **commit-by-date base** (flagged approximate; never anchors a temporal wall).
7. **LLM-rerank PR-body + Dolt-actor disambiguation** (fan-out 557/1008).
8. **Project link_tier/link_source; per-tier coverage report.**

---

## 6. Three hard LEAKAGE gates for the eval (each emits dropped-with-reason)

- **(a) TEMPORAL WALL** — `t_outcome > t_task_start` strict; task prompt + retrieval may reference only artifacts with `timestamp < t_task_start`; back-translated task text is eval-side, frozen, never in the memory store; **DROP any task whose start derives from commit-by-date** (approximate start cannot anchor a wall).
- **(b) DIFF-OVERLAP** — per `(retrieved_memory, gold_patch)` hunk-level Jaccard; **calibrate PER RIG/REGIME** (premortem: a loose global 0.6 leaks the gold patch on the ~59%-trivial dashboard rig; set ~0.2 there). Hard-reject any task where top memory shares a file+hunk-anchor with gold, regardless of Jaccard. Strip raw diffs+SHAs from memory before store. (The one allowed ZFC mechanical-similarity exception.)
- **(c) LOO DEDUP** — canonical identity = **FULL-anchored-slug ∪ branch-root ∪ landed_commit**; partition whole groups, never split; dedup on the UNION. Build assertion: **no two records in different LOO partitions may share a branch-root** (catches run-1/run-2 of the same bead, and pr-link-vs-session_uuid double-entry).

---

## 7. Expected dataset size per tier

| Tier | Definition | Est. size | Confidence |
|------|-----------|-----------|-----------|
| T0 replayable | base+merge SHA pinned, oracle flips verdict | 36-75 sessions | ⚠️ UNVERIFIED — needs gh CI enrichment |
| T1 CI/PR verified | merge SHA + CI rollup | ~118 PRs / ~140 sessions | dashboard VERIFIED at 118 PRs |
| T1/T2 live-ref replayable base | base+landed from live ref | ⚠️ claimed ~266; **MEASURED ~2.6% (72)** — RE-MEASURE | LOW until step 2 redone |
| T2 commit attribution | real commit, approx/blame base | ~600-800 records | medium |
| T3 trace attribution | task↔session↔agent, no verified outcome | ~983 sessions / up to 6200 record_agents | high (spine VERIFIED) |

**HONEST HEADLINE:** sound oracle (T0+T1) ≈ **11.7% of sessions (1.6% of work_records)**, single-rig-dominated (gascity_dashboard). State the denominator explicitly.

---

## 8. Required bias-limitation statement (mechanically enforced, in body)

> The sound-tier population is non-random and single-repo-dominated (~11.7% of sessions, gascity_dashboard), drawn against a ~54% missing-link base rate (Bird et al.). Linked work skews trivial (~59% title-copy issue-commit links) and toward two orchestrator email identities. The "memory helps" claim has **internal validity on the replayable population; external validity is EXPLICITLY UNESTABLISHED.** Results may not transfer to direct-to-main workflows, multi-repo settings, or the non-linkable majority.

Enforcement: the eval harness **emits ONLY per-tier / per-regime / trivial-vs-nontrivial-split figures with required `tier` + `n` fields**; a pooled cross-tier accuracy must be impossible to compute from its output. LOO runs within-tier.

---

## 9. Premortem risk register (ranked likelihood × impact)

| # | Risk | L×I | Early-warning signal | Mechanical mitigation |
|---|------|-----|---------------------|----------------------|
| R1 | **session_uuid fan-out (557/1008) attaches WRONG work_id's verdict** → manufactures memory-arm "successes" (~15-18pp false-success injection) | Very high | `verdict_source_work_id != session_primary_work_id` counter; pass-rate climbs with fan-out degree | One session → ≤1 scored outcome; verdict anchors on LOO canonical identity; ambiguous fan-out = store-only/T3, never scored |
| R2 | **Live-ref yield is a fiction** — 27% claimed, ~2.6% derivable; perishable-asset urgency rests on an unvalidated, 4x-inflated pool | Very high | Step-2 re-measure returns single-digit % | Re-measure before committing to the tier; freeze refs Day 0 regardless; report REAL number; don't headline an unvalidated tier |
| R3 | **Wrong checkout/remote silently corrupts gascity provenance** (worktree aliasing gascity↔gascity-main; 14 remotes) → base SHAs absent from authoritative branch, written without error | High | Zero base-resolution drops in a 14-remote repo (silence = alarm) | `merge-base --is-ancestor <authoritative>/main` write-gate → DROP-with-reason; rig resolution via `git-common-dir`; fail-closed Step-0 table |
| R4 | **Diff-overlap gate mis-calibrated loose** → gold patch leaks as "memory" on the trivial headline rig | High | Per-rig Jaccard distribution; memory-arm pass-rate dose-response vs overlap | Per-rig threshold (~0.2 on trivial rigs); hard reject on shared file+hunk-anchor |
| R5 | **LOO dedup on short slug** → bead run-1 (store) / run-2 (test) leak across wall | Med-high | Per-task count of store memories sharing branch-root | Partition on FULL-anchored-slug; build assertion: no shared branch-root across partitions |
| R6 | **Dashboard oracle (sound core) drifts** — `gh` PAT in env var, GraphQL budget observed at remaining=30, squash-deleted PRs | Medium | PR query returning exactly 0 in batch; rate_limit trending to 0; queryable-PR count dropping | Assert `gh api user` login before harvest (fail closed); gate on rate budget before PR walk; snapshot oracle inputs Day 0 |
| R7 | **Bias statement ignored downstream** — pooled corpus-wide number quoted | Medium | Any accuracy without a tier label + n | Harness physically can't emit a pooled number; tier+n required fields |
| R8 | **Multi-cwd sessions (20-32%, ~197-315)** can't be assigned one rig → skipped or mis-assigned | Med | Missing `n_distinct_cwd` histogram | Resolve cwd→rig via `git-common-dir` (collapses worktree alias); assign rig owning the branch ref; DROP-with-reason `multi_rig_ambiguous` for residual |

---

## 10. What we CANNOT recover (and why)

1. **Squashed/rebased bases** (5-10% mislabel) — squash collapses base..head + reattributes author to merger. Cap via `gh pr --json author`; never eliminate. → T1/T2+suspect, never T0.
2. **gascity orchestration molecules** (`gc-`, 2799 = 39% of corpus) — `mol-*` meta-work about *other people's* PRs, no self-authored landable diff. Structurally **T3-capped**. (Note: the boundary is `gc-` vs `dr-`/`co-`/dashboard `gascity-`; there is NO `ga-` namespace — earlier framing was wrong.)
3. **Deleted/decayed branch refs** — lost permanently post-gc; the reason for Day-0 freeze.
4. **No-git / multi-cwd sessions / local-only Dolt** (no DoltHub remote) — spine T3 only.
5. **~54% general unlinked base rate** — the linked subset is inherently biased; the architecture makes it VISIBLE via tiering, it cannot fix it.

---

## 11. Adopted external methods (cited)

- **SZZ-style blame** (PyDriller-SZZ) for candidate bead↔commit (B-SZZ ~69% recall / mediocre precision → candidates, not ground truth).
- **Terminal-Bench satisfiable-oracle gate** — a bundle is sound only if gold replay flips the verdict.
- **τ-bench DB-state oracle + `pass^k`** reliability metric for non-test outcomes (measures consistency, the right shape for a memory eval).
- **R2E-Gym back-translation** for missing task text (EVAL-SIDE ONLY, never in the store).
- **LLM-reranked issue-commit linking** (EasyLink, ~75.9% P@1) as the 2nd gate after mechanical candidate generation (ZFC-correct: mechanism generates, model judges).
- **PROV-O** as the conceptual schema/vocabulary (Entity wasGeneratedBy Activity wasAssociatedWith Agent = literally task→agent→outcome; `wasInformedBy` = the memory edge). Skip the RDF triplestore.
- **SWE-bench-Live temporal cutoff** as the contamination-boundary model.

---

## 12. Highest-leverage build (single)

**The dashboard merged-PR/CI oracle bundle** (Step 4): the only path to a true replayable+CI-verified TASK→AGENT→OUTCOME tier. ~36-75 (pending verification) fully replayable, CI-attested bundles — the honest sound core the headline stands on. Non-perishable (merged PRs are stable) so it can follow the Day-0 perishable freeze, but its inputs (PR queryability, gh auth, rate budget) must be snapshotted early per R6.
