from __future__ import annotations

import json
from pathlib import Path

import pytest

from membench.beads_ordering import followup_evidence
from membench.beads_ordering.followup_corpus import (
    FAILURE_CASES,
    GRAPH_FAMILIES,
    apply_control_order,
    build_followup_corpora,
    enrich_followup_corpora,
    load_followup_corpora,
    materialize_control_ranks,
    write_followup_corpora,
)
from membench.beads_ordering.followup_evidence import (
    analyze_agent_followup,
    analyze_oracle_rows,
    collect_oracle_evidence,
    depth_first_navigation,
    oracle_page_metrics,
    render_agent_followup_svg,
    render_oracle_report,
    render_oracle_svg,
    summarize_oracle_rows,
    write_oracle_evidence,
)
from membench.beads_ordering.models import (
    BM25FConfig,
    CompactMemory,
    ControlIntent,
    ControlPolicy,
    DiscoveryPage,
    ExhaustedDiscovery,
    ExperimentMode,
    FrozenCorpus,
    MemoryFixture,
    OrderingArm,
    OrderingRunResult,
    OrderingTask,
    TaskSplit,
)
from membench.beads_ordering.mutation import (
    ContinuationEpoch,
    MutationKind,
    RankRefreshPolicy,
    apply_mutation,
    benchmark_rank_scaling,
    build_graph_state,
    build_mutation_schedule,
    candidate_ids,
    continuation_is_valid,
    rank_order,
    replay_rank_refresh,
    run_mutation_experiment,
    scale_graph_state,
    write_mutation_experiment,
    write_rank_scaling,
)
from membench.beads_ordering.runner import CONTROL_ARMS, task_workspace

ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION = ROOT / "fixtures" / "beads_ordering" / "structural-followup-preregistration.json"


def test_targeted_repeat_groups_use_only_repeat_zero_disagreement_or_failure() -> None:
    def row(
        task: str,
        arm: OrderingArm,
        success: bool,
        *,
        repeat: int = 0,
        failure: str | None = None,
    ) -> OrderingRunResult:
        return OrderingRunResult.model_construct(
            task_id=task,
            arm=arm,
            mode=ExperimentMode.NAVIGATION,
            page_size="5",
            repeat=repeat,
            task_success=success,
            failure=failure,
        )

    rows = [
        row("stable", OrderingArm.KEY, True),
        row("stable", OrderingArm.BM25F, True),
        row("disagrees", OrderingArm.KEY, False),
        row("disagrees", OrderingArm.BM25F, True),
        row("failed", OrderingArm.KEY, False, failure="tool failed"),
        row("failed", OrderingArm.BM25F, False),
        row("repeat-only", OrderingArm.KEY, False),
        row("repeat-only", OrderingArm.BM25F, False),
        row("repeat-only", OrderingArm.BM25F, True, repeat=1),
    ]

    selected = followup_evidence.select_targeted_repeat_groups(rows)

    assert selected == [
        {
            "task_id": "disagrees",
            "mode": "navigation",
            "page_size": "5",
            "reasons": ["task-success-disagreement"],
            "repeat_indices": [1, 2],
        },
        {
            "task_id": "failed",
            "mode": "navigation",
            "page_size": "5",
            "reasons": ["infrastructure-failure"],
            "repeat_indices": [1, 2],
        },
    ]


def test_agent_followup_analysis_averages_repeats_before_policy_aggregation() -> None:
    def row(arm: OrderingArm, repeat: int, *, success: bool, tokens: int) -> OrderingRunResult:
        return OrderingRunResult.model_construct(
            run_id=f"task:navigation:{arm.value}:p5:r{repeat}",
            task_id="task",
            arm=arm,
            mode=ExperimentMode.NAVIGATION,
            page_size="5",
            repeat=repeat,
            corpus_size=50,
            total_matched=24,
            primary_page=2,
            acceptable_page=1,
            page_one_acceptable_visible=True,
            pages_requested=1,
            compact_tokens_to_first_useful=tokens,
            retrieval_tokens_to_first_useful=tokens + 10,
            tool_calls_to_first_useful=1,
            retrieval_tool_calls=2,
            time_to_first_useful_ms=1000.0,
            full_recalls=1,
            graph_hops_total=0,
            branching_factor_mean=0.0,
            retrieval_latency_ms=20.0,
            server_candidate_generation_ms=4.0,
            server_ordering_ms=2.0,
            end_to_end_ms=1200.0,
            task_success=success,
            abstained=False,
            premature_stop=False,
            failure=None,
        )

    rows = [
        row(OrderingArm.KEY, 0, success=False, tokens=300),
        row(OrderingArm.KEY, 1, success=True, tokens=100),
        row(OrderingArm.BM25F, 0, success=True, tokens=100),
        row(OrderingArm.BM25F, 1, success=True, tokens=100),
    ]
    analysis = analyze_agent_followup(
        rows,
        {"task": {"graph_family": "family", "failure_case": "case"}},
        bootstrap_resamples=100,
    )

    assert analysis["observation_count"] == 4
    assert analysis["cell_count"] == 2
    key = next(curve for curve in analysis["curves"] if curve["arm"] == "key")
    assert key["task_success_rate"]["estimate"] == 0.5
    assert key["compact_tokens_to_first_useful"]["p50"] == 200
    comparison = next(
        item
        for item in analysis["paired_policy_comparisons"]
        if item["reference"] == "key" and item["contender"] == "bm25f"
    )
    assert comparison["compact_token_reduction_fraction"]["p50"] == 0.5


