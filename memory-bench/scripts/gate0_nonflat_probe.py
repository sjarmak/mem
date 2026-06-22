#!/usr/bin/env python3
"""mem-72sj Gate-0 non-flatness reporter: does the real commit-trailer anchor rank
the memory configs (none-clean / ours / builtin) NON-FLATLY?

Pure reporting over an EXISTING ``summary-3arm-graded.json`` (the output of
``run_grid_3arm_graded.py``). This script invents NO eval logic: it only
aggregates the per-bundle per-arm graded scores already computed by the grid and
reports three things the factorial PRD's Gate 0 (and its R1/R2 mitigations) ask
for:

1. **Discrimination / span** -- per-arm mean of each graded signal, and the
   ours-vs-clean and builtin-vs-clean spans on the load-bearing repro anchor. The
   R1/R2 flat-anchor guard: if the anchor's own best-arm-minus-none span is ~0,
   the ranking is degenerate and Gate 0 is uncomputable on this anchor (NO-GO /
   honest-null), NOT a passing rank-correlation.

2. **Rank stability rho** -- split the bundles into two halves, rank the arms by
   mean ``score_direct`` on each half, and report the Spearman rho between the two
   rankings. rho >= 0.6 with a non-degenerate span = a real, reproducible
   non-flat ranking. With no score variance the ranking is undefined -> rho is
   reported as ``None`` (flat), never silently 0.6.

3. **Verdict** -- mechanical: NON-FLAT requires (a) a non-degenerate span AND
   (b) rho >= 0.6. Anything else is FLAT/uncomputable. The authored go/no-go is
   the orchestrator's; this only reports the mechanical signal.

ZFC: deterministic arithmetic over a frozen JSON. No model call, no new scorer.

    uv run python scripts/gate0_nonflat_probe.py \
        --summary ../.mem/grid-72sj/summary-3arm-graded.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# The graded signal whose per-arm ranking is the headline. score_direct is the
# repro-anchored composite (binary repro floor + S1 test-ratio in the fail region);
# it is the signal the validity gate makes meaningful.
RANK_METRIC = "score_direct"
# Signals reported per-arm alongside the ranking metric (side signals, never gates).
REPORTED_METRICS = (
    "score_direct",
    "repro_passed",
    "test_ratio",
    "diff_sim",
    "judge_score",
)
ARMS = ("none-clean", "ours", "builtin")
# A span below this on the repro-anchored metric is treated as degenerate (the
# arms are tied within grading noise -> no real ranking to correlate). This is the
# PRD R1/R2 "abort if oracle-none span ~= 0" guard, made explicit.
DEGENERATE_SPAN = 1e-9
# Spearman rho threshold for a stable (reproducible) non-flat ranking (PRD Gate 0).
RHO_THRESHOLD = 0.6


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _arm_metric_values(per_bundle: Sequence[dict[str, Any]], arm: str, metric: str) -> list[float]:
    out: list[float] = []
    for b in per_bundle:
        cell = b.get(arm)
        if cell is None:
            continue
        v = cell.get(metric)
        if v is not None:
            out.append(float(v))
    return out


def _rank_arms(per_bundle: Sequence[dict[str, Any]], metric: str) -> dict[str, float]:
    """Mean of ``metric`` per arm over the given bundles."""
    return {arm: _mean(_arm_metric_values(per_bundle, arm, metric)) for arm in ARMS}


def _ranking_order(means: dict[str, float]) -> list[str]:
    """Arms sorted best->worst by mean (deterministic tiebreak on arm name)."""
    return sorted(ARMS, key=lambda a: (-means.get(a, 0.0), a))


def _is_flat(means: dict[str, float]) -> bool:
    """True if the arms are tied within grading noise on this metric -- the spread
    (max-min mean) is degenerate. A flat set of means has NO real ranking: the only
    ordering ``_ranking_order`` can produce is the alphabetical tiebreak, which would
    masquerade as a stable ranking in the split-half rho. Guarding on this is the
    PRD R1 "flat-anchor detector" applied per split-half, not just to the full pool."""
    values = [means.get(arm, 0.0) for arm in ARMS]
    return (max(values) - min(values)) < DEGENERATE_SPAN


def _spearman_rho(order_a: Sequence[str], order_b: Sequence[str]) -> float:
    """Spearman rho between two rankings of the same arms (rank = position)."""
    rank_a = {arm: i for i, arm in enumerate(order_a)}
    rank_b = {arm: i for i, arm in enumerate(order_b)}
    n = len(order_a)
    d2 = sum((rank_a[arm] - rank_b[arm]) ** 2 for arm in order_a)
    return 1.0 - (6.0 * d2) / (n * (n * n - 1))


def analyze(summary: dict[str, Any]) -> dict[str, Any]:
    per_bundle = summary.get("per_bundle", [])
    n = len(per_bundle)

    per_arm_means = {metric: _rank_arms(per_bundle, metric) for metric in REPORTED_METRICS}
    rank_means = per_arm_means[RANK_METRIC]
    full_order = _ranking_order(rank_means)

    # Span on the repro-anchored ranking metric: best non-clean arm minus clean.
    clean = rank_means.get("none-clean", 0.0)
    span_ours = rank_means.get("ours", 0.0) - clean
    span_builtin = rank_means.get("builtin", 0.0) - clean
    best_span = max(abs(span_ours), abs(span_builtin))
    degenerate = best_span < DEGENERATE_SPAN

    # Split-half rank-stability rho (only meaningful with >= 2 bundles per half and
    # some score variance). With < 4 bundles or a degenerate span, rho is undefined.
    rho: float | None = None
    half_orders: list[list[str]] | None = None
    flat_half = False
    if n >= 4 and not degenerate:
        mid = n // 2
        means_a = _rank_arms(per_bundle[:mid], RANK_METRIC)
        means_b = _rank_arms(per_bundle[mid:], RANK_METRIC)
        # A half with no arm separation has only a tiebreak ordering; correlating it
        # would report a spurious rho=1.0 whenever the tiebreak happens to agree (the
        # PRD R1 noise-pass). Such a split cannot establish ranking stability -> rho
        # is undefined, never silently computed off the alphabetical tiebreak.
        if _is_flat(means_a) or _is_flat(means_b):
            flat_half = True
        else:
            order_a = _ranking_order(means_a)
            order_b = _ranking_order(means_b)
            rho = _spearman_rho(order_a, order_b)
            half_orders = [order_a, order_b]

    non_flat = (not degenerate) and (rho is not None) and (rho >= RHO_THRESHOLD)

    return {
        "n_bundles": n,
        "rank_metric": RANK_METRIC,
        "per_arm_means": per_arm_means,
        "full_ranking_best_to_worst": full_order,
        "span_ours_minus_clean": span_ours,
        "span_builtin_minus_clean": span_builtin,
        "best_abs_span": best_span,
        "degenerate_span": degenerate,
        "degenerate_span_threshold": DEGENERATE_SPAN,
        "split_half_rho": rho,
        "split_half_orders": half_orders,
        "split_half_flat": flat_half,
        "rho_threshold": RHO_THRESHOLD,
        "verdict_non_flat": non_flat,
        "verdict_reason": (
            "degenerate span (arms tied within grading noise) -> ranking undefined, "
            "Gate-0 uncomputable on this anchor (honest-null)"
            if degenerate
            else (
                "a split-half has no arm separation (flat half) -> rank stability "
                "undefined, Gate-0 uncomputable on this anchor (honest-null)"
                if flat_half
                else (
                    f"rho={rho} < {RHO_THRESHOLD} or undefined -> unstable ranking"
                    if not non_flat
                    else f"rho={rho} >= {RHO_THRESHOLD} with non-degenerate span -> stable non-flat"
                )
            )
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        type=Path,
        required=True,
        help="path to a run_grid_3arm_graded.py summary-3arm-graded.json",
    )
    parser.add_argument("--out", type=Path, default=None, help="optional JSON report path")
    args = parser.parse_args(argv)

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    report = analyze(summary)
    text = json.dumps(report, indent=2)
    print(text)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\nreport -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
