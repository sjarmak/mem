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

# The alias-guarded multi-session candidate set (mem-qw5): a work_id is
# multi-session iff >=2 DISTINCT non-suspect agents touched it. ``suspect=0``
# is the alias guard (an agent flagged as an alias/self-recurrence does not
# count toward distinct sessions). This is mechanical set membership, not a
# semantic judgment (ZFC).
MULTI_SESSION_SQL = """
SELECT work_id FROM record_agents
WHERE suspect = 0
GROUP BY work_id HAVING count(DISTINCT agent_id) >= 2
"""

# The convoy/epic extension (mem-apg.7): a fanned-out milestone/epic is multi-session
# work even when each of its sibling beads was touched by a single agent. A work_id
# qualifies iff the ISSUE GROUP it belongs to (records sharing its ``gc.var.issue``
# ref) spans >=2 distinct non-suspect agents in total -- i.e. the milestone was worked
# by multiple sessions across its fanned-out beads. Carving a focused sub-bundle out
# of such a convoy means admitting these member beads as candidates (the per-bead
# replayable slice) rather than rejecting the whole fanout. Same alias guard
# (``suspect=0``), same mechanical set membership (ZFC).
CONVOY_EPIC_SQL = """
WITH issue_of AS (
    SELECT work_id, json_extract(record, '$.metadata."gc.var.issue"') AS issue_ref
    FROM work_records
    WHERE json_extract(record, '$.metadata."gc.var.issue"') IS NOT NULL
),
group_agents AS (
    SELECT issue_of.issue_ref AS issue_ref
    FROM issue_of
    JOIN record_agents ON record_agents.work_id = issue_of.work_id
                      AND record_agents.suspect = 0
    GROUP BY issue_of.issue_ref
    HAVING count(DISTINCT record_agents.agent_id) >= 2
)
SELECT issue_of.work_id
FROM issue_of
JOIN group_agents ON group_agents.issue_ref = issue_of.issue_ref
"""

# The two multi-session population definitions ``--ms-population`` selects between.
# ``flat`` reproduces mem-apg.6 exactly; ``convoy-epic`` extends it with the
# issue-group members (the lever is additive -- it never drops a flat candidate).
MS_POPULATIONS = ("flat", "convoy-epic")

ELIGIBILITY_BASE = "trace_path NOT NULL AND base_commit NOT NULL AND status='closed'"
ELIGIBILITY_MULTI_SESSION = ELIGIBILITY_BASE + " AND >=2 distinct non-suspect record_agents"
ELIGIBILITY_CONVOY_EPIC = (
    ELIGIBILITY_BASE
    + " AND member of a multi-session group (flat >=2-agent work_id, OR >=2 distinct"
    " non-suspect agents across its gc.var.issue group)"
)


def _query_ids(store: Path, sql: str) -> set[str]:
    """The work_ids returned by ``sql`` over the read-only store (uri mode=ro)."""
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def load_multi_session_ids(store: Path) -> set[str]:
    """The flat alias-guarded multi-session work_ids (read-only)."""
    return _query_ids(store, MULTI_SESSION_SQL)


def load_convoy_epic_ids(store: Path) -> set[str]:
    """The convoy/epic issue-group members: work_ids whose ``gc.var.issue`` group
    spans >=2 distinct non-suspect agents (read-only). May overlap the flat set
    (a work_id can be both per-bead multi-session and a member of a multi-session
    issue group); ``multi_session_ids`` unions the two, so the overlap is deduped."""
    return _query_ids(store, CONVOY_EPIC_SQL)


def multi_session_ids(store: Path, population: str) -> set[str]:
    """The multi-session candidate ids for ``population``. ``convoy-epic`` is the
    additive extension: the flat set UNION the issue-group members."""
    flat = load_multi_session_ids(store)
    if population == "flat":
        return flat
    if population == "convoy-epic":
        return flat | load_convoy_epic_ids(store)
    raise ValueError(f"unknown ms-population {population!r}; choose one of {MS_POPULATIONS}")


def load_pool(
    store: Path, *, multi_session: bool = False, ms_population: str = "flat"
) -> list[dict[str, Any]]:
    """The bundle-eligible WorkRecords, parsed from the ``record`` JSON column.
    The store is opened read-only (uri mode=ro) — this script never writes to it.
    When ``multi_session`` is set, the pool is restricted to the multi-session
    candidate set named by ``ms_population`` (``flat`` or ``convoy-epic``)."""
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        rows = conn.execute(ELIGIBLE_SQL).fetchall()
    finally:
        conn.close()
    records = [json.loads(row[0]) for row in rows]
    if multi_session:
        ms_ids = multi_session_ids(store, ms_population)
        records = [r for r in records if str(r["work_id"]) in ms_ids]
    return records


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
    out: Path,
    store: Path,
    pool_size: int,
    ranked: list[CandidateAssessment],
    *,
    eligibility: str = ELIGIBILITY_BASE,
) -> None:
    artifact = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "store": str(store),
        "eligibility": eligibility,
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
    *,
    eligibility: str = ELIGIBILITY_BASE,
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
        f"`{store}` (read-only). Pool predicate: `{eligibility}`.",
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
    parser.add_argument(
        "--multi-session",
        action="store_true",
        help="restrict the eligible pool to the alias-guarded multi-session candidate "
        "set (>=2 distinct non-suspect record_agents; mem-qw5/mem-apg.6)",
    )
    parser.add_argument(
        "--ms-population",
        choices=MS_POPULATIONS,
        default="flat",
        help="which multi-session definition when --multi-session is set: 'flat' "
        "(per-work_id >=2 agents; mem-apg.6 reproducible) or 'convoy-epic' (also "
        "include members of a >=2-agent gc.var.issue group; mem-apg.7 carving)",
    )
    args = parser.parse_args()

    pool = [
        assess_shape(record)
        for record in load_pool(
            args.store, multi_session=args.multi_session, ms_population=args.ms_population
        )
    ]
    ranked = rank_candidates(pool, mutation_count_provider=default_mutation_count_provider)

    if not args.multi_session:
        eligibility = ELIGIBILITY_BASE
    elif args.ms_population == "convoy-epic":
        eligibility = ELIGIBILITY_CONVOY_EPIC
    else:
        eligibility = ELIGIBILITY_MULTI_SESSION
    write_artifact(args.json_out, args.store, len(pool), ranked, eligibility=eligibility)
    write_report(args.report_out, args.store, pool, ranked, eligibility=eligibility)

    print(f"pool={len(pool)} ranked -> {args.json_out} and {args.report_out}")
    print("top 10:")
    for i, a in enumerate(ranked[:10], start=1):
        print(
            f"  {i:2d}. {a.work_id}  overall={a.overall:.3f} "
            f"mut={a.mutation_calls} turns={a.trace_turns} rig={a.rig}"
        )


if __name__ == "__main__":
    main()
