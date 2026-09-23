# Premortem: Grounded Factorial Isolation-DAG Generator

Five independent failure lenses, 6-months-hence postmortems, grounded in the actual repo + live web search. All five rated **Critical × High = 12**.

## 1. Risk Registry

| # | Lens | Sev × Lik | Score | Root cause | Top mitigation |
|---|---|---|---|---|---|
| R1 | Construct validity | C × H | 12 | Gate 0 run against the one anchor MEM knows is flat (n=9 single-rig) → noise-on-noise ρ spuriously clears 0.6; HURTS mechanisms authored as metadata but never executed | Flat-anchor detector (abort if anchor's own oracle−none ≈ 0); label-shuffle negative control (ρ must collapse); realization test before any spend |
| R2 | No discriminating real anchor | C × H | 12 | Both the validation path AND the "honest null" exit depend on a discriminating real anchor the corpus can't provide and external corpora can't supply (apg.6: 2 bundles/0 anchorable; apg.8: CSB/EB are eval-noise; scix unproven) | Check anchor-existence in week 1 as a precondition; scope the corpus/outcome-linkage fix (mem-7q6e) as the actual critical path, generator downstream |
| R3 | Scooped / unpublishable | C × H | 12 | Novelty is a *combination* of 4 individually-published axes + grounding wrapper; field publishes memory-eval weekly | Lead with the reusable artifact (generator+corpus), not the "X for memory" analogy; spearpoint the one open axis (real-corpus retrieval-depth sweep); kill benefit-mode headline once real anchors read flat |
| R4 | Scope / interaction-blindness | C × H | 12 | Clean single-factor isolation structurally excludes entangled higher-order failures (the real 40–60% regime); hand-authored interactions only rediscover planted failures; budget forces fractional design dropping the interaction cells | Author HURTS interaction as first-class generated cell with a blind held-out recovery test; power-analyze 2^k up front; validate map predicts arm choice on a real un-authored workload |
| R5 | Integration / runner-inert fields | C × H | 12 | `distractor_memories`/`superseded_memory_ids` have **zero consumers outside schema** (confirmed by grep); two divergent oracle pools; factor toggles change nothing at runtime → every cell behaviorally identical | Per-factor realization test (flipping a factor must move an observable in the trace) as a hard gate; wire seeding through `RetrieveResult.distractor_ids`; unify the oracle pool; CI-fail on schema field with a generator producer but no runtime consumer |

## 2. Cross-Cutting Themes (multi-lens convergence = highest priority)

**Theme A — No discriminating real anchor is THE binding constraint (R1, R2, R4).** Three lenses independently converge: MEM's only reachable real anchor is the flat n=9 single-rig pool (mem-apg.4 oracle−none = −0.007; mem-n9 hard oracle 1/24; mem-p3w 7/9 byte-identical). Gate 0 against it is either *uncomputable* (R2) or *spuriously passes on noise* (R1). And the "honest null" exit is unpublishable without ≥2 independent external corpora that survive the mem-apg.8 legitimacy gate, which the team can't obtain. **This is upstream of the entire generator** — it's a corpus-substrate/outcome-linkage problem (mem-7q6e: no true per-worktree base SHA), a project larger than the generator itself. *(Note: an outcome-linkage PRD + `commitLinkage` ingest are already in flight in the repo — that is the real critical path.)*

**Theme B — The factors are inert at runtime, pre-written into the schema (R5, R1).** `sequence.py` literally says `distractor_memories` and `superseded_memory_ids` are "NOT wired into the skeleton runner yet." Grep confirms zero non-schema consumers. So without the wiring work — which touches every arm's store interface and the Harbor adapter (which builds its oracle pool from `expected_memory_writes` only, with last-write-wins) — toggling interference/supersession changes nothing, every factorial cell is byte-identical, and the diagnosis reports tight, beautiful, meaningless nulls. This failure requires *no drift or bad luck* — shipping as-is guarantees it.

**Theme C — Find-only-what-you-author (R4, R1).** Single-factor clean isolation + hand-authored interactions means the apparatus can only recover failures the team planted, while real memory systems fail on entangled multi-factor dependencies the design factors out. The tautology the PRD forbids in the schema reappears at runtime and in the interaction design.

**Theme D — The white space closed during planning (R3).** Live search surfaced real 2026 preemptors for each differentiator: **arXiv:2603.02473** (retrieval-vs-utilization diagnosis, Beneficial/Harmful/Ignored/Neutral classification, counterfactual, on real LoCoMo — scoops benefit-mode); **MemTrace arXiv:2605.28732** (memory-pipeline DAG + counterfactual Decisive Error Sets root-cause — scoops the isolation-DAG framing); **MemoryAgentBench ICLR'26** (FactConsolidation counterfactual construction); **ForgetEval 2606.15903** (forgetting axis). ⚠ *These citations are sub-agent-reported; verify directly before relying on them.*

## 3. Mitigation Priority List

| Mitigation | Addresses | Cost | Priority |
|---|---|---|---|
| **Anchor-existence precondition** (week 1): does ANY reachable real task set rank configs with a CI clearing 0? If no → generator is blocked by definition | R2, A | Low | **1** |
| **Harden Gate 0**: flat-anchor detector + label-shuffle negative control + pre-registered power analysis (min detectable ρ given ~6–9 configs) | R1, A | Low | **2** |
| **Per-factor realization test** as a hard gate (flip factor → observable moves in trace); CI-fail on schema field with generator producer + zero runtime consumer | R5, B | Medium | **3** |
| **Wire the runner once** through `RetrieveResult.distractor_ids` + unified supersession-aware oracle pool (shared by `conditions.py` and Harbor `adapter.py`) | R5, B | High | 4 |
| **Reframe deliverable** to the reusable artifact + spearpoint the one open axis (real-corpus retrieval-depth sweep); kill benefit-mode headline if anchors read flat | R3, D | Low | 5 |
| **Blind interaction recovery test** + up-front power analysis / resolution-IV+ fractional design | R4, C | Medium | 6 |
| **Scope the corpus/outcome-linkage fix as the real critical path**, generator as downstream consumer | R2, A | High | 7 |

## 4. Design Modification Recommendations (top 5)

1. **Make Gate 0 un-foolable, and check anchor-existence first.** Addresses R1/R2/Theme A. Before building, confirm a real task set ranks configs non-flatly (CI clears 0). Then Gate 0 = ρ ≥ 0.6 **plus** (a) abort if the anchor's own oracle−none span ≈ 0, (b) label-shuffle control that must collapse ρ, (c) pre-registered power. Effort: Low. This is the single highest-leverage change — it converts the most likely failure (silent noise-pass) into an early honest stop.
2. **Realization test gates all spend.** Addresses R5/Theme B. No factor enters the grid until flipping it provably moves a trace observable; CI fails on any schema field with a generator producer and zero runtime consumer. Wire seeding/supersession once at the shared retrieve boundary, not per-arm. Effort: Medium→High.
3. **Treat the corpus/outcome-linkage fix as the actual critical path.** Addresses R2/Theme A. The generator is blocked on a discriminating anchor, which is a substrate problem (mem-7q6e + the in-flight outcome-linkage PRD). Sequence that *first*; the generator consumes it. Effort: High (but already partly underway in the repo).
4. **Reframe + spearpoint.** Addresses R3/R4/Theme D. Lead with the reusable generator+corpus artifact (D&B rewards tooling), not the "CEBaB for memory" analogy reviewers read as incremental; differentiate on the one genuinely open axis competitors disclaimed (real-corpus retrieval-depth sweep; MemTrace used synthetic trajectories, 2603.02473 fixed k=5). Effort: Low.
5. **Blind interaction recovery as a release gate.** Addresses R4/Theme C. A second party plants interactions the generator authors don't know about; "did diagnosis recover an un-planted interaction" is the gate that defends against find-only-what-you-author. Effort: Medium.

## 5. Confidence Note
Unusually high confidence: every theme is corroborated by named repo artifacts (mem-apg.4/.6/.8, mem-n9, mem-7q6e, mem-p3w) and, for R5, by direct code inspection (grep: factor fields have no runtime consumer; two divergent oracle pools). The meta-finding — **the binding constraint is upstream corpus discrimination, not the generator** — is the dominant signal and aligns the entire registry. Theme D's competitive citations are sub-agent-reported and should be verified.
