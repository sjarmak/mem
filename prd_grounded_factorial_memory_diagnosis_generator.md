# PRD: Grounded Factorial Isolation-DAG Generator for Causal Diagnosis of Memory-System Failure/Benefit Modes

> **STATUS: CONDITIONAL — Gate 0 splits into two independent validities; only one is anchor-dependent.** Phase −1b proved structural feasibility (median width 5, 60/60 pilot_filter, single-factor isolation, grounded cost), but that pass is partly tautological. The reconciliation: **Gate 0a (instrument validity)** asks whether the generator + diagnosis discriminate memory *systems* along each authored factor — validated by realization + factor/interaction recovery against a label-shuffle control, *with no real tasks*. It gates the build of the diagnostic generator. **Gate 0b (generalization validity)** asks whether the synthetic per-config ranking predicts real agent behavior — validated by Spearman rank-correlation against a discriminating real anchor. It gates only the *generalization claim* and the at-scale build. Gate 0b is **currently uncomputable** — no real task set ranks memory configs non-flatly (mem-72sj / mem-bxhh.5 flat at N=8, span 0.125) — so the at-scale generalization claim is on hold and that flatness is itself a reported finding; the diagnostic instrument is **not** blocked by it. See `docs/mem-dag-phase-minus1b-recombiner-feasibility.md`.

## Convergence Outcome (post-debate)

A 3-position debate (Build-now / Kill-gate-first / Reframe-the-claim) converged in one round. The Build-now↔Kill-gate-first tension dissolved on two facts: (a) construct validity *is measured on generated traces*, so you cannot run the gate without a generator (Build-now is right); (b) you must not build *at scale* before it passes (Kill-gate-first is right). Reframe named the publishable object that makes the "when to build" dispute moot.

**Resolved plan:**
1. **Build only the minimal apparatus** — the smallest `factorial_dag.py` slice that emits ~6 isolation-DAG traces. This is the *apparatus*, not yet a durable asset; it is the throwaway-free way to make **Gate 0a** runnable. Expand it only to what Gate 0a and the diagnosis layer need; do **not** scale toward a generalization claim until **Gate 0b** passes.
2. **Run Gate 0a first, then Gate 0b** — separate the instrument from its generalization:
   - **Gate 0a (instrument validity, no real anchor):** flip each factor and confirm it moves a trace observable (realization), then confirm the diagnosis recovers the planted main effects + ≥1 interaction with the correct sign, distinct from a label-shuffle negative control. Pass → the diagnostic generator is a real instrument and may be built to the size the diagnosis layer needs. This gate never touches real tasks and is **not** flat-blocked.
   - **Gate 0b (generalization validity, real anchor):** the ~18–24-run graded rank-correlation probe — does the synthetic per-config ranking correlate (Spearman ρ ≥ 0.6, CI excl. 0) with the same arms' ranking on a discriminating real task set? Framed as *which-result-we-report*: ρ ≥ 0.6 → the generalization claim holds, scale the factorial; flat/uncomputable (the current state — no real anchor ranks configs non-flatly) → the headline becomes the **honest null** ("real agent-memory corpora are signal-poor; here is the spec a discriminating corpus must meet"), the at-scale generalization build stays on hold, and the diagnostic instrument from 0a ships as the artifact, with the killed observational-attribution v1 (stuck *because* real traces are width-1) as motivation.
3. **Forbid the tautology mechanically** — every factor must carry a condition where memory is *expected to hurt*; a factor with no HURTS condition does not enter the design.
4. **The publishable object is the interventional methodology + failure/benefit-mode taxonomy + the empirical result (positive or null)** — "CEBaB for agent memory." The generator is apparatus; the methodology/taxonomy/result is the contribution.

**Preserved dissent (Kill-gate-first), re-scoped to Gate 0b:** the original guard — "don't let the apparatus quietly become the at-scale build before validation" — now attaches to **generalization** (Gate 0b), not to the instrument. Gate 0a licenses building the diagnostic generator to the size 0a and the diagnosis layer need; what stays frozen until ρ ≥ 0.6 is *scaling the factorial to support a generalization / leaderboard claim*. The dissent's real target — silent scale-creep ahead of validation — is preserved, pointed at the one claim that needs the real anchor.

