#!/usr/bin/env python3
"""mem-bxhh.3 stage-1 substrate probe — codeprobe ftp anchor coverage, per trigger mode.

2026-06-23 (docs/mem-bxhh3-ours-substrate-data-wall.md): ours coverage was 0/6
against a spine-only store with the anchors patched in by hand. Two things have
changed since: the canonical store was rebuilt with traces + lessons
(2026-07-03, schema v11), and retrieval gained the issue-text trigger
(``mem retrieve <id> --no-trace-query``, Decision 23 / mem-tnyo).

This probe measures, per anchor and per trigger mode, whether the real ``ours``
retrieval returns a non-empty payload from the canonical store:

  * ``trace``      — replay default: query from the anchor's own stored
                     ``trace.errors`` (the curated fail-to-pass failures).
  * ``issue-text`` — ``--no-trace-query``: query from title/task_type only,
                     the dispatch-time fields.

Precondition: the 6 anchors exist in the store as work-records with REAL commit
dates (started = parent commit date, closed = landing commit date). They are
produced by ``build_codeprobe_substrate.py`` (git + Docker curation, no
fabricated timestamps) and imported via ``mem import-records`` /
``mem import-lessons``. This probe REFUSES to run when an anchor is missing —
it never patches records in.

Local/free: sqlite + ``bin/mem retrieve`` only. No agent, no paid tokens.

Reproduce::

    python3 memory-bench/scripts/bxhh3_ours_substrate_probe.py \
        [--store /home/ds/projects/mem/.mem/store.db] [--out coverage.json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_STORE = Path("/home/ds/projects/mem/.mem/store.db")
MEM_BIN = PROJECT_ROOT / "bin/mem"

# The 6 materialized codeprobe ftp anchors (mem-bxhh.3.2 bundles).
ANCHORS = [
    "codeprobe-63fbb5bfdc7f",
    "codeprobe-66a5cffc1388",
    "codeprobe-a5a5e027e6c4",
    "codeprobe-c0efd49c83db",
    "codeprobe-c635ffe72c67",
    "codeprobe-ee435c93d0cf",
]

# CLI flag per trigger mode (replay default = trace).
MODES: dict[str, list[str]] = {"trace": [], "issue-text": ["--no-trace-query"]}


def assert_anchors_present(store: Path) -> None:
    """Fail loudly when an anchor work-record is absent — importing (with real
    commit dates, via build_codeprobe_substrate.py + mem import-records) is a
    deliberate separate step, never something a probe does implicitly."""
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        missing = [
            wid
            for wid in ANCHORS
            if conn.execute("SELECT count(*) FROM work_records WHERE work_id=?", (wid,)).fetchone()[
                0
            ]
            == 0
        ]
    finally:
        conn.close()
    if missing:
        sys.exit(
            f"anchors missing from {store}: {missing}\n"
            "import the curated substrate first (real commit dates):\n"
            "  uv run python scripts/build_codeprobe_substrate.py\n"
            "  mem import-records --file .mem/codeprobe-substrate/records.ndjson --store <store>\n"
            "  mem import-lessons --file .mem/codeprobe-substrate/lessons.ndjson --store <store>"
        )


def probe_retrieve(store: Path, work_id: str, mode: str) -> dict[str, object]:
    """One real ``mem retrieve`` call; returns the per-anchor coverage row."""
    argv = [
        str(MEM_BIN),
        "retrieve",
        work_id,
        *MODES[mode],
        "--scope",
        "same-rig",
        "--store",
        str(store),
        "--json",
    ]
    out = subprocess.run(argv, capture_output=True, text=True, check=False)
    env = json.loads(out.stdout)
    if not env.get("ok"):
        raise RuntimeError(f"retrieve failed for {work_id} ({mode}): {env.get('errors')}")
    data = env.get("data") or {}
    items = data.get("items") or []
    with_lessons = [i for i in items if i.get("lessons")]
    return {
        "work_id": work_id,
        "mode": mode,
        "trigger_count": data.get("trigger_count"),
        "total_matched": data.get("total_matched"),
        "items": len(items),
        "items_with_lessons": len(with_lessons),
        "item_ids": [i.get("work_id") for i in items],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=CANONICAL_STORE)
    parser.add_argument("--out", type=Path, default=None, help="also write rows as JSON")
    args = parser.parse_args(argv)

    assert_anchors_present(args.store)

    rows = [probe_retrieve(args.store, wid, mode) for wid in ANCHORS for mode in MODES]

    print(f"=== per-anchor retrieval against {args.store} (scope same-rig) ===")
    for row in rows:
        print(
            f"  {row['work_id']} [{row['mode']:>10}]: trigger={row['trigger_count']} "
            f"total_matched={row['total_matched']} items={row['items']} "
            f"items_with_lessons={row['items_with_lessons']} {row['item_ids']}"
        )

    print("\n=== OURS RETRIEVAL COVERAGE (of", len(ANCHORS), "anchors) ===")
    for mode in MODES:
        mode_rows = [r for r in rows if r["mode"] == mode]
        any_items = sum(1 for r in mode_rows if int(str(r["items"])) > 0)
        with_lessons = sum(1 for r in mode_rows if int(str(r["items_with_lessons"])) > 0)
        print(
            f"  {mode:>10}: {any_items}/{len(ANCHORS)} record-bearing, "
            f"{with_lessons}/{len(ANCHORS)} lesson-bearing"
        )

    if args.out is not None:
        args.out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"\nrows -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
