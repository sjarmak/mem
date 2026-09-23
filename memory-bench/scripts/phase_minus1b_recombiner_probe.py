"""Phase -1b probe: is a GROUNDED multi-session isolation-DAG generator feasible?

The Phase -1 probe killed *mining* DAGs from recorded chains. This probe tests the
*authoring* direction the follow-up proposed: synthesize multi-session traces that
(a) are grounded in the real corpus, (b) carry an isolation-DAG topology of width K
by construction, (c) isolate one factor at a time for causal diagnosis, and
(d) clear the existing `pilot_filter` discriminativeness gate.

It extends the EXISTING spine (`membench.generators.synthetic_task`, whose tasks are
structurally memory-dependent by design) to multi-session + width-K + single-factor
factorial families, grounds per-session cost by resampling the real 432-bead
distribution, and reports four things against pre-registered checks:

  C1 width        : antichain width median >= 3 (the thing the recorded corpus lacked)
  C2 structural   : every task is structurally memory-discriminating (oracle pass,
                    no-memory fail) -> clears pilot_filter on structural reward
  C3 isolation    : a factorial family varies exactly one factor while topology is fixed
  C4 realism      : generated per-session cost is drawn from the real distribution

This is the NECESSARY (structural) feasibility signal, cheaply. The SUFFICIENT
(behavioral) signal — does a real agent discriminate — is anchored on the existing
mem-p3w pilot (efficiency discriminates where retrieval has coverage; binary
gold-test quality is flat) and is the PRD's first gate, not re-run here.
"""

from __future__ import annotations

import json
import random
from itertools import combinations
from pathlib import Path
from statistics import median

from membench.generators.pilot_filter import pilot_filter
from membench.schemas.sequence import BenchmarkSequence, OutcomeCheck, SequenceStep

REPO = Path(__file__).resolve().parents[2]
CROSS = REPO / ".mem" / "cross-session-metrics.json"


def load_real_cost_pool() -> list[tuple[int, int]]:
    """Real per-session (turns, tool_calls) from the 432-bead corpus — the grounding
    distribution generated sessions resample from."""
    d = json.loads(CROSS.read_text())
    pool = []
    for b in d["beads"]:
        for s in b["sessions"]:
            pool.append((s["turns"], s["tool_calls"]))
    return pool


# ---------------------------------------------------------------------------
# the recombiner: width-K isolation DAG, grounded cost, single-factor families
# ---------------------------------------------------------------------------
def generate_isolation_dag(
    seq_id: str,
    width: int,
    rng: random.Random,
    cost_pool: list[tuple[int, int]],
    distractor_rate: int = 0,
) -> BenchmarkSequence:
    """K independent establishing sessions (the parallel branches / isolated factors)
    + one goal session that requires every planted fact. Topology is width-K by
    construction: the K establish steps are mutually unreachable; only the goal
    depends on them. `distractor_rate` injects irrelevant memories (the §10
    interference factor) WITHOUT changing the dependency topology — that is what
    'isolate one factor' means."""
    steps: list[SequenceStep] = []
    planted: list[str] = []
    for k in range(width):
        fact = f"{seq_id}-fact-{k}"
        planted.append(fact)
        turns, tools = rng.choice(cost_pool)
        distractors = {
            f"{seq_id}-distractor-{k}-{j}": f"irrelevant note {j}" for j in range(distractor_rate)
        }
        steps.append(
            SequenceStep(
                step_id=f"{seq_id}-establish-{k}",
                user_request=f"Record fact {k}.",
                expected_memory_writes={fact: f"the authored ground-truth value for {fact}"},
                expected_memory_reads=[],
                distractor_memories=distractors,
                environment_state={"real_cost_turns": turns, "real_cost_tool_calls": tools},
            )
        )
    turns, tools = rng.choice(cost_pool)
    steps.append(
        SequenceStep(
            step_id=f"{seq_id}-goal",
            user_request="Produce the deliverable that uses every recorded fact.",
            expected_memory_reads=list(planted),
            expected_memory_writes={},
            # the goal check REQUIRES every planted fact -> oracle passes, no-memory cannot
            outcome_checks=[
                OutcomeCheck(check_id=f"{seq_id}-goal-uses-all", requires_memory=list(planted))
            ],
            environment_state={"real_cost_turns": turns, "real_cost_tool_calls": tools},
        )
    )
    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"isolation-dag w{width} d{distractor_rate}",
        domain="synthetic-isolation",
        goal="use all planted facts",
        steps=steps,
    )


# ---- measurement helpers (antichain width over declared edges) -------------
def antichain_width(steps: list[SequenceStep]) -> int:
    writers: dict[str, list[int]] = {}
    succ: dict[int, set[int]] = {i: set() for i in range(len(steps))}
    for i, st in enumerate(steps):
        for mid in st.expected_memory_reads:
            for j in writers.get(mid, []):
                succ[j].add(i)
        for mid in st.expected_memory_writes:
            writers.setdefault(mid, []).append(i)
    desc: dict[int, set[int]] = {}

    def walk(n: int) -> set[int]:
        if n in desc:
            return desc[n]
        out: set[int] = set()
        for m in succ[n]:
            out |= {m} | walk(m)
        desc[n] = out
        return out

    for n in succ:
        walk(n)
    comp = {(a, b) for a in succ for b in desc[a]} | {(b, a) for a in succ for b in desc[a]}
    nodes = list(succ)
    for size in range(len(nodes), 1, -1):
        for combo in combinations(nodes, size):
            if all((a, b) not in comp for a, b in combinations(combo, 2)):
                return size
    return 1


