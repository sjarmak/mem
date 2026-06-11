#!/usr/bin/env python3
"""mem-75t.9 PHASE 3 driver: cross-session iteration metrics over the join sidecar.

Reads the session<->bead join (`scripts/build_session_join.py` output), selects
the multi-session population (beads with >= 2 STRONG-linked sessions that exist
in the store), projects each session transcript onto a
`membench.cross_session.SessionView` (cost via `extract_efficiency`, reads via
`project_claude_stream`, failure signatures via the canonical `mem
extract-errors` subprocess extractor), and writes the metrics artifact
(default `/home/ds/projects/mem/.mem/cross-session-metrics.json`):

- per-rig coverage (beads with >= 2 sessions),
- iterations distribution,
- per-bead summed cost,
- redundant-read overlap rates (session N+1 re-reading session N's files),
- THE headline: within-task cross-session failure recurrence rate.

Transcripts and store are STRICTLY read-only. Session views are cached per
transcript path (one session can be linked to several beads).

Usage (from memory-bench/):

    uv run python scripts/compute_cross_session.py \
        [--join PATH] [--store PATH] [--mem-bin PATH] [--out PATH] \
        [--min-sessions 2] [--limit-beads N]

ZFC: pure plumbing — IO, reuse of existing projections, arithmetic aggregation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membench.cross_session import (
    BeadCrossSession,
    SessionView,
    aggregate_metrics,
    bead_cross_session,
    build_session_view,
)
from membench.harbor.base_rate_spike import make_cli_extractor

DEFAULT_JOIN = "/home/ds/projects/mem/.mem/session-bead-join.json"
DEFAULT_STORE = "/home/ds/projects/mem/.mem/store.db"
DEFAULT_OUT = "/home/ds/projects/mem/.mem/cross-session-metrics.json"
DEFAULT_MEM_BIN = "/home/ds/projects/mem/bin/mem"


def load_rigs(store_path: str) -> dict[str, str]:
    con = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT work_id, rig FROM work_records").fetchall()
    finally:
        con.close()
    return {str(w): str(r) for w, r in rows}


def select_population(
    rows: Sequence[Mapping[str, Any]], *, min_sessions: int
) -> dict[str, list[Mapping[str, Any]]]:
    """work_id -> its strong, in-store link rows (one per session), for beads
    with at least `min_sessions` distinct sessions."""
    by_bead: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        if row["strength"] != "strong" or not row["in_store"]:
            continue
        per_session = by_bead.setdefault(str(row["work_id"]), {})
        # One row per session id: keep the row with the most strong mentions
        # (duplicate session ids across transcript copies are rare but real).
        prev = per_session.get(str(row["session_id"]))
        if prev is None or int(row["n_strong"]) > int(prev["n_strong"]):
            per_session[str(row["session_id"])] = row
    return {
        work_id: list(sessions.values())
        for work_id, sessions in by_bead.items()
        if len(sessions) >= min_sessions
    }


def coverage_by_rig(
    population: Mapping[str, Sequence[Any]], rigs: Mapping[str, str]
) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = {}
    for work_id, sessions in population.items():
        rig = rigs.get(work_id, "unknown")
        entry = table.setdefault(rig, {"beads": 0, "sessions": 0})
        entry["beads"] += 1
        entry["sessions"] += len(sessions)
    return dict(sorted(table.items(), key=lambda kv: -kv[1]["beads"]))


def bead_summary(bead: BeadCrossSession) -> dict[str, Any]:
    """The per-bead detail row persisted in the artifact (file sets dropped —
    they are large and reproducible from the transcripts)."""
    return {
        "work_id": bead.work_id,
        "iterations": bead.iterations,
        "total_turns": bead.total_turns,
        "total_tool_calls": bead.total_tool_calls,
        "total_input_tokens": bead.total_input_tokens,
        "total_output_tokens": bead.total_output_tokens,
        "sessions": [
            {
                "session_id": s.session_id,
                "start": s.start,
                "end": s.end,
                "turns": s.turns,
                "tool_calls": s.tool_calls,
                "n_files_read": len(s.files_read),
                "n_error_signatures": len(s.relaxed_signatures),
            }
            for s in bead.sessions
        ],
        "pairs": [asdict(p) for p in bead.pairs],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--join", default=DEFAULT_JOIN)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--mem-bin", default=DEFAULT_MEM_BIN)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--min-sessions", type=int, default=2)
    parser.add_argument("--limit-beads", type=int, default=None)
    args = parser.parse_args(argv)

    join = json.loads(Path(args.join).read_text(encoding="utf-8"))
    rigs = load_rigs(args.store)
    population = select_population(join["rows"], min_sessions=args.min_sessions)
    if args.limit_beads is not None:
        population = dict(sorted(population.items())[: args.limit_beads])
    coverage = coverage_by_rig(population, rigs)
    n_sessions = len({r["transcript_path"] for rows in population.values() for r in rows})
    print(f"population: {len(population)} beads, {n_sessions} distinct transcripts")

    extractor = make_cli_extractor(args.mem_bin)
    view_cache: dict[str, SessionView] = {}
    beads: list[BeadCrossSession] = []
    skipped: dict[str, str] = {}
    t0 = time.monotonic()
    for i, (work_id, link_rows) in enumerate(sorted(population.items())):
        views: list[SessionView] = []
        for row in link_rows:
            path = str(row["transcript_path"])
            view = view_cache.get(path)
            if view is None:
                try:
                    text = Path(path).read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    skipped[path] = f"unreadable: {exc}"
                    continue
                view = build_session_view(
                    session_id=str(row["session_id"]),
                    transcript_path=path,
                    stream_text=text,
                    extractor=extractor,
                    start=row.get("session_start"),
                    end=row.get("session_end"),
                )
                view_cache[path] = view
            views.append(view)
        if len(views) >= args.min_sessions:
            beads.append(bead_cross_session(work_id, views))
        else:
            skipped[work_id] = "too few readable sessions"
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(population)} beads ({time.monotonic() - t0:.0f}s)")

    summary = aggregate_metrics(beads)
    elapsed = time.monotonic() - t0
    print(f"metrics over {len(beads)} beads in {elapsed:.0f}s")
    print(json.dumps({k: v for k, v in summary.items() if k != "iterations_histogram"}, indent=2))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "join": args.join,
                "min_sessions": args.min_sessions,
                "compute_seconds": round(elapsed, 1),
                "coverage_by_rig": coverage,
                "summary": summary,
                "n_skipped": len(skipped),
                "skipped_sample": dict(list(skipped.items())[:25]),
                "beads": [bead_summary(b) for b in beads],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
