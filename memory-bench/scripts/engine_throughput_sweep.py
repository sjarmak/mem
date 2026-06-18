#!/usr/bin/env python3
"""Sweep concurrency against vLLM and/or SGLang and emit the latency-throughput-KV
frontier — the iterative loop for tuning batching and watching KV/prefix-cache pressure.

For each engine and each concurrency level it replays a workload (the prefix-sharing
probe by default, or a JSONL prompt file), driving N requests through a thread pool of
that width, capturing per-request TTFT / ITL / output-token throughput via the
streaming client, and scraping each engine's KV-cache + prefix-cache + queue gauges
before and after the batch. One JSON row per (engine, concurrency) cell goes to
``--out``; nothing is decided here — the knobs (engine flags, model, quantization) are
set on the servers in ``infra/local-inference``.

ZFC: pure plumbing. Run from memory-bench/ with both servers up:

    uv run python scripts/engine_throughput_sweep.py --engine both \\
        --concurrency 1,4,16,32 --groups 1 --prompts-per-group 64 \\
        --out .mem/engine_sweep.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from membench.engines.client import StreamingClient, StreamResult
from membench.engines.endpoints import EngineEndpoint, resolve_engines
from membench.engines.metrics_scrape import EngineRuntimeStats, scrape_engine_stats
from membench.engines.sweep import SweepRow, aggregate_rows
from membench.engines.workload import load_prompts_jsonl, prefix_sharing_workload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--engine", choices=("vllm", "sglang", "both"), default="both")
    p.add_argument(
        "--concurrency",
        default="1,4,16,32",
        help="comma-separated concurrency levels to sweep",
    )
    p.add_argument("--groups", type=int, default=1, help="distinct shared-prefix groups")
    p.add_argument("--prompts-per-group", type=int, default=64)
    p.add_argument(
        "--prefix-words", type=int, default=800, help="approx words in the shared prefix"
    )
    p.add_argument(
        "--prompts-file",
        type=Path,
        default=None,
        help="JSONL prompt distribution to replay instead of the synthetic workload",
    )
    p.add_argument("--max-tokens", type=int, default=128)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--logprobs", action="store_true", help="request token-level logprobs")
    p.add_argument("--out", type=Path, default=Path(".mem/engine_sweep.jsonl"))
    return p.parse_args(argv)


def _build_workload(args: argparse.Namespace) -> list[list[dict[str, str]]]:
    if args.prompts_file is not None:
        return load_prompts_jsonl(args.prompts_file)
    return prefix_sharing_workload(
        groups=args.groups,
        prompts_per_group=args.prompts_per_group,
        prefix_words=args.prefix_words,
    )


def _run_one(
    client: StreamingClient, messages: list[dict[str, str]], args: argparse.Namespace
) -> StreamResult:
    return client.complete(
        messages,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        logprobs=args.logprobs,
    )


def _safe_scrape(endpoint: EngineEndpoint) -> EngineRuntimeStats | None:
    """Scrape runtime stats, returning None if the metrics endpoint is unreachable —
    a sweep should still record latency even if Prometheus scraping is misconfigured.
    The failure is surfaced on stderr, never silently dropped."""
    try:
        return scrape_engine_stats(endpoint.metrics_url, endpoint.metric_prefix)
    except OSError as exc:
        print(f"  ! metrics scrape failed for {endpoint.name}: {exc}", file=sys.stderr)
        return None


def _sweep_cell(
    endpoint: EngineEndpoint,
    concurrency: int,
    workload: list[list[dict[str, str]]],
    args: argparse.Namespace,
) -> SweepRow:
    client = StreamingClient(endpoint=endpoint)
    before = _safe_scrape(endpoint)
    wall_start = time.perf_counter()
    results: list[StreamResult] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_run_one, client, msgs, args) for msgs in workload]
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception as exc:
                print(f"  ! request failed on {endpoint.name}: {exc}", file=sys.stderr)
    wall_s = time.perf_counter() - wall_start
    after = _safe_scrape(endpoint)
    return aggregate_rows(
        engine=endpoint.name,
        concurrency=concurrency,
        requests=len(workload),
        results=results,
        wall_s=wall_s,
        before=before,
        after=after,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    concurrencies = [int(c) for c in args.concurrency.split(",") if c.strip()]
    engines = resolve_engines()
    selected = list(engines.values()) if args.engine == "both" else [engines[args.engine]]
    workload = _build_workload(args)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as out:
        for endpoint in selected:
            for concurrency in concurrencies:
                print(
                    f"→ {endpoint.name} @ concurrency={concurrency} "
                    f"({len(workload)} requests)...",
                    file=sys.stderr,
                )
                row = _sweep_cell(endpoint, concurrency, workload, args)
                out.write(json.dumps(row.to_dict()) + "\n")
                out.flush()
                print(
                    f"  {endpoint.name} c={concurrency}: "
                    f"thru={_fmt(row.request_throughput)} req/s, "
                    f"ttft_p50={_fmt(row.ttft_p50_s)}s, "
                    f"kv_after={_fmt(row.kv_cache_usage_after)}, "
                    f"prefix_hit_after={_fmt(row.prefix_cache_hit_rate_after)}",
                    file=sys.stderr,
                )
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
