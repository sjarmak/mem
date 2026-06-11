#!/usr/bin/env python3
"""mem-0ut narrative-diff CLI: explicit (cold, warm) pairs x store -> per-pair
narrative diffs + comparative-judge outcomes + the qualitative readout.

The mechanical sibling (`arm_analysis.py`) reports per-arm metric distributions over
DIFFERENT beads. This script is the PAIRED qualitative layer: each input pair names
two work_ids (conventionally left=cold, right=warm) that ran the same task; for each
pair it builds both `Attempt`s from the read-only store, generates the deterministic
`NarrativeDiff`, runs the pairwise comparative judge, and aggregates which arm won
across all pairs.

The pairs file is EXPLICIT experimenter input (never inferred): JSON (a list of
``{"left": ..., "right": ...}`` rows) or CSV (``left,right`` header).

The judge defaults to the offline `StubComparativeJudge` (deterministic, no model,
no network) so a dry run touches nothing external; ``--judge claude`` swaps in
headless ``claude -p``. A pair whose work_id/trace cannot be resolved is a RECORDED
skip (same typed reasons as `arm_analysis`), never a crash.

Usage (from memory-bench/):

    uv run python scripts/arm_narrative.py --pairs pairs.json \
        [--store /home/ds/projects/mem/.mem/store.db] \
        [--scope-manifest <repo>/.claude/brains/<name>.json] \
        [--judge stub|claude] \
        [--out-json .mem/arm-narrative.json] [--report docs/<name>.md]

ZFC: pure plumbing — read-only store IO, file IO, mechanical aggregation; the only
semantic step is the delegated judge call.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arm_analysis import (
    SKIP_NO_TRACE_PATH,
    SKIP_TRACE_FILE_MISSING,
    SKIP_WORK_ID_NOT_IN_STORE,
    open_store_readonly,
    resolve_trace_path,
)

from membench.armcompare import extract_bead_metrics, load_scope_files
from membench.bbon import (
    ClaudeComparativeJudge,
    ComparativeJudge,
    Comparison,
    StubComparativeJudge,
    build_attempt,
    build_comparison,
    compare_attempts,
    generate_narrative_diff,
    summarize_comparisons,
)

DEFAULT_STORE = Path("/home/ds/projects/mem/.mem/store.db")
DEFAULT_OUT_JSON = Path("/home/ds/projects/mem/.mem/arm-narrative.json")

# Default offline verdict: the warm arm (right) wins at low confidence. Mechanical
# placeholder so a dry run produces a full product without a model; --judge claude
# replaces it with a real verdict.
_STUB_WINNER = "B"
_STUB_CONFIDENCE = 0.5


def _pair_row(row: Mapping[str, Any], path: Path, index: int) -> tuple[str, str]:
    """One (left, right) pair from a JSON/CSV row, with a row-located error when a
    side is missing (a bare KeyError gives the experimenter nothing to fix on)."""
    try:
        return str(row["left"]), str(row["right"])
    except KeyError as exc:
        raise ValueError(f"{path}: row {index} missing {exc} (need 'left' and 'right')") from exc


def load_pairs(path: Path) -> list[tuple[str, str]]:
    """The explicit (left, right) work_id pairs. JSON (list of ``{left, right}``
    rows) or CSV (``left,right`` header), by suffix. An empty file or a row missing
    a side raises."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"{path}: JSON must be a list of {{left, right}} rows")
        pairs = [_pair_row(row, path, i) for i, row in enumerate(raw)]
    elif suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as handle:
            pairs = [_pair_row(row, path, i) for i, row in enumerate(csv.DictReader(handle))]
    else:
        raise ValueError(f"{path}: unsupported suffix {suffix!r} (expected .json or .csv)")
    if not pairs:
        raise ValueError(f"{path}: empty pairs file")
    return pairs


def _make_judge(kind: str) -> ComparativeJudge:
    if kind == "stub":
        return StubComparativeJudge(winner=_STUB_WINNER, confidence=_STUB_CONFIDENCE)
    if kind == "claude":
        return ClaudeComparativeJudge()
    raise ValueError(f"unknown judge {kind!r} (expected 'stub' or 'claude')")


def _resolve_attempt(
    con: sqlite3.Connection,
    work_id: str,
    arm: str,
    scope_files: Sequence[str] | None,
) -> Any:
    """(attempt, steps) for one work_id, or a skip dict when its trace can't be
    resolved. The metric vector is computed via `armcompare.extract_bead_metrics`
    and stored on the attempt's ``result``."""
    try:
        record, trace_path = resolve_trace_path(con, work_id)
    except KeyError:
        return {"work_id": work_id, "arm": arm, "reason": SKIP_WORK_ID_NOT_IN_STORE}
    if trace_path is None:
        return {"work_id": work_id, "arm": arm, "reason": SKIP_NO_TRACE_PATH}
    trace_file = Path(trace_path)
    if not trace_file.is_file():
        return {"work_id": work_id, "arm": arm, "reason": SKIP_TRACE_FILE_MISSING}
    stream_text = trace_file.read_text(encoding="utf-8")
    metrics = extract_bead_metrics(record, stream_text, scope_files)
    return build_attempt(work_id, arm, record, stream_text, metrics=metrics.metrics())


