#!/usr/bin/env python3
"""Materialize per-commit ftp-oracle anchor bundles for one rig (mem-bxhh.3.2 /
mem-on3f cross-rig generalization).

Reads the curated fail-to-pass oracle (`membench curate-ftp <rig> ... --out`) and
writes one schema-valid `TaskBundle` per commit into ``.mem/bundles-<rig>/``, each
carrying its fail-to-pass set on ``verification.ftp_oracle`` and a ground-truth
``parent..commit`` gold diff. The rig checkout supplies the diffs + commit
messages; the oracle supplies the parents + ftp sets.

LOCAL: git + file IO only (the oracle's docker legs already ran in curate-ftp).
Re-run-safe -- it overwrites the bundle files.

Usage:
    python scripts/materialize_ftp_anchors.py --rig codeprobe
    python scripts/materialize_ftp_anchors.py --rig scix_experiments
        [--oracle data/ftp-oracle/<rig>.json] [--out .mem/bundles-<rig>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from membench.bundle.anchor import materialize_rig_anchors
from membench.harbor.base_image import CODEPROBE_CACHED_IMAGE
from membench.harbor.env_recon import DEFAULT_RIG_REPOS

# Every rig here must already be curate-able (a non-empty data/ftp-oracle/<rig>.json --
# see that file's README for why gascity_dashboard/gpk are not: 0 commits, pytest-only
# curator). Value is the base image to run the repro/scoring container with; codeprobe
# gets the pre-baked dep-closure image (mem-bxhh.3.1), everything else falls back to
# ftp_curate's own bare default.
_RIG_BASE_IMAGES: dict[str, str] = {
    "codeprobe": CODEPROBE_CACHED_IMAGE,
    "scix_experiments": "python:3.11-bookworm",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig", required=True, choices=sorted(_RIG_BASE_IMAGES))
    parser.add_argument("--oracle", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    oracle = args.oracle or Path(f"data/ftp-oracle/{args.rig}.json")
    out = args.out or Path(f".mem/bundles-{args.rig}")

    if not oracle.exists():
        print(f"error: oracle file not found: {oracle}", file=sys.stderr)
        return 1
    payload = json.loads(oracle.read_text(encoding="utf-8"))
    if "commits" not in payload:
        print(f"error: oracle JSON at {oracle} has no 'commits' key", file=sys.stderr)
        return 1
    written = materialize_rig_anchors(
        args.rig,
        DEFAULT_RIG_REPOS[args.rig],
        payload["commits"],
        out,
        repo=args.rig,
        base_image=_RIG_BASE_IMAGES[args.rig],
    )
    for path in written:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        ftp = bundle["verification"]["ftp_oracle"]
        print(
            f"{path}: {len(bundle['output']['file_diffs'])} gold files, "
            f"{len(ftp['behavioral'])} behavioral / {len(ftp['ftp_tests'])} ftp [{ftp['type']}]"
        )
    print(f"\nmaterialized {len(written)} {args.rig} anchor bundles -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
