# Phase −1 Probe: Negative Result — DAG-Trace-Orchestration Premise Is Empty on This Corpus

**Status:** NO-GO. All three pre-registered gates fired. Do not build the Phase-0 cache/Merkle/scheduler machinery.
**Probe:** `memory-bench/scripts/phase_minus1_probe.py` → `.mem/phase-minus1-probe.json`
**Gating spec:** `prd_dag_trace_orchestration_agent_benchmarking.md` §"Mandatory plan change"

## What was measured

Three cheap, instrument-free measurements over the existing corpus — no cache code, stdlib only.

### G1 — DAG width (is there parallelism to exploit?)
- **Within-sequence memory-op DAG** (10 declared fixtures, edges from `expected_memory_reads ∩ expected_memory_writes`): antichain-width **median 2, max 3**, over 3–4-step hand-authored sequences. 7/10 are width 2, 3/10 are width 3.
- **Bead-session DAG** (432-bead recorded corpus): sessions/bead **median 2** (310 of 432 beads have exactly 2), modeled as a temporal chain → **antichain width 1**. No branching exists in the real recorded corpus.
- **Verdict: NO-GO (do not build scheduler).** The only width > 1 lives in tiny synthetic fixtures; the real corpus is a pure chain.

### G2 — Cost split (does caching the deterministic boundary save anything?)
- Agent-inference token mass: **396M tokens** (45.9M in / 350.5M out) across the corpus.
- Retrieval / grading / cross-session are deterministic arithmetic over already-extracted structure (`cross_session.py` docstring) → **~0 model tokens**.
- Deterministic-setup token fraction (upper bound): **~0%**.
- **Verdict: NO-GO (caching value prop fails).** Cost (dollars) is ~100% agent inference, which the PRD correctly forbids caching. Caching the deterministic boundary saves ~0% of spend. This confirms the contrarian R4 prediction exactly.
- *Honest caveat:* this is the token/dollar axis. Per-session **wall-clock env-setup** (Harbor construction) was not separately measured, but it is moot — G1 shows width-1 chains, so there is no parallel structure across which to amortize setup reuse anyway.

### G3 — Divergence / dependency density (is there memory-dependency structure to compile?)
- Cross-session read dependency: **116/687 consecutive pairs (16.9%)** share *any* read; **mean redundant-read fraction 0.10**.
- **Verdict: TRIVIAL.** 83% of session transitions share no read dependency; dependency density is too low for a meaningful compiled DAG, and divergence is undefined-to-trivial on near-chains.

## Decision

**EMPTY-PREMISE EXIT = True.** Per the pre-registered rule, all three NO-GO gates firing → **STOP**. Do not build `compile_dag` + det/stoch cache + `affected_cone` + scheduler as a benchmarking-throughput play. The premise — that multi-session agent trajectories contain exploitable DAG parallelism and cacheable deterministic cost — does not hold on MEM's corpus.

This corroborates, with direct measurement, what prior repo work already implied (`mem-apg.6` multi-session grid "not constructible"; `mem-n9` N=9 single-repo) and what the premortem's Themes A+B predicted.

## What remains worth doing (not gated out)

The probe kills the *systems/throughput* framing. It does **not** kill:
1. **The memory-conditioned counterfactual-replay validity question** (the methods/D&B nucleus) — but only after the corpus-substrate defect (`mem-7q6e`: no true per-worktree base SHA) is fixed and ≥1 genuine external repo is admitted. The corpus, not the orchestration, is the binding constraint.
2. **`affected_cone` as a MEM-internal ablation convenience** — only if it integrates into the existing runner without a rewrite, which is unproven and now low-priority given G2.

## Reproduction
```
python3 memory-bench/scripts/phase_minus1_probe.py
```
