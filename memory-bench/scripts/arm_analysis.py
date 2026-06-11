#!/usr/bin/env python3
"""mem-0ut arm-analysis CLI: arms file x store -> per-bead metrics + unpaired summary.

Resolves each assigned work_id's trace through the store (STRICTLY read-only:
``file:...?mode=ro``), extracts the five experiment axes per bead via
`membench.armcompare.extract_bead_metrics`, and writes one JSON product with
per-bead vectors, typed skips, and the unpaired per-arm summary
(`summarize_arms`). A missing work_id or trace is a RECORDED skip
(``work_id_not_in_store`` / ``no_trace_path`` / ``trace_file_missing``), never
a crash -- but a malformed record or stream still raises (those are substrate
bugs, not experiment conditions).

Trace resolution order per work_id: ``work_records.trace_path``, else the
record JSON's ``trace.jsonl_path``, else any non-null ``record_agents.trace_ref``
(agent_id order, first wins).

The arms file is EXPLICIT experimenter input (`load_arm_assignment` -- JSON
mapping/rows or CSV), never inferred from the traces. ``--scope-manifest``
optionally points at a brains manifest (``.claude/brains/<name>.json``) whose
in-scope file list feeds the distractor-read rate; without it that metric is
None for every bead.

Usage (from memory-bench/):

    uv run python scripts/arm_analysis.py --arms arms.json \
        [--store /home/ds/projects/mem/.mem/store.db] \
        [--scope-manifest <repo>/.claude/brains/<name>.json] \
        [--out-json .mem/arm-analysis.json] [--report docs/<name>.md]

ZFC: pure plumbing -- read-only store IO, file IO, arithmetic summary.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from membench.armcompare import (
    ARMS,
    BeadMetrics,
    extract_bead_metrics,
    load_arm_assignment,
    load_scope_files,
    summarize_arms,
)

DEFAULT_STORE = Path("/home/ds/projects/mem/.mem/store.db")
DEFAULT_OUT_JSON = Path("/home/ds/projects/mem/.mem/arm-analysis.json")

# Typed skip reasons -- the only conditions the CLI absorbs instead of raising.
SKIP_WORK_ID_NOT_IN_STORE = "work_id_not_in_store"
SKIP_NO_TRACE_PATH = "no_trace_path"
SKIP_TRACE_FILE_MISSING = "trace_file_missing"


def open_store_readonly(store_path: Path) -> sqlite3.Connection:
    """The store connection, pinned read-only at the SQLite level (mode=ro)."""
    return sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)


def resolve_trace_path(con: sqlite3.Connection, work_id: str) -> tuple[dict[str, Any], str | None]:
    """(record JSON, resolved trace path or None) for one work_id.

    Raises KeyError when the work_id is not in the store (the caller records
    the typed skip). Resolution order is documented in the module docstring."""
    row = con.execute(
        "SELECT record, trace_path FROM work_records WHERE work_id = ?", (work_id,)
    ).fetchone()
    if row is None:
        raise KeyError(work_id)
    record: dict[str, Any] = json.loads(row[0])
    if isinstance(row[1], str) and row[1]:
        return record, row[1]
    trace = record.get("trace")
    jsonl = trace.get("jsonl_path") if isinstance(trace, Mapping) else None
    if isinstance(jsonl, str) and jsonl:
        return record, jsonl
    agent_row = con.execute(
        "SELECT trace_ref FROM record_agents "
        "WHERE work_id = ? AND trace_ref IS NOT NULL ORDER BY agent_id LIMIT 1",
        (work_id,),
    ).fetchone()
    return record, agent_row[0] if agent_row else None


def analyze(
    assignment: Mapping[str, str],
    store_path: Path,
    scope_files: Sequence[str] | None,
) -> dict[str, Any]:
    """The full analysis product: per-bead metric rows (with their arm), typed
    skips, and the unpaired summary (None when no bead resolved at all)."""
    per_arm: dict[str, list[BeadMetrics]] = {arm: [] for arm in ARMS}
    per_bead: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    con = open_store_readonly(store_path)

    def skip(work_id: str, arm: str, reason: str, detail: str) -> None:
        skips.append({"work_id": work_id, "arm": arm, "reason": reason, "detail": detail})
        print(f"SKIP  {work_id:<28} {arm:<5} {reason}  {detail}")

    try:
        for work_id in sorted(assignment):
            arm = assignment[work_id]
            try:
                record, trace_path = resolve_trace_path(con, work_id)
            except KeyError:
                skip(work_id, arm, SKIP_WORK_ID_NOT_IN_STORE, str(store_path))
                continue
            if trace_path is None:
                skip(work_id, arm, SKIP_NO_TRACE_PATH, "no trace_path/jsonl_path/trace_ref")
                continue
            trace_file = Path(trace_path)
            if not trace_file.is_file():
                skip(work_id, arm, SKIP_TRACE_FILE_MISSING, trace_path)
                continue
            stream_text = trace_file.read_text(encoding="utf-8")
            metrics = extract_bead_metrics(record, stream_text, scope_files)
            per_arm[arm].append(metrics)
            per_bead.append({"arm": arm, **metrics.model_dump()})
            print(
                f"DONE  {work_id:<28} {arm:<5} turns={metrics.turns} "
                f"tool_calls={metrics.tool_calls} tokens={metrics.total_tokens} "
                f"iters_to_green={metrics.iterations_to_green}"
            )
    finally:
        con.close()

    extracted = any(per_arm[arm] for arm in ARMS)
    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "store": str(store_path),
        "n_assigned": len(assignment),
        "n_extracted": len(per_bead),
        "per_bead": per_bead,
        "skips": skips,
        "summary": summarize_arms(per_arm) if extracted else None,
    }


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def render_report(payload: Mapping[str, Any]) -> str:
    """A compact markdown view of the JSON product (per-arm stats + deltas)."""
    lines = [
        "# Arm analysis: warm vs cold (mem-0ut)",
        "",
        f"Generated: {payload['generated_at']}  ",
        f"Store: `{payload['store']}`  ",
        f"Assigned: {payload['n_assigned']} · extracted: {payload['n_extracted']} "
        f"· skips: {len(payload['skips'])}",
        "",
    ]
    summary = payload["summary"]
    if summary is None:
        lines += ["No bead resolved to a trace -- no summary.", ""]
        return "\n".join(lines)
    lines += [
        f"Design: **{summary['design']}** (different beads per arm; deltas are "
        "warm aggregate - cold aggregate, not per-bead pairs).",
        "",
        "| metric | warm mean | warm median | n | cold mean | cold median | n "
        "| Δ mean | Δ median |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    warm, cold = summary["arms"]["warm"], summary["arms"]["cold"]
    for metric in warm:
        delta = summary["deltas"].get(metric, {})
        lines.append(
            f"| {metric} | {_fmt(warm[metric]['mean'])} | {_fmt(warm[metric]['median'])} "
            f"| {warm[metric]['n']} | {_fmt(cold[metric]['mean'])} "
            f"| {_fmt(cold[metric]['median'])} | {cold[metric]['n']} "
            f"| {_fmt(delta.get('mean_delta'))} | {_fmt(delta.get('median_delta'))} |"
        )
    if payload["skips"]:
        lines += ["", "## Skips", ""]
        lines += [
            f"- `{s['work_id']}` ({s['arm']}): {s['reason']} -- {s['detail']}"
            for s in payload["skips"]
        ]
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arms", type=Path, required=True, help="work_id -> arm file (.json or .csv)"
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument(
        "--scope-manifest",
        type=Path,
        default=None,
        help="brains manifest JSON for the distractor-read rate (optional)",
    )
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument(
        "--report", type=Path, default=None, help="optional markdown report path (e.g. docs/x.md)"
    )
    args = parser.parse_args(argv)

    assignment = load_arm_assignment(args.arms)
    scope_files = load_scope_files(args.scope_manifest) if args.scope_manifest else None
    payload = analyze(assignment, args.store, scope_files)
    payload["arms_file"] = str(args.arms)
    payload["scope_manifest"] = str(args.scope_manifest) if args.scope_manifest else None

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nwrote {args.out_json}  (extracted={payload['n_extracted']} "
        f"skips={len(payload['skips'])})"
    )
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(payload), encoding="utf-8")
        print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
