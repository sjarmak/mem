#!/usr/bin/env python3
"""mem-apg.4 headline report builder — aggregate mem-apg.3's per-rung grid scores
into the ablation score-vs-information curve (ARCHITECTURE.md D17).

Input: ``.mem/grid/summary.json`` — the headline paired grid mem-apg.3 produced
(per (bundle, rung) scores: ``repro_passed``, ``score_artifact``, efficiency
counters). Output: a deterministic markdown headline report plus the machine
artifact it is read off (``.mem/grid/headline-curve.json``).

What the report carries (the bead's contract):

- per-rung reward, mean + spread, decomposed into its two legs (the deterministic
  gold-test repro guard and the artifact-F1 term) — NEVER a hand-weighted composite
  headline number; weighting is the learned part, deliberately left out;
- the score-vs-information curve, per-task and aggregate, on the real executable
  ladder (``none`` < ``oracle`` on this run);
- the saturation point and minimum-useful-information combination — REFUSED honestly
  when the live ladder is too short to locate them (architect H2: ≥4 rungs), via the
  same ``curve.py`` contract the grid is scored against, not a fabricated verdict;
- held-out N and per-rung / per-source coverage, reported with no silent truncation;
- the efficiency leg (tokens / turns / tool-calls) as the dec-gck headline axis,
  reported as per-bundle paired deltas (median-robust), quality as the guard;
- the merged-diff outcome-lift footnote: the gold-test repro proxy on the bundles
  that carry it, plus the standing fact that the true merged-PR/CI oracle is
  structurally uncomputable on this corpus (no bead→PR→repo linkage).

ZFC: pure plumbing — file IO and deterministic aggregation (grouping, arithmetic
means/medians, the curve's own CI). No semantic judgment, no weighting decision.

Usage (from memory-bench/):

    uv run python scripts/build_headline_report.py \
        [--summary .mem/grid/summary.json] \
        [--out-json .mem/grid/headline-curve.json] \
        [--report docs/mem-apg.4-ablation-headline.md]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

from membench.grading.ablation import DEFAULT_RUNGS
from membench.grading.curve import (
    InsufficientLadderError,
    ScoreInformationCurve,
    build_curve,
    min_useful_combo,
    saturation_point,
)
from membench.grading.trace_score import RewardComponents, RewardRecord

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The two reward legs the grid scores per run. Reported separately — the bead
# forbids collapsing them into a weighted headline scalar.
REPRO_LEG = "repro_passed"
ARTIFACT_LEG = "score_artifact"

# Efficiency counters (the dec-gck headline axis): lower is cheaper.
EFFICIENCY_FIELDS = ("output_tokens", "input_tokens", "turns", "tool_calls")


@dataclass(frozen=True)
class LegStat:
    """One rung's distribution of a single signal over the held-out tasks. ``spread``
    is the population standard deviation; ``lo``/``hi`` the observed range. ``n`` is
    how many tasks carried the signal (``< n_tasks`` when a row could not be scored)."""

    rung: str
    mean: float
    spread: float
    lo: float
    hi: float
    n: int


def _leg_stats(summary: dict[str, Any], field: str) -> list[LegStat]:
    """Per-rung mean + spread of ``field`` over the bundles that carry it.

    Rows whose value is ``None`` (e.g. a repro that could not be scored) are dropped
    from that rung's sample and surface as a reduced ``n`` rather than a silent zero."""
    stats: list[LegStat] = []
    for rung in summary["conditions"]:
        values = [
            row[rung][field] for row in summary["per_bundle"] if row[rung].get(field) is not None
        ]
        if not values:
            continue
        stats.append(
            LegStat(
                rung=rung,
                mean=fmean(values),
                spread=pstdev(values) if len(values) > 1 else 0.0,
                lo=min(values),
                hi=max(values),
                n=len(values),
            )
        )
    return stats


