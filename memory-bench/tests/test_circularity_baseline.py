"""Tests for the token-overlap (lexical) circularity baseline (mem-lvp.34).

The diagnostic guard: a dumb token-overlap retriever (`LexicalTopKMemory`) is run
over the IDENTICAL scope-filtered candidate pool the comparison uses and scored,
through the SAME `score_harvest` path, against the JUDGED ``relevant_ids``. If `ours`
scores ~= that baseline against the judge, the judged ground truth may be a proxy for
ours's own keyword / failure-signature mechanism — circularity is flagged LIVE.

All wiring is driven through injected fakes and the real in-process lexical arm — no
SDK, no Ollama, no built `mem` CLI, no live judge.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from membench.compare import (
    DEFAULT_CIRCULARITY_DELTA,
    CircularityVerdict,
    circularity_check,
    score_harvest,
)
from membench.compare.io import load_corpus, load_queries, load_relevance
from membench.compare.retrieval_compare import harvest_ours, harvest_semantic
from membench.memory_systems.lexical_system import LexicalTopKMemory
from membench.memory_systems.ours_system import OursMemory, OursQuery
from membench.validity import QueryWork, WorkRef

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "compare-ours-mem0"


# --------------------------------------------------------------------------- #
# Fakes / fixtures — query work B in rig r1; cross_rig pool is {A, C}.
# --------------------------------------------------------------------------- #
def _ours_runner(work_ids: list[str]) -> Callable[[OursQuery], dict[str, object]]:
    def run(query: OursQuery) -> dict[str, object]:
        return {
            "items": [{"work_id": w, "citation": {"work_id": w}, "lessons": []} for w in work_ids],
            "total_matched": len(work_ids),
            "near_duplicate_top": False,
            "fts_truncated": False,
        }

    return run


def _corpus() -> list[WorkRef]:
    return [
        WorkRef(work_id="A", rig="r2", closed="2024-01-01"),
        WorkRef(work_id="C", rig="r2", closed="2024-02-01"),
        WorkRef(work_id="G", rig="r1", closed="2024-02-15"),
    ]


def _query() -> QueryWork:
    return QueryWork(work_id="B", rig="r1", started="2024-06-01")


_CORPUS_TEXT = {
    "A": "alpha: cert expired in the tls handshake renew the cert",
    "C": "charlie: qdrant connection pool exhausted widen the pool",
    "G": "golf: same-rig tls handshake note",
}


# --------------------------------------------------------------------------- #
# Closeness rule + verdict shape
# --------------------------------------------------------------------------- #
def test_default_delta_is_documented_constant() -> None:
    # The closeness rule is an explicit, documented threshold — no hidden number.
    assert DEFAULT_CIRCULARITY_DELTA == 0.05


def test_flags_circularity_when_ours_matches_baseline() -> None:
    # ours retrieves exactly A (the cert match); the lexical baseline over the same
    # pool, queried on the cert text, also surfaces A first. Both score the same
    # against the judged relevant set {A} -> delta ~ 0 -> circularity flagged.
    ours = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    lexical = LexicalTopKMemory(top_k=10)
    verdict = circularity_check(
        _query(),
        "cert expired tls handshake",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        baseline=lexical,
        relevant_ids=["A"],
    )
    assert isinstance(verdict, CircularityVerdict)
    assert verdict.flagged is True
    assert verdict.baseline.arm == "lexical"
    assert verdict.ours_score is not None
    assert verdict.baseline_score is not None
    assert verdict.delta is not None
    assert verdict.delta <= DEFAULT_CIRCULARITY_DELTA
    assert "A" in verdict.baseline.retrieved_ids


def test_no_flag_when_ours_beats_baseline() -> None:
    # Make ours strictly better than the dumb baseline on the judged set: the judged
    # relevant id is G's content, which lives in the same-rig pool. On cross_rig the
    # baseline can only reach {A, C}; ours is stubbed to surface A (relevant). With a
    # relevant set ours hits but the lexical baseline misses, ours clears the baseline
    # by more than the closeness delta -> no flag.
    ours = OursMemory(store_path="unused", runner=_ours_runner(["A", "C"]))
    lexical = LexicalTopKMemory(top_k=10)
    # Query text that overlaps NOTHING in the pool -> lexical baseline returns empty
    # and scores 0 against {A}; ours retrieves A -> precision/recall > 0 -> gap > delta.
    verdict = circularity_check(
        _query(),
        "zzzz nonoverlapping query tokens",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        baseline=lexical,
        relevant_ids=["A"],
    )
    assert verdict.flagged is False
    assert verdict.baseline.retrieved_ids == []
    assert verdict.baseline_score == 0.0
    assert verdict.ours_score is not None and verdict.ours_score > 0.0
    assert verdict.delta is not None and verdict.delta > DEFAULT_CIRCULARITY_DELTA


def test_diagnostic_only_never_gates() -> None:
    # The verdict is diagnostic-only by contract: it carries win_eligible=True
    # regardless of the flag, so it can never gate the headline by itself.
    ours = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    lexical = LexicalTopKMemory(top_k=10)
    verdict = circularity_check(
        _query(),
        "cert expired tls handshake",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        baseline=lexical,
        relevant_ids=["A"],
    )
    assert verdict.diagnostic_only is True
    assert verdict.win_eligible is True  # diagnostic-only never removes eligibility


def test_empty_relevant_set_yields_unmeasured_no_flag() -> None:
    # No judged relevant set -> nothing to be circular about; scores are None and the
    # flag is False (absence, never a fabricated match).
    ours = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    lexical = LexicalTopKMemory(top_k=10)
    verdict = circularity_check(
        _query(),
        "cert expired",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        baseline=lexical,
        relevant_ids=[],
    )
    assert verdict.flagged is False
    assert verdict.ours_score is None
    assert verdict.baseline_score is None
    assert verdict.delta is None
    assert "not measured" in verdict.reason


def test_custom_metric_and_delta() -> None:
    # The primary metric and closeness delta are explicit inputs (documented, not
    # hidden). recall is selectable; a wider delta still flags a tie.
    ours = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    lexical = LexicalTopKMemory(top_k=10)
    verdict = circularity_check(
        _query(),
        "cert expired tls handshake",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        baseline=lexical,
        relevant_ids=["A"],
        metric="recall",
        delta=0.10,
    )
    assert verdict.metric == "recall"
    assert verdict.closeness_threshold == 0.10
    assert verdict.flagged is True


def test_unknown_metric_raises() -> None:
    ours = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    lexical = LexicalTopKMemory(top_k=10)
    with pytest.raises(ValueError, match="unknown metric"):
        circularity_check(
            _query(),
            "cert",
            _corpus(),
            _CORPUS_TEXT,
            ours=ours,
            baseline=lexical,
            relevant_ids=["A"],
            metric="f1",
        )


def test_negative_delta_raises() -> None:
    ours = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    lexical = LexicalTopKMemory(top_k=10)
    with pytest.raises(ValueError, match="delta must be >= 0"):
        circularity_check(
            _query(),
            "cert",
            _corpus(),
            _CORPUS_TEXT,
            ours=ours,
            baseline=lexical,
            relevant_ids=["A"],
            delta=-0.1,
        )


def test_verdict_envelope_is_json_round_trippable() -> None:
    ours = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    lexical = LexicalTopKMemory(top_k=10)
    verdict = circularity_check(
        _query(),
        "cert expired tls handshake",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        baseline=lexical,
        relevant_ids=["A"],
    )
    blob = verdict.to_envelope()
    assert blob["circularity_flagged"] is verdict.flagged
    assert blob["metric"] == "precision"
    assert blob["baseline_arm"] == "lexical"
    assert blob["closeness_threshold"] == DEFAULT_CIRCULARITY_DELTA
    assert blob["diagnostic_only"] is True
    assert "baseline_score" in blob and "ours_score" in blob


# --------------------------------------------------------------------------- #
# Pre-harvested seam: the baseline scores the SAME pool over the SAME score path.
# --------------------------------------------------------------------------- #
def test_baseline_reuses_score_harvest_seam() -> None:
    # The baseline ArmComparison must equal what score_harvest produces over the
    # lexical harvest — proving no duplicate scorer was introduced.
    lexical = LexicalTopKMemory(top_k=10)
    harvest = harvest_semantic(
        lexical, _query(), "cert expired tls handshake", _corpus(), _CORPUS_TEXT
    )
    expected = score_harvest(harvest, ["A"])

    ours = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    verdict = circularity_check(
        _query(),
        "cert expired tls handshake",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        baseline=LexicalTopKMemory(top_k=10),
        relevant_ids=["A"],
    )
    assert verdict.baseline.retrieved_ids == expected.retrieved_ids
    assert verdict.baseline.precision == expected.precision


def test_ours_harvest_matches_replay() -> None:
    # ours is harvested through the SAME harvest_ours seam compare_arms uses.
    ours_a = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    h = harvest_ours(ours_a, _query(), _corpus())
    expected = score_harvest(h, ["A"])

    ours_b = OursMemory(store_path="unused", runner=_ours_runner(["A"]))
    verdict = circularity_check(
        _query(),
        "cert expired tls handshake",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours_b,
        baseline=LexicalTopKMemory(top_k=10),
        relevant_ids=["A"],
    )
    assert verdict.ours_score == expected.precision


# --------------------------------------------------------------------------- #
# CI test on the example fixtures with the in-process lexical arm.
# --------------------------------------------------------------------------- #
def test_circularity_on_example_fixtures() -> None:
    corpus, corpus_text = load_corpus(_EXAMPLE / "corpus.json")
    queries = load_queries(_EXAMPLE / "queries.json")
    relevance = load_relevance(_EXAMPLE / "relevance.json")

    # Mirror the smoke ours stub: the cert-expiry failure-signature match.
    ours_hits = {"fix-tls-handshake-timeout": ["tls-cert-expiry-scix"]}

    for query, query_text in queries:
        ours = OursMemory(
            store_path="<stub>", runner=_ours_runner(ours_hits.get(query.work_id, []))
        )
        verdict = circularity_check(
            query,
            query_text,
            corpus,
            corpus_text,
            ours=ours,
            baseline=LexicalTopKMemory(top_k=10),
            relevant_ids=relevance.get(query.work_id, []),
        )
        assert isinstance(verdict, CircularityVerdict)
        assert verdict.diagnostic_only is True
        assert verdict.baseline.arm == "lexical"
        # The example judged set has 3 relevant ids; the lexical baseline runs over
        # the same cross_rig pool and is measured (not None).
        assert verdict.baseline_score is not None
        assert verdict.ours_score is not None
