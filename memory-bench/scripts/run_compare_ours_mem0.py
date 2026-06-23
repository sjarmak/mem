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
  --relevance  JSON object {query_work_id: [relevant_work_id, ...]} — the authored
               ground-truth relevant set (intersected with the LOO set per query).

Output: one JSON line per query (`ComparisonResult.model_dump()`) to --out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from membench.compare import compare_arms
from membench.memory_systems.local_stack import LocalModelStack, LocalStackUnavailableError
from membench.memory_systems.mem0_system import Mem0Memory
from membench.memory_systems.ours_system import OursMemory
from membench.validity import QueryWork, WorkRef


def _as_dict(value: object, ctx: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{ctx}: expected a JSON object, got {type(value).__name__}")
    return value


def _req_str(row: dict[str, object], key: str, ctx: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{ctx}: missing or non-string field {key!r}")
    return value


def _opt_str(row: dict[str, object], key: str, ctx: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{ctx}: field {key!r} must be a string when present")
    return value


def _str_list(value: object, ctx: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{ctx}: expected a JSON list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{ctx}: list entries must be strings")
        out.append(item)
    return out


def load_corpus(path: Path) -> tuple[list[WorkRef], dict[str, str]]:
    """Parse the corpus file into LOO WorkRefs + the per-work seed text. Validates
    at the boundary — a malformed row raises rather than seeding a half-built arm."""
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: corpus must be a JSON list")
    refs: list[WorkRef] = []
    text: dict[str, str] = {}
    for i, entry in enumerate(raw):
        row = _as_dict(entry, f"{path}[{i}]")
        work_id = _req_str(row, "work_id", f"{path}[{i}]")
        refs.append(
            WorkRef(
                work_id=work_id,
                rig=_req_str(row, "rig", f"{path}[{i}]"),
                closed=_opt_str(row, "closed", f"{path}[{i}]"),
                convoy_id=_opt_str(row, "convoy_id", f"{path}[{i}]"),
                pr=_opt_str(row, "pr", f"{path}[{i}]"),
                external_ref=_opt_str(row, "external_ref", f"{path}[{i}]"),
                supersedes=tuple(_str_list(row.get("supersedes", []), f"{path}[{i}].supersedes")),
            )
        )
        text[work_id] = _req_str(row, "text", f"{path}[{i}]")
    return refs, text


def load_queries(path: Path) -> list[tuple[QueryWork, str]]:
    """Parse query works + their derived query_text (the string the semantic arm
    embeds; e.g. `B`'s failure message or title)."""
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: queries must be a JSON list")
    out: list[tuple[QueryWork, str]] = []
    for i, entry in enumerate(raw):
        row = _as_dict(entry, f"{path}[{i}]")
        query = QueryWork(
            work_id=_req_str(row, "work_id", f"{path}[{i}]"),
            rig=_req_str(row, "rig", f"{path}[{i}]"),
            started=_req_str(row, "started", f"{path}[{i}]"),
            convoy_id=_opt_str(row, "convoy_id", f"{path}[{i}]"),
            pr=_opt_str(row, "pr", f"{path}[{i}]"),
            external_ref=_opt_str(row, "external_ref", f"{path}[{i}]"),
        )
        out.append((query, _req_str(row, "query_text", f"{path}[{i}]")))
    return out


def load_relevance(path: Path) -> dict[str, list[str]]:
    raw = _as_dict(json.loads(path.read_text(encoding="utf-8")), str(path))
    return {key: _str_list(value, f"{path}[{key}]") for key, value in raw.items()}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ours vs mem0 retrieval-quality comparison")
    parser.add_argument("--store", required=True, help="path to the built mem work-audit store")
    parser.add_argument("--mem-bin", default="./bin/mem", help="path to the mem retrieval CLI")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--relevance", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scope", default="cross_rig", choices=["cross_rig", "same_rig_temporal"])
    parser.add_argument("--top-k", type=int, default=10)
    return parser.parse_args(argv)


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
                stack_telemetry=stack.telemetry_dict(),
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
