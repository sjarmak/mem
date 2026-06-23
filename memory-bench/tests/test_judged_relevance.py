"""Tests for the pool->judge->relevant_ids glue + judged-relevance artifact (mem-lvp.32).

Pure plumbing over a FAKE judge — no daemon, no SDK, no CLI, no network. The glue
harvests each arm ONCE (the .30 pooler), judges every (query B, pooled-candidate)
pair with the .31 relevance judge, and emits the SAME ``dict{query_work_id ->
[work_id]}`` shape `io.load_relevance` returns, so it drops straight into
`compare_arms` with an unchanged signature.

Covered invariants:
* end-to-end pool->judge->relevant_ids->compare_arms over a fake judge;
* self-describing artifact frozen to disk (pool_depth D, judge_model, prompt_version,
  per-pair verdict+rationale), persisted INCREMENTALLY (resumable from cache);
* bounded budget (candidates/query capped at the pooled union at depth D;
  judged_pairs recorded) and a per-pair cache that prevents re-paying;
* judge identity + prompt_version recorded in `ComparisonResult.stack_telemetry`;
* fail loud: dead judge -> `LocalStackUnavailableError`; malformed -> raises;
  transient timeout retries ONCE then surfaces (no hiding loop).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from membench.bbon.comparative_judge import StubComparativeJudge
from membench.compare import compare_arms
from membench.compare.io import load_corpus, load_queries
from membench.compare.judged_relevance import PairCache, harvest_and_judge, judge_relevance
from membench.compare.relevance_judge import DEFAULT_PROMPT_VERSION, RelevanceJudgeError
from membench.memory_systems.lexical_system import LexicalTopKMemory
from membench.memory_systems.local_stack import LocalStackUnavailableError
from membench.memory_systems.ours_system import OursMemory, OursQuery
from membench.validity import QueryWork, WorkRef

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "compare-ours-mem0"


# --------------------------------------------------------------------------- #
# Fixtures: the runnable example corpus + a stubbed `ours` arm (no built store).
# --------------------------------------------------------------------------- #
_OURS_STUB_HITS: dict[str, list[str]] = {
    "fix-tls-handshake-timeout": ["tls-cert-expiry-scix"],
}


def _ours_stub(hits: list[str]) -> Callable[[OursQuery], dict[str, object]]:
    def run(_query: OursQuery) -> dict[str, object]:
        return {
            "items": [{"work_id": w, "citation": {"work_id": w}, "lessons": []} for w in hits],
            "total_matched": len(hits),
            "near_duplicate_top": bool(hits),
            "fts_truncated": False,
        }

    return run


def _load_example() -> tuple[list[WorkRef], dict[str, str], list[tuple[QueryWork, str]]]:
    corpus, corpus_text = load_corpus(_EXAMPLES / "corpus.json")
    queries = load_queries(_EXAMPLES / "queries.json")
    return corpus, corpus_text, queries


def _yes_judge() -> StubComparativeJudge:
    """A fake judge that always returns a well-formed 'relevant: true' verdict."""

    def fn(_prompt: str) -> str:
        return json.dumps(
            {
                "relevant": True,
                "transferable_lesson": "renew the expired cert before the handshake",
                "rationale": "both works fix a tls handshake by renewing the cert.",
            }
        )

    return StubComparativeJudge(fn=fn, model="fake-judge")


def _no_judge() -> StubComparativeJudge:
    def fn(_prompt: str) -> str:
        return json.dumps(
            {
                "relevant": False,
                "transferable_lesson": "no shared lesson",
                "rationale": "different subsystems entirely.",
            }
        )

    return StubComparativeJudge(fn=fn, model="fake-judge")


# --------------------------------------------------------------------------- #
# End-to-end: pool -> judge -> relevant_ids -> compare_arms
# --------------------------------------------------------------------------- #
def test_end_to_end_relevant_ids_feed_compare_arms(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    semantic = LexicalTopKMemory(top_k=10)

    judged = harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=semantic,
        pool_depth=10,
        judge=_yes_judge(),
        cache=PairCache(tmp_path / "cache.jsonl"),
    )

    # Emits the SAME dict shape io.load_relevance returns.
    relevant = judged.relevant_ids()
    assert isinstance(relevant, dict)
    assert query.work_id in relevant
    assert all(isinstance(w, str) for w in relevant[query.work_id])
    # Every judged-relevant id is a member of the pooled candidate union.
    assert set(relevant[query.work_id]) <= set(judged.pooled[query.work_id])

    # Feeds compare_arms unchanged.
    result = compare_arms(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=semantic,
        relevant_ids=relevant[query.work_id],
        pool_depth=10,
        stack_telemetry=judged.telemetry(),
    )
    assert result.work_id == query.work_id
    assert result.relevant_count == len(set(relevant[query.work_id]))


def test_yes_judge_marks_whole_pool_relevant(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    judged = harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=_yes_judge(),
        cache=PairCache(tmp_path / "c.jsonl"),
    )
    assert set(judged.relevant_ids()[query.work_id]) == set(judged.pooled[query.work_id])


def test_no_judge_yields_empty_relevant(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    judged = harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=_no_judge(),
        cache=PairCache(tmp_path / "c.jsonl"),
    )
    assert judged.relevant_ids()[query.work_id] == []


# --------------------------------------------------------------------------- #
# Artifact: self-describing, frozen to disk, resumable
# --------------------------------------------------------------------------- #
def test_artifact_is_self_describing(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    judged = harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=_yes_judge(),
        cache=PairCache(tmp_path / "c.jsonl"),
    )
    out = tmp_path / "artifact.json"
    judged.write(out)
    blob = json.loads(out.read_text(encoding="utf-8"))
    assert blob["pool_depth"] == 10
    assert blob["judge_model"] == "fake-judge"
    assert blob["prompt_version"] == DEFAULT_PROMPT_VERSION
    assert query.work_id in blob["relevant_ids"]
    # Per-pair verdicts carry verdict + rationale.
    pairs = blob["pairs"]
    assert pairs, "expected per-pair records"
    for pair in pairs:
        assert pair["query_work_id"] == query.work_id
        assert "relevant" in pair
        assert pair["rationale"]
        assert pair["transferable_lesson"]


def test_cache_persists_incrementally_one_pair_at_a_time(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    cache_path = tmp_path / "c.jsonl"

    judge_calls = [0]

    def fn(_prompt: str) -> str:
        judge_calls[0] += 1
        return json.dumps(
            {
                "relevant": True,
                "transferable_lesson": "cert renewal",
                "rationale": "shared root cause.",
            }
        )

    harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=StubComparativeJudge(fn=fn, model="fake-judge"),
        cache=PairCache(cache_path),
    )
    # One JSONL line per judged pair, written as we go.
    lines = [ln for ln in cache_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == judge_calls[0]
    assert len(lines) >= 1


def test_cache_resumes_without_re_paying(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    cache_path = tmp_path / "c.jsonl"

    calls = [0]

    def fn(_prompt: str) -> str:
        calls[0] += 1
        return json.dumps(
            {"relevant": True, "transferable_lesson": "x", "rationale": "shared cause."}
        )

    judge = StubComparativeJudge(fn=fn, model="fake-judge")

    harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=judge,
        cache=PairCache(cache_path),
    )
    first = calls[0]
    assert first >= 1

    # Second run over a fresh cache loaded from the same file: zero new judge calls.
    harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=judge,
        cache=PairCache(cache_path),
    )
    assert calls[0] == first, "cached pairs must not be re-judged"


# --------------------------------------------------------------------------- #
# Bounded budget
# --------------------------------------------------------------------------- #
def test_budget_capped_at_pooled_union(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    judged = harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=_yes_judge(),
        cache=PairCache(tmp_path / "c.jsonl"),
    )
    pool_size = len(judged.pooled[query.work_id])
    assert judged.judged_pairs == pool_size
    assert judged.judged_pairs <= pool_size  # never exceeds the pooled union


# --------------------------------------------------------------------------- #
# Telemetry: judge identity + prompt_version recorded
# --------------------------------------------------------------------------- #
def test_telemetry_records_judge_identity(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    judged = harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=_yes_judge(),
        cache=PairCache(tmp_path / "c.jsonl"),
        stack_telemetry={"chat_model": "nomic"},
    )
    tel = judged.telemetry()
    assert tel["judge_model"] == "fake-judge"
    assert tel["judge_prompt_version"] == DEFAULT_PROMPT_VERSION
    # The semantic stack telemetry is preserved alongside the judge identity.
    assert tel["chat_model"] == "nomic"

    result = compare_arms(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        relevant_ids=judged.relevant_ids()[query.work_id],
        pool_depth=10,
        stack_telemetry=tel,
    )
    assert result.stack_telemetry["judge_model"] == "fake-judge"
    assert result.stack_telemetry["judge_prompt_version"] == DEFAULT_PROMPT_VERSION


# --------------------------------------------------------------------------- #
# Fail loud
# --------------------------------------------------------------------------- #
class _DeadJudge:
    model = "dead"

    def complete(self, _prompt: str) -> str:
        raise LocalStackUnavailableError("Ollama daemon not reachable")


def test_dead_judge_raises_local_stack_unavailable(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    with pytest.raises(LocalStackUnavailableError):
        harvest_and_judge(
            query,
            query_text,
            corpus,
            corpus_text,
            ours=ours,
            semantic=LexicalTopKMemory(top_k=10),
            pool_depth=10,
            judge=_DeadJudge(),
            cache=PairCache(tmp_path / "c.jsonl"),
        )


def test_malformed_reply_raises(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(_OURS_STUB_HITS[query.work_id]))
    bad = StubComparativeJudge(fn=lambda _p: "not json at all", model="fake-judge")
    with pytest.raises(RelevanceJudgeError):
        harvest_and_judge(
            query,
            query_text,
            corpus,
            corpus_text,
            ours=ours,
            semantic=LexicalTopKMemory(top_k=10),
            pool_depth=10,
            judge=bad,
            cache=PairCache(tmp_path / "c.jsonl"),
        )


class _FlakyThenOkJudge:
    """Raises TimeoutError on its FIRST call, then succeeds on every call — the
    transient-timeout-then-retry path. ``ours`` is harvested first, so its hit is
    pooled first; that first pair takes the timeout and is recovered by the retry."""

    model = "flaky"

    def __init__(self) -> None:
        self.calls = 0
        self.timeouts = 0

    def complete(self, _prompt: str) -> str:
        self.calls += 1
        if self.calls == 1:
            self.timeouts += 1
            raise TimeoutError("transient")
        return json.dumps(
            {"relevant": True, "transferable_lesson": "x", "rationale": "shared cause."}
        )


def test_transient_timeout_retries_once_then_succeeds(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(["tls-cert-expiry-scix"]))
    judge = _FlakyThenOkJudge()
    judged = harvest_and_judge(
        query,
        query_text,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=judge,
        cache=PairCache(tmp_path / "c.jsonl"),
    )
    # The timeout was retried (one extra call beyond the pool size) and recovered:
    # the first pooled pair (ours's hit) ends up judged relevant despite the hiccup.
    pool_size = len(judged.pooled[query.work_id])
    assert judge.timeouts == 1
    assert judge.calls == pool_size + 1
    assert "tls-cert-expiry-scix" in judged.relevant_ids()[query.work_id]


class _AlwaysTimeoutJudge:
    model = "down"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, _prompt: str) -> str:
        self.calls += 1
        raise TimeoutError("still down")


def test_persistent_timeout_surfaces_after_one_retry(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    query, query_text = queries[0]
    ours = OursMemory(store_path="<stub>", runner=_ours_stub(["tls-cert-expiry-scix"]))
    judge = _AlwaysTimeoutJudge()
    with pytest.raises(TimeoutError):
        harvest_and_judge(
            query,
            query_text,
            corpus,
            corpus_text,
            ours=ours,
            semantic=LexicalTopKMemory(top_k=10),
            pool_depth=10,
            judge=judge,
            cache=PairCache(tmp_path / "c.jsonl"),
        )
    # The FIRST pair exhausts exactly one retry (2 attempts) then surfaces — the run
    # halts there, so no further pairs are attempted (no hiding loop).
    assert judge.calls == 2


# --------------------------------------------------------------------------- #
# Multi-query merge (the driver's pooled-judge mode aggregates over all queries)
# --------------------------------------------------------------------------- #
def test_judge_relevance_merges_all_queries(tmp_path: Path) -> None:
    corpus, corpus_text, queries = _load_example()
    ours = OursMemory(
        store_path="<stub>",
        runner=_ours_stub(_OURS_STUB_HITS[queries[0][0].work_id]),
    )
    judged = judge_relevance(
        queries,
        corpus,
        corpus_text,
        ours=ours,
        semantic=LexicalTopKMemory(top_k=10),
        pool_depth=10,
        judge=_yes_judge(),
        cache=PairCache(tmp_path / "c.jsonl"),
    )
    # Every query is represented in the merged pooled + relevant maps.
    relevant = judged.relevant_ids()
    for query, _ in queries:
        assert query.work_id in judged.pooled
        assert query.work_id in relevant
    # judged_pairs equals the total pooled candidates across queries (budget bound).
    assert judged.judged_pairs == sum(len(v) for v in judged.pooled.values())
