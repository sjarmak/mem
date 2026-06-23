"""Free, local smoke of the ours-vs-semantic retrieval-quality bridge.

This exercises the FULL bridge — fixture loading, the LOO boundary, the scope/rig
filter, seeding, backend-id translation, the leak re-check, and scoring — with NO
SDK, NO Ollama, and NO built `mem` CLI:

- the semantic side is the real in-process ``lexical`` arm (token-overlap top-k), a
  stand-in that plugs into the exact same `write`/`query` seam ``mem0`` uses;
- the ``ours`` side is stubbed (the real arm needs a built store): the stub returns
  the exact failure-signature match ``ours`` would surface for this query.

Run:  uv run python examples/compare-ours-mem0/smoke.py
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from membench.compare import compare_arms
from membench.compare.io import load_corpus, load_queries, load_relevance
from membench.memory_systems.lexical_system import LexicalTopKMemory
from membench.memory_systems.ours_system import OursMemory, OursQuery

_HERE = Path(__file__).parent

# What the real `ours` arm (failure-signature retrieval over the work-audit graph)
# would surface for the example query: the exact cert-expired AssertionError match.
# In a real run this comes from `mem retrieve` over a built store, not a stub.
_OURS_STUB_HITS: dict[str, list[str]] = {
    "fix-tls-handshake-timeout": ["tls-cert-expiry-scix"],
}


def _ours_stub(hits: list[str]) -> Callable[[OursQuery], dict[str, object]]:
    def run(query: OursQuery) -> dict[str, object]:
        return {
            "items": [{"work_id": w, "citation": {"work_id": w}, "lessons": []} for w in hits],
            "total_matched": len(hits),
            "near_duplicate_top": bool(hits),
            "fts_truncated": False,
        }

    return run


def main() -> int:
    corpus, corpus_text = load_corpus(_HERE / "corpus.json")
    queries = load_queries(_HERE / "queries.json")
    relevance = load_relevance(_HERE / "relevance.json")

    semantic = LexicalTopKMemory(top_k=10)
    for query, query_text in queries:
        ours = OursMemory(
            store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS.get(query.work_id, []))
        )
        result = compare_arms(
            query,
            query_text,
            corpus,
            corpus_text,
            ours=ours,
            semantic=semantic,
            relevant_ids=relevance.get(query.work_id, []),
            scope="cross_rig",
            pool_depth=10,
            stack_telemetry={"note": "lexical stand-in; mem0 plugs in behind the same seam"},
        )
        print(f"\nquery={result.work_id}  track={result.arms[0].scope}")
        print(f"  candidate pool (cross_rig, LOO-bounded) = {result.eligible_count}")
        print(f"  authored relevant ∩ pool                = {result.relevant_count}")
        for arm in result.arms:
            print(
                f"  {arm.arm:>7}  P={_fmt(arm.precision)}  R={_fmt(arm.recall)}  "
                f"MRR={_fmt(arm.mrr)}  nDCG={_fmt(arm.ndcg)}  "
                f"chars={arm.injected_context_chars}  retrieved={arm.retrieved_ids}"
            )
    return 0


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
