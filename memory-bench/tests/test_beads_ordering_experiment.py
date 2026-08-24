from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from membench.beads_ordering.analysis import analyze_results, percentile
from membench.beads_ordering.client import BeadsExperimentClient, candidate_parity
from membench.beads_ordering.corpus import build_frozen_corpus
from membench.beads_ordering.models import (
    BM25FConfig,
    OrderingArm,
    OrderingRunResult,
    ToolLogEntry,
)
from membench.beads_ordering.report import render_markdown, render_page_size_svg
from membench.beads_ordering.scoring import score_agent_run
from membench.beads_ordering.tool import memory_references, visible_page


def _completed(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["bd"], 0, json.dumps(payload), "")


def test_client_uses_explicit_binary_and_exhausts_continuation() -> None:
    calls: list[tuple[list[str], str]] = []
    pages = iter(
        [
            {
                "items": [
                    {
                        "id": "m2",
                        "key": "m2",
                        "title": "B",
                        "lifecycle": "active",
                        "excerpt": "b",
                        "matched_fields": ["body"],
                        "rank": 1,
                    }
                ],
                "query": "deploy",
                "order": "bm25f",
                "page_size": 1,
                "total_matched": 2,
                "complete": False,
                "continuation": "cursor-1",
                "candidate_digest": "same",
                "bm25f": {
                    "key_weight": 6.0,
                    "alias_weight": 5.0,
                    "title_weight": 3.0,
                    "body_weight": 1.0,
                    "k1": 1.2,
                    "b": 0.75,
                },
                "schema_version": 1,
            },
            {
                "items": [
                    {
                        "id": "m1",
                        "key": "m1",
                        "title": "A",
                        "lifecycle": "active",
                        "excerpt": "a",
                        "matched_fields": ["title"],
                        "rank": 2,
                    }
                ],
                "query": "deploy",
                "order": "bm25f",
                "page_size": 1,
                "total_matched": 2,
                "complete": True,
                "candidate_digest": "same",
                "bm25f": {
                    "key_weight": 6.0,
                    "alias_weight": 5.0,
                    "title_weight": 3.0,
                    "body_weight": 1.0,
                    "k1": 1.2,
                    "b": 0.75,
                },
                "schema_version": 1,
            },
        ]
    )

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, str(kwargs["cwd"])))
        return _completed(next(pages))

    client = BeadsExperimentClient(
        beads_bin="/opt/exp/bd",
        workspace="/tmp/corpus",
        page_size=1,
        bm25f=BM25FConfig(),
        runner=runner,
    )
    result = client.exhaust("deploy", OrderingArm.BM25F)

    assert [item.id for item in result.items] == ["m2", "m1"]
    assert calls[0][0][0] == "/opt/exp/bd"
    assert calls[0][1] == "/tmp/corpus"
    assert "--experimental-order" in calls[0][0]
    assert calls[1][0][-2:] == ["--continuation", "cursor-1"]


def test_candidate_parity_allows_only_order_rank_and_continuation_to_differ() -> None:
    base_item = {
        "id": "m1",
        "key": "m1",
        "title": "Title",
        "lifecycle": "active",
        "excerpt": "same",
        "matched_fields": ["body"],
    }
    pages = {}
    for arm, rank in ((OrderingArm.KEY, 2), (OrderingArm.NAVIGATION, 1), (OrderingArm.BM25F, 3)):
        pages[arm] = {
            "items": [{**base_item, "rank": rank}],
            "total_matched": 1,
            "candidate_digest": "digest",
        }
    assert candidate_parity(pages)["candidate_ids"] == ["m1"]

    pages[OrderingArm.BM25F]["items"][0]["excerpt"] = "extra content"
    with pytest.raises(ValueError, match="projection parity"):
        candidate_parity(pages)


def test_frozen_corpus_is_nested_realistic_and_fully_labelled() -> None:
    first = build_frozen_corpus(seed=5877)
    second = build_frozen_corpus(seed=5877)
    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.memories) == 500
    assert {task.corpus_size for task in first.tasks} == {50, 100, 500}
    assert len(first.tasks) >= 12

    by_id = {memory.id: memory for memory in first.memories}
    for task in first.tasks:
        corpus = first.memories[: task.corpus_size]
        matches = {
            memory.id
            for memory in corpus
            if task.query.lower() in memory.key.lower()
            or task.query.lower() in memory.stored_value().lower()
        }
        labelled = {
            task.primary_relevant,
            *task.acceptable_entry_points,
            *task.distractors,
        }
        assert matches == labelled
        assert task.primary_relevant in by_id
        assert by_id[task.primary_relevant].body
        assert task.acceptable_entry_points
        assert any(by_id[mid].lifecycle == "archived" for mid in task.distractors)
        assert any(by_id[mid].provenance == "agent" for mid in matches)
        assert any(by_id[mid].provenance == "human" for mid in matches)


