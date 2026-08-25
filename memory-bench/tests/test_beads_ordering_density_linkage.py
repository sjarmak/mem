from __future__ import annotations

import json
from pathlib import Path

import pytest

from membench.beads_ordering.density_linkage import (
    CANDIDATE_COUNTS,
    LINKAGE_LEVELS,
    LinkageLevel,
    build_density_linkage_variant,
    load_density_linkage_manifest,
    write_density_linkage_manifest,
)
from membench.beads_ordering.density_linkage_evidence import (
    analyze_density_linkage_oracle,
    write_density_linkage_oracle,
)
from membench.beads_ordering.followup_corpus import load_followup_corpora
from membench.beads_ordering.models import MemoryFixture, OrderingArm

ROOT = Path(__file__).resolve().parents[1]
BASE_FIXTURES = ROOT / "fixtures" / "beads_ordering" / "followup"
PREREGISTRATION = ROOT / "fixtures" / "beads_ordering" / "density-linkage-preregistration.json"


def _literal_matches(memories: tuple[MemoryFixture, ...], query: str) -> set[str]:
    lowered = query.lower()
    return {
        memory.id
        for memory in memories
        if lowered in memory.key.lower() or lowered in memory.stored_value(500).lower()
    }


def _content_without_graph(memory: MemoryFixture) -> dict[str, object]:
    return memory.model_dump(
        exclude={"references", "structural_ranks_by_corpus", "control_ranks_by_task"}
    )


def test_density_linkage_variants_are_deterministic_nested_and_label_exactly() -> None:
    base = load_followup_corpora(BASE_FIXTURES)["incident-runbook-sparse-authority"]
    task = next(task for task in base.tasks if task.corpus_size == 500)

    first = {
        count: build_density_linkage_variant(
            base,
            task=task,
            candidate_count=count,
            linkage_level=LinkageLevel.NATIVE,
        )
        for count in CANDIDATE_COUNTS
    }
    second = build_density_linkage_variant(
        base,
        task=task,
        candidate_count=40,
        linkage_level=LinkageLevel.NATIVE,
    )

    assert first[40].corpus.model_dump_json() == second.corpus.model_dump_json()
    assert first[40].recipe == second.recipe
    candidate_sets: list[set[str]] = []
    for count, variant in first.items():
        transformed_task = variant.corpus.tasks[0]
        assert transformed_task.corpus_size == 500
        assert transformed_task.query == task.query
        assert transformed_task.primary_relevant == task.primary_relevant
        assert transformed_task.acceptable_entry_points == task.acceptable_entry_points
        labelled = {
            transformed_task.primary_relevant,
            *transformed_task.acceptable_entry_points,
            *transformed_task.distractors,
        }
        assert len(labelled) == count
        assert _literal_matches(variant.corpus.memories, task.query) == labelled
        assert set(variant.recipe.candidate_ids) == labelled
        candidate_sets.append(labelled)

    assert candidate_sets[0] < candidate_sets[1] < candidate_sets[2]


def test_linkage_levels_change_only_links_and_derived_ranks() -> None:
    base = load_followup_corpora(BASE_FIXTURES)["migration-correction-temporal-chain"]
    task = next(
        task for task in base.tasks if task.corpus_size == 500 and task.split.value == "heldout"
    )
    variants = {
        level: build_density_linkage_variant(
            base,
            task=task,
            candidate_count=40,
            linkage_level=level,
        )
        for level in LINKAGE_LEVELS
    }

    for index in range(500):
        content = {
            json.dumps(_content_without_graph(variant.corpus.memories[index]), sort_keys=True)
            for variant in variants.values()
        }
        assert len(content) == 1

    sparse_edges = variants[LinkageLevel.SPARSE].edge_set
    native_edges = variants[LinkageLevel.NATIVE].edge_set
    enriched_edges = variants[LinkageLevel.ENRICHED].edge_set
    assert sparse_edges < native_edges < enriched_edges
    assert (
        variants[LinkageLevel.SPARSE].recipe.graph_metrics.edge_count
        < variants[LinkageLevel.NATIVE].recipe.graph_metrics.edge_count
    )
    assert (
        variants[LinkageLevel.NATIVE].recipe.graph_metrics.edge_count
        < variants[LinkageLevel.ENRICHED].recipe.graph_metrics.edge_count
    )
    assert variants[LinkageLevel.SPARSE].recipe.graph_metrics.entry_to_primary_reachable