def _reward_records(summary: dict[str, Any]) -> list[RewardRecord]:
    """Reconstruct the per-run reward records the curve aggregates.

    The grid stored the two reward terms separately; rebuild each run's
    ``RewardComponents`` so the curve's ``combined_reward`` matches the established D17
    contract. The deterministic term is the gold-test repro outcome (``repro_passed``);
    ``score_artifact`` is the file-comprehension F1, slotted into ``rubric_score`` as the
    available quality term on this judge-free run (no OSS judge has been wired — it would
    occupy the same slot). ``path_reached`` is True: an exact inference for the
    ``test_repro`` rows and an approximation for the single ``diff_sim`` row whose repro
    is ``None`` (which keeps the deterministic term at its not-resolved floor — and whose
    artifact F1 is 0.0, so the reward is 0.0 either way)."""
    records: list[RewardRecord] = []
    for row in summary["per_bundle"]:
        work_id = row["work_id"]
        for rung in summary["conditions"]:
            cell = row[rung]
            records.append(
                RewardRecord(
                    work_id=work_id,
                    rung=rung,
                    repeat_idx=0,
                    components=RewardComponents(
                        path_reached=True,
                        trace_error_resolved=cell.get(REPRO_LEG) == 1.0,
                        rubric_score=cell[ARTIFACT_LEG],
                    ),
                )
            )
    return records


@dataclass(frozen=True)
class CurveReadout:
    """The score-vs-information curve plus the D17 readouts it does or does not
    support. ``saturation`` / ``min_useful`` are the rung name when the ladder is long
    enough, or ``None`` with ``refusal`` explaining why (architect H2)."""

    curve: ScoreInformationCurve
    saturation: str | None
    min_useful: str | None
    refusal: str | None

    @property
    def floor_lift(self) -> float | None:
        return self.curve.floor_lift

    @property
    def ceiling_gap(self) -> float | None:
        return self.curve.ceiling_gap


def build_readout(records: list[RewardRecord]) -> CurveReadout:
    """Build the reward curve and attempt the saturation / min-useful readouts,
    surfacing the curve's own refusal verbatim when the ladder is too short."""
    curve = build_curve(records)
    try:
        saturation = saturation_point(curve).rung
        min_useful = min_useful_combo(curve).rung
        refusal = None
    except InsufficientLadderError as exc:
        saturation = None
        min_useful = None
        refusal = str(exc)
    return CurveReadout(curve=curve, saturation=saturation, min_useful=min_useful, refusal=refusal)


def _reward_span(curve: ScoreInformationCurve) -> dict[str, Any] | None:
    """The reward delta across the executable ladder: top rung mean - bottom rung mean,
    in ladder order. This is the readout a short ``none``→``oracle`` ladder DOES
    support, where ``floor_lift`` / ``ceiling_gap`` (which need ``ours``) return None.
    None for a degenerate single-rung curve."""
    rungs = curve.rungs
    if len(rungs) < 2:
        return None
    return {
        "from_rung": rungs[0].rung,
        "to_rung": rungs[-1].rung,
        "delta": rungs[-1].mean_reward - rungs[0].mean_reward,
    }


def _per_task_curve(
    summary: dict[str, Any], reward_records: list[RewardRecord]
) -> list[dict[str, Any]]:
    """Each held-out task's reward at every executable rung — the per-task curve the
    aggregate is the mean of. One row per bundle; ``None`` where a rung's reward could
    not be formed (the artifact term is always present, so this stays populated)."""
    records = {(r.work_id, r.rung): r.reward for r in reward_records}
    rows: list[dict[str, Any]] = []
    for bundle in summary["per_bundle"]:
        work_id = bundle["work_id"]
        rows.append(
            {
                "work_id": work_id,
                "rewards": {rung: records.get((work_id, rung)) for rung in summary["conditions"]},
            }
        )
    return rows