def test_agent_followup_svg_plots_repeat_balanced_page_curves() -> None:
    analysis = {
        "curves": [
            {
                "mode": "navigation",
                "arm": "key",
                "page_size": "5",
                "pages_requested": {"p50": 2.0},
            },
            {
                "mode": "navigation",
                "arm": "bm25f",
                "page_size": "5",
                "pages_requested": {"p50": 1.0},
            },
            {
                "mode": "navigation",
                "arm": "key",
                "page_size": "all",
                "pages_requested": {"p50": 1.0},
            },
            {
                "mode": "search-only",
                "arm": "key",
                "page_size": "5",
                "pages_requested": {"p50": 3.0},
            },
        ]
    }

    svg = render_agent_followup_svg(analysis, mode="navigation")

    assert "Repeat-balanced pages consumed" in svg
    assert "key" in svg
    assert "bm25f" in svg
    assert "search-only" not in svg
    assert ">all<" in svg


def test_followup_preregistration_locks_decisions_before_new_outcomes() -> None:
    """A maintainer can audit what evidence may change each architecture decision."""

    payload = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["status"] == "locked-before-followup-outcomes"
    assert payload["new_outcomes_examined"] is False
    assert payload["independent_unit"] == "task-within-graph-family"
    assert payload["prior_experiment"] == {
        "fixture_sha256": "aff439bc0aea511777c038212135d8fb6d04cd8dedd5c7352f1096c2e345e333",
        "analysis_sha256": "be638a2ba114e601345397283da90a00dbfc35e988725683aad0ced7fdaeda27",
        "preregistration_sha256": (
            "5224e52033b08de9a6011f8b061abccd80bb35289eb4518e92f061278c4f1082"
        ),
        "tracked_diff_sha256": ("82623e6c7a13e89d9574dfc1dc9095dda7f76a9647401a84f64ef41aad22b14b"),
    }

    matrix = payload["retrieval_matrix"]
    assert set(matrix["policies"]) == {
        "key",
        "indegree",
        "outdegree",
        "pagerank",
        "reverse-pagerank",
        "bm25f",
    }
    assert matrix["page_sizes"] == ["5", "10", "20", "all"]
    assert matrix["modes"] == ["search-only", "navigation"]
    assert matrix["candidate_membership_must_match"] is True

    assert set(payload["structural_failure_cases"]) == {
        "archived-or-stale-hub",
        "new-unlinked-relevant-memory",
        "reference-cycle",
        "disconnected-component",
        "high-outdegree-distractor",
        "superseding-chain",
        "link-inflation",
    }
    assert set(payload["mutation_replay"]["rank_refresh_policies"]) == {
        "exact-global",
        "periodic-5",
        "periodic-20",
        "stale-until-read",
        "incremental-if-feasible",
    }
    assert payload["control_matrix"]["policies"] == [
        "automatic",
        "pin-boost-demote",
        "strategy-selection",
        "raw-numeric-rank",
    ]

    decisions = payload["decision_thresholds"]
    assert set(decisions) == {
        "default_structural_order",
        "query_specific_ranking_ownership",
        "rank_freshness",
        "control_surface",
        "specialized_plumbing",
    }
    assert decisions["default_structural_order"]["task_success_noninferiority_margin"] == 0.05
    assert decisions["query_specific_ranking_ownership"]["median_pages_saved"] == 1.0
    assert decisions["query_specific_ranking_ownership"]["median_compact_token_saving"] == 0.2
    assert decisions["rank_freshness"]["max_p90_extra_pages"] == 1.0

    metric_decisions = payload["metrics_to_decisions"]
    required_metrics = {
        "page_one_useful_probability",
        "pages_to_first_useful",
        "compact_tokens_to_first_useful",
        "task_success",
        "top_k_rank_overlap",
        "rank_refresh_ms",
        "mutation_latency_ms",
        "continuation_invalidations",
        "manual_interventions",
        "implementation_complexity",
    }
    assert required_metrics <= set(metric_decisions)
    assert all(metric_decisions[name] for name in required_metrics)

    assert payload["analysis"]["cluster_by"] == ["graph_family", "task_id"]
    assert payload["analysis"]["report_confidence_intervals"] is True
    assert payload["analysis"]["report_p50_p90"] is True
    assert "production Memory schema redesign" in payload["out_of_scope"]


def test_followup_minimal_change_plan_is_linked_from_preregistration() -> None:
    payload = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    plan = ROOT.parent / payload["minimal_change_plan"]

    text = plan.read_text(encoding="utf-8")
    assert "# Structural Memory ordering follow-up" in text
    assert "## Minimal change plan" in text
    assert "No new production search index" in text
    assert "structural-followup-preregistration.json" in text


