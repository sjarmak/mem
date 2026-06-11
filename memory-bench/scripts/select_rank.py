#!/usr/bin/env python3
"""SELECT ranking run over the REAL bundle-eligible pool (mem-75t.7.4 acceptance).

Opens the live store STRICTLY READ-ONLY, loads the bundle-eligible pool
(``trace_path NOT NULL AND base_commit NOT NULL AND status='closed'``), projects
each stored WorkRecord JSON into the Mapping shape ``membench.assess`` reads,
ranks the pool with ``rank_candidates`` using the real (file-reading) mutation-call
provider, and writes:

- a machine artifact (ranked work_ids + per-criterion sub-scores), and
- a human report skeleton with the top-20 table and the top-10 facts
  (title + transcript size).

ZFC boundary: this script is pure mechanism — SQL, JSON projection, arithmetic
ranking, table formatting. The "is this bead a self-contained unit of work?"
judgment is NOT computed here: the report's eyeball section is authored by the
assessing model/human after the run (plan §4 P3: model picks, mechanism gathers).

Run from memory-bench/:  uv run python scripts/select_rank.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from membench.assess import (
    RUBRIC_V1,
    WEIGHTS,
    CandidateAssessment,
    default_mutation_count_provider,
    rank_candidates,
)

DEFAULT_STORE = Path("/home/ds/projects/mem/.mem/store.db")
DEFAULT_JSON_OUT = Path("/home/ds/projects/mem/.mem/select-ranking.json")
DEFAULT_REPORT_OUT = Path(__file__).resolve().parents[2] / ".gc/docs/mem-75t.7.4-select-ranking.md"

# Bundle-eligible pool (same predicate as the mem-75t.7.1 validation run).
ELIGIBLE_SQL = """
SELECT record FROM work_records
WHERE trace_path IS NOT NULL AND base_commit IS NOT NULL AND status = 'closed'
ORDER BY work_id
"""


def load_pool(store: Path) -> list[dict[str, Any]]:
    """The bundle-eligible WorkRecords, parsed from the ``record`` JSON column.
    The store is opened read-only (uri mode=ro) — this script never writes to it."""
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        rows = conn.execute(ELIGIBLE_SQL).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


def assess_shape(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project a stored WorkRecord into the Mapping shape ``membench.assess``
    reads (the ``validity.query_from_record`` field names). The only re-homing
    needed: the store nests the parsed turn count at ``trace.run.n_turns`` while
    the rubric reads ``trace.n_turns``. Returns a fresh dict — the loaded record
    is never mutated."""
    trace = record.get("trace") or {}
    run = trace.get("run") or {}
    shaped_trace = dict(trace)
    n_turns = run.get("n_turns")
    if isinstance(n_turns, int) and "n_turns" not in shaped_trace:
        shaped_trace["n_turns"] = n_turns
    return {**record, "trace": shaped_trace}


def _trace_path(record: Mapping[str, Any]) -> str | None:
    path = (record.get("trace") or {}).get("jsonl_path")
    return path if isinstance(path, str) else None


def _trace_size_bytes(record: Mapping[str, Any]) -> int | None:
    path = _trace_path(record)
    if path is None:
        return None
    p = Path(path)
    return p.stat().st_size if p.is_file() else None


def assessment_as_dict(rank: int, assessment: CandidateAssessment) -> dict[str, Any]:
    return {
        "rank": rank,
        "work_id": assessment.work_id,
        "rig": assessment.rig,
        "overall": round(assessment.overall, 4),
        "env_reconstructable": assessment.env_reconstructable,
        "replayable": assessment.replayable,
        "mutation_calls": assessment.mutation_calls,
        "trace_turns": assessment.trace_turns,
        "scores": {s.name: {"score": s.score, "reasoning": s.reasoning} for s in assessment.scores},
    }