## Problem Statement

Multi-session agent-memory benchmarks (LongMemEval, LoCoMo, MemoryArena, BEAM, MemoryAgentBench) score *capabilities* but treat the memory system as a black box: they tell you *that* a config missed, not *which* mechanism (retrieval depth vs stale supersession vs distractor interference vs failed consolidation) caused it — or, on the positive side, *which* mechanism delivered a benefit. The failure-attribution subfield (Who&When, TRAIL) attacks this but works *observationally* on recorded traces and is stuck at 11–53% accuracy — and MEM's own Phase −1 probe showed exactly why observational attribution plateaus: recorded multi-session traces are width-1 chains, so there is no controlled contrast to attribute from.

To attribute causally you must **intervene by design**. This PRD proposes a generator that *authors* multi-session benchmark traces with an **isolation-DAG topology** — width-K independent branches, each isolating one factor — grounded by recombining real step content/cost from MEM's 432-bead / 1119-session recorded corpus. The DAG is the **experiment-design graph** (independent branches = orthogonal factors under one-at-a-time variation); parallelization is a free byproduct of factor-independence, never the objective. The payoff the recorded corpus can never produce: a per-config **failure AND benefit mode** map ("config X fails specifically under supersession×interference; benefits from consolidation only at retrieval-depth ≥ 5").

## Goals & Non-Goals

### Goals
- A `generators/factorial_dag.py` (Tier-0, pure-Python, seed-reproducible) that emits width-K isolation-DAG `BenchmarkSequence`s with a factorial layer over {retrieval-depth, consolidation, supersession/staleness, interference} × memory-arm, driving the **already-dormant** `SequenceStep` factor fields.
- A diagnosis layer that attributes per-config score deltas to factor **main effects + interactions** via the existing matched-pair / paired-bootstrap seam (`interruption.py` `matched_key`, `handoff_efficiency`).
- Grounding: recombine *real* cost tuples, file-read sets, and error signatures harvested from transcripts (cheap deterministic projections already in `cross_session.py`); author only the planted-fact prose.
- A **graded** metric (mem-g6a Option-C score vector) and **guaranteed retrieval coverage** by construction — fixing mem-p3w's two binding limiters (binary-metric flatness, 2/9 coverage).

### Non-Goals
- **NOT** "the first controlled memory benchmark" — ForgetEval (2606.15903) and OMAC (ICLR 2026) already occupy the forgetting / block-isolation slices. Claim only the all-four-axis factorial + benefit-mode + recombinant-grounding combination.
- **NOT** a throughput/parallelization play — that framing killed v1. Width is *experimental-validity evidence* (factor independence), full stop.
- **NOT** a leaderboard benchmark — this is a *diagnostic* generator (relative failure/benefit-mode rankings), which is the only framing partly immune to Goodhart.
- **NOT** main-effects-only — a design that authors clean single-factor branches and ignores interactions describes a world without the hard (interaction-driven) memory failures.
- **NOT** assuming "memory helps" — every factor must include conditions where memory is expected to *hurt* (pollution, stale-lesson misfire), and the generator must predict those correctly.

## Requirements

### Must-Have

- **Requirement (GATE 0a — INSTRUMENT VALIDITY; run FIRST; gates the diagnostic build; no real anchor): the suite discriminates memory systems along each authored factor.** On the ~6-trace minimal apparatus, run the 3-arm harness (`run_grid_3arm.py`, none-clean/ours/builtin) with the **graded** metric and show (i) **realization** — flipping each factor moves a trace observable (a high-interference cell injects distractors a top-k arm surfaces; a supersession cell makes v1 unretrievable), and (ii) **recovery** — the diagnosis layer recovers the planted main effects + ≥1 interaction with the correct sign.
  - Acceptance: every authored `(factor, level)` produces the expected directional change in its observable, AND a **label-shuffle negative control collapses** the recovered effects (recovery is signal, not noise). This gate uses **no real tasks** and is therefore **not** subject to the flat-anchor blocker. Pass → the diagnostic generator is a valid instrument and may be built to the size the diagnosis layer needs.

