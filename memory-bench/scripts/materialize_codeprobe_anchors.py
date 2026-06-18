#!/usr/bin/env python3
"""Materialize the 5 codeprobe landing-commit anchor bundles (mem-bxhh.3.2).

Reads the curated fail-to-pass oracle (`membench curate-ftp codeprobe ... --out`)
and writes one schema-valid `TaskBundle` per commit into ``.mem/bundles-codeprobe/``,
each carrying its behavioral fail-to-pass set on ``verification.ftp_oracle`` and a
ground-truth ``parent..commit`` gold diff. The codeprobe checkout supplies the
diffs + commit messages; the oracle supplies the parents + ftp sets.

LOCAL: git + file IO only (the oracle's docker legs already ran in curate-ftp).
Re-run-safe -- it overwrites the bundle files.

Usage:
    python scripts/materialize_codeprobe_anchors.py \
        [--oracle .mem/ftp-oracle/codeprobe.json] [--out .mem/bundles-codeprobe]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from membench.bundle.anchor import materialize_rig_anchors
from membench.harbor.base_image import CODEPROBE_CACHED_IMAGE
from membench.harbor.env_recon import DEFAULT_RIG_REPOS

RIG = "codeprobe"
_DEFAULT_ORACLE = Path(".mem/ftp-oracle/codeprobe.json")
_DEFAULT_OUT = Path(".mem/bundles-codeprobe")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, default=_DEFAULT_ORACLE)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()

    payload = json.loads(args.oracle.read_text(encoding="utf-8"))
    commits = payload["commits"]
    written = materialize_rig_anchors(
        RIG,
        DEFAULT_RIG_REPOS[RIG],
        commits,
        args.out,
        repo=RIG,
        base_image=CODEPROBE_CACHED_IMAGE,
    )
    for path in written:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        ftp = bundle["verification"]["ftp_oracle"]
        print(
            f"{path}: {len(bundle['output']['file_diffs'])} gold files, "
            f"{len(ftp['behavioral'])} behavioral / {len(ftp['ftp_tests'])} ftp [{ftp['type']}]"
        )
    print(f"\nmaterialized {len(written)} codeprobe anchor bundles -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