def test_sparse_links_do_not_invent_reachability_for_unlinked_primary() -> None:
    base = load_followup_corpora(BASE_FIXTURES)["platform-documentation-hub-spoke"]
    task = next(
        task for task in base.tasks if task.corpus_size == 100 and task.split.value == "heldout"
    )

    variants = {
        level: build_density_linkage_variant(
            base,
            task=task,
            candidate_count=40,
            linkage_level=level,
        )
        for level in LINKAGE_LEVELS
    }

    assert all(
        not variant.recipe.graph_metrics.entry_to_primary_reachable for variant in variants.values()
    )
    assert all(
        variant.recipe.graph_metrics.shortest_entry_to_primary_hops is None
        for variant in variants.values()
    )


def test_each_variant_has_complete_derived_orders_and_primary_first_control() -> None:
    base = load_followup_corpora(BASE_FIXTURES)["data-schema-dependency-dag"]
    task = next(task for task in base.tasks if task.corpus_size == 50)
    variant = build_density_linkage_variant(
        base,
        task=task,
        candidate_count=10,
        linkage_level=LinkageLevel.ENRICHED,
    )
    transformed_task = variant.corpus.tasks[0]

    for arm in (
        OrderingArm.INDEGREE,
        OrderingArm.OUTDEGREE,
        OrderingArm.PAGERANK,
        OrderingArm.REVERSE_PAGERANK,
    ):
        ranks = [
            memory.structural_ranks_by_corpus["500"][arm.value]
            for memory in variant.corpus.memories
        ]
        assert sorted(ranks) == list(range(1, 501))

    control_ranks = {
        memory.id: memory.control_ranks_by_task[transformed_task.task_id][
            OrderingArm.CONTROL_SEMANTIC.value
        ]
        for memory in variant.corpus.memories
    }
    assert control_ranks[transformed_task.primary_relevant] == 1
    assert [control_ranks[memory_id] for memory_id in transformed_task.acceptable_entry_points] == [
        *range(2, 2 + len(transformed_task.acceptable_entry_points))
    ]
    assert sorted(control_ranks.values()) == list(range(1, 501))


def test_manifest_freezes_small_recipes_and_loads_fail_closed(tmp_path: Path) -> None:
    out = tmp_path / "density-linkage-manifest.json"
    manifest = write_density_linkage_manifest(
        BASE_FIXTURES,
        out,
        preregistration=PREREGISTRATION,
    )

    assert manifest["schema_version"] == 1
    assert manifest["status"] == "frozen-before-density-linkage-outcomes"
    assert manifest["base_task_count"] == 21
    assert manifest["variant_count"] == 21 * len(CANDIDATE_COUNTS) * len(LINKAGE_LEVELS)
    assert manifest["candidate_counts"] == list(CANDIDATE_COUNTS)
    assert manifest["linkage_levels"] == [level.value for level in LINKAGE_LEVELS]
    assert out.stat().st_size < 2_000_000

    loaded = load_density_linkage_manifest(BASE_FIXTURES, out)
    assert loaded.keys() == {entry["variant_id"] for entry in manifest["variants"]}
    first_id = manifest["variants"][0]["variant_id"]
    assert loaded[first_id].recipe.corpus_sha256 == manifest["variants"][0]["corpus_sha256"]

    with pytest.raises(FileExistsError):
        write_density_linkage_manifest(
            BASE_FIXTURES,
            out,
            preregistration=PREREGISTRATION,
        )

    payload = json.loads(out.read_text(encoding="utf-8"))
    payload["variants"][0]["candidate_ids"].pop()
    out.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest recipe drift"):
        load_density_linkage_manifest(BASE_FIXTURES, out)


