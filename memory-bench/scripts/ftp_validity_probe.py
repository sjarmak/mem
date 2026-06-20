#!/usr/bin/env python3
"""Free pre-flight: run the CSB oracle-validity gate over the ftp-oracle anchor
bundles with `FtpReproRunner` (mem-bxhh.3.3).

For each bundle this applies the gold diff (must reproduce: every curated ftp nodeid
green, test_ratio 1.0) and the empty diff (must fail: all ftp red, test_ratio 0.0) in
the rig container -- the exact ftp-subset scoring path the PAID 3-arm smoke run uses,
but with ZERO agent calls. A bundle that passes here has a sound oracle the graded
arms can discriminate against; one that fails is excluded (its red->green property
does not hold under the runner) and reported, never silently scored.

This is the local proof the verification wiring is correct before any paid token is
spent. LOCAL: git + Docker pytest only.

    uv run python scripts/ftp_validity_probe.py [--bundles-dir .mem/bundles-codeprobe]
        [--out .mem/grid-codeprobe/validity-summary.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from membench.grading.validity_gate import ValidityResult, validity_gate
from membench.harbor.ftp_repro import FtpReproRunner
from membench.schemas.bundle import TaskBundle


def load_bundles(bundles_dir: Path) -> list[TaskBundle]:
    """Every ``*.json`` anchor bundle in ``bundles_dir`` that carries an ftp oracle,
    in sorted path order. A bundle without ``verification.ftp_oracle`` is not an ftp
    anchor and is skipped (reported by the caller via the count delta)."""
    bundles: list[TaskBundle] = []
    for path in sorted(bundles_dir.glob("*.json")):
        bundle = TaskBundle.model_validate_json(path.read_text(encoding="utf-8"))
        if bundle.verification.ftp_oracle is not None:
            bundles.append(bundle)
    return bundles


def run(bundles: Sequence[TaskBundle]) -> list[ValidityResult]:
    """Validity-gate every bundle through one shared `FtpReproRunner` (its worktree
    cache is reused across bundles that share a base commit; ``close`` removes them)."""
    results: list[ValidityResult] = []
    with FtpReproRunner() as runner:
        for bundle in bundles:
            ftp = bundle.verification.ftp_oracle
            if ftp is None:  # load_bundles filtered to ftp anchors; guard the invariant
                raise RuntimeError(f"{bundle.work_id}: ftp_oracle is None after ftp-only filter")
            result = validity_gate(bundle, test_runner=runner)
            print(
                f"VALIDITY  {bundle.work_id}  valid={result.valid}  "
                f"[{ftp.type}, {len(ftp.ftp_tests)} ftp]  "
                f"gold_ratio={result.gold_test_ratio} empty_ratio={result.empty_test_ratio}"
                + ("" if result.valid else f"  ({result.reason})")
            )
            results.append(result)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, default=Path(".mem/bundles-codeprobe"))
    parser.add_argument(
        "--out", type=Path, default=Path(".mem/grid-codeprobe/validity-summary.json")
    )
    args = parser.parse_args(argv)

    bundles = load_bundles(args.bundles_dir)
    if not bundles:
        print(f"error: no ftp-oracle bundles under {args.bundles_dir}", file=sys.stderr)
        return 1

    results = run(bundles)
    valid = [r.work_id for r in results if r.valid]
    invalid = [r.work_id for r in results if not r.valid]
    summary = {
        "bundles_dir": str(args.bundles_dir),
        "n_bundles": len(results),
        "n_valid": len(valid),
        "valid": valid,
        "invalid": invalid,
        "results": [r.model_dump() for r in results],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(valid)}/{len(results)} bundles have a sound ftp oracle -> {args.out}")
    if invalid:
        print(f"excluded (oracle does not hold under the runner): {invalid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
