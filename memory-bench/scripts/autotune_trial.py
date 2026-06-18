#!/usr/bin/env python3
"""Run ONE autotune experiment: apply a config, sweep, score, append to the ledger.

This is the autoresearch "train for 5 minutes, check the metric, keep or discard"
step. The agent (driven by ``membench/autotune/program.md``) writes a fresh config
JSON, calls this, reads the printed decision + the ledger, and proposes the next
config. The next-config DECISION is the agent's; this script is pure plumbing.

Run from memory-bench/ with the target engine already serving:

    uv run python scripts/autotune_trial.py \\
        --config .mem/autotune/next.json \\
        --ledger .mem/autotune/ledger.jsonl \\
        --slo 0.5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from membench.autotune.config import TrialConfig
from membench.autotune.ledger import (
    TrialRecord,
    append_record,
    best_record,
    keep_decision,
    next_trial_id,
    read_ledger,
)
from membench.autotune.objective import score_rows
from membench.engines.endpoints import resolve_engines
from membench.engines.run import sweep_engine
from membench.engines.workload import prefix_sharing_workload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True, help="the trial config JSON")
    p.add_argument("--ledger", type=Path, default=Path(".mem/autotune/ledger.jsonl"))
    p.add_argument(
        "--slo",
        type=float,
        required=True,
        help="TTFT p50 service-level objective in seconds (the latency bar)",
    )
    p.add_argument("--note", default="", help="optional free-text note recorded with the trial")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config = TrialConfig.from_json_file(args.config)
    endpoint = resolve_engines()[config.engine]
    workload = prefix_sharing_workload(
        groups=config.groups,
        prompts_per_group=config.prompts_per_group,
        prefix_words=config.prefix_words,
    )

    print(
        f"→ trial: {config.engine} @ concurrency={list(config.concurrencies)} "
        f"({config.total_requests} requests/cell, SLO ttft_p50<={args.slo}s)",
        file=sys.stderr,
    )
    rows = sweep_engine(
        endpoint,
        config.concurrencies,
        workload,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        logprobs=config.logprobs,
        on_row=lambda r: print(
            f"  c={r.concurrency}: out_tps={_fmt(r.output_token_throughput)} "
            f"ttft_p50={_fmt(r.ttft_p50_s)}s kv_after={_fmt(r.kv_cache_usage_after)}",
            file=sys.stderr,
        ),
    )
    objective = score_rows(rows, ttft_p50_slo_s=args.slo)

    prior = read_ledger(args.ledger)
    prior_best = best_record(prior)
    record = TrialRecord(
        trial_id=next_trial_id(prior),
        config=config,
        objective=objective,
        note=args.note,
    )
    kept = keep_decision(record, prior_best)
    append_record(args.ledger, record)

    _report(record, prior_best, kept)
    return 0


def _report(record: TrialRecord, prior_best: TrialRecord | None, kept: bool) -> None:
    obj = record.objective
    verdict = "KEEP (new best)" if kept else "DISCARD"
    prior_score = prior_best.objective.score if prior_best else 0.0
    if not obj.slo_met:
        why = f"SLO MISS — no cell met ttft_p50<={obj.ttft_p50_slo_s}s"
    else:
        why = (
            f"score={obj.score:.1f} out_tps @ c={obj.best_concurrency} "
            f"(ttft_p50={_fmt(obj.best_ttft_p50_s)}s)"
        )
    print(
        f"\ntrial {record.trial_id}: {verdict}\n  {why}\n  prior best: {prior_score:.1f}",
        file=sys.stderr,
    )


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
