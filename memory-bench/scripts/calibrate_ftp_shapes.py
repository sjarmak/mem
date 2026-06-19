#!/usr/bin/env python3
"""Operator entrypoint: run the mem-bxhh.5 synthetic↔real-ftp calibration (Gate 0).

Runs the scripted memory arms over the shape-grounded synthetic tasks (each reproduces
a real fail-to-pass shape), compares their per-arm lift ranking to the recorded real
anchor (mem-1fl8), and prints the Gate-0 verdict — the rank correlation or its absence.

Pure-Python and model-free (the package policy): the synthetic arms run under the
reference ScriptedAgent, and the real anchor is the recorded mem-1fl8 result (re-running
it is a paid Harbor grid). Run from the ``memory-bench`` dir:

    PYTHONPATH=. python3 scripts/calibrate_ftp_shapes.py
    PYTHONPATH=. python3 scripts/calibrate_ftp_shapes.py --out docs/mem-bxhh5-calibration.md
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from membench.generators import (
    FTP_SHAPES,
    SHAPE_BLUEPRINTS,
    assert_shapes_grounded,
    generate_shape_sequences,
    memory_dependent_shapes,
)
from membench.report.ftp_calibration import (
    calibrate,
    format_calibration_report,
    mem1fl8_anchor,
)
from membench.report.synthetic_arms import eval_arms_over_sequences

# The scripted arms the synthetic suite can run (none baseline + id-exact + lexical).
_SYNTHETIC_ARMS = ["none", "oracle", "filesystem", "lexical"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=None, help="write the report to this path")
    ap.add_argument("--rho-threshold", type=float, default=0.6)
    args = ap.parse_args()

    # Fail closed if the shape taxonomy has drifted from the frozen corpus.
    assert_shapes_grounded()

    sequences = generate_shape_sequences()
    with tempfile.TemporaryDirectory() as tmp:
        results = eval_arms_over_sequences(sequences, _SYNTHETIC_ARMS, fs_base_dir=tmp)
    synthetic_lifts = {r.arm: r.lift for r in results}

    anchor = mem1fl8_anchor()
    verdict = calibrate(synthetic_lifts, anchor, rho_threshold=args.rho_threshold)
    report = format_calibration_report(verdict)

    reproduced = {bp.shape_id for bp in SHAPE_BLUEPRINTS}
    shape_lines = [
        f"  [{'x' if s.shape_id in reproduced else ' '}] {s.shape_id}: {s.summary}"
        for s in memory_dependent_shapes()
    ]
    header = (
        f"Real ftp shapes catalogued: {len(FTP_SHAPES)} "
        f"({len(memory_dependent_shapes())} memory-dependent; [x] = a synthetic blueprint "
        f"reproduces it)\n" + "\n".join(shape_lines) + "\n"
    )
    print(header)
    print(report)

    if args.out is not None:
        args.out.write_text(header + "\n" + report + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
