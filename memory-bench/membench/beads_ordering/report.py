from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import fmean
from typing import Any

from membench.beads_ordering.analysis import analyze_results
from membench.beads_ordering.models import OrderingArm, OrderingRunResult


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _curve_table(curves: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| order | page | page-1 useful | pages p50 / p90 | compact tokens p50 / p90 | "
        "tool calls p50 | time-to-useful p50 ms | recalls p50 | success | server order p50 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in curves:
        pages = row.get("pages_to_first_useful") or {}
        tokens = row["compact_result_tokens"]
        calls = row["tool_calls"]
        useful_time = row.get("time_to_first_useful_ms") or {}
        recalls = row["recalls"]
        server = row["server_ordering_ms"]
        lines.append(
            "| {arm} | {page} | {visible:.0%} | {pages50} / {pages90} | "
            "{tokens50} / {tokens90} | {calls50} | {time50} | {recalls50} | "
            "{success:.0%} | {server50} |".format(
                arm=row["arm"],
                page=row["page_size"],
                visible=float(row["page_one_acceptable_probability"]),
                pages50=_fmt(pages.get("p50")),
                pages90=_fmt(pages.get("p90")),
                tokens50=_fmt(tokens["p50"]),
                tokens90=_fmt(tokens["p90"]),
                calls50=_fmt(calls["p50"]),
                time50=_fmt(useful_time.get("p50")),
                recalls50=_fmt(recalls["p50"]),
                success=float(row["task_success_rate"]),
                server50=_fmt(server["p50"]),
            )
        )
    return "\n".join(lines)


def _mean(rows: Sequence[OrderingRunResult], field: str) -> float:
    return fmean(float(getattr(row, field)) for row in rows)


def render_markdown(rows: Sequence[OrderingRunResult], analysis: Mapping[str, Any]) -> str:
    baseline = [row for row in rows if row.arm is OrderingArm.KEY]
    bm25f = [row for row in rows if row.arm is OrderingArm.BM25F]
    baseline_page_one = fmean(1.0 if row.page_one_acceptable_visible else 0.0 for row in baseline)
    match_counts = sorted({row.total_matched for row in rows})
    material = analysis.get("largest_page_size_with_material_ranking_gap")
    if baseline and bm25f:
        token_delta = _mean(baseline, "compact_result_tokens") - _mean(
            bm25f, "compact_result_tokens"
        )
        success_delta = _mean(bm25f, "task_success") - _mean(baseline, "task_success")
    else:
        token_delta = 0.0
        success_delta = 0.0
    conclusion = (
        "The pre-registered material-gap rule was met through page size " + str(material) + "."
        if material is not None
        else "The pre-registered material-gap rule was not met at any measured page size."
    )
    return (
        "# Beads Memory pre-pagination ordering experiment\n\n"
        "This report isolates ordering after the existing literal matcher has produced one fixed "
        "candidate set. Server-side matching/scoring time is reported separately from what the "
        "agent ingested.\n\n"
        "## Direct answers\n\n"
        f"1. Realistic frozen match sets in this corpus range from {match_counts[0]} to "
        f"{match_counts[-1]} candidates.\n"
        f"2. Under key ordering, a useful Memory was visible on page 1 in "
        f"{baseline_page_one:.0%} of measured runs.\n"
        "3. Per-page cost is visible in the page-size table and burial correlations below; "
        "compact tokens and tool calls are model-facing costs, while Beads compute is separate.\n"
        f"4. Across the recorded grid, BM25F changed mean compact ingestion by "
        f"{token_delta:+.1f} tokens relative to key order.\n"
        f"5. BM25F changed task success by {success_delta:+.1%}; interpret retrieval-cost and "
        "outcome effects separately.\n"
        "6. Compare the depth-first-mode rows/results with natural mode to judge whether graph "
        "navigation erases the initial-ordering effect.\n"
        f"7. {conclusion}\n"
        "8. This PoC supports added Beads complexity only if the measured ingestion/round-trip "
        "reduction is material without a success regression; it does not establish a production "
        "indexing design.\n\n"
        "## Page-size curves\n\n"
        + _curve_table(analysis["page_size_curves"])
        + "\n\n## Mechanical versus BM25F crossover\n\n"
        "Material means at least one p50 page or 20% mean compact-token reduction, with no "
        "success regression.\n\n"
        "```json\n" + json.dumps(analysis["mechanical_vs_bm25f"], indent=2) + "\n```\n\n"
        "## Baseline burial correlations\n\n"
        "```json\n" + json.dumps(analysis["baseline_burial_correlations"], indent=2) + "\n```\n"
    )


def render_page_size_svg(analysis: Mapping[str, Any]) -> str:
    curves = analysis["page_size_curves"]
    width, height = 900, 470
    left, right, top, bottom = 70, 30, 45, 70
    plot_width = width - left - right
    plot_height = height - top - bottom
    labels = ["5", "10", "20", "50", "all"]
    colors = {"key": "#555555", "navigation": "#2a6fbb", "bm25f": "#bb3e03"}
    values = [
        float(row["pages_to_first_useful"]["p50"])
        for row in curves
        if row.get("pages_to_first_useful")
    ]
    ymax = max(values, default=1.0)

    def x(label: str) -> float:
        return left + labels.index(label) * plot_width / (len(labels) - 1)

    def y(value: float) -> float:
        return top + plot_height * (1 - value / max(1.0, ymax))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="70" y="25" font-family="sans-serif" font-size="17">'
        "Pages consumed before first useful Memory (p50)</text>",
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#777"/>',
        f'<line x1="{left}" y1="{top + plot_height}" x2="{width-right}" '
        f'y2="{top + plot_height}" stroke="#777"/>',
    ]
    for label in labels:
        parts.append(
            f'<text x="{x(label)}" y="{height-35}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="13">{html.escape(label)}</text>'
        )
    for tick in range(5):
        value = ymax * tick / 4
        parts.append(
            f'<text x="{left-10}" y="{y(value)+4}" text-anchor="end" '
            f'font-family="sans-serif" font-size="12">{value:.1f}</text>'
        )
    for arm in ("key", "navigation", "bm25f"):
        arm_rows = {
            str(row["page_size"]): row
            for row in curves
            if row["arm"] == arm and row.get("pages_to_first_useful")
        }
        points = [
            (x(label), y(float(arm_rows[label]["pages_to_first_useful"]["p50"])))
            for label in labels
            if label in arm_rows
        ]
        if not points:
            continue
        encoded = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        parts.append(
            f'<polyline points="{encoded}" fill="none" stroke="{colors[arm]}" '
            'stroke-width="2.5"/>'
        )
        for px, py in points:
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="{colors[arm]}"/>')
        last_x, last_y = points[-1]
        parts.append(
            f'<text x="{last_x-8}" y="{last_y-8}" text-anchor="end" '
            f'font-family="sans-serif" font-size="13" fill="{colors[arm]}">{arm}</text>'
        )
    parts.append(
        f'<text x="{left + plot_width/2}" y="{height-8}" text-anchor="middle" '
        'font-family="sans-serif" font-size="13">page size</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def write_report(rows: Sequence[OrderingRunResult], out_dir: Path) -> dict[str, object]:
    analysis = analyze_results(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n", "utf-8")
    (out_dir / "report.md").write_text(render_markdown(rows, analysis), "utf-8")
    (out_dir / "page-size-pages.svg").write_text(render_page_size_svg(analysis), "utf-8")
    return analysis