def _literal_matches(memories: tuple[MemoryFixture, ...], query: str) -> set[str]:
    lowered = query.lower()
    return {
        memory.id
        for memory in memories
        if lowered in memory.key.lower() or lowered in memory.stored_value().lower()
    }


def test_followup_corpora_are_independent_deterministic_and_fully_labelled() -> None:
    first = build_followup_corpora(seed=5878)
    second = build_followup_corpora(seed=5878)

    assert first.keys() == second.keys() == GRAPH_FAMILIES.keys()
    assert {corpus.model_dump_json() for corpus in first.values()} == {
        corpus.model_dump_json() for corpus in second.values()
    }
    assert len(first) == 7
    assert {spec.failure_case for spec in GRAPH_FAMILIES.values()} == set(FAILURE_CASES)

    heldout_count = 0
    development_count = 0
    for family, corpus in first.items():
        assert len(corpus.memories) == 500
        assert len(corpus.tasks) == 4
        assert {task.graph_family for task in corpus.tasks} == {family}
        assert {task.failure_case for task in corpus.tasks} == {GRAPH_FAMILIES[family].failure_case}
        assert {task.corpus_size for task in corpus.tasks if task.split is TaskSplit.HELDOUT} == {
            50,
            100,
            500,
        }
        heldout_count += sum(task.split is TaskSplit.HELDOUT for task in corpus.tasks)
        development_count += sum(task.split is TaskSplit.DEVELOPMENT for task in corpus.tasks)

        memory_ids = {memory.id for memory in corpus.memories}
        for memory in corpus.memories:
            assert set(memory.references) <= memory_ids
        for task in corpus.tasks:
            nested = corpus.memories[: task.corpus_size]
            labelled = {
                task.primary_relevant,
                *task.acceptable_entry_points,
                *task.distractors,
            }
            assert _literal_matches(nested, task.query) == labelled
            assert labelled <= {memory.id for memory in nested}
            assert task.control_intent is not None
            controlled = {
                *task.control_intent.pin,
                *task.control_intent.boost,
                *task.control_intent.demote,
                *task.control_intent.raw_numeric_ranks,
            }
            assert controlled <= labelled

    assert heldout_count == 21
    assert development_count == 7


def test_followup_corpora_cover_weak_titles_navigation_and_lifecycle_edges() -> None:
    corpora = build_followup_corpora(seed=5878)

    assert any(
        task.query.lower()
        not in next(
            memory.title.lower() for memory in corpus.memories if memory.id == task.primary_relevant
        )
        for corpus in corpora.values()
        for task in corpus.tasks
    )
    assert any(
        memory.lifecycle == "archived" for corpus in corpora.values() for memory in corpus.memories
    )
    assert any(
        memory.provenance == "agent" for corpus in corpora.values() for memory in corpus.memories
    )
    assert any(
        memory.provenance == "human" for corpus in corpora.values() for memory in corpus.memories
    )
    assert all(task.acceptable_entry_points for corpus in corpora.values() for task in corpus.tasks)


def test_followup_structural_orders_are_materialized_per_independent_corpus() -> None:
    corpora = build_followup_corpora(seed=5878)

    def fake_orders(
        memories: tuple[MemoryFixture, ...], *, artifact_repo: Path
    ) -> dict[str, tuple[str, ...]]:
        assert artifact_repo == Path("/structural-source")
        ids = tuple(memory.id for memory in reversed(memories))
        return dict.fromkeys(
            ("indegree", "outdegree", "pagerank", "reverse-pagerank", "hits-authority", "hits-hub"),
            ids,
        )

    enriched = enrich_followup_corpora(
        corpora,
        artifact_repo=Path("/structural-source"),
        order_fn=fake_orders,
    )

    for corpus in enriched.values():
        for size in (50, 100, 500):
            ranks = [
                memory.structural_ranks_by_corpus[str(size)]["reverse-pagerank"]
                for memory in corpus.memories[:size]
            ]
            assert sorted(ranks) == list(range(1, size + 1))


