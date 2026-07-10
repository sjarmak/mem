#!/usr/bin/env python3
"""Offline operator entrypoint: generate a CORPUS of synthetic worlds over a seed range.

Thin seed-loop wrapper over ``generate_worlds.generate_and_freeze`` — one frozen world
per seed under ``<out>/<seed>/`` — that aggregates the memory-necessity admission counts
across the whole corpus so you get a single "N admitted / M generated" verdict instead of
reading each per-world summary by hand.

Like ``generate_worlds.py`` this is operator tooling, NOT run in CI: it calls a LOCAL NIM
(see the mem-3453 bead for standing one up; the image is pulled and NGC_API_KEY lives in
the repo ``.env``). Run from the ``memory-bench`` dir with ``PYTHONPATH=.``:

    PYTHONPATH=. python3 scripts/generate_corpus.py --seeds 0-19 --personas 4 --tasks 4 \
        --nim-endpoint http://localhost:8001/v1 --nim-model meta/llama-3.1-8b-instruct

``--seeds`` accepts an inclusive range (``0-19``), a comma list (``0,2,5``), or a mix
(``0-4,10,12-14``). A failing seed (e.g. a NIM hiccup) is reported and skipped, not fatal,
so a long sweep survives a transient blip; the exit code is non-zero if any seed failed.
An aggregate ``<out>/corpus_summary.json`` is written with the per-seed admission rows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_worlds import WorldResult, generate_and_freeze

from membench.generators.nemo.model_provider import DEFAULT_NIM_ENDPOINT, DEFAULT_NIM_MODEL


def parse_seed_spec(spec: str) -> list[int]:
    """Parse ``0-19``, ``0,2,5``, or ``0-4,10,12-14`` into a sorted, de-duplicated seed list."""
    seeds: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                raise ValueError(f"empty seed range '{part}' (lo > hi)")
            seeds.update(range(lo, hi + 1))
        else:
            seeds.add(int(part))
    if not seeds:
        raise ValueError(f"no seeds parsed from '{spec}'")
    return sorted(seeds)


def _summarise(results: list[WorldResult], failures: list[tuple[int, str]]) -> dict[str, object]:
    total_seqs = sum(r.total for r in results)
    admitted = sum(r.admitted for r in results)
    return {
        "seeds_requested": len(results) + len(failures),
        "seeds_generated": len(results),
        "seeds_failed": [{"seed": s, "error": e} for s, e in failures],
        "tasks_generated": total_seqs,
        "tasks_admitted": admitted,
        "tasks_rejected": total_seqs - admitted,
        "worlds": [
            {
                "seed": r.seed,
                "org_name": r.org_name,
                "domain": r.domain,
                "out_dir": str(r.out_dir),
                "admitted": r.admitted,
                "total": r.total,
                "sequences": [
                    {
                        "sequence_id": a.sequence_id,
                        "accepted": a.accepted,
                        "delta": a.delta,
                        "oracle_reward": a.oracle_reward,
                        "no_memory_reward": a.no_memory_reward,
                    }
                    for a in r.admissions
                ],
            }
            for r in results
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", required=True, help="range/list, e.g. '0-19', '0,2,5', '0-4,10'")
    ap.add_argument("--personas", type=int, default=4, help="NeMo records (one per persona)")
    ap.add_argument("--tasks", type=int, default=2, help="sequences materialised per world")
    ap.add_argument("--facts", type=int, default=3, help="facts (subjects) per task")
    ap.add_argument("--nim-endpoint", default=DEFAULT_NIM_ENDPOINT)
    ap.add_argument("--nim-model", default=DEFAULT_NIM_MODEL)
    ap.add_argument("--out", default="fixtures/worlds")
    ap.add_argument(
        "--tool-requiring",
        action="store_true",
        help="materialise goals as memory-gated tool actions (mem-31vl) instead of text answers",
    )
    args = ap.parse_args()

    seeds = parse_seed_spec(args.seeds)
    print(f"generating corpus over {len(seeds)} seeds: {seeds}")

    results: list[WorldResult] = []
    failures: list[tuple[int, str]] = []
    for seed in seeds:
        try:
            results.append(
                generate_and_freeze(
                    seed=seed,
                    personas=args.personas,
                    tasks=args.tasks,
                    facts=args.facts,
                    nim_endpoint=args.nim_endpoint,
                    nim_model=args.nim_model,
                    out=args.out,
                    tool_requiring=args.tool_requiring,
                )
            )
        except Exception as exc:  # one bad seed must not abort the whole sweep
            print(f"[seed {seed}] FAILED: {exc}")
            failures.append((seed, f"{type(exc).__name__}: {exc}"))

    summary = _summarise(results, failures)
    summary_path = Path(args.out) / "corpus_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== corpus summary ===")
    print(
        f"seeds: {summary['seeds_generated']}/{summary['seeds_requested']} generated"
        f" ({len(failures)} failed)"
    )
    print(
        f"tasks: {summary['tasks_admitted']} admitted / {summary['tasks_generated']} generated"
        f" ({summary['tasks_rejected']} rejected by memory-necessity gate)"
    )
    print(f"wrote {summary_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
