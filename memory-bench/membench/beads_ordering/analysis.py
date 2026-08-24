from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from statistics import fmean

from membench.beads_ordering.models import OrderingArm, OrderingRunResult


def percentile(values: Sequence[float | int], quantile: float) -> float:
    if not values:
        raise ValueError("percentile needs at least one value")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: Sequence[float | int]) -> dict[str, float | int]:
    return {
        "n": len(values),
        "mean": fmean(values),
        "p50": percentile(values, 0.5),
        "p90": percentile(values, 0.9),
        "min": min(values),
        "max": max(values),
    }


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    x_mean, y_mean = fmean(xs), fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    x_scale = math.sqrt(sum((x - x_mean) ** 2 for x in xs))
    y_scale = math.sqrt(sum((y - y_mean) ** 2 for y in ys))
    return numerator / (x_scale * y_scale) if x_scale and y_scale else None


def _match_stratum(total: int) -> str:
    if total <= 20:
        return "1-20"
    if total <= 50:
        return "21-50"
    if total <= 100:
        return "51-100"
    return "101+"


def _burial_stratum(page: int) -> str:
    if page == 1:
        return "page-1"
    if page <= 3:
        return "pages-2-3"
    return "page-4+"


def _mean(rows: Sequence[OrderingRunResult], field: str) -> float:
    return fmean(float(getattr(row, field)) for row in rows)


def _slope(rows: Sequence[OrderingRunResult], field: str) -> float | None:
    xs = [float(row.pages_requested) for row in rows]
    ys = [float(getattr(row, field)) for row in rows]
    if len(set(xs)) < 2:
        return None
    x_mean = fmean(xs)
    denominator = sum((value - x_mean) ** 2 for value in xs)
    if not denominator:
        return None
    y_mean = fmean(ys)
    return (
        sum(
            (x_value - x_mean) * (y_value - y_mean) for x_value, y_value in zip(xs, ys, strict=True)
        )
        / denominator
    )


def _page_size_sort(label: str) -> int:
    return 1_000_000 if label == "all" else int(label)


