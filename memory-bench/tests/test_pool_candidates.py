"""Tests for the equal-depth candidate pooler + retrieve-once harvest (mem-lvp.30).

Pure ZFC mechanism — no model, no daemon, no CLI. The pooler fixes the
pool-contribution bias (`ours` uncapped vs the semantic arm's `top_k`) by taking
EXACTLY top-D from each arm regardless of native length, and the harvest helper
guarantees retrieval runs once per arm per query so the ids that are pooled are
the same ids that are scored.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from membench.compare import (
    ArmHarvest,
    compare_arms,
    harvest_ours,
    harvest_semantic,
    pool_candidates,
)
from membench.memory_systems.mem0_system import Mem0Memory
from membench.memory_systems.ours_system import OursMemory, OursQuery
from membench.memory_systems.semantic_base import SemanticHit
from membench.validity import QueryWork, WorkRef, loo_bounded


# --------------------------------------------------------------------------- #
# Fixtures (mirroring test_retrieval_compare): B in rig r1; cross_rig pool {A,C}.
# --------------------------------------------------------------------------- #
def _corpus() -> list[WorkRef]:
    return [
        WorkRef(work_id="A", rig="r2", closed="2024-01-01"),
        WorkRef(work_id="C", rig="r2", closed="2024-02-01"),
        WorkRef(work_id="G", rig="r1", closed="2024-02-15"),
        WorkRef(work_id="E", rig="r2", closed="2024-03-01", convoy_id="cv1"),
        WorkRef(work_id="D", rig="r2", closed="2024-12-01"),
    ]


def _query() -> QueryWork:
    return QueryWork(work_id="B", rig="r1", started="2024-06-01", convoy_id="cv1")


_CORPUS_TEXT = {
    "A": "alpha: cert expired in tls handshake",
    "C": "charlie: timeout in handshake",
    "G": "golf: same-rig tls note",
    "E": "echo text",
    "D": "delta text",
}


class CountingSemanticClient:
    """A deterministic client that records every `query` call so the harvest's
    retrieve-once invariant is observable. Stores in insertion order, returns the
    scope's seeded items ranked best-first, capped at top_k."""

    def __init__(self) -> None:
        self._scopes: dict[str, list[tuple[str, str]]] = {}
        self._n = 0
        self.query_calls = 0

    def store(self, *, scope: str, content: str, memory_id: str) -> str:
        self._n += 1
        backend_id = f"bk-{self._n}"
        self._scopes.setdefault(scope, []).append((backend_id, content))
        return backend_id

    def query(self, *, scope: str, query_text: str, top_k: int) -> list[SemanticHit]:
        self.query_calls += 1
        seeded = [
            SemanticHit(memory_id=bid, content=content, score=1.0 / (i + 1))
            for i, (bid, content) in enumerate(self._scopes.get(scope, []))
        ]
        return seeded[:top_k]

    def clear(self, *, scope: str) -> None:
        self._scopes[scope] = []


def _counting_ours_runner(
    work_ids: list[str],
) -> tuple[Callable[[OursQuery], dict[str, object]], list[int]]:
    """An `OursMemory` runner returning a fixed work_id list, plus a mutable
    counter list recording how many times it was invoked."""
    calls = [0]

    def run(query: OursQuery) -> dict[str, object]:
        calls[0] += 1
        return {
            "items": [{"work_id": w, "citation": {"work_id": w}, "lessons": []} for w in work_ids],
            "total_matched": len(work_ids),
            "near_duplicate_top": False,
            "fts_truncated": False,
        }

    return run, calls


# --------------------------------------------------------------------------- #
# pool_candidates: equal-depth, symmetric contribution
# --------------------------------------------------------------------------- #
def test_pool_takes_top_d_from_each_arm() -> None:
    ours = ArmHarvest(
        arm="ours",
        scope="cross_rig",
        retrieved_ids=["A", "C"],
        injected_context_chars=0,
        latency_ms=1.0,
        retrieval_truncated=False,
        leak_checked=True,
    )
    semantic = ArmHarvest(
        arm="mem0",
        scope="cross_rig",
        retrieved_ids=["C", "G"],
        injected_context_chars=0,
        latency_ms=1.0,
        retrieval_truncated=False,
        leak_checked=True,
    )
    pooled = pool_candidates([ours, semantic], depth=2)
    # Ordered union, deduped: A, C from ours then G from semantic (C already in).
    assert pooled == ("A", "C", "G")


def test_pool_symmetric_contribution_truncates_longer_arm() -> None:
    # ours is far longer than D; only its first D contribute, exactly like the
    # semantic arm's native top_k=D. This is the bias the pooler exists to remove.
    ours = ArmHarvest(
        arm="ours",
        scope="cross_rig",
        retrieved_ids=["A", "C", "G", "X", "Y", "Z"],
        injected_context_chars=0,
        latency_ms=1.0,
        retrieval_truncated=False,
        leak_checked=True,
    )
    semantic = ArmHarvest(
        arm="mem0",
        scope="cross_rig",
        retrieved_ids=["C"],
        injected_context_chars=0,
        latency_ms=1.0,
        retrieval_truncated=False,
        leak_checked=True,
    )
    pooled = pool_candidates([ours, semantic], depth=2)
    # ours contributes EXACTLY its top-2 {A, C}; X/Y/Z are truncated away.
    assert pooled == ("A", "C")
    assert "X" not in pooled and "Z" not in pooled


