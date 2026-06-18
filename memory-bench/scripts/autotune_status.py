#!/usr/bin/env python3
"""Show the autotune ledger: every trial ranked, with the current best highlighted.

The morning report. Read-only — never mutates the ledger.

    uv run python scripts/autotune_status.py --ledger .mem/autotune/ledger.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from membench.autotune.ledger import best_record, read_ledger


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, default=Path(".mem/autotune/ledger.jsonl"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    records = read_ledger(args.ledger)
    if not records:
        print(f"no trials yet in {args.ledger}", file=sys.stderr)
        return 0

    best = best_record(records)
    best_id = best.trial_id if best else None
    print(f"{len(records)} trials in {args.ledger}\n")
    print(f"{'':2}{'id':>4} {'score':>9} {'engine':>10} {'c*':>4} {'ttft':>7}  config")
    for r in sorted(records, key=lambda x: x.objective.score, reverse=True):
        obj = r.objective
        marker = "★ " if r.trial_id == best_id else "  "
        cfg = r.config
        print(
            f"{marker}{r.trial_id:>4} {obj.score:>9.1f} {cfg.engine:>10} "
            f"{_fmt(obj.best_concurrency):>4} {_fmt(obj.best_ttft_p50_s):>7}  "
            f"conc={list(cfg.concurrencies)} groups={cfg.groups} "
            f"ppg={cfg.prompts_per_group} max_tok={cfg.max_tokens}"
        )
    return 0


def _fmt(v: object) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


if __name__ == "__main__":
    raise SystemExit(main())