def analyze_results(rows: Sequence[OrderingRunResult]) -> dict[str, object]:
    if not rows:
        raise ValueError("analysis needs at least one run")
    metrics = (
        "pages_requested",
        "compact_result_tokens",
        "compact_tokens_to_first_useful",
        "retrieval_tokens_to_first_useful",
        "tool_calls_to_first_useful",
        "retrieval_tool_calls",
        "retrieval_latency_ms",
        "server_candidate_generation_ms",
        "server_ordering_ms",
        "end_to_end_ms",
        "full_recalls",
        "graph_hops_after_first_useful",
        "reference_edges_exposed",
    )
    by_arm: dict[str, dict[str, object]] = {}
    for arm in OrderingArm:
        arm_rows = [row for row in rows if row.arm is arm]
        if not arm_rows:
            continue
        by_arm[arm.value] = {
            metric: _distribution([getattr(row, metric) for row in arm_rows]) for metric in metrics
        }
        by_arm[arm.value]["task_success_rate"] = fmean(
            1.0 if row.task_success else 0.0 for row in arm_rows
        )

    by_mode_arm: dict[str, dict[str, object]] = {}
    for mode in sorted({row.mode.value for row in rows}):
        for arm in OrderingArm:
            group = [row for row in rows if row.mode.value == mode and row.arm is arm]
            if not group:
                continue
            key = f"{mode}/{arm.value}"
            by_mode_arm[key] = {
                metric: _distribution([getattr(row, metric) for row in group]) for metric in metrics
            }
            by_mode_arm[key]["task_success_rate"] = fmean(
                1.0 if row.task_success else 0.0 for row in group
            )

    page_size_groups: dict[tuple[str, str, str], list[OrderingRunResult]] = defaultdict(list)
    for row in rows:
        page_size_groups[(row.mode.value, row.arm.value, row.page_size)].append(row)
    page_size_curves = [
        {
            "mode": key[0],
            "arm": key[1],
            "page_size": key[2],
            "n": len(group),
            "page_one_acceptable_probability": fmean(
                1.0 if row.page_one_acceptable_visible else 0.0 for row in group
            ),
            "pages_to_first_useful": (
                _distribution(
                    [
                        row.pages_to_first_useful
                        for row in group
                        if row.pages_to_first_useful is not None
                    ]
                )
                if any(row.pages_to_first_useful is not None for row in group)
                else None
            ),
            "compact_result_tokens": _distribution(
                [row.compact_tokens_to_first_useful for row in group]
            ),
            "tool_calls": _distribution([row.tool_calls_to_first_useful for row in group]),
            "time_to_first_useful_ms": (
                _distribution(
                    [
                        row.time_to_first_useful_ms
                        for row in group
                        if row.time_to_first_useful_ms is not None
                    ]
                )
                if any(row.time_to_first_useful_ms is not None for row in group)
                else None
            ),
            "recalls": _distribution([row.full_recalls for row in group]),
            "graph_hops": _distribution([row.graph_hops_after_first_useful for row in group]),
            "retrieval_tokens": _distribution(
                [row.retrieval_tokens_to_first_useful for row in group]
            ),
            "branching_factor": _distribution([row.branching_factor_mean for row in group]),
            "task_success_rate": fmean(1.0 if row.task_success else 0.0 for row in group),
            "server_candidate_generation_ms": _distribution(
                [row.server_candidate_generation_ms for row in group]
            ),
            "server_ordering_ms": _distribution([row.server_ordering_ms for row in group]),
        }
        for key, group in sorted(
            page_size_groups.items(),
            key=lambda item: (item[0][0], item[0][1], _page_size_sort(item[0][2])),
        )
    ]

    bounded_vs_unbounded: list[dict[str, object]] = []
    for mode in sorted({row.mode.value for row in rows}):
        for arm in OrderingArm:
            full = page_size_groups.get((mode, arm.value, "all"), [])
            if not full:
                continue
            full_tokens = _mean(full, "compact_result_tokens")
            full_success = _mean(full, "task_success")
            full_time_values = [
                row.time_to_first_useful_ms
                for row in full
                if row.time_to_first_useful_ms is not None
            ]
            full_time = fmean(full_time_values) if full_time_values else None
            for (group_mode, group_arm, size), group in page_size_groups.items():
                if group_mode != mode or group_arm != arm.value or size == "all":
                    continue
                time_values = [
                    row.time_to_first_useful_ms
                    for row in group
                    if row.time_to_first_useful_ms is not None
                ]
                bounded_vs_unbounded.append(
                    {
                        "mode": mode,
                        "arm": arm.value,
                        "page_size": size,
                        "compact_tokens_mean_delta": _mean(group, "compact_result_tokens")
                        - full_tokens,
                        "time_to_useful_mean_delta_ms": (
                            fmean(time_values) - full_time
                            if time_values and full_time is not None
                            else None
                        ),
                        "success_rate_delta": _mean(group, "task_success") - full_success,
                    }
                )

    mechanical_vs_bm25f: list[dict[str, object]] = []
    numeric_material: dict[str, list[int]] = defaultdict(list)
    policy_vs_bm25f: list[dict[str, object]] = []
    for mode in sorted({row.mode.value for row in rows}):
        size_labels = sorted(
            {
                size
                for group_mode, arm, size in page_size_groups
                if group_mode == mode and arm in {OrderingArm.KEY.value, OrderingArm.BM25F.value}
            },
            key=_page_size_sort,
        )
        for size in size_labels:
            bm25f_rows = page_size_groups.get((mode, OrderingArm.BM25F.value, size), [])
            if not bm25f_rows:
                continue
            for arm in OrderingArm:
                if arm is OrderingArm.BM25F:
                    continue
                policy_rows = page_size_groups.get((mode, arm.value, size), [])
                if not policy_rows:
                    continue
                pages_delta = percentile(
                    [row.pages_requested for row in policy_rows], 0.5
                ) - percentile([row.pages_requested for row in bm25f_rows], 0.5)
                policy_tokens = _mean(policy_rows, "compact_result_tokens")
                bm25f_tokens = _mean(bm25f_rows, "compact_result_tokens")
                token_reduction = (
                    0.0 if policy_tokens == 0 else (policy_tokens - bm25f_tokens) / policy_tokens
                )
                success_delta = _mean(bm25f_rows, "task_success") - _mean(
                    policy_rows, "task_success"
                )
                material_gap = (pages_delta >= 1.0 or token_reduction >= 0.2) and success_delta >= 0
                comparison = {
                    "mode": mode,
                    "policy": arm.value,
                    "page_size": size,
                    "policy_pages_p50_minus_bm25f": pages_delta,
                    "bm25f_compact_token_reduction_fraction": token_reduction,
                    "bm25f_success_rate_minus_policy": success_delta,
                    "material_gap": material_gap,
                }
                policy_vs_bm25f.append(comparison)
                if arm is OrderingArm.KEY:
                    mechanical_vs_bm25f.append(comparison)
                    if size != "all" and material_gap:
                        numeric_material[mode].append(int(size))

    baseline = [row for row in rows if row.arm is OrderingArm.KEY]
    correlations = {
        mode: {
            metric: _pearson(
                [
                    float(min(filter(None, (row.primary_page, row.acceptable_page))))
                    for row in baseline
                    if row.mode.value == mode
                ],
                [float(getattr(row, metric)) for row in baseline if row.mode.value == mode],
            )
            for metric in metrics
        }
        for mode in sorted({row.mode.value for row in baseline})
    }

    baseline_pages = {
        (row.task_id, row.repeat, row.page_size, row.mode.value): min(
            filter(None, (row.primary_page, row.acceptable_page))
        )
        for row in baseline
    }
    grouped: dict[tuple[str, int, str, str, str], list[OrderingRunResult]] = defaultdict(list)
    for row in rows:
        baseline_page = baseline_pages.get(
            (row.task_id, row.repeat, row.page_size, row.mode.value),
            min(filter(None, (row.primary_page, row.acceptable_page))),
        )
        grouped[
            (
                row.mode.value,
                row.corpus_size,
                _match_stratum(row.total_matched),
                _burial_stratum(baseline_page),
                row.arm.value,
            )
        ].append(row)
    strata = [
        {
            "mode": key[0],
            "corpus_size": key[1],
            "match_set": key[2],
            "baseline_burial": key[3],
            "arm": key[4],
            "n": len(group),
            "pages_p50": percentile([row.pages_requested for row in group], 0.5),
            "tokens_p50": percentile([row.retrieval_related_tokens for row in group], 0.5),
            "success_rate": fmean(1.0 if row.task_success else 0.0 for row in group),
        }
        for key, group in sorted(grouped.items())
    ]
    page_cost = [
        {
            "mode": key[0],
            "arm": key[1],
            "page_size": key[2],
            "n": len(group),
            "compact_tokens_per_additional_page": _slope(group, "compact_tokens_to_first_useful"),
            "tool_calls_per_additional_page": _slope(group, "tool_calls_to_first_useful"),
            "retrieval_latency_ms_per_additional_page": _slope(group, "retrieval_latency_ms"),
            "end_to_end_ms_per_additional_page": _slope(group, "end_to_end_ms"),
        }
        for key, group in sorted(
            page_size_groups.items(),
            key=lambda item: (item[0][0], item[0][1], _page_size_sort(item[0][2])),
        )
    ]

    navigation_effects: list[dict[str, object]] = []
    for arm in OrderingArm:
        for size in sorted({row.page_size for row in rows}, key=_page_size_sort):
            search_rows = page_size_groups.get(("search-only", arm.value, size), [])
            navigation_rows = page_size_groups.get(("navigation", arm.value, size), [])
            if not search_rows or not navigation_rows:
                continue
            navigation_effects.append(
                {
                    "arm": arm.value,
                    "page_size": size,
                    "navigation_retrieval_tokens_mean_delta": _mean(
                        navigation_rows, "retrieval_related_tokens"
                    )
                    - _mean(search_rows, "retrieval_related_tokens"),
                    "navigation_latency_mean_delta_ms": _mean(
                        navigation_rows, "retrieval_latency_ms"
                    )
                    - _mean(search_rows, "retrieval_latency_ms"),
                    "navigation_success_rate_delta": _mean(navigation_rows, "task_success")
                    - _mean(search_rows, "task_success"),
                    "navigation_primary_reach_rate": _mean(
                        navigation_rows, "navigation_reached_primary"
                    ),
                    "navigation_graph_hops_mean": _mean(
                        navigation_rows, "graph_hops_after_first_useful"
                    ),
                }
            )
    return {
        "runs": len(rows),
        "by_arm": by_arm,
        "by_mode_arm": by_mode_arm,
        "baseline_burial_correlations": correlations,
        "page_size_curves": page_size_curves,
        "bounded_vs_unbounded": sorted(
            bounded_vs_unbounded,
            key=lambda row: (str(row["arm"]), _page_size_sort(str(row["page_size"]))),
        ),
        "mechanical_vs_bm25f": mechanical_vs_bm25f,
        "policy_vs_bm25f": policy_vs_bm25f,
        "largest_page_size_with_material_ranking_gap": {
            mode: max(sizes) if sizes else None for mode, sizes in sorted(numeric_material.items())
        },
        "material_gap_definition": (
            "mechanical p50 pages is >=1 above BM25F or BM25F reduces mean compact tokens "
            "by >=20%, without reducing success"
        ),
        "strata": strata,
        "page_cost_slopes": page_cost,
        "navigation_effects": navigation_effects,
    }
