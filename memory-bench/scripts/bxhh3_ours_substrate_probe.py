#!/usr/bin/env python3
"""mem-bxhh.3 (Option A) — measure the codeprobe ``ours`` retrieval substrate.

Builds the MAXIMALLY-GENEROUS substrate and reports per-anchor retrieval
coverage. Every knob favours the ``ours`` arm, so a 0 result is decisive:

  * corpus = the v8 codeprobe spine + EVERY available codeprobe ``trace_errors``
    row (union of ``store-lobt.db`` v7 + ``store.db`` v5) + EVERY available
    codeprobe lesson, mirrored into the records' JSON so they are matchable
    (``retrieve()`` iterates ``record.trace.errors`` from the record JSON; the
    ``trace_errors`` table only locates candidates).
  * anchors carry their REAL landing-commit dates (widest honest LOO window).
  * anchors carry a faithful failure-signature query rendered from the curated
    ftp oracle's failing tests.

Local/free: only sqlite + ``bin/mem retrieve``. No agent, no paid tokens.

Reproduce::

    python3 memory-bench/scripts/bxhh3_ours_substrate_probe.py
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

WT = Path("/home/ds/projects/mem-bxhh.3")
SRC = WT / ".mem/store.db"  # v8 spine (corpus + anchors)
RICH = [
    Path("/home/ds/projects/mem/.mem/store-lobt.db"),  # v7: lessons + traces
    Path("/home/ds/projects/mem/.mem/store.db"),  # v5: more codeprobe traces
]
OUT = Path("/tmp/bxhh3-ours-substrate-measure.db")  # scratch; never committed
BUNDLES = WT / ".mem/bundles-codeprobe"

# anchor work_id -> real landing-commit ISO date (from `git show` in codeprobe)
ANCHORS: dict[str, str] = {
    "codeprobe-63fbb5bfdc7f": "2026-06-16T17:27:24",
    "codeprobe-66a5cffc1388": "2026-06-15T18:08:37",
    "codeprobe-a5a5e027e6c4": "2026-06-15T18:08:02",
    "codeprobe-c0efd49c83db": "2026-06-13T11:07:04",
    "codeprobe-c635ffe72c67": "2026-04-30T22:41:41",
    "codeprobe-ee435c93d0cf": "2026-06-16T10:04:03",
}


@dataclass
class MergeStats:
    trace_errors: int = 0
    lessons: int = 0
    te_records: set[str] = field(default_factory=set)
    lesson_records: set[str] = field(default_factory=set)


def render_query_errors(work_id: str) -> list[dict[str, object]]:
    """Faithful failure-signature query from the curated ftp oracle: each failing
    test node becomes an AssertionError-class pytest trace error (the real red
    state the landing commit turned green)."""
    bundle = json.loads((BUNDLES / f"{work_id}.json").read_text())
    oracle = bundle["verification"]["ftp_oracle"]
    errs: list[dict[str, object]] = []
    for node in oracle.get("ftp_tests", []):
        path_part = node.split("::")[0]
        file = path_part.replace(".", "/") + ".py"
        errs.append(
            {
                "tool": "pytest",
                "severity": "error",
                "message": f"AssertionError: failing test {node} (red before landing commit)",
                "file": file,
                "line": 1,
            }
        )
    return errs


def merge_codeprobe_signal(dst: sqlite3.Connection) -> MergeStats:
    """Union every available codeprobe trace_errors + lessons into dst and mirror
    them into the records' JSON. id is re-assigned so the FTS AFTER INSERT
    trigger indexes the new rowid."""
    stats = MergeStats()
    seen_te: set[tuple[str, str, str]] = set()
    seen_lesson: set[tuple[str, str]] = set()
    for path in RICH:
        if not path.exists() or path.stat().st_size == 0:
            continue
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for te in src.execute(
            "SELECT work_id, signature, tool, severity, file, line, col, error_class, message "
            "FROM trace_errors WHERE work_id LIKE 'codeprobe%'"
        ).fetchall():
            key = (te[0], te[1], te[8])  # work_id, signature, message
            if key in seen_te:
                continue
            seen_te.add(key)
            dst.execute(
                "INSERT INTO trace_errors"
                "(work_id, signature, tool, severity, file, line, col, error_class, message) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                te,
            )
            stats.trace_errors += 1
            stats.te_records.add(te[0])
        try:
            lessons = src.execute(
                "SELECT work_id, extracted_at, commit_sha, payload "
                "FROM lessons WHERE work_id LIKE 'codeprobe%'"
            ).fetchall()
        except sqlite3.OperationalError:
            lessons = []
        for le in lessons:
            lkey = (le[0], le[3])
            if lkey in seen_lesson:
                continue
            seen_lesson.add(lkey)
            dst.execute(
                "INSERT INTO lessons(work_id, extracted_at, commit_sha, payload) VALUES (?,?,?,?)",
                le,
            )
            stats.lessons += 1
            stats.lesson_records.add(le[0])
        src.close()

    # retrieve() matches against record.trace.errors from the record JSON, so
    # mirror every merged corpus error back into its record's JSON.
    for wid in sorted(stats.te_records):
        row = dst.execute("SELECT record FROM work_records WHERE work_id=?", (wid,)).fetchone()
        if row is None:
            continue
        rec = json.loads(row[0])
        errs: list[dict[str, object]] = []
        for tool, sev, file, line, message in dst.execute(
            "SELECT tool, severity, file, line, message FROM trace_errors WHERE work_id=?",
            (wid,),
        ).fetchall():
            errs.append(
                {
                    "tool": tool,
                    "severity": sev if sev in ("error", "warning", "info") else "error",
                    "message": message,
                    "file": file,
                    "line": int(line) if line is not None else 0,
                }
            )
        rec["trace"] = {"jsonl_path": f"merged-corpus-trace/{wid}.jsonl", "errors": errs}
        dst.execute("UPDATE work_records SET record=? WHERE work_id=?", (json.dumps(rec), wid))
    return stats


def patch_anchors(dst: sqlite3.Connection) -> None:
    for wid, real_date in ANCHORS.items():
        row = dst.execute("SELECT record FROM work_records WHERE work_id=?", (wid,)).fetchone()
        if row is None:
            print(f"  WARN: anchor {wid} not in corpus", file=sys.stderr)
            continue
        rec = json.loads(row[0])
        rec.setdefault("lifecycle", {})
        rec["lifecycle"]["created"] = real_date
        rec["lifecycle"]["started"] = real_date
        rec["trace"] = {
            "jsonl_path": f"synthetic-anchor-query/{wid}.jsonl",
            "errors": render_query_errors(wid),
        }
        dst.execute(
            "UPDATE work_records SET record=?, started_at=?, created_at=? WHERE work_id=?",
            (json.dumps(rec), real_date, real_date, wid),
        )


def probe_retrieve(mem_bin: str, work_id: str) -> dict[str, object]:
    out = subprocess.run(
        [mem_bin, "retrieve", work_id, "--scope", "same-rig", "--store", str(OUT), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    env = json.loads(out.stdout)
    data = env.get("data") or {}
    items = data.get("items") or []
    with_lessons = [i for i in items if i.get("lessons")]
    return {
        "work_id": work_id,
        "trigger_count": data.get("trigger_count"),
        "total_matched": data.get("total_matched"),
        "items": len(items),
        "items_with_lessons": len(with_lessons),
        "item_ids": [i.get("work_id") for i in items],
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SRC, OUT)
    dst = sqlite3.connect(OUT)
    stats = merge_codeprobe_signal(dst)
    patch_anchors(dst)
    dst.commit()
    try:
        dst.execute("INSERT INTO trace_errors_fts(trace_errors_fts) VALUES('rebuild')")
        dst.commit()
    except sqlite3.OperationalError as exc:
        print("FTS rebuild note:", exc, file=sys.stderr)
    cp_te = dst.execute(
        "SELECT count(*) FROM trace_errors WHERE work_id LIKE 'codeprobe%'"
    ).fetchone()[0]
    cp_les = dst.execute("SELECT count(*) FROM lessons WHERE work_id LIKE 'codeprobe%'").fetchone()[
        0
    ]
    dst.close()

    print("=== measurement substrate built ===")
    print(
        f"  merged codeprobe trace_errors: {stats.trace_errors} "
        f"(records: {sorted(stats.te_records)})"
    )
    print(
        f"  merged codeprobe lessons:      {stats.lessons} "
        f"(records: {sorted(stats.lesson_records)})"
    )
    print(f"  store now: codeprobe trace_errors={cp_te} lessons={cp_les}")
    print(f"  store: {OUT}")

    print("\n=== per-anchor free retrieval (scope same-rig) ===")
    mem = str(WT / "bin/mem")
    summary: list[dict[str, object]] = [probe_retrieve(mem, wid) for wid in ANCHORS]
    for rec in summary:
        print(
            f"  {rec['work_id']}: trigger={rec['trigger_count']} "
            f"total_matched={rec['total_matched']} items={rec['items']} "
            f"items_with_lessons={rec['items_with_lessons']} {rec['item_ids']}"
        )

    cov = sum(1 for r in summary if int(str(r["items_with_lessons"])) > 0)
    print(
        f"\n=== OURS RETRIEVAL COVERAGE: {cov}/{len(ANCHORS)} anchors "
        "with a non-empty lesson-bearing payload ==="
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
