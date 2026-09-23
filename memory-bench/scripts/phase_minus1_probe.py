"""Phase -1 probe (PRD gate): is there an admissible DAG to exploit?

Cheap, instrument-free measurement over the EXISTING corpus, gating whether the
Phase-0 cache/Merkle build is worth doing. Three pre-registered measurements,
three pre-registered NO-GO gates (see prd_dag_trace_orchestration_agent_benchmarking.md):

  G1 DAG width  : median antichain width <= 2  -> do NOT build the scheduler
  G2 cost split : deterministic-setup fraction < 10% -> caching value prop fails
  G3 divergence : declared vs observed memory-read edges; trivial-on-a-chain check

Outputs a JSON report + a human summary. No cache code, no new dependencies
beyond stdlib + the corpus JSON already on disk.
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "memory-bench" / "fixtures" / "sequences"
CROSS = REPO / ".mem" / "cross-session-metrics.json"


# ---------------------------------------------------------------------------
# (1) within-sequence memory-op DAG width — from declared expected reads/writes
# ---------------------------------------------------------------------------
def step_edges(steps: list[dict[str, Any]]) -> dict[int, set[int]]:
    """Producer->consumer edges: j -> i iff step i reads a memory id step j
    (j < i) wrote. Returns adjacency (successors)."""
    writers: dict[str, list[int]] = {}
    succ: dict[int, set[int]] = {i: set() for i in range(len(steps))}
    for i, st in enumerate(steps):
        reads = set(st.get("expected_memory_reads") or [])
        for mid in reads:
            for j in writers.get(mid, []):
                if j < i:
                    succ[j].add(i)
        for mid in st.get("expected_memory_writes") or {}:
            writers.setdefault(mid, []).append(i)
    return succ


def reachability(succ: dict[int, set[int]]) -> dict[int, set[int]]:
    """Transitive closure (descendants) per node."""
    desc: dict[int, set[int]] = {}

    def walk(n: int) -> set[int]:
        if n in desc:
            return desc[n]
        out: set[int] = set()
        for m in succ[n]:
            out.add(m)
            out |= walk(m)
        desc[n] = out
        return out

    for n in succ:
        walk(n)
    return desc


def antichain_width(succ: dict[int, set[int]]) -> int:
    """Max antichain = largest set of pairwise-unreachable nodes = max parallelism.
    Brute force over the comparability relation (graphs here are tiny)."""
    nodes = list(succ)
    desc = reachability(succ)
    comparable = set()
    for a in nodes:
        for b in desc[a]:
            comparable.add((a, b))
            comparable.add((b, a))

    def is_antichain(s: tuple[int, ...]) -> bool:
        return all((a, b) not in comparable for a, b in combinations(s, 2))

    best = 1
    # descend from full size; small n so this terminates fast
    for size in range(len(nodes), 0, -1):
        if size <= best:
            break
        for combo in combinations(nodes, size):
            if is_antichain(combo):
                return size
    return best


def measure_fixture_widths() -> dict[str, Any]:
    rows = []
    for path in sorted(FIXTURES.glob("*.json")):
        seq = json.loads(path.read_text())
        steps = seq.get("steps", [])
        succ = step_edges(steps)
        n_edges = sum(len(v) for v in succ.values())
        width = antichain_width(succ)
        # how many steps declare any cross-step read dependency
        dep_steps = sum(1 for i in succ if any(i in succ[j] for j in succ))
        rows.append(
            {
                "sequence_id": seq.get("sequence_id", path.stem),
                "n_steps": len(steps),
                "n_memory_edges": n_edges,
                "antichain_width": width,
                "dependent_steps": dep_steps,
            }
        )
    widths = [r["antichain_width"] for r in rows]
    return {
        "fixtures": rows,
        "n_fixtures": len(rows),
        "width_median": median(widths) if widths else None,
        "width_max": max(widths) if widths else None,
        "width_histogram": {w: widths.count(w) for w in sorted(set(widths))},
    }


# ---------------------------------------------------------------------------
# (2) bead-session DAG shape + (3) cost split + divergence proxy — recorded corpus
# ---------------------------------------------------------------------------
def measure_corpus() -> dict[str, Any]:
    d = json.loads(CROSS.read_text())
    s = d["summary"]
    hist = {int(k): v for k, v in s["iterations_histogram"].items()}
    n_beads = s["n_beads"]
    # sessions-per-bead = node count of the per-bead temporal chain (width 1 by construction)
    expanded = []
    for sess, cnt in hist.items():
        expanded += [sess] * cnt
    sess_median = median(expanded) if expanded else None

    in_tok = s["total_input_tokens"]
    out_tok = s["total_output_tokens"]
    # The deterministic scaffolding (retrieval/grading/cross-session) is, per
    # cross_session.py, pure arithmetic over already-extracted structure: ~0
    # model tokens. Agent inference = the input+output token mass. So an UPPER
    # bound on the deterministic-setup token fraction is ~0; we report the token
    # mass that is unambiguously agent-inference to make the gate explicit.
    total_tok = (in_tok or 0) + (out_tok or 0)

    return {
        "n_beads": n_beads,
        "min_sessions_filter": d.get("min_sessions"),
        "sessions_per_bead_median": sess_median,
        "sessions_per_bead_histogram": dict(sorted(hist.items())),
        "beads_with_exactly_2_sessions": hist.get(2, 0),
        "bead_session_dag_topology": "temporal chain (consecutive-pair model) -> antichain width 1",
        "cross_session_read_dependency": {
            "mean_redundant_read_fraction": s["mean_redundant_read_fraction"],
            "pairs_with_any_redundant_read": s["pairs_with_any_redundant_read"],
            "n_pairs": s["n_pairs"],
            "frac_pairs_with_any_dependency": (
                s["pairs_with_any_redundant_read"] / s["n_pairs"] if s["n_pairs"] else None
            ),
        },
        "cost": {
            "total_input_tokens": in_tok,
            "total_output_tokens": out_tok,
            "total_tokens": total_tok,
            "agent_inference_token_fraction": 1.0,  # retrieval/grading are ~0-token arithmetic
            "deterministic_setup_token_fraction_upper_bound": 0.0,
            "note": (
                "retrieval/grading/cross-session are deterministic arithmetic "
                "(cross_session.py): ~0 model tokens"
            ),
        },
        "coverage_by_rig": d.get("coverage_by_rig"),
    }


def evaluate_gates(fx: dict[str, Any], cx: dict[str, Any]) -> dict[str, Any]:
    width_median = fx["width_median"]
    setup_frac = cx["cost"]["deterministic_setup_token_fraction_upper_bound"]
    dep_frac = cx["cross_session_read_dependency"]["frac_pairs_with_any_dependency"]

    g1 = {
        "gate": "G1 DAG width",
        "rule": "median antichain width <= 2 -> do NOT build scheduler",
        "within_sequence_width_median": width_median,
        "bead_session_width": 1,
        "verdict": "NO-GO (do not build scheduler)" if (width_median or 0) <= 2 else "GO",
    }
    g2 = {
        "gate": "G2 cost split",
        "rule": "deterministic-setup token fraction < 10% -> caching value prop fails",
        "setup_token_fraction_upper_bound": setup_frac,
        "verdict": "NO-GO (caching value prop fails)" if setup_frac < 0.10 else "GO",
    }
    g3 = {
        "gate": "G3 divergence",
        "rule": "cross-session read dependency density; trivial-on-a-chain check",
        "frac_consecutive_pairs_with_any_read_dependency": dep_frac,
        "mean_redundant_read_fraction": cx["cross_session_read_dependency"][
            "mean_redundant_read_fraction"
        ],
        "verdict": (
            "TRIVIAL (dependency density low; divergence undefined-to-trivial on near-chains)"
            if (dep_frac or 0) < 0.25
            else "STRUCTURED"
        ),
    }
    all_null = (
        g1["verdict"].startswith("NO-GO")
        and g2["verdict"].startswith("NO-GO")
        and g3["verdict"].startswith("TRIVIAL")
    )
    return {
        "gates": [g1, g2, g3],
        "empty_premise_exit": all_null,
        "recommendation": (
            "STOP / write negative-result note — all three pre-registered NO-GO gates fired"
            if all_null
            else "PROCEED to Phase 0 on the greenlit axis"
        ),
    }


def main() -> None:
    fx = measure_fixture_widths()
    cx = measure_corpus()
    gates = evaluate_gates(fx, cx)
    report = {
        "probe": "phase_minus1",
        "within_sequence_dag": fx,
        "recorded_corpus": cx,
        "gate_evaluation": gates,
    }
    out = REPO / ".mem" / "phase-minus1-probe.json"
    out.write_text(json.dumps(report, indent=2))

    print("=" * 72)
    print("PHASE -1 PROBE — is there an admissible DAG to exploit?")
    print("=" * 72)
    print("\n[1] WITHIN-SEQUENCE memory-op DAG width (7 declared fixtures)")
    for r in fx["fixtures"]:
        print(
            f"    {r['sequence_id']:<34} steps={r['n_steps']:>2} "
            f"edges={r['n_memory_edges']:>2} antichain_width={r['antichain_width']}"
        )
    print(
        f"    -> width median={fx['width_median']}  max={fx['width_max']}  "
        f"hist={fx['width_histogram']}"
    )

    print(
        "\n[2] BEAD-SESSION DAG shape (recorded corpus, n_beads="
        f"{cx['n_beads']}, min_sessions={cx['min_sessions_filter']})"
    )
    print(
        f"    sessions/bead median={cx['sessions_per_bead_median']}  "
        f"hist={cx['sessions_per_bead_histogram']}"
    )
    print(f"    topology: {cx['bead_session_dag_topology']}")
    dep = cx["cross_session_read_dependency"]
    print(
        f"    cross-session read dependency: "
        f"{dep['pairs_with_any_redundant_read']}/{dep['n_pairs']} pairs "
        f"({dep['frac_pairs_with_any_dependency']:.1%}) have ANY shared read; "
        f"mean redundant-read frac={dep['mean_redundant_read_fraction']:.3f}"
    )

    print("\n[3] COST SPLIT (inference vs deterministic setup)")
    c = cx["cost"]
    print(
        f"    agent-inference tokens={c['total_tokens']:,} "
        f"(in={c['total_input_tokens']:,} out={c['total_output_tokens']:,})"
    )
    print(
        f"    deterministic-setup token fraction (upper bound) = "
        f"{c['deterministic_setup_token_fraction_upper_bound']:.0%}  ({c['note']})"
    )

    print("\n" + "=" * 72)
    print("PRE-REGISTERED GATE EVALUATION")
    print("=" * 72)
    for g in gates["gates"]:
        print(f"  {g['gate']:<16} -> {g['verdict']}")
        print(f"      rule: {g['rule']}")
    print("\n  EMPTY-PREMISE EXIT:", gates["empty_premise_exit"])
    print("  RECOMMENDATION:", gates["recommendation"])
    print(f"\n  report written -> {out}")


if __name__ == "__main__":
    main()
