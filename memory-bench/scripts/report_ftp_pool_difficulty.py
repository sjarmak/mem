#!/usr/bin/env python3
"""Report the gold-file/difficulty distribution of a cross-rig ftp anchor pool
(mem-on3f). FREE: file IO + Docker-free JSON parsing only.

    uv run python scripts/report_ftp_pool_difficulty.py \
        --bundles-dir .mem/bundles-codeprobe --bundles-dir .mem/bundles-scix_experiments
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from run_grid_3arm_ftp import load_ftp_bundles

from membench.generators.ftp_difficulty import band_pool
from membench.schemas.bundle import TaskBundle


def build_report(bundles: Sequence[TaskBundle]) -> dict[str, Any]:
    """The distribution `report_ftp_pool_difficulty` reports: pool size by rig,
    tertile-band counts, and per-anchor detail -- the exact shape written to
    ``--out`` and printed to stdout."""
    stats = band_pool(bundles)
    rig_by_work_id = {b.work_id: b.rig for b in bundles}
    by_rig = Counter(b.rig for b in bundles)
    by_band = Counter(s.band for s in stats)
    return {
        "n_bundles": len(bundles),
        "by_rig": dict(sorted(by_rig.items())),
        "by_band": {band: by_band.get(band, 0) for band in ("easy", "medium", "hard")},
        "anchors": [
            {
                "work_id": s.work_id,
                "rig": rig_by_work_id[s.work_id],
                "gold_files": s.gold_files,
                "ftp_tests": s.ftp_tests,
                "band": s.band,
            }
            for s in stats
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, default=None, help="also write the report as JSON")
    args = parser.parse_args(argv)

    bundles = load_ftp_bundles(args.bundles_dir)
    if not bundles:
        print(f"error: no ftp-oracle bundles under {args.bundles_dir}", file=sys.stderr)
        return 1

    report = build_report(bundles)

    n_rigs = len(report["by_rig"])
    print(f"cross-rig ftp pool: {report['n_bundles']} anchor(s) across {n_rigs} rig(s)")
    for rig, n in report["by_rig"].items():
        print(f"  {rig}: {n}")
    print("\ndifficulty bands (tertile, by gold-file-count):")
    for band, n in report["by_band"].items():
        print(f"  {band}: {n}")
    print("\nper-anchor detail:")
    for a in report["anchors"]:
        print(
            f"  {a['work_id']}: {a['gold_files']} gold file(s), "
            f"{a['ftp_tests']} ftp test(s) [{a['band']}]"
        )

    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
