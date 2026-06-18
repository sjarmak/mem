#!/usr/bin/env python3
"""Calibrate the autotune workload to the harness's real prompt shape.

Reads the benchmark sequences, measures the memory-prefix / tail / sharing shape, and
writes a ``TrialConfig`` whose synthetic workload reproduces it — so the sweep loads
the engine with *your* shape, not a synthetic guess. No GPU, no engine needed.

    uv run python scripts/autotune_calibrate.py \\
        --fixtures fixtures/sequences --engine vllm --target-requests 64 \\
        --out .mem/autotune/calibrated.json

Then run the trial it emits:
    uv run python scripts/autotune_trial.py --config .mem/autotune/calibrated.json --slo 0.5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from membench.autotune.calibrate import ShapeStats, calibrated_config, measure_sequences
from membench.schemas.sequence import BenchmarkSequence


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixtures", type=Path, default=Path("fixtures/sequences"))
    p.add_argument("--engine", choices=("vllm", "sglang", "tokenspeed"), default="vllm")
    p.add_argument("--concurrency", default="1,4,16,32")
    p.add_argument(
        "--target-requests",
        type=int,
        default=64,
        help="scale groups so total requests/cell ~ this, preserving the real sharing ratio",
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="decode length; default = measured write-word proxy (fixtures carry no real outputs)",
    )
    p.add_argument("--out", type=Path, default=Path(".mem/autotune/calibrated.json"))
    return p.parse_args(argv)


def _load_sequences(fixtures: Path) -> tuple[list[BenchmarkSequence], list[str]]:
    """Load every ``*.json`` that validates as a BenchmarkSequence. Files that do not
    validate (e.g. a ``.jsonl`` anchor or a non-sequence fixture) are skipped and
    reported by name — never silently — so a miscount is visible."""
    sequences: list[BenchmarkSequence] = []
    skipped: list[str] = []
    for path in sorted(fixtures.glob("*.json")):
        try:
            sequences.append(
                BenchmarkSequence.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except (ValueError, OSError) as exc:
            skipped.append(f"{path.name}: {str(exc).splitlines()[0][:60]}")
    return sequences, skipped


def _print_summary(stats: ShapeStats, skipped: list[str]) -> None:
    print("measured prompt shape:", file=sys.stderr)
    for k, v in stats.to_dict().items():
        shown = "n/a" if v is None else (f"{v:.1f}" if isinstance(v, float) else v)
        print(f"  {k:24s} {shown}", file=sys.stderr)
    if skipped:
        print(
            f"  (skipped {len(skipped)} non-sequence file(s): {', '.join(skipped)})",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not args.fixtures.is_dir():
        print(f"fixtures dir not found: {args.fixtures}", file=sys.stderr)
        return 2

    sequences, skipped = _load_sequences(args.fixtures)
    if not sequences:
        print(f"no valid BenchmarkSequence files in {args.fixtures}", file=sys.stderr)
        return 1

    stats = measure_sequences(sequences)
    _print_summary(stats, skipped)

    concurrencies = tuple(int(c) for c in args.concurrency.split(",") if c.strip())
    config = calibrated_config(
        stats,
        engine=args.engine,
        concurrencies=concurrencies,
        target_requests=args.target_requests,
        max_tokens=args.max_tokens,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")

    print(
        f"\ncalibrated config → {args.out}\n"
        f"  prefix_words={config.prefix_words} (real memory-prefix size), "
        f"groups={config.groups} x prompts_per_group={config.prompts_per_group} "
        f"= {config.total_requests} requests/cell, max_tokens={config.max_tokens}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
