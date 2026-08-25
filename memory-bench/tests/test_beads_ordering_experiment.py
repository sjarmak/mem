from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

import membench.beads_ordering.tool as ordering_tool
from membench.beads_ordering.analysis import analyze_results, percentile
from membench.beads_ordering.client import BeadsExperimentClient, candidate_parity
from membench.beads_ordering.corpus import build_frozen_corpus
from membench.beads_ordering.models import (
    BM25FConfig,
    ExperimentMode,
    MemoryFixture,
    OrderingArm,
    OrderingRunResult,
    ToolLogEntry,
)
from membench.beads_ordering.ranked_searching import (
    RANKED_SEARCHING_PRIORS,
    enrich_with_ranked_searching,
)
from membench.beads_ordering.report import render_markdown, render_page_size_svg
from membench.beads_ordering.runner import git_diff_sha256
from membench.beads_ordering.scoring import score_agent_run
from membench.beads_ordering.tool import ToolConfig, memory_references, visible_page
from membench.cli import _repeat_indices, _select_ordering_tasks


def _completed(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["bd"], 0, json.dumps(payload), "")


def test_repeat_indices_support_targeted_repeat_offsets() -> None:
    assert list(_repeat_indices(start=0, count=1)) == [0]
    assert list(_repeat_indices(start=1, count=2)) == [1, 2]
    with pytest.raises(ValueError, match="non-negative"):
        _repeat_indices(start=-1, count=2)
    with pytest.raises(ValueError, match="positive"):
        _repeat_indices(start=1, count=0)


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
    pages: dict[OrderingArm, dict[str, Any]] = {}
    for arm, rank in (
        (OrderingArm.KEY, 2),
        (OrderingArm.PAGERANK, 1),
        (OrderingArm.BM25F, 3),
    ):
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
    assert len(first.tasks) == 36

    development = [task for task in first.tasks if task.split == "development"]
    heldout = [task for task in first.tasks if task.split == "heldout"]
    assert len(development) == 12
    assert len(heldout) == 24
    assert all(task.source_kind == "authored" for task in development)
    assert sum(task.source_kind == "sanitized-real-derived" for task in heldout) >= 12
    assert all(task.source_shape for task in heldout)
    assert all(
        len(task.source_provenance_hash) == 16
        for task in heldout
        if task.source_kind == "sanitized-real-derived"
    )
    assert {size: sum(task.corpus_size == size for task in heldout) for size in (50, 100, 500)} == {
        50: 8,
        100: 8,
        500: 8,
    }

    by_id = {memory.id: memory for memory in first.memories}
    for size in (50, 100, 500):
        prefix_ids = {memory.id for memory in first.memories[:size]}
        assert all(set(memory.references) <= prefix_ids for memory in first.memories[:size])
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


