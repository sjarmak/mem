"""Pins the committed example fixtures + the end-to-end bridge (the CI smoke).

Loads `examples/compare-ours-mem0/*.json` through the shared IO loaders and runs the
full comparison with the real in-process `lexical` arm (semantic stand-in) and a
stubbed `ours` runner — no SDK, no Ollama, no built CLI. This is the free smoke,
frozen so a fixture or bridge regression fails loudly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pytest import approx

from membench.compare import compare_arms
from membench.compare.io import load_corpus, load_queries, load_relevance
from membench.memory_systems.lexical_system import LexicalTopKMemory
from membench.memory_systems.ours_system import OursMemory, OursQuery

_FIXTURES = Path(__file__).parent.parent / "examples" / "compare-ours-mem0"


def _ours_stub(hits: list[str]) -> Callable[[OursQuery], dict[str, object]]:
    def run(query: OursQuery) -> dict[str, object]:
        return {
            "items": [{"work_id": w, "citation": {"work_id": w}, "lessons": []} for w in hits],
            "total_matched": len(hits),
            "near_duplicate_top": bool(hits),
            "fts_truncated": False,
        }

    return run


def test_example_fixtures_load_and_compare() -> None:
    corpus, corpus_text = load_corpus(_FIXTURES / "corpus.json")
    queries = load_queries(_FIXTURES / "queries.json")
    relevance = load_relevance(_FIXTURES / "relevance.json")

    assert len(queries) == 1
    query, query_text = queries[0]
    assert query.work_id == "fix-tls-handshake-timeout"

    result = compare_arms(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=OursMemory(store_path="<stub>", runner=_ours_stub(["tls-cert-expiry-scix"])),
        semantic=LexicalTopKMemory(top_k=10),
        relevant_ids=relevance[query.work_id],
        scope="cross_rig",
        pool_depth=10,
    )

    # cross_rig pool = the 3 other-rig LOO-survivors (sibling/future/supersedes
    # excluded; same-rig tls-fix-gascity dropped by the track).
    assert result.eligible_count == 3
    # authored relevant {cert-expiry, handshake-timeout, tls-fix-gascity} ∩ pool
    # drops the same-rig gascity record -> 2.
    assert result.relevant_count == 2

    arms = {a.arm: a for a in result.arms}

    # `ours`: precise but narrow — the exact-signature hit only.
    ours = arms["ours"]
    assert ours.retrieved_ids == ["tls-cert-expiry-scix"]
    assert ours.precision == 1.0
    assert ours.recall == 0.5
    assert ours.leak_checked is True

    # `lexical`: broad — recovers both relevant records plus a distractor.
    lexical = arms["lexical"]
    assert set(lexical.retrieved_ids) == {
        "tls-cert-expiry-scix",
        "handshake-timeout-dashboard",
        "qdrant-pool-scix",
    }
    assert lexical.recall == 1.0
    assert lexical.precision == approx(2 / 3)
    assert lexical.leak_checked is True