def test_control_ranks_are_task_specific_complete_and_executable() -> None:
    corpus = build_followup_corpora(seed=5878)["platform-documentation-hub-spoke"]

    def fake_orders(
        memories: tuple[MemoryFixture, ...], *, artifact_repo: Path
    ) -> dict[str, tuple[str, ...]]:
        del artifact_repo
        ids = tuple(memory.id for memory in memories)
        return {
            "indegree": ids[1:] + ids[:1],
            "outdegree": ids[-1:] + ids[:-1],
            "pagerank": tuple(reversed(ids)),
            "reverse-pagerank": ids,
            "hits-authority": ids[2:] + ids[:2],
            "hits-hub": ids[3:] + ids[:3],
        }

    enriched = enrich_followup_corpora(
        {"platform-documentation-hub-spoke": corpus},
        artifact_repo=Path("/structural-source"),
        order_fn=fake_orders,
    )["platform-documentation-hub-spoke"]
    controlled = materialize_control_ranks(enriched)

    for task in controlled.tasks:
        assert task.control_intent is not None
        memories = controlled.memories[: task.corpus_size]
        for arm in (
            OrderingArm.CONTROL_AUTOMATIC,
            OrderingArm.CONTROL_SEMANTIC,
            OrderingArm.CONTROL_STRATEGY,
            OrderingArm.CONTROL_RAW,
        ):
            ranks = [memory.control_ranks_by_task[task.task_id][arm.value] for memory in memories]
            assert sorted(ranks) == list(range(1, task.corpus_size + 1))

        first = memories[0].stored_value(task.corpus_size, control_task_id=task.task_id)
        assert "structural_rank_control_automatic:" in first
        assert "structural_rank_control_semantic:" in first
        assert "structural_rank_control_strategy:" in first
        assert "structural_rank_control_raw:" in first
        assert "structural_rank_control_automatic:" not in memories[0].stored_value(
            task.corpus_size
        )

        base = tuple(
            memory.id
            for memory in sorted(
                memories,
                key=lambda memory: memory.structural_ranks_by_corpus[str(task.corpus_size)][
                    "reverse-pagerank"
                ],
            )
        )
        automatic = tuple(
            memory.id
            for memory in sorted(
                memories,
                key=lambda memory: memory.control_ranks_by_task[task.task_id][
                    OrderingArm.CONTROL_AUTOMATIC.value
                ],
            )
        )
        assert automatic == base


def test_control_arms_use_task_scoped_workspaces() -> None:
    corpus = build_followup_corpora(seed=5878)["platform-documentation-hub-spoke"]
    task = corpus.tasks[0]
    root = Path("/tmp/followup-workspaces")

    assert {arm.value for arm in CONTROL_ARMS} == {
        "control-automatic",
        "control-semantic",
        "control-strategy",
        "control-raw",
    }
    assert task_workspace(root, task, task_scoped=False) == root / "corpus-100"
    assert task_workspace(root, task, task_scoped=True) == root / "tasks" / task.task_id


def test_oracle_page_metrics_account_for_whole_visible_pages() -> None:
    items = tuple(
        {
            "id": memory_id,
            "key": memory_id,
            "title": memory_id.upper(),
            "lifecycle": "active",
            "excerpt": "compact",
            "matched_fields": ["body"],
            "rank": rank,
        }
        for rank, memory_id in enumerate(("d1", "d2", "d3", "entry", "primary"), start=1)
    )

    bounded = oracle_page_metrics(
        items=items,
        useful_ids={"entry", "primary"},
        page_size=3,
        query="deploy",
    )
    unbounded = oracle_page_metrics(
        items=items,
        useful_ids={"entry", "primary"},
        page_size="all",
        query="deploy",
    )

    assert bounded["first_useful_rank"] == 4
    assert bounded["pages_to_first_useful"] == 2
    assert bounded["records_to_first_useful"] == 5
    assert bounded["page_one_useful"] is False
    assert unbounded["pages_to_first_useful"] == 1
    assert unbounded["records_to_first_useful"] == 5
    assert (
        unbounded["response_tokens_to_first_useful"]
        > bounded["response_tokens_to_first_useful"] / 2
    )


def test_depth_first_navigation_reports_reachability_and_branching() -> None:
    graph = {
        "entry": ("wrong", "hub"),
        "wrong": (),
        "hub": ("cycle", "primary"),
        "cycle": ("entry",),
        "primary": (),
    }

    reached = depth_first_navigation(graph, start="entry", target="primary")
    missed = depth_first_navigation(graph, start="wrong", target="primary")

    assert reached == {
        "reached": True,
        "hops": 4,
        "recalls": 5,
        "edges_exposed": 5,
        "branching_factor_mean": 1.0,
        "branching_factor_max": 2,
    }
    assert missed["reached"] is False
    assert missed["recalls"] == 1


def test_oracle_summary_reports_distributions_and_page_one_probability() -> None:
    rows = [
        {
            "arm": "key",
            "page_size": "5",
            "page_one_useful": False,
            "pages_to_first_useful": 3,
            "response_tokens_to_first_useful": 300,
        },
        {
            "arm": "key",
            "page_size": "5",
            "page_one_useful": True,
            "pages_to_first_useful": 1,
            "response_tokens_to_first_useful": 100,
        },
    ]

    summary = summarize_oracle_rows(rows)
    cell = summary["curves"][0]
    assert cell["page_one_useful_probability"] == 0.5
    assert cell["pages_to_first_useful"]["p50"] == 2
    assert cell["pages_to_first_useful"]["p90"] == pytest.approx(2.8)
    report = render_oracle_report(summary)
    svg = render_oracle_svg(summary, metric="page_one")
    assert "deterministic ordering and navigation oracle" in report
    assert "not agent outcomes" in report
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')


