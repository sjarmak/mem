#!/usr/bin/env python3
"""Operator entrypoint: build (and re-freeze) the real external anchor (mem-mmuu).

Two subcommands, mirroring the two halves of the adaptation contract:

* ``fetch`` — the ONE network step: download a stride sample of BIG-bench
  ``list_functions`` task files at a pinned upstream commit into
  ``fixtures/external_anchor/raw/``. Run once by an operator; CI never fetches.
* ``adapt`` — pure offline: derive ``fixtures/sequences/
  list_functions_schema_anchor.jsonl`` + ``fixtures/external_anchor/
  manifest.json`` from the frozen raw files, then verify the round trip.

Run from the ``memory-bench`` dir:

    PYTHONPATH=. python3 scripts/adapt_external_anchor.py fetch --commit <sha>
    PYTHONPATH=. python3 scripts/adapt_external_anchor.py adapt --source-commit <sha>
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

from membench.generators.anchor_adaptation import (
    RAW_DIR,
    adapt_raw_dir,
    build_anchor_manifest,
    read_anchor_manifest,
    render_anchor_jsonl,
    verify_anchor,
    write_anchor_manifest,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ANCHOR_DIR = FIXTURES / "external_anchor"
ANCHOR_PATH = FIXTURES / "sequences" / "list_functions_schema_anchor.jsonl"

_RAW_URL = (
    "https://raw.githubusercontent.com/google/BIG-bench/{commit}"
    "/bigbench/benchmark_tasks/list_functions/{task}/task.json"
)
# c001, c009, ... c249 — a stride-8 sample over the 250 subtasks, so the frozen
# corpus spans the concept range instead of one contiguous block.
_TASK_STRIDE = 8
_TASK_COUNT = 250


def _fetch(commit: str) -> int:
    raw_dir = ANCHOR_DIR / RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    tasks = [f"c{i:03d}" for i in range(1, _TASK_COUNT + 1, _TASK_STRIDE)]
    for task in tasks:
        url = _RAW_URL.format(commit=commit, task=task)
        with urllib.request.urlopen(url, timeout=30) as resp:
            (raw_dir / f"{task}.json").write_bytes(resp.read())
        print(f"fetched {task}")
    print(f"froze {len(tasks)} raw tasks at {commit} under {raw_dir}")
    print(f"next: PYTHONPATH=. python3 {Path(__file__).name} adapt --source-commit {commit}")
    return 0


def _adapt(source_commit: str | None) -> int:
    if source_commit is None:
        source_commit = read_anchor_manifest(ANCHOR_DIR).source_commit
        print(f"reusing source_commit from existing manifest: {source_commit}")

    rows = adapt_raw_dir(ANCHOR_DIR / RAW_DIR)
    ANCHOR_PATH.write_text(render_anchor_jsonl(rows), encoding="utf-8")
    manifest = build_anchor_manifest(ANCHOR_DIR, ANCHOR_PATH, source_commit=source_commit)
    manifest_path = write_anchor_manifest(manifest, ANCHOR_DIR)
    print(f"wrote {ANCHOR_PATH} ({manifest.n_tasks} tasks)")
    print(f"wrote {manifest_path}")

    result = verify_anchor(ANCHOR_DIR, ANCHOR_PATH)
    if not result.ok:
        for m in result.mismatches:
            print(f"VERIFY FAIL: {m}")
        return 1
    print("verify: ok (raw hashes, fixture hash, re-adaptation all match)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fetch = sub.add_parser("fetch", help="download raw task files at a pinned commit (network)")
    fetch.add_argument("--commit", required=True, help="full BIG-bench commit SHA to pin")
    adapt = sub.add_parser("adapt", help="derive the anchor JSONL + manifest (offline)")
    adapt.add_argument(
        "--source-commit",
        default=None,
        help="upstream SHA the raw files were fetched at (default: reuse the manifest's)",
    )
    args = ap.parse_args()
    if args.cmd == "fetch":
        return _fetch(args.commit)
    return _adapt(args.source_commit)


if __name__ == "__main__":
    raise SystemExit(main())
