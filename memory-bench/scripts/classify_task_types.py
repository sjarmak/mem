#!/usr/bin/env python3
"""mem-75t.11 driver: model-classify free-form beads into task types.

Reads the store (read-only), skips beads the TS ingest types mechanically
(formula/structural — see `membench.task_types.mechanical_task_type`), skips
beads already in the cache, and classifies the rest in batches via headless
`claude -p` on the OAuth runtime (routine classification -> Haiku tier).

Output artifact (default `/home/ds/projects/mem/.mem/task-types.json`):
`{entries: {work_id: {task_type, model, classified_at}}}` — consumed by
`mem build-store --task-types <path>`, which writes the labels with
`task_type_source='model'` so model-tagged types are always distinguishable
from mechanical ones. The cache is incremental: nightly runs only classify
beads that are new since the last run.

ZFC: the judgment is the model call; this script is batching + validation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membench.task_types import BeadItem, classify, mechanical_task_type

DEFAULT_STORE = "/home/ds/projects/mem/.mem/store.db"
DEFAULT_OUT = "/home/ds/projects/mem/.mem/task-types.json"
DEFAULT_MODEL = "haiku"
CLAUDE_TIMEOUT_S = 300.0


def load_unclassified(store_path: str, cached: set[str]) -> list[BeadItem]:
    con = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT work_id, rig, title, record FROM work_records").fetchall()
    finally:
        con.close()
    items: list[BeadItem] = []
    for work_id, rig, title, record in rows:
        if work_id in cached:
            continue
        metadata = json.loads(record).get("metadata") or {}
        if mechanical_task_type(str(title), metadata) is not None:
            continue
        items.append(BeadItem(work_id=str(work_id), rig=str(rig), title=str(title)))
    return items


def claude_runner(model: str) -> callable:
    def run(prompt: str) -> str:
        completed = subprocess.run(
            ["claude", "-p", prompt, "--model", model],
            capture_output=True,
            text=True,
            check=False,
            timeout=CLAUDE_TIMEOUT_S,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"claude -p failed: {completed.stderr.strip()[:200]}")
        return completed.stdout

    return run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--limit", type=int, default=None, help="debug: classify only N beads")
    parser.add_argument("--dry-run", action="store_true", help="report counts, no model calls")
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    cache: dict = {"entries": {}}
    if out_path.is_file():
        cache = json.loads(out_path.read_text(encoding="utf-8"))
        if not isinstance(cache.get("entries"), dict):
            raise SystemExit(f"{out_path} exists but has no entries{{}} — refusing to clobber")

    items = load_unclassified(args.store, set(cache["entries"]))
    print(f"unclassified beads: {len(items)} (cache holds {len(cache['entries'])})")
    if args.limit is not None:
        items = items[: args.limit]
    if args.dry_run or not items:
        return 0

    t0 = time.monotonic()
    entries, counters = classify(
        items,
        claude_runner(args.model),
        model=args.model,
        classified_at=datetime.now(UTC).isoformat(),
        batch_size=args.batch_size,
        on_progress=lambda msg: print(f"  {msg} ({time.monotonic() - t0:.0f}s)"),
    )
    print(f"classified {len(entries)}/{len(items)} in {time.monotonic() - t0:.0f}s; {counters}")

    cache["entries"].update(entries)
    cache["updated_at"] = datetime.now(UTC).isoformat()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path} ({len(cache['entries'])} total entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