- **Requirement (GATE 0b — GENERALIZATION VALIDITY; gates only the generalization claim + at-scale build): external-rank-correlation against a discriminating real anchor.** Compare the synthetic per-config ranking (from Gate 0a's runs) to the ranking the same arms produce on a held-out *real* task set.
  - Acceptance: Spearman ρ ≥ 0.6 (CI excluding 0) between synthetic-suite and real-suite rankings, **hardened** by (a) abort if the anchor's own oracle−none span ≈ 0 (flat-anchor detector), (b) a label-shuffle control that collapses ρ, (c) a pre-registered min-detectable-ρ power analysis. **Current state: uncomputable** — no real task set ranks memory configs non-flatly (mem-72sj / mem-bxhh.5 flat, span 0.125 at N=8; apg.4 flat; the coding corpus "can't carry the signal"; scix_experiments unproven). While uncomputable, the **generalization claim and the at-scale factorial are on hold** — but the diagnostic instrument (Gate 0a) is not blocked. The null ("no real anchor discriminates") is itself a publishable finding; the standing fix is corpus expansion (real external repos with PR→CI outcome linkage) and forward-capture (mem-31kz), which when landed makes 0b computable.

- **Requirement: factorial generator driving the dormant schema fields.** `generate_factorial_family(seed, factors)` builds one `TopologySkeleton` per K, then layers the 2^k non-depth factors onto a frozen-K skeleton, writing each into `distractor_memories` (interference), `superseded_memory_ids` + v1→v2 distinct-id writes (supersession/staleness), `record_class`/`disposition` (consolidation oracle), and `expected_memory_reads` depth.
  - Acceptance: `antichain_width` is **invariant** across every non-depth factor toggle within a family (unit test, no agent runs) — the isolation guarantee; and every `(factor, level)` cell appears equally (balance test).

- **Requirement: runner acts on the factor fields (the real cost, not the generator).** Teach the runner to actually seed `distractor_memories` into the store and enforce `superseded_memory_ids` v1-staleness — both currently flagged "NOT wired into the skeleton runner yet" in `sequence.py`.
  - Acceptance: an integration test shows a high-interference cell injects N distractors into the retrieval store and a supersession cell makes v1 genuinely unretrievable (v2 only) — i.e. the toggles are not inert metadata.

- **Requirement: interaction coverage.** The factorial authors and recovers at least one non-additive interaction (e.g. staleness×interference) where memory is expected to *hurt*.
  - Acceptance: on a small graded pilot, the diagnosis layer reports a statistically significant interaction term whose sign matches the authored expectation (memory hurts), distinct from either main effect.

### Should-Have

- **Requirement: diagnosis reporter.** `report/factorial_diagnosis.py` consumes per-cell graded score vectors keyed by `matched_key` and emits per-factor main effects + interactions via paired-bootstrap.
  - Acceptance: produces a failure/benefit-mode map with effect sizes + CIs for a balanced family; pure arithmetic (ZFC-clean), all semantic judgment delegated to the graded-metric judge.

- **Requirement: cheap content harvest.** Recombine real `(turns, tool_calls, tokens)`, `files_read`, and `relaxed_signatures` from transcripts via `cross_session.build_session_view` into generated steps.
  - Acceptance: generated per-session cost distribution matches the real corpus (already shown: 145≈142); file-read/error-signature realism reported as a distribution match, with the honest caveat that *topology* is authored, not grounded.

- **Requirement: fractional design for budget.** Provide a Plackett-Burman / screening fractional design so main effects fit a bounded inference budget before any full factorial.
  - Acceptance: a documented design that estimates all main effects in ≤ K×arms×seeds runs, with the dropped interactions logged (no silent truncation).

### Nice-to-Have

- **Requirement: phenomenology validation against the real corpus.** Check that synthetic supersession cells reproduce the real corpus's failure-recurrence signature and that interference cells reproduce its redundant-read signature (`cross_session.py` metrics).
  - Acceptance: synthetic-cell recurrence/redundant-read rates fall within the real corpus's observed band.

## Design Considerations

**The load-bearing tension: cost-grounding is real but topology-grounding is not.** Phase −1b grounds per-branch *cost* (cheap-to-fake axis: 145≈142) while *authoring* the width-K topology — the exact structure the real corpus lacks (width 1). The contribution is framed as "a *designed* interventional probe"; the *external realism* of that topology is a **generalization** claim that lives on Gate 0b, not 0a. While Gate 0b is uncomputable, the topology-realism claim is unfalsifiable and must be stated as a scope bound, not asserted — but the instrument's diagnostic validity (Gate 0a) stands regardless, because 0a judges whether authored factors move observables, not whether the topology matches real traces.

**Three rungs of validity, not one.** (1) *Structural* — the 60/60 pilot_filter pass is tautological (plant→require→observe); it stays a *necessary* admission gate and proves nothing on its own. (2) *Instrument* (Gate 0a) — flipping a factor moves the right observable and the diagnosis recovers the planted effect vs a shuffle control; this is *sufficient to call the generator a valid diagnostic instrument*, and needs no real tasks. (3) *Generalization* (Gate 0b) — the synthetic ranking tracks a real anchor's ranking; this is what licenses claims about real agent behavior, and is the only rung the flat-anchor problem blocks. Collapsing (2) and (3) into a single kill-gate is the error this reconciliation fixes: it would let a missing real anchor veto a generator whose instrument validity is independently provable.

**Isolation vs entanglement.** Optimizing for single-factor isolation risks excluding interaction-driven failures — which is where real memory systems fail. Mitigated by making interaction coverage Must-Have, not an afterthought.

**Novelty is a narrow sliver.** Against ForgetEval/OMAC/CEBaB/CheckList, the only genuinely new claims are (a) all-four-axis factorial, (b) benefit-mode (not just failure-mode) attribution, (c) recombinant grounding from a real corpus. Read the ForgetEval PDF and BEAM/STATE-Bench primaries before writing the paper to nail the boundary.

**Cost is adverse.** Every factorial cell costs full agent inference (~145 turns); parallelism is wall-clock only (Phase −1 G2: ~0% cacheable). A 2^k × widths × arms × seeds design explodes — hence the fractional-design Should-Have and the "run more real tasks instead" alternative that **Gate 0b** must beat.

## Open Questions

- **Does MEM possess any real task set that ranks memory configs non-flatly?** This fact decides **Gate 0b** (generalization), not the whole project: it gates the generalization claim and the at-scale factorial, while Gate 0a (instrument validity) proceeds without it. Every artifact read suggests no today (mem-72sj / mem-bxhh.5 flat); resolve before making any generalization / leaderboard claim, not before building the diagnostic instrument.
- Is consolidation a factor *inside* the factorial or the outer arm-loop? (`consolidating_system.py` suggests it changes retrieval policy + provenance together — possibly not cleanly isolable.)
- How unbalanced does the factorial get if `pilot_filter` rejects high-interference cells at a higher rate? Over-generate + rejection-sample, or report a fractional design?
- Does the cheap harvest (real file-reads, error signatures) carry enough realism that planted-fact prose can stay Tier-0 authored, or does surface prose need a model (offline, frozen, `generator_version`-tagged)?
- Does ForgetEval's "Memora real-session cross-validation" already cover the grounding differentiator?

## Risk Registry (post-premortem)

Five failure lenses, all **Critical × High** (full analysis: `premortem_grounded_factorial_memory_diagnosis_generator.md`).

| # | Failure | Top mitigation |
|---|---|---|
| R1 | Gate 0b spuriously passes on noise (flat anchor ranked vs flat anchor) | flat-anchor detector + label-shuffle negative control + pre-registered power |
| R2 | No discriminating real anchor exists → Gate 0b uncomputable AND the honest null is unpublishable (needs ≥2 external corpora that pass the apg.8 legitimacy gate) | anchor-existence precondition for the generalization claim; corpus/outcome-linkage fix is the real critical path (does NOT block the Gate 0a instrument build) |
| R3 | Scooped — 4 real 2026 papers cover the axes (2603.02473, MemTrace 2605.28732, MemoryAgentBench, ForgetEval ⚠verify) | lead with reusable artifact; spearpoint the one open axis (real-corpus retrieval-depth) |
| R4 | Interaction-blindness — clean isolation excludes entangled real failures; find-only-what-you-author | blind interaction-recovery release gate; power-analyze 2^k; validate map predicts arm choice on a real workload |
| R5 | **Factors inert at runtime** — `distractor_memories`/`superseded_memory_ids` have zero runner consumers (grep-confirmed); every cell behaviorally identical | per-factor realization test as a hard gate (this IS Gate 0a); wire seeding once at the shared retrieve boundary; unify the oracle pool |

**Dominant meta-finding (Themes A+B converge):** the two binding constraints map cleanly onto the two validities — **no discriminating real anchor** (corpus-substrate/outcome-linkage problem, mem-7q6e) blocks **Gate 0b (generalization)**, and **the factor fields are inert at runtime** (pre-written into the schema comments) blocks **Gate 0a (instrument)**. The instrument blocker is local and fixable now (wire the seeding at the shared retrieve boundary, R5); the anchor blocker is upstream and larger than the generator. So the cheap **Gate-0a** experiment can run first regardless and must be hardened against a spurious noise-pass; the highest-value work for **Gate 0b** is the **outcome-linkage/corpus fix already in flight in the repo** (`docs/prd-task-agent-outcome-linkage.md`, `src/ingest/commitLinkage.ts`), with the generator as a downstream consumer.

### Mandatory plan changes (supersede the Convergence Outcome where stricter)

1. **Anchor-existence precondition gates Gate 0b, not the instrument build (corrected):** confirm a real task set ranks memory configs with a CI clearing 0 before making any *generalization* claim or scaling the factorial. If none (the current state) → the **generalization claim** is blocked and the standing redirect is the corpus/outcome-linkage fix — but the **diagnostic instrument** (Gate 0a) is buildable now, because its validity does not depend on a real anchor. The original "before any generator code" framing conflated the two validities and is superseded.
2. **Harden Gate 0b (generalization):** ρ ≥ 0.6 **and** (a) abort if the anchor's own oracle−none span ≈ 0, (b) label-shuffle control collapses ρ, (c) pre-registered min-detectable-ρ power analysis.
3. **Realization + recovery test gates all spend (this IS Gate 0a, instrument):** no factor enters the grid until flipping it provably moves a trace observable and the diagnosis recovers it vs a shuffle control; CI-fail any schema field with a generator producer and zero runtime consumer (R5).

## Research Provenance

Three independent lenses (full outputs in session history):

- **Prior Art & Methods Positioning** (Med-High): named the two real competitors (ForgetEval 2606.15903, OMAC ICLR'26); framed the nugget as "CEBaB for agent memory" → NeurIPS D&B/COLM; mapped 12 systems × {synthetic-gen, factorial-isolation, causal-attribution, real-grounded, memory-specific}; flagged that the killed v1 *is* the stuck observational-attribution subfield, converting the dead idea into motivation.
- **First-Principles Design Grounded in MEM** (High on design, Med on behavioral signal): showed the schema is 80% factorial-ready (dormant fields), `matched_key` is the attribution seam, the runner-acts-on-fields work is the real cost; specified `factorial_dag.py` + `factorial_diagnosis.py` + the harvest split (cheap structural vs expensive prose).
- **Contrarian / Construct Validity** (Med-High on diagnosis): showed C2 is tautological and C4 cost-grounding is a decoy while topology is the ungrounded axis the real corpus contradicts; exposed the behavioral anchor's weakness (7/9 byte-identical, 2/9 mixed-sign, apg.4 −0.007); made the construct-validity gate a true kill-gate (now split — the kill-gate is Gate 0b; instrument validity is the separate, anchor-free Gate 0a) and named the single cheapest de-risking experiment.

**Convergence:** methods frame right but narrow; sell width as factor-independence not throughput; build is tractable on existing spine; instrument validity (Gate 0a) is provable on the spine without a real anchor.
**Divergence (unresolved, → Gate 0b only):** is the authored topology *externally* valid, and does any real anchor exist to test it against? This is the generalization question; it does not gate the diagnostic instrument.