def test_oracle_analysis_report_and_writer_cover_paired_controls(tmp_path: Path) -> None:
    rows = []
    for task_index, family in enumerate(("family-a", "family-b"), start=1):
        for arm, useful_rank in (
            ("key", 6),
            ("reverse-pagerank", 4),
            ("control-automatic", 4),
            ("control-semantic", 1),
        ):
            rows.append(
                {
                    "task_id": f"task-{task_index}",
                    "graph_family": family,
                    "failure_case": f"failure-{task_index}",
                    "corpus_size": 50,
                    "total_matched": 12,
                    "baseline_burial_depth": 6,
                    "arm": arm,
                    "page_size": "5",
                    "page_one_useful": useful_rank <= 5,
                    "pages_to_first_useful": 1 if useful_rank <= 5 else 2,
                    "response_tokens_to_first_useful": 100 if useful_rank <= 5 else 200,
                    "primary_rank": useful_rank + 1,
                    "first_useful_rank": useful_rank,
                    "records_to_first_useful": 5 if useful_rank <= 5 else 10,
                    "one_shot_candidate_generation_ms": 2.0,
                    "one_shot_ordering_ms": 0.5,
                    "dfs_reached_primary": arm != "key",
                    "dfs_graph_hops": 1,
                }
            )

    analysis = analyze_oracle_rows(rows)
    assert analysis["task_count"] == 2
    assert analysis["graph_family_count"] == 2
    assert analysis["paired_comparisons"]
    assert "blocked-authentication" in analysis["agent_followup_status"]
    assert "Pages to first useful" in render_oracle_svg(analysis, metric="pages_p90")
    with pytest.raises(ValueError, match="unknown oracle plot"):
        render_oracle_svg(analysis, metric="not-a-metric")

    manifest = write_oracle_evidence(rows, tmp_path / "evidence", provenance={"sha": "abc"})
    assert manifest["row_count"] == len(rows)
    assert (tmp_path / "evidence" / "report.md").is_file()
    assert (tmp_path / "evidence" / "page-one.svg").is_file()


