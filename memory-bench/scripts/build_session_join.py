#!/usr/bin/env python3
"""mem-75t.9 PHASE 1 driver: build the session<->bead join SIDECAR.

Walks the on-disk Claude Code transcript corpus (~19k jsonl files), runs the
content scan (`membench.session_join`) over every transcript, and writes the
sidecar join artifact (default `/home/ds/projects/mem/.mem/session-bead-join.json`).
This is a SIDECAR, not a store change — PHASE 2 (schema bump) is deferred to
mem-75t.4; the store and the transcripts are STRICTLY read-only here.

Three sections in the output:

- `rows`: per (session_file, work_id) link — strength, first/last mention
  timestamps, session start/end timestamps, `in_store` flag (the id grammar can
  surface ids that are not store work_ids, e.g. standalone gc session tokens).
- `calibration`: agreement against the store's existing one-session-per-bead
  assignee links (work_records.trace_path): for each of those links, did the
  content scan independently find the bead in that transcript? That rate is the
  calibration number for the whole approach.
- `dolt_history` (source b): per-bead assignee transitions read from the
  ALREADY-RUNNING shared city dolt server via a READ-ONLY client connection
  (the same `dolt --host ... sql -q` path mem's doltRunner uses). This NEVER
  starts a server (`bd dolt start` is forbidden — it downs every project on the
  machine). Reachability failures are recorded as gaps, never fatal.

Usage (from memory-bench/):

    uv run python scripts/build_session_join.py \
        [--roots GLOB ...] [--store PATH] [--out PATH] [--skip-dolt] [--limit N]

ZFC: pure plumbing — read-only IO, id-grammar scanning, arithmetic agreement.
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membench.session_join import (
    assignee_sessions,
    derive_prefixes,
    load_store_work_ids,
    scan_transcript_lines,
    work_id_pattern,
)

DEFAULT_ROOTS = (
    "/home/ds/.claude/projects",
    "/home/ds/.claude-homes/*/.claude/projects",
)
DEFAULT_STORE = "/home/ds/projects/mem/.mem/store.db"
DEFAULT_OUT = "/home/ds/projects/mem/.mem/session-bead-join.json"

# Read-only client connection to the shared city dolt server (mem's doltRunner
# defaults: src/ingest/beads.ts). Connecting as a CLIENT only — never a server.
DOLT_HOST = "127.0.0.1"
DOLT_PORT = 29620
DOLT_TIMEOUT_S = 300.0

_HISTORY_SQL = (
    "select id, assignee, min(commit_date) as first_seen, max(commit_date) as last_seen "
    "from dolt_history_issues where assignee is not null and assignee <> '' "
    "group by id, assignee"
)


def iter_transcripts(roots: Sequence[str]) -> list[Path]:
    """All *.jsonl transcripts under the expanded root globs, sorted."""
    files: set[Path] = set()
    for root in roots:
        for match in glob.glob(root):
            base = Path(match)
            if base.is_dir():
                files.update(p for p in base.rglob("*.jsonl") if p.is_file())
    return sorted(files)


def scan_corpus(
    files: Sequence[Path], prefixes: frozenset[str], store_ids: frozenset[str]
) -> tuple[list[dict[str, Any]], int]:
    """Stream-scan every transcript; returns (link rows, unreadable count)."""
    pattern = work_id_pattern(prefixes)
    rows: list[dict[str, Any]] = []
    unreadable = 0
    for path in files:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                scan = scan_transcript_lines(handle, pattern)
        except OSError:
            unreadable += 1
            continue
        session_id = scan.session_id or path.stem
        for link in scan.links:
            rows.append(
                {
                    "session_id": session_id,
                    "transcript_path": str(path),
                    "work_id": link.work_id,
                    "strength": link.strength,
                    "t_first": link.t_first,
                    "t_last": link.t_last,
                    "session_start": scan.session_start,
                    "session_end": scan.session_end,
                    "n_strong": link.n_strong,
                    "n_weak": link.n_weak,
                    "in_store": link.work_id in store_ids,
                }
            )
    return rows, unreadable


def load_store_assignee_links(store_path: str) -> list[tuple[str, str]]:
    """The store's existing (work_id, trace_path) assignee links, read-only."""
    import sqlite3

    con = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        pairs = con.execute(
            "SELECT work_id, trace_path FROM work_records WHERE trace_path IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return [(str(w), str(t)) for w, t in pairs]


def calibrate(
    rows: Iterable[Mapping[str, Any]], store_links: Sequence[tuple[str, str]]
) -> dict[str, Any]:
    """Agreement of the content scan with the store's assignee links.

    For each store (work_id, trace_path): was the bead found by content scan in
    that exact transcript (any strength / strong)? Files absent on disk are
    counted separately — the scan cannot speak to them."""
    by_path: dict[str, dict[str, str]] = {}
    for row in rows:
        by_path.setdefault(str(row["transcript_path"]), {})[str(row["work_id"])] = str(
            row["strength"]
        )

    total = len(store_links)
    missing_file = found_any = found_strong = 0
    misses: list[dict[str, str]] = []
    for work_id, trace_path in store_links:
        if not Path(trace_path).is_file():
            missing_file += 1
            continue
        strength = by_path.get(trace_path, {}).get(work_id)
        if strength is not None:
            found_any += 1
            if strength == "strong":
                found_strong += 1
        else:
            misses.append({"work_id": work_id, "trace_path": trace_path})
    scannable = total - missing_file
    return {
        "store_links": total,
        "transcript_missing_on_disk": missing_file,
        "scannable": scannable,
        "found_any": found_any,
        "found_strong": found_strong,
        "agreement_any": found_any / scannable if scannable else None,
        "agreement_strong": found_strong / scannable if scannable else None,
        "misses_sample": misses[:25],
    }


def read_dolt_history(
    rigs: Sequence[str], *, host: str, port: int, timeout_s: float
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Source (b): per-bead distinct session assignees from dolt history.

    READ-ONLY client queries against the already-running shared server; a rig
    that fails (timeout, missing history table, server unreachable) is recorded
    as a gap and skipped — never fatal, never a server start."""
    per_bead: dict[str, list[str]] = {}
    gaps: dict[str, str] = {}
    for rig in rigs:
        if not rig.replace("_", "").replace("-", "").isalnum():
            gaps[rig] = "unsafe identifier, skipped"
            continue
        try:
            completed = subprocess.run(
                [
                    "dolt",
                    "--host",
                    host,
                    "--port",
                    str(port),
                    "--user",
                    "root",
                    "--password",
                    "",
                    "--no-tls",
                    "sql",
                    "-r",
                    "json",
                    "-q",
                    f"use `{rig}`; {_HISTORY_SQL}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
            )
        except FileNotFoundError:
            gaps[rig] = "dolt client not installed"
            continue
        except subprocess.TimeoutExpired:
            gaps[rig] = f"query timed out after {timeout_s:.0f}s"
            continue
        if completed.returncode != 0:
            gaps[rig] = (completed.stderr.strip() or "non-zero exit")[:200]
            continue
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            gaps[rig] = f"unparseable dolt output: {completed.stdout[:120]!r}"
            continue
        sessions = assignee_sessions(payload.get("rows") or [])
        for work_id, agents in sessions.items():
            per_bead[work_id] = list(agents)
    return per_bead, gaps


def cross_validate(
    rows: Iterable[Mapping[str, Any]], dolt_sessions: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """Content-scan vs dolt-history iteration counts for beads seen by both."""
    content_sessions: dict[str, set[str]] = {}
    for row in rows:
        if row["strength"] == "strong" and row["in_store"]:
            content_sessions.setdefault(str(row["work_id"]), set()).add(str(row["session_id"]))

    common = sorted(set(content_sessions) & set(dolt_sessions))
    exact = sum(1 for w in common if len(content_sessions[w]) == len(dolt_sessions[w]))
    content_ge = sum(1 for w in common if len(content_sessions[w]) >= len(dolt_sessions[w]))
    diffs = [len(content_sessions[w]) - len(dolt_sessions[w]) for w in common]
    return {
        "beads_in_both": len(common),
        "beads_content_only": len(set(content_sessions) - set(dolt_sessions)),
        "beads_dolt_only": len(set(dolt_sessions) - set(content_sessions)),
        "exact_count_match": exact,
        "exact_count_match_rate": exact / len(common) if common else None,
        "content_ge_dolt_rate": content_ge / len(common) if common else None,
        "mean_count_diff_content_minus_dolt": sum(diffs) / len(diffs) if diffs else None,
    }


def load_store_rigs(store_path: str) -> list[str]:
    import sqlite3

    con = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT DISTINCT rig FROM work_records").fetchall()
    finally:
        con.close()
    return sorted(str(r[0]) for r in rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--roots", nargs="*", default=list(DEFAULT_ROOTS))
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--skip-dolt", action="store_true", help="skip source (b) entirely")
    parser.add_argument("--dolt-host", default=DOLT_HOST)
    parser.add_argument("--dolt-port", type=int, default=DOLT_PORT)
    parser.add_argument("--limit", type=int, default=None, help="debug: scan only N transcripts")
    args = parser.parse_args(argv)

    store_ids = load_store_work_ids(args.store)
    prefixes = derive_prefixes(store_ids)
    print(f"store: {len(store_ids)} work_ids, {len(prefixes)} prefixes: {sorted(prefixes)}")

    files = iter_transcripts(args.roots)
    if args.limit is not None:
        files = files[: args.limit]
    print(f"scanning {len(files)} transcripts ...")
    t0 = time.monotonic()
    rows, unreadable = scan_corpus(files, prefixes, store_ids)
    scan_seconds = time.monotonic() - t0
    linked_sessions = len({r["transcript_path"] for r in rows})
    print(
        f"scan done in {scan_seconds:.1f}s: {len(rows)} link rows across "
        f"{linked_sessions} transcripts ({unreadable} unreadable)"
    )

    calibration = calibrate(rows, load_store_assignee_links(args.store))
    print(f"calibration: {calibration['found_any']}/{calibration['scannable']} agreement")

    dolt_payload: dict[str, Any] | None = None
    dolt_validation: dict[str, Any] | None = None
    if not args.skip_dolt:
        rigs = load_store_rigs(args.store)
        per_bead, gaps = read_dolt_history(
            rigs, host=args.dolt_host, port=args.dolt_port, timeout_s=DOLT_TIMEOUT_S
        )
        dolt_payload = {
            "rigs_attempted": rigs,
            "rig_gaps": gaps,
            "beads_with_session_assignee": len(per_bead),
            "per_bead_sessions": per_bead,
        }
        dolt_validation = cross_validate(rows, per_bead)
        print(
            f"dolt history: {len(per_bead)} beads with session assignees, "
            f"{len(gaps)} rig gaps; cross-validation beads_in_both="
            f"{dolt_validation['beads_in_both']}"
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "roots": list(args.roots),
        "store": args.store,
        "n_prefixes": len(prefixes),
        "n_transcripts": len(files),
        "n_transcripts_unreadable": unreadable,
        "n_transcripts_with_links": linked_sessions,
        "scan_seconds": round(scan_seconds, 1),
        "calibration": calibration,
        "dolt_history": dolt_payload,
        "dolt_cross_validation": dolt_validation,
        "rows": rows,
    }
    out_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