def test_score_agent_run_accounts_to_first_useful_memory() -> None:
    logs = [
        ToolLogEntry(
            sequence=1,
            operation="search",
            started_at="2026-01-01T00:00:00Z",
            elapsed_ms=10,
            response_bytes=300,
            response_tokens_estimate=75,
            visible_ids=["d1", "entry"],
            total_matched=8,
        ),
        ToolLogEntry(
            sequence=2,
            operation="recall",
            started_at="2026-01-01T00:00:01Z",
            elapsed_ms=5,
            response_bytes=400,
            response_tokens_estimate=100,
            memory_id="entry",
            references=["primary"],
        ),
        ToolLogEntry(
            sequence=3,
            operation="recall",
            started_at="2026-01-01T00:00:02Z",
            elapsed_ms=6,
            response_bytes=500,
            response_tokens_estimate=125,
            memory_id="primary",
        ),
    ]
    result = score_agent_run(
        task_id="t1",
        corpus_size=100,
        arm=OrderingArm.NAVIGATION,
        repeat=0,
        primary_id="primary",
        acceptable_ids={"entry"},
        expected_facts=["LEASE_TTL=90s"],
        forbidden_facts=["LEASE_TTL=30s"],
        final_answer="Use the corrected lease.\nDECISION: LEASE_TTL=90s",
        logs=logs,
        input_tokens=900,
        output_tokens=60,
        end_to_end_ms=2500,
        primary_rank=7,
        acceptable_rank=2,
        page_size=2,
    )
    assert result.pages_requested == 1
    assert result.compact_records_visible == 2
    assert result.compact_result_bytes == 300
    assert result.full_recalls == 2
    assert result.first_recalled_relevant is True
    assert result.graph_hops_after_first_useful == 1
    assert result.retrieval_related_tokens == 300
    assert result.task_success is True
    assert result.premature_stop is False


def test_score_reaches_useful_memory_through_reference_recall() -> None:
    logs = [
        ToolLogEntry(
            sequence=1,
            operation="search",
            started_at="2026-01-01T00:00:00Z",
            elapsed_ms=10,
            response_bytes=100,
            response_tokens_estimate=25,
            visible_ids=["distractor"],
            total_matched=20,
        ),
        ToolLogEntry(
            sequence=2,
            operation="recall",
            started_at="2026-01-01T00:00:01Z",
            elapsed_ms=5,
            response_bytes=200,
            response_tokens_estimate=50,
            memory_id="primary",
        ),
        ToolLogEntry(
            sequence=3,
            operation="recall",
            started_at="2026-01-01T00:00:02Z",
            elapsed_ms=4,
            response_bytes=50,
            response_tokens_estimate=13,
            error="failed recall",
        ),
    ]
    result = score_agent_run(
        task_id="graph-entry",
        corpus_size=100,
        arm=OrderingArm.KEY,
        repeat=0,
        primary_id="primary",
        acceptable_ids=set(),
        expected_facts=["SAFE=1"],
        forbidden_facts=[],
        final_answer="DECISION: SAFE=1",
        logs=logs,
        input_tokens=100,
        output_tokens=10,
        end_to_end_ms=100,
        primary_rank=20,
        acceptable_rank=None,
        page_size=5,
    )
    assert result.pages_to_first_useful == 1
    assert result.time_to_first_useful_ms == 15
    assert result.full_recalls == 1
    assert result.premature_stop is False


def test_agent_page_projection_hides_order_and_scorer_configuration() -> None:
    raw = {
        "items": [{"id": "m1", "rank": 1}],
        "query": "deploy",
        "order": "bm25f",
        "page_size": 5,
        "unbounded": False,
        "total_matched": 12,
        "complete": False,
        "continuation": "next",
        "candidate_digest": "secret",
        "bm25f": {"key_weight": 6},
        "candidate_generation_ms": 1.2,
        "ordering_ms": 0.8,
    }
    shown = visible_page(raw, page_size_label="5")
    assert shown == {
        "items": [{"id": "m1", "rank": 1}],
        "query": "deploy",
        "total_matched": 12,
        "page_size": "5",
        "complete": False,
        "continuation": "next",
    }
    assert memory_references("---\nreferences: [m2, task-1]\n---\nbody") == (
        "m2",
        "task-1",
    )


def test_analysis_reports_distributions_strata_and_burial_correlation(tmp_path: Path) -> None:
    rows = []
    for index, (arm, pages, tokens) in enumerate(
        [
            (OrderingArm.KEY, 1, 100),
            (OrderingArm.KEY, 4, 400),
            (OrderingArm.BM25F, 1, 100),
            (OrderingArm.BM25F, 2, 200),
        ]
    ):
        rows.append(
            OrderingRunResult(
                run_id=f"r{index}",
                task_id=f"t{index % 2}",
                corpus_size=50,
                arm=arm,
                repeat=0,
                page_size="5",
                total_matched=20,
                primary_rank=8,
                primary_page=4,
                acceptable_rank=2,
                acceptable_page=1,
                pages_requested=pages,
                pages_to_first_useful=pages,
                page_one_acceptable_visible=True,
                compact_records_visible=pages * 5,
                compact_result_bytes=tokens * 4,
                compact_result_tokens=tokens,
                retrieval_tool_calls=pages,
                time_to_first_useful_ms=100 * pages,
                full_recalls=1,
                first_recalled_relevant=True,
                graph_hops_after_first_useful=0,
                retrieval_related_tokens=tokens,
                retrieval_latency_ms=20 * pages,
                server_candidate_generation_ms=5 * pages,
                server_ordering_ms=2 * pages,
                end_to_end_ms=1000 * pages,
                task_success=True,
                abstained=False,
                premature_stop=False,
                agent_input_tokens=1000,
                agent_output_tokens=100,
            )
        )
    report = analyze_results(rows)
    assert percentile([1, 2, 10], 0.5) == 2
    assert report["by_arm"]["key"]["pages_requested"]["p50"] == 2.5
    assert "baseline_burial_correlations" in report
    assert report["largest_page_size_with_material_ranking_gap"] == 5
    assert report["mechanical_vs_bm25f"][0]["material_gap"] is True
    assert report["strata"]
    markdown = render_markdown(rows, report)
    assert "Server-side matching/scoring time" in markdown
    assert "Mechanical versus BM25F crossover" in markdown
    svg = render_page_size_svg(report)
    assert svg.startswith("<svg")
    assert "page size" in svg
