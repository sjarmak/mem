"""LexicalTopKMemory — the deterministic query/top-k arm (mem-zt1c).

It must (a) rank the whole store by query token-overlap (not by requested ids), (b) be
deterministic, (c) cap at top_k, (d) clear on reset, and (e) seed world-noise without
emitting telemetry. These properties are what make Confusion/Staleness measurable.
"""

from __future__ import annotations

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.base import RetrievalRequest
from membench.memory_systems.lexical_system import LexicalTopKMemory, _tokenize
from membench.runtime import IdClock, StepContext


def _ctx() -> StepContext:
    return StepContext(trial_id="t1", session_id="s1", step_id="st1", clock=IdClock())


def _seed(arm: LexicalTopKMemory, items: dict[str, str]) -> None:
    for mid, content in items.items():
        arm.write(mid, content, _ctx())


def test_seed_does_not_advance_a_separate_step_clock() -> None:
    # The runner gives seed() its OWN clock so the step's event ids are unperturbed.
    arm = LexicalTopKMemory()
    arm.reset("t1")
    step_clock = IdClock()
    seed_clock = IdClock()
    seed_ctx = StepContext(trial_id="t1", session_id="s1", step_id="st1", clock=seed_clock)
    arm.seed({"d1": "deploy timeout distractor", "d2": "region distractor"}, seed_ctx)
    # the seed clock advanced (two writes), the unrelated step clock did NOT.
    assert seed_clock.latency_ms() > 0.0
    assert step_clock.latency_ms() == 0.0


def test_factory_builds_lexical() -> None:
    assert isinstance(build_memory_system("lexical"), LexicalTopKMemory)


def test_tokenize_lowercases_and_splits_alphanumeric() -> None:
    assert _tokenize("Deploy-timeout is 30s!") == {"deploy", "timeout", "is", "30s"}


def test_retrieve_ranks_by_query_overlap_not_requested_ids() -> None:
    arm = LexicalTopKMemory()
    arm.reset("t1")
    _seed(
        arm,
        {
            "hit": "the production deploy timeout is 30s",
            "miss": "unrelated retention window content",
        },
    )
    # requested_ids names ONLY 'miss', but retrieval keys off query overlap, so 'hit' wins
    # and 'miss' (no overlap with the query) is dropped — the arm ignores requested_ids.
    res = arm.retrieve(
        RetrievalRequest(query_text="the production deploy timeout", requested_ids=["miss"]),
        _ctx(),
    )
    assert list(res.payloads) == ["hit"]


def test_retrieve_is_deterministic_with_id_tiebreak() -> None:
    arm = LexicalTopKMemory()
    arm.reset("t1")
    # Two items with identical query-overlap; id-ascending tiebreak fixes the order.
    _seed(arm, {"b-id": "deploy timeout value", "a-id": "deploy timeout value"})
    req = RetrievalRequest(query_text="deploy timeout")
    first = list(arm.retrieve(req, _ctx()).payloads)
    second = list(arm.retrieve(req, _ctx()).payloads)
    assert first == second == ["a-id", "b-id"]


def test_top_k_caps_returned_items() -> None:
    arm = LexicalTopKMemory(top_k=2)
    arm.reset("t1")
    _seed(arm, {f"id{i}": "deploy timeout match" for i in range(5)})
    res = arm.retrieve(RetrievalRequest(query_text="deploy timeout"), _ctx())
    assert len(res.payloads) == 2


def test_zero_overlap_items_excluded() -> None:
    arm = LexicalTopKMemory()
    arm.reset("t1")
    _seed(arm, {"x": "completely orthogonal tokens here"})
    res = arm.retrieve(RetrievalRequest(query_text="deploy timeout region"), _ctx())
    assert res.payloads == {}


def test_reset_clears_store() -> None:
    arm = LexicalTopKMemory()
    arm.reset("t1")
    _seed(arm, {"id": "deploy timeout"})
    arm.reset("t2")
    assert arm.retrieve(RetrievalRequest(query_text="deploy timeout"), _ctx()).payloads == {}


def test_top_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="top_k must be >= 1"):
        LexicalTopKMemory(top_k=0)


def test_seed_persists_items_for_retrieval() -> None:
    arm = LexicalTopKMemory()
    arm.reset("t1")
    seed_ctx = StepContext(trial_id="t1", session_id="s1", step_id="st1", clock=IdClock())
    arm.seed({"d1": "deploy timeout distractor", "d2": "region distractor"}, seed_ctx)
    # seed() must persist the items so retrieval can surface them...
    res = arm.retrieve(RetrievalRequest(query_text="deploy timeout region"), _ctx())
    assert {"d1", "d2"}.issubset(res.payloads)


def test_seed_is_noop_for_non_writing_arm() -> None:
    # oracle does not support writes; the ABC seed() short-circuits rather than raising.
    oracle = build_memory_system("oracle")
    oracle.seed({"d1": "noise"}, _ctx())  # must not raise