def test_structural_finalists_were_registered_without_heldout_outcomes() -> None:
    path = (
        Path(__file__).resolve().parents[1] / "results" / "beads_ordering" / "preregistration.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["status"] == "locked-before-heldout-ranking"
    assert payload["development_task_count"] == 12
    assert len(payload["screened_priors"]) == 6
    assert payload["selection_rules"]["heldout_outcomes_used"] is False
    assert payload["selected_finalists"] == {
        "entry_point_oriented": "reverse-pagerank",
        "branch_heavy": "hits-hub",
    }
    assert payload["agent_matrix"]["arms"] == [
        "key",
        "reverse-pagerank",
        "hits-hub",
        "bm25f",
    ]
    assert payload["agent_matrix"]["page_sizes"] == ["5", "10", "20", "all"]


def test_ordering_task_selection_can_hold_development_tasks_out_of_agent_runs() -> None:
    corpus = build_frozen_corpus(seed=5877)

    heldout = _select_ordering_tasks(corpus, task_ids_raw="", split_raw="heldout")
    assert len(heldout) == 24
    assert all(task.split == "heldout" for task in heldout)

    selected = _select_ordering_tasks(
        corpus,
        task_ids_raw=f"{heldout[0].task_id},{heldout[-1].task_id}",
        split_raw="heldout",
    )
    assert [task.task_id for task in selected] == [heldout[0].task_id, heldout[-1].task_id]

    with pytest.raises(SystemExit, match="does not match task split"):
        _select_ordering_tasks(
            corpus,
            task_ids_raw=corpus.tasks[0].task_id,
            split_raw="heldout",
        )


def test_ranked_searching_orders_are_materialized_per_nested_corpus() -> None:
    corpus = build_frozen_corpus(seed=5877)

    def fake_orders(
        memories: tuple[MemoryFixture, ...], *, artifact_repo: Path
    ) -> dict[str, tuple[str, ...]]:
        assert artifact_repo == Path("/artifact")
        ids = tuple(memory.id for memory in reversed(memories))
        return dict.fromkeys(RANKED_SEARCHING_PRIORS, ids)

    enriched = enrich_with_ranked_searching(
        corpus, artifact_repo=Path("/artifact"), order_fn=fake_orders
    )
    first = enriched.memories[0]
    assert first.structural_ranks_by_corpus["50"]["pagerank"] == 50
    assert first.structural_ranks_by_corpus["500"]["hits-hub"] == 500
    stored = first.stored_value(50)
    assert "structural_rank_pagerank: 50" in stored
    assert "structural_rank_hits_hub: 50" in stored


def test_score_agent_run_accounts_to_first_useful_memory() -> None:
    logs = [
        ToolLogEntry(
            sequence=1,
            operation="search",
            started_at="2026-01-01T00:00:00Z",
            elapsed_ms=10,
            response_bytes=300,
            response_tokens_estimate=75,
            visible_ids=("d1", "entry"),
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
            references=("primary",),
        ),
        ToolLogEntry(
            sequence=3,
            operation="recall",
            started_at="2026-01-01T00:00:02Z",
            elapsed_ms=6,
            response_bytes=500,
            response_tokens_estimate=125,
            memory_id="primary",
            followed_reference=True,
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
        mem_git_diff_sha256="mem-diff",
        beads_git_diff_sha256="beads-diff",
    )
    assert result.pages_requested == 1
    assert result.compact_records_visible == 2
    assert result.compact_result_bytes == 300
    assert result.compact_tokens_to_first_useful == 75
    assert result.retrieval_tokens_to_first_useful == 75
    assert result.tool_calls_to_first_useful == 1
    assert result.full_recalls == 2
    assert result.first_recalled_relevant is True
    assert result.graph_hops_after_first_useful == 1
    assert result.retrieval_related_tokens == 300
    assert result.task_success is True
    assert result.premature_stop is False
    assert result.mem_git_diff_sha256 == "mem-diff"
    assert result.beads_git_diff_sha256 == "beads-diff"


def test_git_diff_digest_pins_tracked_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "eval@example.invalid"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Eval"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)

    empty = git_diff_sha256(tmp_path)
    assert empty == hashlib.sha256(b"").hexdigest()
    tracked.write_text("after\n", encoding="utf-8")
    changed = git_diff_sha256(tmp_path)
    assert changed != empty
    assert git_diff_sha256(tmp_path) == changed


def test_score_reaches_useful_memory_through_reference_recall() -> None:
    logs = [
        ToolLogEntry(
            sequence=1,
            operation="search",
            started_at="2026-01-01T00:00:00Z",
            elapsed_ms=10,
            response_bytes=100,
            response_tokens_estimate=25,
            visible_ids=("distractor",),
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
    assert result.compact_tokens_to_first_useful == 25
    assert result.retrieval_tokens_to_first_useful == 75
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
        "continuation_available": True,
    }
    assert memory_references("---\nreferences: [m2, task-1]\n---\nbody") == (
        "m2",
        "task-1",
    )


def test_search_words_cannot_change_frozen_query_or_become_a_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_bd(_config: ToolConfig, arguments: list[str]) -> dict[str, object]:
        seen.append(arguments)
        return {
            "items": [],
            "query": "frozen query",
            "total_matched": 0,
            "complete": True,
        }

    monkeypatch.setattr(ordering_tool, "_bd", fake_bd)
    config = ToolConfig(
        beads_bin="/opt/bd",
        workspace=str(tmp_path),
        query="frozen query",
        arm=OrderingArm.KEY,
        page_size=5,
        log_path=str(tmp_path / "tool.jsonl"),
        agent_started_monotonic_ns=0,
    )
    payload, _ = ordering_tool.execute(config, "search", "agent supplied different words")
    assert payload["query"] == "frozen query"
    assert "--continuation" not in seen[0]
    with pytest.raises(ordering_tool.BeadsToolError, match="already performed"):
        ordering_tool.execute(config, "search")


def test_tool_retains_exact_continuation_without_model_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_bd(_config: ToolConfig, arguments: list[str]) -> dict[str, object]:
        seen.append(arguments)
        return {
            "items": [],
            "query": "frozen query",
            "total_matched": 10,
            "complete": len(seen) == 2,
            "continuation": "cursor-exact" if len(seen) == 1 else "",
        }

    monkeypatch.setattr(ordering_tool, "_bd", fake_bd)
    config = ToolConfig(
        beads_bin="/opt/bd",
        workspace=str(tmp_path),
        query="frozen query",
        arm=OrderingArm.KEY,
        page_size=5,
        log_path=str(tmp_path / "tool.jsonl"),
        agent_started_monotonic_ns=0,
    )
    first, first_log = ordering_tool.execute(config, "search")
    assert first["continuation_available"] is True
    assert first_log.continuation == "cursor-exact"
    second, _ = ordering_tool.execute(config, "continue")
    assert second["complete"] is True
    assert seen[1][-2:] == ["--continuation", "cursor-exact"]


def test_tool_enforces_search_only_and_reference_navigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_bd(_config: ToolConfig, arguments: list[str]) -> dict[str, object]:
        if arguments[0] == "memories":
            return {
                "items": [{"id": "entry"}],
                "query": "frozen query",
                "total_matched": 2,
                "complete": True,
            }
        memory_id = arguments[1]
        references = "[primary]" if memory_id == "entry" else "[]"
        return {
            "key": memory_id,
            "found": True,
            "value": f"---\nreferences: {references}\n---\nbody",
        }

    monkeypatch.setattr(ordering_tool, "_bd", fake_bd)
    search_only = ToolConfig(
        beads_bin="/opt/bd",
        workspace=str(tmp_path),
        query="frozen query",
        arm=OrderingArm.KEY,
        page_size=5,
        agent_started_monotonic_ns=0,
        mode=ExperimentMode.SEARCH_ONLY,
        log_path=str(tmp_path / "search-only.jsonl"),
    )
    ordering_tool.execute(search_only, "search")
    ordering_tool.execute(search_only, "recall", "entry")
    with pytest.raises(ordering_tool.BeadsToolError, match="search-only"):
        ordering_tool.execute(search_only, "recall", "primary")
    search_only_logs = ordering_tool._existing_logs(Path(search_only.log_path))
    assert not any(entry.followed_reference for entry in search_only_logs)

    navigation = ToolConfig(
        beads_bin="/opt/bd",
        workspace=str(tmp_path),
        query="frozen query",
        arm=OrderingArm.KEY,
        page_size=5,
        agent_started_monotonic_ns=0,
        mode=ExperimentMode.NAVIGATION,
        log_path=str(tmp_path / "navigation.jsonl"),
    )
    ordering_tool.execute(navigation, "search")
    ordering_tool.execute(navigation, "recall", "entry")
    _, followed = ordering_tool.execute(navigation, "recall", "primary")
    assert followed.followed_reference is True


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
    report = cast(dict[str, Any], analyze_results(rows))
    assert percentile([1, 2, 10], 0.5) == 2
    assert report["by_arm"]["key"]["pages_requested"]["p50"] == 2.5
    assert "baseline_burial_correlations" in report
    assert report["largest_page_size_with_material_ranking_gap"] == {"navigation": 5}
    assert report["mechanical_vs_bm25f"][0]["material_gap"] is True
    paired = next(
        row
        for row in report["paired_policy_vs_bm25f"]
        if row["policy"] == "key" and row["page_size"] == "5"
    )
    assert paired["n_tasks"] == 2
    assert paired["n_pairs"] == 2
    assert paired["pages_saved_by_bm25f"]["p50"] == 1
    assert report["mechanism_recommendation"]["status"] == "insufficient-data"
    assert report["strata"]
    markdown = render_markdown(rows, report)
    assert "Server-side matching/scoring time" in markdown
    assert "Mechanical versus BM25F crossover" in markdown
    assert "Paired task-clustered policy deltas" in markdown
    assert "Pre-registered mechanism gate" in markdown
    svg = render_page_size_svg(report)
    assert svg.startswith("<svg")
    assert "page size" in svg


def test_mechanism_recommendation_requires_navigation_advantage() -> None:
    def grid(*, navigation_advantage: bool) -> list[OrderingRunResult]:
        rows: list[OrderingRunResult] = []
        policies = (
            OrderingArm.KEY,
            OrderingArm.REVERSE_PAGERANK,
            OrderingArm.HITS_HUB,
            OrderingArm.BM25F,
        )
        for task_index in range(24):
            for mode in (ExperimentMode.SEARCH_ONLY, ExperimentMode.NAVIGATION):
                for page_size in ("5", "10", "20", "all"):
                    for arm in policies:
                        bounded = page_size != "all"
                        bm25f_advantage = (
                            arm is OrderingArm.BM25F
                            and bounded
                            and (mode is ExperimentMode.SEARCH_ONLY or navigation_advantage)
                        )
                        pages = 1 if bm25f_advantage or not bounded else 2
                        tokens = 100 if bm25f_advantage or not bounded else 200
                        rows.append(
                            OrderingRunResult(
                                run_id=(f"t{task_index}:{mode.value}:{arm.value}:p{page_size}:r0"),
                                task_id=f"t{task_index}",
                                corpus_size=(50, 100, 500)[task_index % 3],
                                arm=arm,
                                mode=mode,
                                repeat=0,
                                page_size=page_size,
                                total_matched=40,
                                primary_rank=8,
                                primary_page=2,
                                acceptable_rank=2,
                                acceptable_page=1,
                                pages_requested=pages,
                                pages_to_first_useful=pages,
                                page_one_acceptable_visible=True,
                                compact_records_visible=pages * 5,
                                compact_result_bytes=tokens * 4,
                                compact_result_tokens=tokens,
                                compact_tokens_to_first_useful=tokens,
                                retrieval_tokens_to_first_useful=tokens,
                                tool_calls_to_first_useful=pages,
                                retrieval_tool_calls=pages,
                                time_to_first_useful_ms=pages * 100,
                                full_recalls=1,
                                first_recalled_relevant=True,
                                graph_hops_after_first_useful=0,
                                retrieval_related_tokens=tokens,
                                retrieval_latency_ms=pages * 20,
                                server_candidate_generation_ms=5,
                                server_ordering_ms=2,
                                end_to_end_ms=pages * 1000,
                                task_success=True,
                                abstained=False,
                                premature_stop=False,
                                agent_input_tokens=1000,
                                agent_output_tokens=100,
                            )
                        )
        return rows

    earned = analyze_results(grid(navigation_advantage=True))["mechanism_recommendation"]
    assert earned["initial_grid_complete"] is True
    assert earned["status"] == "recommend-bm25f-ownership"

    erased = analyze_results(grid(navigation_advantage=False))["mechanism_recommendation"]
    assert erased["initial_grid_complete"] is True
    assert erased["status"] == "prefer-query-independent-ordering-navigation"
    assert erased["navigation_advantage_material"] is False
