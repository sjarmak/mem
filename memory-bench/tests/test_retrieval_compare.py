"""Tests for the `ours` vs semantic-arm retrieval-quality bridge (mem-compare).

Both arms are driven through injected fakes — no SDK, no Ollama, no built `mem`
CLI — so the wiring, id-translation, LOO re-check, and scoring are pinned in CI
exactly as the real (local-model) clients will run behind the same seams.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from membench.compare import (
    ArmComparison,
    compare_arms,
    ours_replay,
    seed_semantic_arm,
    semantic_replay,
)
from membench.memory_systems.mem0_system import Mem0Memory
from membench.memory_systems.ours_system import OursMemory, OursQuery
from membench.memory_systems.semantic_base import SemanticHit
from membench.runtime import IdClock, StepContext
from membench.validity import LeakageError, QueryWork, WorkRef, loo_bounded


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeSemanticClient:
    """A deterministic `SemanticMemoryClient`: mints ``bk-<n>`` ids on store and
    returns the scope's seeded items in insertion order on query. ``extra_hits``
    are appended unconditionally — used to simulate a backend leaking an id the
    harness never seeded."""

    def __init__(self, extra_hits: list[SemanticHit] | None = None) -> None:
        self._scopes: dict[str, list[tuple[str, str]]] = {}
        self._n = 0
        self._extra = extra_hits or []

    def store(self, *, scope: str, content: str, memory_id: str) -> str:
        self._n += 1
        backend_id = f"bk-{self._n}"
        self._scopes.setdefault(scope, []).append((backend_id, content))
        return backend_id

    def query(self, *, scope: str, query_text: str, top_k: int) -> list[SemanticHit]:
        seeded = [
            SemanticHit(memory_id=bid, content=content, score=1.0 / (i + 1))
            for i, (bid, content) in enumerate(self._scopes.get(scope, []))
        ]
        return (seeded + self._extra)[:top_k]

    def clear(self, *, scope: str) -> None:
        self._scopes[scope] = []


def _ours_runner_returning(
    work_ids: list[str],
) -> Callable[[OursQuery], dict[str, object]]:
    """An `OursMemory` runner stub returning the retrieval-v1 `--json` `data` shape
    for a fixed set of work_ids."""

    def run(query: OursQuery) -> dict[str, object]:
        return {
            "items": [{"work_id": w, "citation": {"work_id": w}, "lessons": []} for w in work_ids],
            "total_matched": len(work_ids),
            "near_duplicate_top": False,
            "fts_truncated": False,
        }

    return run


# --------------------------------------------------------------------------- #
# Fixtures: a small corpus where the LOO boundary excludes 2 of 4 records.
# --------------------------------------------------------------------------- #
def _corpus() -> list[WorkRef]:
    return [
        WorkRef(work_id="A", rig="r1", closed="2024-01-01"),
        WorkRef(work_id="C", rig="r1", closed="2024-02-01"),
        # convoy sibling of the query -> excluded by isSibling
        WorkRef(work_id="E", rig="r1", closed="2024-03-01", convoy_id="cv1"),
        # closed AFTER the query started -> excluded by the strict temporal cut
        WorkRef(work_id="D", rig="r1", closed="2024-12-01"),
    ]


def _query() -> QueryWork:
    return QueryWork(work_id="B", rig="r1", started="2024-06-01", convoy_id="cv1")


_CORPUS_TEXT = {
    "A": "alpha: cert expired in tls handshake",
    "C": "charlie: timeout in handshake",
    "E": "echo text",
    "D": "delta text",
}


def test_loo_fixture_excludes_sibling_and_future() -> None:
    eligible = {ref.work_id for ref in loo_bounded(_corpus(), _query())}
    assert eligible == {"A", "C"}


def test_seed_maps_backend_ids_to_work_ids() -> None:
    arm = Mem0Memory(client=FakeSemanticClient())
    arm.reset("t-seed")
    ctx = StepContext(trial_id="t-seed", session_id="B", step_id="seed", clock=IdClock())
    eligible = loo_bounded(_corpus(), _query())

    mapping = seed_semantic_arm(arm, eligible, _CORPUS_TEXT, ctx)

    assert set(mapping.values()) == {"A", "C"}
    assert all(bid.startswith("bk-") for bid in mapping)


def test_seed_missing_text_raises() -> None:
    arm = Mem0Memory(client=FakeSemanticClient())
    arm.reset("t-missing")
    ctx = StepContext(trial_id="t-missing", session_id="B", step_id="seed", clock=IdClock())
    eligible = loo_bounded(_corpus(), _query())

    with pytest.raises(ValueError, match="no corpus_text for eligible work_id"):
        seed_semantic_arm(arm, eligible, {"A": "only A"}, ctx)


def test_semantic_replay_scores_and_leak_checks() -> None:
    arm = Mem0Memory(client=FakeSemanticClient(), top_k=10)
    ac = semantic_replay(arm, _query(), "cert expired", _corpus(), _CORPUS_TEXT, relevant_ids=["A"])

    assert set(ac.retrieved_ids) == {"A", "C"}
    assert ac.recall == 1.0  # A (the only relevant) was retrieved
    assert ac.precision == 0.5  # 1 of 2 retrieved is relevant
    assert ac.injected_context_chars > 0
    assert ac.leak_checked is True


def test_semantic_replay_raises_on_leaked_id() -> None:
    # Backend returns an id the harness never seeded -> unmapped -> not in the LOO
    # set -> the harness re-check must fail loud, never silently drop it.
    leaky = FakeSemanticClient(extra_hits=[SemanticHit(memory_id="bk-leak", content="x")])
    arm = Mem0Memory(client=leaky, top_k=10)

    with pytest.raises(LeakageError):
        semantic_replay(arm, _query(), "q", _corpus(), _CORPUS_TEXT, relevant_ids=["A"])


def test_ours_replay_scores() -> None:
    ours = OursMemory(store_path="unused", runner=_ours_runner_returning(["A"]))
    ac = ours_replay(ours, _query(), _corpus(), relevant_ids=["A"])

    assert ac.retrieved_ids == ["A"]
    assert ac.precision == 1.0
    assert ac.recall == 1.0
    assert ac.scope == "cross_rig"
    assert ac.leak_checked is True


def test_empty_relevant_set_yields_none_metrics() -> None:
    ours = OursMemory(store_path="unused", runner=_ours_runner_returning(["A"]))
    ac = ours_replay(ours, _query(), _corpus(), relevant_ids=[])

    assert ac.precision is None
    assert ac.recall is None
    assert ac.mrr is None
    assert ac.ndcg is None


def test_compare_arms_aggregates_and_intersects_relevant_with_loo() -> None:
    ours = OursMemory(store_path="unused", runner=_ours_runner_returning(["A"]))
    semantic = Mem0Memory(client=FakeSemanticClient(), top_k=10)

    result = compare_arms(
        _query(),
        "cert expired",
        _corpus(),
        _CORPUS_TEXT,
        ours=ours,
        semantic=semantic,
        # "D" is relevant-but-LOO-withheld; it must be dropped from the denominator.
        relevant_ids=["A", "D"],
        stack_telemetry={"chat_model": "llama3", "ollama_embedding_model": "nomic-embed-text"},
    )

    assert result.eligible_count == 2
    assert result.relevant_count == 1  # D dropped: not LOO-eligible
    assert {a.arm for a in result.arms} == {"ours", "mem0"}
    assert all(isinstance(a, ArmComparison) for a in result.arms)
    assert result.stack_telemetry["chat_model"] == "llama3"