def test_collect_oracle_evidence_checks_real_cli_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = MemoryFixture(
        id="entry",
        key="entry",
        title="Entry",
        references=("primary",),
        body="deploy entry",
    )
    primary = MemoryFixture(
        id="primary",
        key="primary",
        title="Primary",
        body="deploy primary",
    )
    task = OrderingTask(
        task_id="heldout-task",
        corpus_size=2,
        query="deploy",
        instruction="decide",
        primary_relevant="primary",
        acceptable_entry_points=("entry",),
        distractors=(),
        expected_facts=("SAFE",),
        forbidden_facts=("UNSAFE",),
        split=TaskSplit.HELDOUT,
        graph_family="tiny-family",
        failure_case="tiny-failure",
        control_intent=ControlIntent(),
    )
    corpus = FrozenCorpus(memories=(entry, primary), tasks=(task,), seed=5878)
    validation = tmp_path / "validation"
    validation.mkdir()
    (validation / "tiny-family.json").write_text(
        json.dumps(
            {
                "tasks": {
                    task.task_id: {
                        "candidate_digest": "digest",
                        "total_matched": 2,
                        "ranks": {"key": {"entry": 1, "primary": 2}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        def exhaust(self, query: str, arm: OrderingArm) -> ExhaustedDiscovery:
            assert query == "deploy"
            assert arm is OrderingArm.KEY
            bm25f = BM25FConfig()
            items = (
                CompactMemory(
                    id="entry",
                    key="entry",
                    title="Entry",
                    lifecycle="active",
                    excerpt="deploy entry",
                    matched_fields=("body",),
                    rank=1,
                ),
                CompactMemory(
                    id="primary",
                    key="primary",
                    title="Primary",
                    lifecycle="active",
                    excerpt="deploy primary",
                    matched_fields=("body",),
                    rank=2,
                ),
            )
            page = DiscoveryPage(
                items=items,
                query=query,
                order=arm,
                page_size=2,
                unbounded=True,
                total_matched=2,
                complete=True,
                candidate_digest="digest",
                bm25f=bm25f,
                candidate_generation_ms=2,
                ordering_ms=1,
            )
            return ExhaustedDiscovery(items=items, pages=(page,), candidate_digest="digest")

    monkeypatch.setattr(followup_evidence, "BeadsExperimentClient", FakeClient)
    rows = collect_oracle_evidence(
        corpora={"tiny-family": corpus},
        validation_dir=validation,
        workspace_root=tmp_path / "workspaces",
        beads_bin="/fake/bd",
        arms=(OrderingArm.KEY,),
        page_sizes=(1, "all"),
        bm25f=BM25FConfig(),
    )

    assert len(rows) == 2
    assert rows[0]["dfs_reached_primary"] is True
    assert rows[0]["dfs_graph_hops"] == 1
    assert rows[0]["estimated_repeated_candidate_generation_ms"] == 2


def test_followup_suite_writer_emits_deterministic_inventory(tmp_path: Path) -> None:
    def fake_orders(
        memories: tuple[MemoryFixture, ...], *, artifact_repo: Path
    ) -> dict[str, tuple[str, ...]]:
        assert artifact_repo == Path("/structural-source")
        ids = tuple(memory.id for memory in memories)
        return dict.fromkeys(
            (
                "indegree",
                "outdegree",
                "pagerank",
                "reverse-pagerank",
                "hits-authority",
                "hits-hub",
            ),
            ids,
        )

    out = tmp_path / "suite"
    manifest = write_followup_corpora(
        out,
        artifact_repo=Path("/structural-source"),
        order_fn=fake_orders,
    )

    assert manifest["schema_version"] == 1
    assert manifest["seed"] == 5878
    assert manifest["family_count"] == 7
    assert manifest["heldout_task_count"] == 21
    assert set(manifest["families"]) == set(GRAPH_FAMILIES)
    for family, entry in manifest["families"].items():
        path = out / entry["path"]
        assert path.is_file()
        corpus = json.loads(path.read_text(encoding="utf-8"))
        assert len(corpus["memories"]) == 500
        assert {task["graph_family"] for task in corpus["tasks"]} == {family}

    with pytest.raises(FileExistsError, match="already exists"):
        write_followup_corpora(
            out,
            artifact_repo=Path("/structural-source"),
            order_fn=fake_orders,
        )

    before = (out / "manifest.json").read_bytes()
    write_followup_corpora(
        out,
        artifact_repo=Path("/structural-source"),
        order_fn=fake_orders,
        overwrite=True,
    )
    assert (out / "manifest.json").read_bytes() == before
    assert load_followup_corpora(out).keys() == GRAPH_FAMILIES.keys()

    first_path = out / next(iter(manifest["families"].values()))["path"]
    first_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        load_followup_corpora(out)


def test_cli_freezes_followup_suite_through_existing_membench_entrypoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from membench import cli

    seen: dict[str, object] = {}

    def fake_write(
        out: Path,
        *,
        artifact_repo: Path,
        seed: int,
        overwrite: bool,
    ) -> dict[str, object]:
        seen.update(
            out=out,
            artifact_repo=artifact_repo,
            seed=seed,
            overwrite=overwrite,
        )
        return {"family_count": 7, "heldout_task_count": 21}

    monkeypatch.setattr(cli, "write_followup_corpora", fake_write, raising=False)
    out = tmp_path / "followup"
    assert (
        cli.main(
            [
                "beads-ordering-followup-freeze",
                "--out",
                str(out),
                "--structural-order-source",
                "/structural-source",
                "--overwrite",
            ]
        )
        == 0
    )
    assert seen == {
        "out": out,
        "artifact_repo": Path("/structural-source"),
        "seed": 5878,
        "overwrite": True,
    }


def test_control_orders_are_deterministic_permutations_without_candidate_expansion() -> None:
    corpus = build_followup_corpora(seed=5878)["platform-documentation-hub-spoke"]
    task = next(task for task in corpus.tasks if task.split is TaskSplit.HELDOUT)
    candidates = tuple(
        memory.id
        for memory in corpus.memories[: task.corpus_size]
        if memory.id in {task.primary_relevant, *task.acceptable_entry_points, *task.distractors}
    )
    base = tuple(reversed(candidates))

    for policy in ControlPolicy:
        ordered = apply_control_order(
            candidates=candidates,
            base_order=base,
            intent=task.control_intent,
            policy=policy,
        )
        assert len(ordered) == len(candidates)
        assert set(ordered) == set(candidates)
        assert ordered == apply_control_order(
            candidates=candidates,
            base_order=base,
            intent=task.control_intent,
            policy=policy,
        )

    assert (
        apply_control_order(
            candidates=candidates,
            base_order=base,
            intent=task.control_intent,
            policy=ControlPolicy.SEMANTIC,
        )[0]
        in task.control_intent.pin
    )
    assert apply_control_order(
        candidates=candidates,
        base_order=base,
        intent=task.control_intent,
        policy=ControlPolicy.RAW_NUMERIC,
    )[0] == min(
        task.control_intent.raw_numeric_ranks, key=task.control_intent.raw_numeric_ranks.get
    )


def test_control_order_rejects_invalid_or_duplicate_inputs() -> None:
    task = build_followup_corpora(seed=5878)["platform-documentation-hub-spoke"].tasks[0]
    assert task.control_intent is not None

    with pytest.raises(ValueError, match="permutation"):
        apply_control_order(
            candidates=("a", "b"),
            base_order=("a", "a"),
            intent=task.control_intent,
            policy=ControlPolicy.AUTOMATIC,
        )


def test_experimental_rank_scorer_matches_pinned_structural_orders() -> None:
    path = (
        ROOT / "fixtures" / "beads_ordering" / "followup" / "incident-runbook-sparse-authority.json"
    )
    corpus = json.loads(path.read_text(encoding="utf-8"))
    memories = tuple(MemoryFixture.model_validate(raw) for raw in corpus["memories"][:100])

    for strategy in ("indegree", "outdegree", "pagerank", "reverse-pagerank"):
        expected = tuple(
            memory.id
            for memory in sorted(
                memories,
                key=lambda memory: memory.structural_ranks_by_corpus["100"][strategy],
            )
        )
        assert rank_order(memories, strategy=strategy) == expected


def test_mutation_schedule_is_deterministic_and_covers_registered_events() -> None:
    corpus = build_followup_corpora(seed=5878)["incident-runbook-sparse-authority"]
    state = build_graph_state(corpus, size=100)

    first = build_mutation_schedule(state, count=40, seed=5878)
    second = build_mutation_schedule(state, count=40, seed=5878)

    assert first == second
    assert [event.sequence for event in first] == list(range(1, 41))
    assert {event.kind for event in first} == set(MutationKind)

    current = state
    for event in first:
        previous_digest = current.digest
        current = apply_mutation(current, event)
        assert current.digest != previous_digest


def test_rank_refresh_policies_have_distinct_freshness_and_compute_placement() -> None:
    corpus = build_followup_corpora(seed=5878)["migration-correction-temporal-chain"]
    state = build_graph_state(corpus, size=100)
    events = build_mutation_schedule(state, count=6, seed=5878)

    exact = replay_rank_refresh(
        state,
        events,
        policy=RankRefreshPolicy.EXACT_GLOBAL,
        strategy="reverse-pagerank",
    )
    periodic = replay_rank_refresh(
        state,
        events,
        policy=RankRefreshPolicy.PERIODIC_5,
        strategy="reverse-pagerank",
    )
    lazy = replay_rank_refresh(
        state,
        events,
        policy=RankRefreshPolicy.STALE_UNTIL_READ,
        strategy="reverse-pagerank",
    )
    incremental = replay_rank_refresh(
        state,
        events,
        policy=RankRefreshPolicy.INCREMENTAL_IF_FEASIBLE,
        strategy="reverse-pagerank",
    )

    assert all(step.supported and step.rank_age_events == 0 for step in exact)
    assert all(step.refresh_phase == "mutation" for step in exact)
    assert [step.rank_age_events for step in periodic] == [1, 2, 3, 4, 0, 1]
    assert periodic[4].refresh_phase == "mutation"
    assert all(step.rank_age_events == 0 for step in lazy)
    assert all(step.refresh_phase == "read" for step in lazy)
    assert all(step.top_10_overlap == 1.0 for step in exact)
    assert all(not step.supported for step in incremental)
    assert all("batch-only" in step.feasibility_reason for step in incremental)


def test_candidate_membership_is_snapshot_bound_not_policy_bound() -> None:
    corpus = build_followup_corpora(seed=5878)["data-schema-dependency-dag"]
    task = next(task for task in corpus.tasks if task.corpus_size == 100)
    state = build_graph_state(corpus, size=100)
    expected = {task.primary_relevant, *task.acceptable_entry_points, *task.distractors}

    assert set(candidate_ids(state, task.query)) == expected
    orders = {
        strategy: rank_order(state.memories, strategy=strategy)
        for strategy in ("indegree", "outdegree", "pagerank", "reverse-pagerank")
    }
    for order in orders.values():
        assert {memory_id for memory_id in order if memory_id in expected} == expected


def test_continuation_epoch_invalidates_on_state_or_rank_change() -> None:
    corpus = build_followup_corpora(seed=5878)["security-policy-cross-team-network"]
    state = build_graph_state(corpus, size=50)
    token = ContinuationEpoch(state_digest=state.digest, rank_epoch=3)

    assert continuation_is_valid(token, state_digest=state.digest, rank_epoch=3)
    assert not continuation_is_valid(token, state_digest=state.digest, rank_epoch=4)
    event = build_mutation_schedule(state, count=1, seed=5878)[0]
    changed = apply_mutation(state, event)
    assert not continuation_is_valid(token, state_digest=changed.digest, rank_epoch=3)


def test_mutation_experiment_preserves_same_snapshot_candidates_across_policies() -> None:
    corpus = build_followup_corpora(seed=5878)["incident-runbook-sparse-authority"]
    rows = run_mutation_experiment(
        {"incident-runbook-sparse-authority": corpus},
        sizes=(50,),
        event_count=7,
        seed=5878,
    )

    assert len(rows) == 7 * len(RankRefreshPolicy)
    by_sequence: dict[int, list[object]] = {}
    for row in rows:
        by_sequence.setdefault(row.sequence, []).append(row)
        assert row.page_size == 10
        assert row.continuation_invalidated is True
    for sequence_rows in by_sequence.values():
        assert len({row.candidate_digest for row in sequence_rows}) == 1
        assert len({row.total_matched for row in sequence_rows}) == 1

    exact = [row for row in rows if row.policy is RankRefreshPolicy.EXACT_GLOBAL]
    assert all(row.extra_pages_to_first_useful == 0 for row in exact)
    assert all(row.top_10_overlap == 1.0 for row in exact)
    unsupported = [row for row in rows if row.policy is RankRefreshPolicy.INCREMENTAL_IF_FEASIBLE]
    assert unsupported and all(not row.supported for row in unsupported)


def test_mutation_writer_emits_raw_manifest_and_p50_p90(tmp_path: Path) -> None:
    corpus = build_followup_corpora(seed=5878)["incident-runbook-sparse-authority"]
    rows = run_mutation_experiment(
        {"incident-runbook-sparse-authority": corpus},
        sizes=(50,),
        event_count=7,
        seed=5878,
    )
    out = tmp_path / "mutation"
    payload = write_mutation_experiment(
        rows,
        out,
        provenance={
            "mem_git_sha": "a" * 40,
            "mem_git_diff_sha256": "b" * 64,
            "beads_git_sha": "c" * 40,
            "beads_git_diff_sha256": "d" * 64,
            "beads_bin_sha256": "e" * 64,
            "structural_order_source_git_sha": "f" * 40,
            "fixture_manifest_sha256": "1" * 64,
            "preregistration_sha256": "2" * 64,
        },
        seed=5878,
        event_count=7,
    )

    assert (out / "raw-results.jsonl").is_file()
    assert (out / "manifest.json").is_file()
    assert (out / "analysis.json").is_file()
    assert payload["row_count"] == len(rows)
    assert payload["rank_parameters"] == {"damping": 0.85, "iterations": 100}
    analysis = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    assert set(analysis["by_policy"]) == {policy.value for policy in RankRefreshPolicy}
    for summary in analysis["by_policy"].values():
        assert "rank_refresh_ms" in summary
        assert {"p50", "p90"} <= set(summary["rank_refresh_ms"])
        assert "top_10_overlap" in summary


def test_cli_runs_mutation_replay_with_explicit_binary_provenance(tmp_path: Path) -> None:
    from membench import cli

    out = tmp_path / "mutation-cli"
    fixture_dir = ROOT / "fixtures" / "beads_ordering" / "followup"
    preregistration = (
        ROOT / "fixtures" / "beads_ordering" / "structural-followup-preregistration.json"
    )
    assert (
        cli.main(
            [
                "beads-ordering-followup-mutations",
                "--fixture-dir",
                str(fixture_dir),
                "--beads-repo",
                str(ROOT.parent),
                "--beads-bin",
                "/bin/true",
                "--preregistration",
                str(preregistration),
                "--sizes",
                "50",
                "--event-count",
                "1",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_count"] == 7 * len(RankRefreshPolicy)
    assert manifest["provenance"]["beads_bin_sha256"]
    assert manifest["provenance"]["mem_git_sha"]
    assert manifest["provenance"]["structural_order_source_git_sha"] == (
        "b1c55df8c3bd799262bdf2eede2b3e3f705d8848"
    )


def test_scaled_graph_state_is_deterministic_and_reference_closed() -> None:
    corpus = build_followup_corpora(seed=5878)["incident-runbook-sparse-authority"]
    first = scale_graph_state(corpus, size=2000)
    second = scale_graph_state(corpus, size=2000)

    assert first.digest == second.digest
    assert len(first.memories) == 2000
    ids = {memory.id for memory in first.memories}
    assert len(ids) == 2000
    assert all(set(memory.references) <= ids for memory in first.memories)
    assert first.memories[:500] == corpus.memories


def test_rank_scaling_reports_optimized_large_corpus_curve(tmp_path: Path) -> None:
    corpus = build_followup_corpora(seed=5878)["incident-runbook-sparse-authority"]
    rows = benchmark_rank_scaling(
        {"incident-runbook-sparse-authority": corpus},
        sizes=(50, 1000),
        repeats=2,
    )

    assert len(rows) == 4
    assert {row.corpus_size for row in rows} == {50, 1000}
    assert {row.arithmetic for row in rows if row.corpus_size == 50} == {"pinned-update-order"}
    assert {row.arithmetic for row in rows if row.corpus_size == 1000} == {
        "aggregated-dangling-mass"
    }
    payload = write_rank_scaling(rows, tmp_path)
    assert payload["row_count"] == 4
    assert (tmp_path / "rank-scaling.jsonl").is_file()
    analysis = json.loads((tmp_path / "rank-scaling-analysis.json").read_text())
    assert set(analysis["by_corpus_size"]) == {"50", "1000"}
    assert {"p50", "p90"} <= set(analysis["by_corpus_size"]["1000"]["compute_ms"])


def test_cli_runs_rank_scaling_with_provenance(tmp_path: Path) -> None:
    from membench import cli

    out = tmp_path / "scaling-cli"
    assert (
        cli.main(
            [
                "beads-ordering-followup-rank-scaling",
                "--fixture-dir",
                str(ROOT / "fixtures" / "beads_ordering" / "followup"),
                "--beads-repo",
                str(ROOT.parent),
                "--beads-bin",
                "/bin/true",
                "--sizes",
                "50",
                "--repeats",
                "1",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    manifest = json.loads((out / "rank-scaling-manifest.json").read_text())
    assert manifest["row_count"] == 7
    assert manifest["provenance"]["beads_bin_sha256"]
    assert manifest["arithmetic_boundary"] == {
        "behavior_sizes": "pinned-update-order",
        "compute-only-sizes": "aggregated-dangling-mass",
    }
