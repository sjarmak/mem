#!/usr/bin/env python3
"""mem-0ut path-a live-bead measurement bridge (mem-0ut.3, Deliverable 2).

The dispatch hook stamps the wire-level contract
(`docs/mem-0ut-dispatch-contract.md`) onto each A/B bead's metadata, which mem
ingest carries onto ``WorkRecord.metadata``:

  * ``gc.brain_arm``         — "warm" | "cold"
  * ``gc.brain_fork_ts``     — ISO-8601 fork boundary (warm only)
  * ``gc.brain_parent_sid``  — the brain session id (warm only)

This bridge reads those keys from a LIVE store, derives armcompare's inputs, and
hands them to the LANDED `scripts/arm_analysis.py` — it REUSES the fork-aware
measurement (mem-0ut.1, 883e2a3), it does NOT re-implement any metric:

  1. ``work_id -> gc.brain_arm`` becomes the arms assignment;
  2. for warm records, ``record.fork = {parent_sid, fork_ts}`` is injected from
     the metadata — exactly the ``fork.fork_ts`` key `fork_boundary_for` reads to
     trim the inherited brain prefix;
  3. a measurement store (records + trace_path, plus record_agents for the
     trace_ref fallback) is written and passed to `arm_analysis.py`.

A record whose ``gc.brain_arm`` is absent/unknown is NOT an A/B bead and is
skipped. A warm record missing ``gc.brain_fork_ts`` flows through unstamped, so
`arm_analysis` records it in ``fork_warnings`` (the loud mismeasurement signal,
never a silent inversion).

ZFC: pure mechanical join — sqlite IO + dict projection. No semantic heuristics.

Usage (from memory-bench/):

    uv run python scripts/arm_analysis_live.py --store <live.db> \
        [--out-json .mem/arm-analysis-live.json] [--report docs/x.md]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ARM_KEY = "gc.brain_arm"
FORK_TS_KEY = "gc.brain_fork_ts"
PARENT_SID_KEY = "gc.brain_parent_sid"
ARMS = ("warm", "cold")

SCRIPT_DIR = Path(__file__).resolve().parent


def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def derive_inputs(live_store: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """(arm assignment, measurement rows) from a live store's bead metadata.

    ``rows`` carry the record JSON with ``fork`` injected for warm beads (from
    ``gc.brain_fork_ts``/``gc.brain_parent_sid``) and the resolved ``trace_path``.
    Non-A/B beads (no/unknown ``gc.brain_arm``) are dropped."""
    con = _open_ro(live_store)
    try:
        records = con.execute("SELECT work_id, record, trace_path FROM work_records").fetchall()
    finally:
        con.close()
    assignment: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for work_id, record_json, trace_path in records:
        record: dict[str, Any] = json.loads(record_json)
        meta = record.get("metadata")
        arm = meta.get(ARM_KEY) if isinstance(meta, Mapping) else None
        if arm not in ARMS:
            continue  # not an A/B-dispatched bead
        if arm == "warm" and isinstance(meta, Mapping):
            fork: dict[str, str] = {}
            if isinstance(meta.get(FORK_TS_KEY), str):
                fork["fork_ts"] = meta[FORK_TS_KEY]
            if isinstance(meta.get(PARENT_SID_KEY), str):
                fork["parent_sid"] = meta[PARENT_SID_KEY]
            if fork:
                record = {**record, "fork": fork}
        assignment[str(work_id)] = str(arm)
        rows.append({"work_id": str(work_id), "record": record, "trace_path": trace_path})
    return assignment, rows


def write_measurement_store(store: Path, rows: list[dict[str, Any]], live_store: Path) -> None:
    """A measurement store armcompare consumes: ``work_records`` with the
    fork-injected record JSON + trace_path, and ``record_agents`` copied verbatim
    so the ``trace_ref`` resolution fallback still works."""
    if store.exists():
        store.unlink()
    con = sqlite3.connect(store)
    con.execute(
        "CREATE TABLE work_records (work_id TEXT PRIMARY KEY, record TEXT, trace_path TEXT)"
    )
    con.execute("CREATE TABLE record_agents (work_id TEXT, agent_id TEXT, trace_ref TEXT)")
    for row in rows:
        con.execute(
            "INSERT INTO work_records VALUES (?,?,?)",
            (row["work_id"], json.dumps(row["record"]), row["trace_path"]),
        )
    live = _open_ro(live_store)
    try:
        agents = live.execute("SELECT work_id, agent_id, trace_ref FROM record_agents").fetchall()
    except sqlite3.OperationalError:
        agents = []
    finally:
        live.close()
    keep = {row["work_id"] for row in rows}
    for work_id, agent_id, trace_ref in agents:
        if work_id in keep:
            con.execute("INSERT INTO record_agents VALUES (?,?,?)", (work_id, agent_id, trace_ref))
    con.commit()
    con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", type=Path, required=True, help="LIVE store built by mem ingest")
    ap.add_argument("--out-json", type=Path, default=Path(".mem/arm-analysis-live.json"))
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="scratch dir for the derived arms file + measurement store (default: out-json's dir)",
    )
    args = ap.parse_args(argv)

    assignment, rows = derive_inputs(args.store)
    if not assignment:
        print("no A/B beads (no gc.brain_arm metadata) in store; nothing to measure")
        return 1

    work_dir = args.work_dir or args.out_json.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    arms_file = work_dir / "arms-live.json"
    measurement_store = work_dir / "measurement-store.db"
    arms_file.write_text(json.dumps(assignment, indent=2), encoding="utf-8")
    write_measurement_store(measurement_store, rows, args.store)

    cmd = [
        "uv",
        "run",
        "python",
        str(SCRIPT_DIR / "arm_analysis.py"),
        "--arms",
        str(arms_file),
        "--store",
        str(measurement_store),
        "--out-json",
        str(args.out_json),
    ]
    if args.report is not None:
        cmd += ["--report", str(args.report)]
    n = {a: sum(1 for v in assignment.values() if v == a) for a in ARMS}
    print(f"derived {len(assignment)} A/B beads (warm={n['warm']} cold={n['cold']}); measuring…")
    return subprocess.run(cmd, cwd=SCRIPT_DIR.parent, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