def analyze(
    pairs: Sequence[tuple[str, str]],
    store_path: Path,
    scope_files: Sequence[str] | None,
    judge: ComparativeJudge,
) -> dict[str, Any]:
    """The full qualitative product: per-pair narrative-diff summaries + judge
    verdicts, typed skips, and the win-by-arm summary (None when no pair resolved)."""
    comparisons: list[Comparison] = []
    per_pair: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    con = open_store_readonly(store_path)
    try:
        for left_id, right_id in pairs:
            left = _resolve_attempt(con, left_id, "cold", scope_files)
            right = _resolve_attempt(con, right_id, "warm", scope_files)
            if isinstance(left, dict):
                skips.append(left)
                print(f"SKIP  {left_id:<28} cold   {left['reason']}")
                continue
            if isinstance(right, dict):
                skips.append(right)
                print(f"SKIP  {right_id:<28} warm   {right['reason']}")
                continue
            left_attempt, left_steps = left
            right_attempt, right_steps = right
            diff = generate_narrative_diff(left_attempt, right_attempt, left_steps, right_steps)
            judgment = compare_attempts(left_attempt, right_attempt, diff, judge)
            comparison = build_comparison(left_attempt, right_attempt, diff, judgment)
            comparisons.append(comparison)
            per_pair.append(comparison.model_dump())
            print(
                f"DONE  {left_id} (cold) vs {right_id} (warm)  "
                f"winner={comparison.winner_arm} conf={comparison.confidence:.2f}"
            )
    finally:
        con.close()

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "store": str(store_path),
        "n_pairs": len(pairs),
        "n_resolved": len(comparisons),
        "judge_model": judge.model,
        "per_pair": per_pair,
        "skips": skips,
        "summary": summarize_comparisons(comparisons) if comparisons else None,
    }


def render_report(payload: Mapping[str, Any]) -> str:
    """A compact markdown view: the win-by-arm summary + each pair's verdict and
    narrative-diff summary (the qualitative evidence trail)."""
    lines = [
        "# Narrative-diff judge: warm vs cold (mem-0ut)",
        "",
        f"Generated: {payload['generated_at']}  ",
        f"Store: `{payload['store']}`  ",
        f"Judge model: `{payload['judge_model']}`  ",
        f"Pairs: {payload['n_pairs']} · resolved: {payload['n_resolved']} "
        f"· skips: {len(payload['skips'])}",
        "",
    ]
    summary = payload["summary"]
    if summary is None:
        lines += ["No pair resolved to both traces -- no summary.", ""]
        return "\n".join(lines)
    wins = ", ".join(f"{arm}: {n}" for arm, n in sorted(summary["wins_by_arm"].items()))
    lines += [
        f"Wins by arm: {wins}  ",
        f"Mean judge confidence: {summary['mean_confidence']:.3f}",
        "",
        "## Per-pair verdicts",
        "",
    ]
    for pair in payload["per_pair"]:
        lines += [
            f"### {pair['left_work_id']} (cold) vs {pair['right_work_id']} (warm) "
            f"-- winner: **{pair['winner_arm']}** (conf {pair['confidence']:.2f})",
            "",
            "```",
            pair["summary"],
            "```",
            "",
            f"_Judge rationale:_ {pair['rationale']}",
            "",
        ]
    if payload["skips"]:
        lines += ["## Skips", ""]
        lines += [f"- `{s['work_id']}` ({s['arm']}): {s['reason']}" for s in payload["skips"]]
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs",
        type=Path,
        required=True,
        help="explicit (left, right) work_id pairs (.json/.csv)",
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument(
        "--scope-manifest",
        type=Path,
        default=None,
        help="brains manifest JSON for the metric vector's distractor-read rate (optional)",
    )
    parser.add_argument(
        "--judge",
        choices=("stub", "claude"),
        default="stub",
        help="stub (offline, deterministic) or claude (headless claude -p)",
    )
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument(
        "--report", type=Path, default=None, help="optional markdown report path (e.g. docs/x.md)"
    )
    args = parser.parse_args(argv)

    pairs = load_pairs(args.pairs)
    scope_files = load_scope_files(args.scope_manifest) if args.scope_manifest else None
    judge = _make_judge(args.judge)
    payload = analyze(pairs, args.store, scope_files, judge)
    payload["pairs_file"] = str(args.pairs)
    payload["scope_manifest"] = str(args.scope_manifest) if args.scope_manifest else None

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nwrote {args.out_json}  (resolved={payload['n_resolved']} "
        f"skips={len(payload['skips'])})"
    )
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(payload), encoding="utf-8")
        print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