def test_cli_freezes_and_materializes_one_density_linkage_variant(tmp_path: Path) -> None:
    from membench import cli

    manifest_path = tmp_path / "manifest.json"
    assert (
        cli.main(
            [
                "beads-ordering-density-linkage-freeze",
                "--fixture-dir",
                str(BASE_FIXTURES),
                "--preregistration",
                str(PREREGISTRATION),
                "--out",
                str(manifest_path),
            ]
        )
        == 0
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    variant_id = manifest["variants"][0]["variant_id"]
    fixture_path = tmp_path / "variant.json"

    assert (
        cli.main(
            [
                "beads-ordering-density-linkage-materialize",
                "--fixture-dir",
                str(BASE_FIXTURES),
                "--manifest",
                str(manifest_path),
                "--variant-id",
                variant_id,
                "--out",
                str(fixture_path),
            ]
        )
        == 0
    )
    corpus = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert len(corpus["memories"]) == 500
    assert corpus["tasks"][0]["task_id"] == variant_id


def test_density_linkage_oracle_analysis_preserves_factors_and_interactions(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = []
    for task_index in range(2):
        for linkage, key_rank, pagerank_rank in (
            ("sparse", 8, 7),
            ("native", 8, 4),
            ("enriched", 8, 2),
        ):
            for arm, rank in (("key", key_rank), ("pagerank", pagerank_rank)):
                rows.append(
                    {
                        "variant_id": f"task-{task_index}-{linkage}",
                        "base_task_id": f"task-{task_index}",
                        "graph_family": f"family-{task_index}",
                        "candidate_count": 40,
                        "linkage_level": linkage,
                        "arm": arm,
                        "page_size": "5",
                        "first_useful_rank": rank,
                        "page_one_useful": rank <= 5,
                        "pages_to_first_useful": 1 if rank <= 5 else 2,
                        "response_tokens_to_first_useful": 100 if rank <= 5 else 200,
                        "dfs_reached_primary": True,
                        "dfs_graph_hops": 1,
                        "dfs_recalls": 2,
                        "dfs_edges_exposed": 3,
                        "dfs_branching_factor_mean": 1.5,
                        "dfs_branching_factor_max": 2,
                    }
                )

    analysis = analyze_density_linkage_oracle(rows)

    assert analysis["row_count"] == 12
    assert analysis["base_task_count"] == 2
    assert len(analysis["curves"]) == 6
    pagerank_native = next(
        item
        for item in analysis["paired_policy_contrasts"]
        if item["reference"] == "key"
        and item["contender"] == "pagerank"
        and item["linkage_level"] == "native"
    )
    assert pagerank_native["page_one_gain"] == 1.0
    interaction = analysis["linkage_interactions"][0]
    assert interaction["contender"] == "pagerank"
    assert interaction["page_one_gain_change_enriched_minus_sparse"] == 1.0

    manifest = write_density_linkage_oracle(rows, tmp_path, provenance={"mem_git_sha": "a"})
    assert manifest["row_count"] == 12
    assert (tmp_path / "raw-oracle-results.jsonl").is_file()
    assert (tmp_path / "analysis.json").is_file()
    assert (tmp_path / "report.md").is_file()


def test_cli_runs_density_linkage_oracle_with_explicit_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from membench import cli

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "structural_order_source_git_sha": "b" * 40,
                "preregistration_sha256": "c" * 64,
                "variants": [],
            }
        ),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "load_density_linkage_manifest", lambda *_: {})

    def fake_collect(**kwargs: object) -> list[dict[str, object]]:
        seen["collect"] = kwargs
        return []

    def fake_write(rows: object, out: Path, *, provenance: dict[str, object]) -> dict[str, object]:
        seen.update(rows=rows, out=out, provenance=provenance)
        return {"row_count": 0}

    monkeypatch.setattr(cli, "collect_density_linkage_oracle", fake_collect)
    monkeypatch.setattr(cli, "write_density_linkage_oracle", fake_write)
    out = tmp_path / "oracle"
    assert (
        cli.main(
            [
                "beads-ordering-density-linkage-oracle",
                "--fixture-dir",
                str(BASE_FIXTURES),
                "--manifest",
                str(manifest_path),
                "--workspace-root",
                str(tmp_path / "workspaces"),
                "--beads-repo",
                str(ROOT.parent),
                "--beads-bin",
                "/bin/true",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    provenance = seen["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["beads_bin_sha256"]
    assert provenance["mem_git_sha"]
    assert provenance["structural_order_source_git_sha"] == "b" * 40
    assert seen["out"] == out