def test_pool_depth_required_and_positive() -> None:
    ours = ArmHarvest(
        arm="ours",
        scope=None,
        retrieved_ids=["A"],
        injected_context_chars=0,
        latency_ms=1.0,
        retrieval_truncated=False,
        leak_checked=True,
    )
    with pytest.raises(ValueError, match="depth must be >= 1"):
        pool_candidates([ours], depth=0)


# --------------------------------------------------------------------------- #
# retrieve-once harvest: scored == harvested, one call per arm per query
# --------------------------------------------------------------------------- #
def test_harvest_ours_runs_runner_once() -> None:
    runner, calls = _counting_ours_runner(["A", "C"])
    ours = OursMemory(store_path="unused", runner=runner)
    h = harvest_ours(ours, _query(), _corpus(), scope="cross_rig")
    assert h.retrieved_ids == ["A", "C"]
    assert calls[0] == 1
    assert h.leak_checked is True


def test_harvest_semantic_runs_query_once() -> None:
    client = CountingSemanticClient()
    arm = Mem0Memory(client=client, top_k=10)
    h = harvest_semantic(arm, _query(), "cert expired", _corpus(), _CORPUS_TEXT, scope="cross_rig")
    # cross_rig pool {A, C}; G is same-rig and dropped.
    assert set(h.retrieved_ids) == {"A", "C"}
    assert client.query_calls == 1


def test_compare_arms_retrieves_once_per_arm_and_scores_harvested() -> None:
    runner, ours_calls = _counting_ours_runner(["A", "C"])
    ours = OursMemory(store_path="unused", runner=runner)
    client = CountingSemanticClient()
    semantic = Mem0Memory(client=client, top_k=10)

    result = compare_arms(
        _query(),
        "cert expired",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        semantic=semantic,
        relevant_ids=["A"],
        pool_depth=2,
    )

    # Retrieval ran exactly once per arm for this query.
    assert ours_calls[0] == 1
    assert client.query_calls == 1
    # The scored ids are exactly the harvested ids (no second retrieval).
    arms = {a.arm: a for a in result.arms}
    assert arms["ours"].retrieved_ids == ["A", "C"]
    assert set(arms["mem0"].retrieved_ids) == {"A", "C"}
    assert result.pool_depth == 2


# --------------------------------------------------------------------------- #
# corpus_text completeness + judged==pooled domain
# --------------------------------------------------------------------------- #
def test_pool_raises_on_id_missing_from_corpus_text() -> None:
    runner, _ = _counting_ours_runner(["A", "C"])
    ours = OursMemory(store_path="unused", runner=runner)
    client = CountingSemanticClient()
    semantic = Mem0Memory(client=client, top_k=10)
    incomplete = {"A": "alpha"}  # C is missing -> seeding raises first.

    with pytest.raises(ValueError):
        compare_arms(
            _query(),
            "cert expired",
            _corpus(),
            incomplete,
            ours=ours,
            semantic=semantic,
            relevant_ids=["A"],
            pool_depth=2,
        )


def test_pooled_ids_all_present_in_corpus_text() -> None:
    # Every pooled id must resolve to corpus_text (the judge's input domain).
    runner, _ = _counting_ours_runner(["A", "C"])
    ours = OursMemory(store_path="unused", runner=runner)
    client = CountingSemanticClient()
    semantic = Mem0Memory(client=client, top_k=10)
    result = compare_arms(
        _query(),
        "cert expired",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        semantic=semantic,
        relevant_ids=["A"],
        pool_depth=2,
    )
    for arm in result.arms:
        for wid in arm.retrieved_ids:
            assert wid in _CORPUS_TEXT


# --------------------------------------------------------------------------- #
# per-query shuffle with a recorded seed
# --------------------------------------------------------------------------- #
def test_pool_shuffle_is_deterministic_for_a_seed() -> None:
    ours = ArmHarvest(
        arm="ours",
        scope="cross_rig",
        retrieved_ids=["A", "C", "G"],
        injected_context_chars=0,
        latency_ms=1.0,
        retrieval_truncated=False,
        leak_checked=True,
    )
    pooled = pool_candidates([ours], depth=3)
    s1 = pool_candidates([ours], depth=3, shuffle_seed=42)
    s2 = pool_candidates([ours], depth=3, shuffle_seed=42)
    assert s1 == s2  # same seed -> same order
    assert set(s1) == set(pooled)  # same members, order may differ


def test_compare_arms_records_shuffle_seed() -> None:
    runner, _ = _counting_ours_runner(["A", "C"])
    ours = OursMemory(store_path="unused", runner=runner)
    client = CountingSemanticClient()
    semantic = Mem0Memory(client=client, top_k=10)
    result = compare_arms(
        _query(),
        "cert expired",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        semantic=semantic,
        relevant_ids=["A"],
        pool_depth=2,
        shuffle_seed=7,
    )
    assert result.shuffle_seed == 7
    # The recorded pooled candidate order is reproducible from the seed.
    assert set(result.pooled_candidates) == {"A", "C"}


def test_loo_fixture_sanity() -> None:
    eligible = {ref.work_id for ref in loo_bounded(_corpus(), _query())}
    assert eligible == {"A", "C", "G"}
