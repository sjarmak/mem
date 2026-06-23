"""Driver: `ours` vs `mem0` head-to-head, retrieval-quality only (mem-compare).

Runs the `membench.compare` bridge over a set of query works, scoring each arm's
retrieval against an authored relevant set under the harness LOO boundary. NO agent
re-run and NO outcome lift — this is the free/local retrieval-quality lane. The
outcome-lift comparison is the paid Harbor path and is deliberately not here.

PROVISIONING (required before a real run — see docs/mem-compare-ours-mem0-scaffold.md):

  1. SDK:    uv add mem0ai qdrant-client          # not in uv.lock today
  2. models: ollama serve
             ollama pull nomic-embed-text
             ollama pull llama3                    # defaults; override via MEMBENCH_* env
  3. store:  a built mem work-audit store the `ours` arm reads (./bin/mem must exist)

`preflight` fails loud (LocalStackUnavailableError) with the exact `ollama pull` to
run if the stack is not up, so a missing backend never silently degrades to a paid
API. A missing mem0 SDK surfaces as an actionable install hint.

INPUT FILES
  --corpus     JSON list of {work_id, rig, text, closed?, convoy_id?, pr?,
               external_ref?, supersedes?[]} — the prior-work corpus + seed text.
  --queries    JSON list of {work_id, rig, started, query_text, convoy_id?, pr?,
               external_ref?} — the held-out query works `B`.

RELEVANT SET (two mutually exclusive modes)
  --relevance      JSON object {query_work_id: [relevant_work_id, ...]} — a
                   HAND-AUTHORED ground-truth relevant set (intersected with the LOO
                   set per query). The original, pre-judge mode.
  --pooled-judge   DERIVE the relevant set: pool both arms' top-D candidates and judge
                   each (B, candidate) over the LocalModelStack relevance judge
                   (mem-lvp.32), instead of hand-authoring relevance.json. Pass
                   --judged-relevance for the resumable verdict cache + frozen
                   artifact path; the run echoes the resolved judge + chat model.

Output: one JSON line per query (`ComparisonResult.model_dump()`) to --out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from membench.bbon.local_stack_judge import LocalStackComparativeJudge
from membench.compare import compare_arms
from membench.compare.io import load_corpus, load_queries, load_relevance
from membench.compare.judged_relevance import PairCache, judge_relevance
from membench.memory_systems.local_stack import LocalModelStack, LocalStackUnavailableError
from membench.memory_systems.mem0_system import Mem0Memory
from membench.memory_systems.ours_system import OursMemory


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ours vs mem0 retrieval-quality comparison")
    parser.add_argument("--store", required=True, help="path to the built mem work-audit store")
    parser.add_argument("--mem-bin", default="./bin/mem", help="path to the mem retrieval CLI")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument(
        "--relevance",
        type=Path,
        help="hand-authored relevant set; mutually exclusive with --pooled-judge",
    )
    parser.add_argument(
        "--pooled-judge",
        action="store_true",
        help="derive the relevant set by judging the pooled candidates (mem-lvp.32)",
    )
    parser.add_argument(
        "--judged-relevance",
        type=Path,
        help="path for the frozen judged-relevance artifact (with --pooled-judge)",
    )
    parser.add_argument(
        "--judge-cache",
        type=Path,
        help="resumable per-pair verdict cache (JSONL); defaults beside --judged-relevance",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scope", default="cross_rig", choices=["cross_rig", "same_rig_temporal"])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--pool-depth",
        type=int,
        default=10,
        help="equal-depth candidate contribution D: exactly top-D from each arm is pooled",
    )
    args = parser.parse_args(argv)
    if args.pooled_judge == bool(args.relevance):
        parser.error("pass exactly one of --relevance or --pooled-judge")
    if args.pooled_judge and args.judged_relevance is None:
        parser.error("--pooled-judge requires --judged-relevance for the frozen artifact")
    return args


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    # Fail loud at the boundary if the local stack is not provisioned — never let a
    # backend silently fall back to a paid API.
    stack = LocalModelStack.from_env()
    try:
        stack.preflight(require_chat=True)
    except LocalStackUnavailableError as exc:
        print(f"local model stack not ready: {exc}", file=sys.stderr)
        return 2

    try:
        semantic = Mem0Memory(top_k=args.top_k)
    except ImportError as exc:
        print(
            f"mem0 SDK not installed ({exc}). Run: uv add mem0ai qdrant-client",
            file=sys.stderr,
        )
        return 2

    ours = OursMemory(store_path=args.store, mem_bin=args.mem_bin, limit=args.top_k)

    corpus, corpus_text = load_corpus(args.corpus)
    queries = load_queries(args.queries)

    # Resolve the relevant set: hand-authored relevance.json, or the pooled-judge
    # derivation (mem-lvp.32). The two are mutually exclusive — _parse_args already
    # rejects passing both / neither.
    relevance: dict[str, list[str]]
    telemetry = stack.telemetry_dict()
    if args.pooled_judge:
        judge = LocalStackComparativeJudge(stack=stack)
        # The relevance judge runs the pinned chat model; fail loud here too if the
        # daemon is down, with the same actionable message.
        try:
            judge.preflight()
        except LocalStackUnavailableError as exc:
            print(f"relevance judge not ready: {exc}", file=sys.stderr)
            return 2
        cache_path = args.judge_cache or args.judged_relevance.with_suffix(".cache.jsonl")
        judged = judge_relevance(
            queries,
            corpus,
            corpus_text,
            ours=ours,
            semantic=semantic,
            pool_depth=args.pool_depth,
            judge=judge,
            cache=PairCache(cache_path),
            scope=args.scope,
            stack_telemetry=telemetry,
        )
        relevance = judged.relevant_ids()
        judged.write(args.judged_relevance)
        # The artifact telemetry carries the judge identity (V2 confound) — adopt it
        # for compare_arms so the judge model + prompt_version land in every row.
        telemetry = judged.telemetry()
        print(
            f"pooled-judge: judge={judge.model} chat={stack.chat_model} "
            f"prompt={telemetry.get('judge_prompt_version')} -> {args.judged_relevance}",
            file=sys.stderr,
        )
    else:
        relevance = load_relevance(args.relevance)

    written = 0
    with args.out.open("w", encoding="utf-8") as handle:
        for query, query_text in queries:
            result = compare_arms(
                query,
                query_text,
                corpus,
                corpus_text,
                ours=ours,
                semantic=semantic,
                relevant_ids=relevance.get(query.work_id, []),
                scope=args.scope,
                pool_depth=args.pool_depth,
                stack_telemetry=telemetry,
            )
            handle.write(json.dumps(result.model_dump(), sort_keys=True) + "\n")
            written += 1
            for arm in result.arms:
                print(
                    f"{query.work_id} {arm.arm:>5}  P={arm.precision}  R={arm.recall}  "
                    f"chars={arm.injected_context_chars}"
                )

    print(f"wrote {written} comparison rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