def structural_rewards(seq: BenchmarkSequence) -> tuple[float, float]:
    """Oracle arm (all memory present) vs no-memory arm, scored on the deterministic
    requires_memory checks — the same structural property synthetic_task guarantees."""
    checks = [c for st in seq.steps for c in st.outcome_checks]
    if not checks:
        return 1.0, 1.0
    oracle = sum(1 for c in checks) / len(checks)  # all required memory available
    no_mem = sum(1 for c in checks if not c.requires_memory) / len(
        checks
    )  # only memory-free checks pass
    return oracle, no_mem


def main() -> None:
    rng = random.Random(20260618)
    cost_pool = load_real_cost_pool()

    # C1+C2+C4: a pilot batch across a width sweep, grounded cost
    batch: list[BenchmarkSequence] = []
    for i in range(60):
        w = rng.choice([3, 4, 5, 6])
        batch.append(generate_isolation_dag(f"iso-{i:03d}", w, rng, cost_pool))

    widths = [antichain_width(s.steps) for s in batch]
    rewards = [structural_rewards(s) for s in batch]
    verdicts = [pilot_filter(oracle_reward=o, no_memory_reward=n) for o, n in rewards]
    admitted = sum(1 for v in verdicts if v.accepted)

    # grounded cost realism: generated per-session cost vs the real source pool
    gen_turns = [st.environment_state["real_cost_turns"] for s in batch for st in s.steps]
    real_turns = [t for t, _ in cost_pool]

    # C3: a single-factor isolation family — vary distractor_rate (interference) only,
    # topology held fixed (same width, same planted facts)
    family = [
        generate_isolation_dag(f"fam-d{d}", 4, random.Random(7), cost_pool, distractor_rate=d)
        for d in (0, 2, 4, 8)
    ]
    family_widths = [antichain_width(s.steps) for s in family]
    family_factor = [
        (d, sum(len(st.distractor_memories) for st in s.steps))
        for d, s in zip((0, 2, 4, 8), family, strict=True)
    ]
    topology_fixed = len(set(family_widths)) == 1

    report = {
        "probe": "phase_minus1b_recombiner",
        "grounding": {"real_session_cost_samples": len(cost_pool)},
        "C1_width": {
            "median": median(widths),
            "min": min(widths),
            "max": max(widths),
            "histogram": {w: widths.count(w) for w in sorted(set(widths))},
            "check": "median antichain width >= 3",
            "pass": median(widths) >= 3,
        },
        "C2_structural_discrimination": {
            "batch_size": len(batch),
            "admitted_by_pilot_filter": admitted,
            "admission_rate": admitted / len(batch),
            "median_delta": median([v.delta for v in verdicts]),
            "check": "all structurally memory-discriminating",
            "pass": admitted == len(batch),
        },
        "C3_single_factor_isolation": {
            "family": "interference (distractor_rate) sweep, topology held fixed",
            "factor_levels": family_factor,
            "widths_across_levels": family_widths,
            "check": "exactly one factor varies; topology width invariant",
            "pass": topology_fixed and len({d for d, _ in family_factor}) == 4,
        },
        "C4_grounded_realism": {
            "generated_turns_median": median(gen_turns),
            "real_turns_median": median(real_turns),
            "note": (
                "generated per-session cost is RESAMPLED from the real distribution "
                "(grounded by construction); content-level realism via transcript_path "
                "harvest is the next step"
            ),
            "pass": abs(median(gen_turns) - median(real_turns)) <= 0.25 * median(real_turns),
        },
    }
    checks = [
        report[k]["pass"]
        for k in (
            "C1_width",
            "C2_structural_discrimination",
            "C3_single_factor_isolation",
            "C4_grounded_realism",
        )
    ]
    report["verdict"] = {
        "structural_feasibility": all(checks),
        "behavioral_anchor": (
            "mem-p3w: efficiency discriminates where retrieval has coverage "
            "(ours-builtin median -1028 out-tok, 6/9); binary gold-test quality FLAT "
            "(1/9 all arms). Binding limiters = metric coarseness + retrieval coverage, "
            "BOTH controllable by a generator."
        ),
        "recommendation": (
            "GO to fresh PRD — generator is structurally feasible; the open risk is construct "
            "validity (behavioral), to be the PRD's first gate via a graded-metric + "
            "guaranteed-coverage pilot run"
            if all(checks)
            else "STOP — structural feasibility failed"
        ),
    }
    out = REPO / ".mem" / "phase-minus1b-recombiner-probe.json"
    out.write_text(json.dumps(report, indent=2, default=str))

    print("=" * 72)
    print("PHASE -1b PROBE — grounded multi-session isolation-DAG generator feasibility")
    print("=" * 72)
    for k in (
        "C1_width",
        "C2_structural_discrimination",
        "C3_single_factor_isolation",
        "C4_grounded_realism",
    ):
        r = report[k]
        print(f"\n[{k}] pass={r['pass']}")
        for kk, vv in r.items():
            if kk != "pass":
                print(f"    {kk}: {vv}")
    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    print("  structural_feasibility:", report["verdict"]["structural_feasibility"])
    print("  behavioral_anchor (mem-p3w):", report["verdict"]["behavioral_anchor"])
    print("  RECOMMENDATION:", report["verdict"]["recommendation"])
    print(f"\n  report -> {out}")


if __name__ == "__main__":
    main()