def write_artifact(
    out: Path, store: Path, pool_size: int, ranked: list[CandidateAssessment]
) -> None:
    artifact = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "store": str(store),
        "eligibility": "trace_path NOT NULL AND base_commit NOT NULL AND status='closed'",
        "pool_size": pool_size,
        "rubric": list(RUBRIC_V1),
        "weights": dict(WEIGHTS),
        "ranking": [assessment_as_dict(i + 1, a) for i, a in enumerate(ranked)],
    }
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")


def _top_table(ranked: list[CandidateAssessment], by_id: Mapping[str, Mapping[str, Any]]) -> str:
    header = (
        "| # | work_id | rig | overall | mut calls | turns | trace KB | env | title |\n"
        "|---:|---|---|---:|---:|---:|---:|---:|---|\n"
    )
    rows = []
    for i, a in enumerate(ranked, start=1):
        record = by_id[a.work_id]
        size = _trace_size_bytes(record)
        title = str(record.get("title") or "").replace("|", "\\|")
        if len(title) > 70:
            title = title[:67] + "..."
        rows.append(
            f"| {i} | {a.work_id} | {a.rig} | {a.overall:.3f} | {a.mutation_calls} "
            f"| {a.trace_turns} | {size // 1024 if size is not None else '?'} "
            f"| {a.criterion('env_reconstructable').score:.1f} | {title} |"
        )
    return header + "\n".join(rows) + "\n"


def write_report(
    out: Path,
    store: Path,
    pool: list[dict[str, Any]],
    ranked: list[CandidateAssessment],
) -> None:
    by_id = {str(r["work_id"]): r for r in pool}
    rigs: dict[str, int] = {}
    for record in pool:
        rigs[str(record["rig"])] = rigs.get(str(record["rig"]), 0) + 1
    replayable = sum(1 for a in ranked if a.replayable)
    gated = [a for a in ranked if not (a.env_reconstructable and a.replayable)]
    today = datetime.date.today().isoformat()
    lines = [
        "# mem-75t.7.4 — SELECT ranking on the real bundle-eligible pool",
        "",
        f"Generated by `memory-bench/scripts/select_rank.py` on {today} from",
        f"`{store}` (read-only). Pool predicate: `trace_path NOT NULL AND",
        "base_commit NOT NULL AND status='closed'`.",
        "",
        f"- **Pool**: {len(pool)} records — "
        + ", ".join(f"{rig} {n}" for rig, n in sorted(rigs.items(), key=lambda kv: -kv[1]))
        + ".",
        f"- **Rubric**: RUBRIC_V1 ({', '.join(RUBRIC_V1)}), weights {dict(WEIGHTS)}.",
        f"- **Replayable (>=1 mutation call)**: {replayable}/{len(ranked)}; "
        f"**gated out** (env-unreconstructable or zero mutation calls): {len(gated)}.",
        "- Machine artifact: `.mem/select-ranking.json` (full per-criterion sub-scores).",
        "",
        "## Top 20",
        "",
        _top_table(ranked[:20], by_id),
        "## Top-10 self-containment eyeball",
        "",
        "_The lines below are an authored assessment (model/human judgment over bead",
        "title + trace volume), NOT a rubric output — the script only emits the facts",
        "table above (ZFC: the mechanism gathers signals; the model picks)._",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    args = parser.parse_args()

    pool = [assess_shape(record) for record in load_pool(args.store)]
    ranked = rank_candidates(pool, mutation_count_provider=default_mutation_count_provider)

    write_artifact(args.json_out, args.store, len(pool), ranked)
    write_report(args.report_out, args.store, pool, ranked)

    print(f"pool={len(pool)} ranked -> {args.json_out} and {args.report_out}")
    print("top 10:")
    for i, a in enumerate(ranked[:10], start=1):
        print(
            f"  {i:2d}. {a.work_id}  overall={a.overall:.3f} "
            f"mut={a.mutation_calls} turns={a.trace_turns} rig={a.rig}"
        )


if __name__ == "__main__":
    main()