def _efficiency_rollup(summary: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Surface the per-bundle paired-delta aggregates the grid already computed,
    restricted to the efficiency counters (the dec-gck headline axis)."""
    gaps = summary.get("gaps", {})
    return {field: gaps[field] for field in EFFICIENCY_FIELDS if field in gaps}


def _source_coverage(summary: dict[str, Any]) -> dict[str, int]:
    """Held-out bundles grouped by rig (the ``<rig>-<hash>`` work-id prefix). One rig
    here is itself the finding — single-source external validity is a stated limit."""
    counts: dict[str, int] = {}
    for row in summary["per_bundle"]:
        rig = row["work_id"].rsplit("-", 1)[0]
        counts[rig] = counts.get(rig, 0) + 1
    return counts


def assemble(summary: dict[str, Any]) -> dict[str, Any]:
    """The full machine artifact the markdown is rendered from."""
    reward_records = _reward_records(summary)
    readout = build_readout(reward_records)
    repro = _leg_stats(summary, REPRO_LEG)
    artifact = _leg_stats(summary, ARTIFACT_LEG)
    return {
        "schema": "headline-curve.v1",
        "held_out_n": len(summary["per_bundle"]),
        "executable_rungs": list(summary["conditions"]),
        "ladder_order": list(DEFAULT_RUNGS),
        "reward_curve": [
            {
                "rung": r.rung,
                "mean_reward": r.mean_reward,
                "lower_bound": r.lower_bound,
                "upper_bound": r.upper_bound,
                "n_tasks": r.n_tasks,
            }
            for r in readout.curve.rungs
        ],
        "reward_span": _reward_span(readout.curve),
        "floor_lift": readout.floor_lift,
        "ceiling_gap": readout.ceiling_gap,
        "saturation_point": readout.saturation,
        "min_useful_combo": readout.min_useful,
        "ladder_refusal": readout.refusal,
        "per_task_curve": _per_task_curve(summary, reward_records),
        "reward_legs": {
            REPRO_LEG: [vars(s) for s in repro],
            ARTIFACT_LEG: [vars(s) for s in artifact],
        },
        "efficiency": _efficiency_rollup(summary),
        "source_coverage": _source_coverage(summary),
        "rung_availability": summary.get("rung_availability", {}),
        "quality_guard": summary.get("quality_guard", {}),
    }


def _fmt(value: float | None, places: int = 3) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _leg_row(stats: Sequence[LegStat], rung: str) -> LegStat | None:
    return next((s for s in stats if s.rung == rung), None)


def render_markdown(artifact: dict[str, Any]) -> str:
    """Render the headline report by composing the per-section renderers. Pure string
    assembly over the machine artifact — no recomputation, so the doc and the JSON can
    never drift."""
    sections = (
        _render_header,
        _render_curve_section,
        _render_per_task_section,
        _render_legs_section,
        _render_saturation_section,
        _render_efficiency_section,
        _render_coverage_section,
        _render_footnote_section,
    )
    lines: list[str] = []
    for section in sections:
        lines.extend(section(artifact))
    return "\n".join(lines)


def _render_header(artifact: dict[str, Any]) -> list[str]:
    sources = artifact["source_coverage"]
    counts = ", ".join(f"{rig} ({c})" for rig, c in sorted(sources.items()))
    return [
        "# mem-apg.4 — Headline: ablation score-vs-information curve",
        "",
        "Aggregates mem-apg.3's per-rung grid scores (`.mem/grid/summary.json`) into the "
        "D17 ablation headline. Generated by `scripts/build_headline_report.py` from the "
        "scored artifacts — pure deterministic aggregation (ZFC), no re-scoring and no "
        "hand-weighted composite.",
        "",
        f"**Held-out N = {artifact['held_out_n']} bundles**, {counts}, 1 repeat each. "
        f"Executable rungs this run: **{' < '.join(artifact['executable_rungs'])}** "
        "(information increasing left→right).",
        "",
    ]


def _render_curve_section(artifact: dict[str, Any]) -> list[str]:
    lines = [
        "## Score-vs-information curve (aggregate reward)",
        "",
        "Per-rung reward = mean over held-out tasks of the run's `combined_reward` "
        "(the established D17 contract: deterministic gold-test term + artifact term, "
        "repeats collapsed within task). The two legs are broken out below so nothing "
        "hides behind the weighting.",
        "",
        "| rung | mean reward | 95% CI | n tasks |",
        "|---|---:|---|---:|",
    ]
    for r in artifact["reward_curve"]:
        ci = f"[{_fmt(r['lower_bound'])}, {_fmt(r['upper_bound'])}]"
        lines.append(f"| {r['rung']} | {_fmt(r['mean_reward'])} | {ci} | {r['n_tasks']} |")
    lines.append("")
    span = artifact["reward_span"]
    if span is not None:
        lines.append(
            f"- **Reward span** ({span['to_rung']} - {span['from_rung']}): "
            f"{_fmt(span['delta'])} reward — the information ceiling barely separates "
            "from the zero-memory floor on this pool; the curve is nearly flat in reward, "
            "which is why the dec-gck headline reads off the efficiency axis (below), not "
            "this one."
        )
    lines.append(
        "- `floor_lift` (ours - none) and `ceiling_gap` (oracle - ours) are "
        f"{_fmt(artifact['floor_lift'])} / {_fmt(artifact['ceiling_gap'])} — both need "
        "the `ours` rung, absent from this run (see coverage)."
    )
    lines.append("")
    return lines


def _render_per_task_section(artifact: dict[str, Any]) -> list[str]:
    rungs = artifact["executable_rungs"]
    lines = [
        "### Per-task curve (the rows the aggregate averages)",
        "",
        "| bundle | " + " | ".join(rungs) + " |",
        "|---|" + "---:|" * len(rungs),
    ]
    for row in artifact["per_task_curve"]:
        cells = " | ".join(_fmt(row["rewards"].get(rung)) for rung in rungs)
        lines.append(f"| {row['work_id'].rsplit('-', 1)[-1]} | {cells} |")
    lines.append("")
    return lines


def _render_legs_section(artifact: dict[str, Any]) -> list[str]:
    repro = [LegStat(**s) for s in artifact["reward_legs"][REPRO_LEG]]
    art = [LegStat(**s) for s in artifact["reward_legs"][ARTIFACT_LEG]]
    lines = [
        "## Reward legs (decomposed — no weighted headline number)",
        "",
        "| rung | repro pass-rate (n) | artifact-F1 mean ± sd | range |",
        "|---|---|---|---|",
    ]
    for rung in artifact["executable_rungs"]:
        rp = _leg_row(repro, rung)
        af = _leg_row(art, rung)
        rp_cell = f"{_fmt(rp.mean)} ({rp.n})" if rp else "—"
        af_cell = f"{_fmt(af.mean)} ± {_fmt(af.spread)}" if af else "—"
        rng_cell = f"[{_fmt(af.lo)}, {_fmt(af.hi)}]" if af else "—"
        lines.append(f"| {rung} | {rp_cell} | {af_cell} | {rng_cell} |")
    lines.append("")
    return lines


def _render_saturation_section(artifact: dict[str, Any]) -> list[str]:
    lines = ["## Saturation point & minimum-useful information combination", ""]
    if artifact["ladder_refusal"] is not None:
        rungs = artifact["executable_rungs"]
        lines += [
            "**REFUSED — the live ladder is too short to locate either readout.** "
            "`grading/curve.py` raises rather than fabricate a verdict:",
            "",
            f"> {artifact['ladder_refusal']}",
            "",
            "Both readouts need interior resolution and a combination axis (≥4 rungs); "
            f"the executable ladder here has {len(rungs)} ({', '.join(rungs)}). They "
            "become computable once mem-whi lands the `builtin` / `ours+builtin` rungs "
            "and a distiller populates `ours`.",
        ]
    else:
        lines += [
            f"- **Saturation point:** {artifact['saturation_point']}",
            f"- **Minimum-useful combo:** {artifact['min_useful_combo']}",
        ]
    lines.append("")
    return lines


def _render_efficiency_section(artifact: dict[str, Any]) -> list[str]:
    lines = [
        "## Efficiency leg (dec-gck headline axis) — paired deltas, oracle - none",
        "",
        "Negative = the file-list oracle rung was cheaper. Medians are reported "
        "alongside means because a single no-edit confound (km0wj) dominates the mean.",
        "",
        "| metric | median Δ | mean Δ | n oracle > none | n pairs |",
        "|---|---:|---:|---:|---:|",
    ]
    efficiency = artifact["efficiency"]
    # Iterate the canonical field order so an absent metric surfaces as an explicit
    # "(not reported)" row rather than vanishing from the table (no silent truncation).
    for field in EFFICIENCY_FIELDS:
        g = efficiency.get(field)
        if g is None:
            lines.append(f"| {field} | (not reported) | | | |")
            continue
        lines.append(
            f"| {field} | {_fmt(g['median_delta'], 1)} | {_fmt(g['mean_delta'], 1)} | "
            f"{g['n_oracle_gt_none']} | {g['n_pairs']} |"
        )
    lines += [
        "",
        "The efficiency effect is **bundle-conditional in both sign and magnitude** — "
        "the file list redirects *where* the effort goes more than it reduces the total.",
        "",
    ]
    return lines


def _render_coverage_section(artifact: dict[str, Any]) -> list[str]:
    n = artifact["held_out_n"]
    sources = artifact["source_coverage"]
    source_cells = ", ".join(f"{rig}: {c}/{n}" for rig, c in sorted(sources.items()))
    lines = [
        "## Per-rung / per-source coverage",
        "",
        f"- **Source:** single rig — {source_cells}. External validity is bounded to one "
        "codebase; cross-rig coverage needs the rig-expansion in mem-e3h2.",
        "- **Rung availability (full D17 ladder):**",
        "",
        "| rung | status |",
        "|---|---|",
    ]
    availability = artifact["rung_availability"]
    # Canonical ladder first, then any extra rungs present in the grid (e.g. `curated`)
    # appended in sorted order so none is silently dropped from coverage.
    extra = sorted(r for r in availability if r not in DEFAULT_RUNGS)
    for rung in list(DEFAULT_RUNGS) + extra:
        lines.append(f"| {rung} | {_availability_text(availability.get(rung))} |")
    lines.append("")
    return lines


def _render_footnote_section(artifact: dict[str, Any]) -> list[str]:
    guard = artifact["quality_guard"]
    passes = guard.get("repro_passed", {})
    scored = guard.get("repro_scored_pairs") or guard.get("repro_scored_rows")
    # Denominator per rung = the rung's own repro-scored task count (a rung whose run
    # fell back to diff_sim was NOT repro-scored), so the fraction never overstates
    # coverage. Falls back to held-out N only if the leg is absent entirely.
    repro_n = {s["rung"]: s["n"] for s in artifact["reward_legs"][REPRO_LEG]}
    pass_summary = ", ".join(
        f"{rung} {passes.get(rung, 0)}/{repro_n.get(rung, artifact['held_out_n'])}"
        for rung in artifact["executable_rungs"]
    )
    return [
        "## Merged-diff outcome-lift (opportunistic footnote)",
        "",
        f"- **Gold-test repro (the merged diff's own fail-to-pass tests):** {pass_summary} "
        f"over {scored} scored pairs — **flat across the information rung.** Memory neither "
        "buys nor costs merged-diff reproduction anywhere in the pool.",
        "- **The true merged-PR/CI outcome oracle remains structurally uncomputable** on "
        "this corpus: the held-out bundles carry only `issue_work_id` + fanout, no "
        "bead→PR→repo→commit linkage (see memory `mem-corpus-no-outcome-linkage`, "
        "mem-apg.5). The repro proxy above is the only outcome signal available, and it "
        "is reported as a footnote, not the headline.",
        "",
    ]


def _availability_text(avail: Any) -> str:
    """One-line rung-availability cell from the grid's ``rung_availability`` value,
    which is either a status string or a ``{status, reason}`` object."""
    if avail is None:
        return "not reported"
    if isinstance(avail, str):
        return avail
    status = avail.get("status", "?")
    reason = avail.get("reason")
    return f"**{status}** — {reason}" if reason else f"**{status}**"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the mem-apg.4 headline report.")
    parser.add_argument(
        "--summary",
        type=Path,
        default=PROJECT_ROOT / ".mem" / "grid" / "summary.json",
        help="Path to mem-apg.3's grid summary.json.",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=PROJECT_ROOT / ".mem" / "grid" / "headline-curve.json",
        help="Where to write the machine artifact.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "docs" / "mem-apg.4-ablation-headline.md",
        help="Where to write the markdown report.",
    )
    args = parser.parse_args(argv)

    summary = json.loads(args.summary.read_text())
    artifact = assemble(summary)
    report = render_markdown(artifact)

    args.out_json.write_text(json.dumps(artifact, indent=2) + "\n")
    args.report.write_text(report + "\n")
    print(f"wrote {args.out_json}")
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
