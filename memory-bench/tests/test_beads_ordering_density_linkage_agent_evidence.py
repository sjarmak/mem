from __future__ import annotations

import json
from pathlib import Path

import pytest

from membench.beads_ordering.density_linkage_agent_evidence import (
    analyze_density_linkage_agents,
    build_density_linkage_repeat_manifest,
    build_density_linkage_replication_manifest,
    validate_density_linkage_agent_grid,
    write_density_linkage_agent_evidence,
)
from membench.beads_ordering.density_linkage_plots import render_density_linkage_plots
from membench.beads_ordering.models import ExperimentMode, OrderingArm, OrderingRunResult


def _row(
    task_id: str,
    arm: OrderingArm,
    *,
    page_size: str = "5",
    success: bool,
    pages: int,
    tokens: int,
    page_one: bool,
    failure: str | None = None,
) -> OrderingRunResult:
    return OrderingRunResult.model_construct(
        run_id=f"{task_id}:navigation:{arm.value}:p{page_size}:r0",
        task_id=task_id,
        query="private query text",
        corpus_size=500,
        arm=arm,
        mode=ExperimentMode.NAVIGATION,
        repeat=0,
        page_size=page_size,
        total_matched=int(task_id.split("-c")[1].split("-")[0]),
        primary_rank=20,
        primary_page=4,
        acceptable_rank=8,
        acceptable_page=2,
        pages_requested=pages,
        pages_to_first_useful=pages,
        page_one_acceptable_visible=page_one,
        compact_records_visible=pages * 5,
        compact_result_bytes=tokens * 4,
        compact_result_tokens=tokens,
        compact_bytes_to_first_useful=tokens * 4,
        compact_tokens_to_first_useful=tokens,
        retrieval_tokens_to_first_useful=tokens + 25,
        tool_calls_to_first_useful=pages,
        retrieval_tool_calls=pages + 1,
        time_to_first_useful_ms=float(pages * 1000),
        full_recalls=1,
        first_recalled_relevant=True,
        graph_hops_after_first_useful=0,
        graph_hops_total=0,
        reference_edges_exposed=0,
        branching_factor_mean=0.0,
        branching_factor_max=0,
        navigation_reached_primary=False,
        retrieval_related_tokens=tokens + 100,
        retrieval_latency_ms=float(pages * 20),
        server_candidate_generation_ms=3.0,
        server_ordering_ms=2.0,
        end_to_end_ms=float(pages * 1200),
        task_success=success,
        abstained=False,
        premature_stop=False,
        agent_input_tokens=tokens,
        agent_output_tokens=20,
        mem_git_sha="a" * 40,
        mem_git_dirty=False,
        mem_git_diff_sha256="0" * 64,
        beads_git_sha="b" * 40,
        beads_git_dirty=True,
        beads_git_diff_sha256="1" * 64,
        beads_bin_sha256="2" * 64,
        structural_order_source_git_sha="c" * 40,
        agent_model="fixed-model",
        agent_cli_version="1.0",
        final_answer="private model answer",
        failure=failure,
    )


def _metadata() -> dict[str, dict[str, object]]:
    metadata: dict[str, dict[str, object]] = {}
    for family, base_task in (("family-a", "task-a"), ("family-b", "task-b")):
        for count in (10, 150):
            for linkage in ("sparse", "enriched"):
                task_id = f"{base_task}-c{count}-{linkage}"
                metadata[task_id] = {
                    "base_task_id": base_task,
                    "graph_family": family,
                    "failure_case": "case",
                    "candidate_count": count,
                    "linkage_level": linkage,
                    "baseline_burial_depth": 20,
                }
    return metadata


def _synthetic_rows() -> list[OrderingRunResult]:
    rows: list[OrderingRunResult] = []
    for task_id, metadata in _metadata().items():
        count = int(metadata["candidate_count"])
        linkage = str(metadata["linkage_level"])
        if linkage == "enriched":
            policy_values = {
                OrderingArm.KEY: (False, 3, 300, False),
                OrderingArm.PAGERANK: (True, 2, 200, True),
                OrderingArm.BM25F: (True, 1, 100, True),
            }
        else:
            policy_values = {
                OrderingArm.KEY: (True, 3, 300, False),
                OrderingArm.PAGERANK: (True, 3, 300, False),
                OrderingArm.BM25F: (True, 1, 100, True),
            }
        for arm, (success, pages, tokens, page_one) in policy_values.items():
            rows.append(
                _row(
                    task_id,
                    arm,
                    success=success,
                    pages=pages,
                    tokens=tokens,
                    page_one=page_one,
                )
            )
        rows.append(
            _row(
                task_id,
                OrderingArm.CONTROL_SEMANTIC,
                page_size="all",
                success=count == 10,
                pages=1,
                tokens=100 if count == 10 else 300,
                page_one=True,
            )
        )
    return rows


def test_combined_analysis_preserves_paired_density_policy_and_linkage_contrasts() -> None:
    analysis = analyze_density_linkage_agents(
        _synthetic_rows(),
        _metadata(),
        bootstrap_seed=11,
        bootstrap_resamples=200,
    )

    pagerank = next(
        contrast
        for contrast in analysis["policy_contrasts"]
        if contrast["candidate_count"] == 150
        and contrast["linkage_level"] == "enriched"
        and contrast["reference"] == "key"
        and contrast["contender"] == "pagerank"
    )
    assert pagerank["page_one_gain"]["estimate"] == 1.0
    assert pagerank["pages_saved"]["p50"] == 1.0
    assert pagerank["compact_token_reduction_fraction"]["p50"] == pytest.approx(1 / 3)
    assert pagerank["task_success_delta"]["estimate"] == 1.0

    interaction = next(
        contrast
        for contrast in analysis["linkage_interactions"]
        if contrast["candidate_count"] == 150
        and contrast["reference"] == "key"
        and contrast["contender"] == "pagerank"
    )
    assert interaction["page_one_gain_change_enriched_minus_sparse"]["estimate"] == 1.0
    assert interaction["pages_saved_change_enriched_minus_sparse"]["p50"] == 1.0

    density = next(
        contrast
        for contrast in analysis["density_endpoint_contrasts"]
        if contrast["linkage_level"] == "enriched"
    )
    assert density["task_success_drop_10_to_150"]["estimate"] == 1.0
    assert density["correct_use_failure_increase_10_to_150"]["estimate"] == 1.0
    assert density["retrieval_token_growth_10_to_150"]["p50"] == 200.0
    gates = analysis["decision_gates"]
    assert gates["candidate_density_behaviorally_material"] is True
    assert gates["pagerank_benefit_link_dependent"] is True
    assert gates["structural_default_supported"] is False
    assert gates["query_specific_beads_ownership_supported"] is False

    family_tail = next(
        row
        for row in analysis["family_policy_tails"]
        if row["candidate_count"] == 150
        and row["linkage_level"] == "enriched"
        and row["reference"] == "key"
        and row["contender"] == "pagerank"
        and row["graph_family"] == "family-a"
    )
    assert family_tail["metrics"]["pages_saved"]["p50"] == 1.0
    assert family_tail["metrics"]["pages_saved"]["p90"] == 1.0

    task_tail = next(
        row
        for row in analysis["task_policy_tails"]
        if row["candidate_count"] == 150
        and row["linkage_level"] == "enriched"
        and row["reference"] == "key"
        and row["contender"] == "pagerank"
    )
    pages_tail = task_tail["metrics"]["pages_saved"]
    assert pages_tail["negative_count"] == 0
    assert pages_tail["p50"] == 1.0
    assert pages_tail["p90"] == 1.0
    assert pages_tail["bottom"][0]["base_task_id"] == "task-a"
    assert pages_tail["top"][0]["graph_family"] == "family-b"


def test_replication_manifest_locks_only_the_decision_edge() -> None:
    metadata = {
        f"task-{task}--c{candidate:03d}--links-{linkage}": {
            "base_task_id": f"task-{task}",
            "candidate_count": candidate,
            "linkage_level": linkage,
        }
        for task in ("a", "b")
        for candidate in (40, 150)
        for linkage in ("sparse", "native", "enriched")
    }

    manifest = build_density_linkage_replication_manifest(
        metadata,
        primary_analysis_sha256="f" * 64,
        model="claude-secondary-exact-id",
        expected_base_task_count=2,
    )

    assert manifest["status"] == "sealed-before-secondary-configuration-outcomes"
    assert manifest["selected_cell_count"] == 18
    assert manifest["factors"] == {
        "candidate_count": 150,
        "page_size": "5",
        "mode": "navigation",
        "linkage_levels": ["sparse", "native", "enriched"],
        "arms": ["key", "pagerank", "bm25f"],
        "repeat": 0,
    }
    assert all("--c150--" in run_id for run_id in manifest["run_ids"])
    assert all(":navigation:" in run_id and ":p5:r0" in run_id for run_id in manifest["run_ids"])
    assert manifest["secondary_configuration"]["model"] == "claude-secondary-exact-id"


def test_grid_validation_detects_embedded_failure_and_provenance_drift() -> None:
    rows = _synthetic_rows()
    failed = rows[0].model_copy(update={"failure": "oauth expired"})
    drifted = rows[1].model_copy(update={"mem_git_sha": "d" * 40})
    rows = [failed, drifted, *rows[2:]]

    integrity = validate_density_linkage_agent_grid(rows, _metadata())

    assert integrity["observation_count"] == len(rows)
    assert integrity["embedded_failure_count"] == 1
    assert integrity["embedded_failure_run_ids"] == [failed.run_id]
    assert integrity["provenance_cardinality"]["mem_git_sha"] == 2
    assert integrity["unknown_task_ids"] == []
    assert integrity["duplicate_run_ids"] == []


def test_evidence_writer_excludes_queries_model_text_failures_and_credentials(
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rows[0] = rows[0].model_copy(update={"failure": "private credential path /secret/home"})

    manifest = write_density_linkage_agent_evidence(
        rows,
        _metadata(),
        tmp_path,
        provenance={"source": "three balanced shards"},
        bootstrap_resamples=100,
    )

    sanitized = (tmp_path / "sanitized-observations.jsonl").read_text(encoding="utf-8")
    assert "private query text" not in sanitized
    assert "private model answer" not in sanitized
    assert "/secret/home" not in sanitized
    assert manifest["privacy_projection"].startswith("per-run metrics only")
    analysis = json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["integrity"]["embedded_failure_count"] == 1
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "PageRank versus key" in report
    assert "BM25F versus PageRank" in report
    assert "Linkage interaction" in report
    assert "Graph-family tails" in report
    assert "Task-level tails" in report
    assert "Targeted repeats" in report
    assert (tmp_path / "targeted-repeat-manifest.json").exists()
    assert "targeted_repeat_manifest_sha256" in manifest


def test_evidence_writer_preserves_the_locked_pre_outcome_repeat_manifest(
    tmp_path: Path,
) -> None:
    locked = tmp_path / "locked" / "repeat-manifest.json"
    locked.parent.mkdir()
    locked_bytes = b'{"status":"sealed-before-repeat-outcomes","run_ids":["fixed:r1"]}\n'
    locked.write_bytes(locked_bytes)
    out = tmp_path / "evidence"

    manifest = write_density_linkage_agent_evidence(
        _synthetic_rows(),
        _metadata(),
        out,
        provenance={"source": "combined primary observations"},
        bootstrap_resamples=20,
        locked_repeat_manifest=locked,
    )

    assert (out / "targeted-repeat-manifest.json").read_bytes() == locked_bytes
    assert manifest["targeted_repeat_manifest_source"] == "locked-pre-outcome-input"


def test_cli_combines_density_linkage_agent_shards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from membench import cli

    manifest_path = tmp_path / "density-manifest.json"
    manifest_path.write_text('{"schema_version": 1}\n', encoding="utf-8")
    locked_repeat_manifest = tmp_path / "locked-repeat.json"
    locked_repeat_manifest.write_text('{"run_ids": []}\n', encoding="utf-8")
    raw_paths = []
    for index, row in enumerate(_synthetic_rows()[:2]):
        raw = tmp_path / f"shard-{index}.jsonl"
        raw.write_text(row.model_dump_json() + "\n", encoding="utf-8")
        raw_paths.append(raw)
    metadata = _metadata()
    monkeypatch.setattr(cli, "load_density_linkage_manifest", lambda *_: metadata)
    seen: dict[str, object] = {}

    def fake_write(*args: object, **kwargs: object) -> dict[str, object]:
        seen["rows"] = args[0]
        seen["metadata"] = args[1]
        seen["out"] = args[2]
        seen["provenance"] = kwargs["provenance"]
        seen["locked_repeat_manifest"] = kwargs["locked_repeat_manifest"]
        return {"observation_count": 2, "usable_observation_count": 2}

    monkeypatch.setattr(cli, "write_density_linkage_agent_evidence", fake_write)
    out = tmp_path / "analysis"
    assert (
        cli.main(
            [
                "beads-ordering-density-linkage-agent-analyze",
                "--fixture-dir",
                str(tmp_path),
                "--manifest",
                str(manifest_path),
                "--raw",
                *(str(path) for path in raw_paths),
                "--locked-repeat-manifest",
                str(locked_repeat_manifest),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert len(seen["rows"]) == 2  # type: ignore[arg-type]
    assert seen["metadata"] == metadata
    assert seen["out"] == out
    assert seen["locked_repeat_manifest"] == locked_repeat_manifest
    provenance = seen["provenance"]
    assert isinstance(provenance, dict)
    assert len(provenance["raw_input_sha256s"]) == 2


def test_cli_seals_density_linkage_replication_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from membench import cli

    manifest_path = tmp_path / "density-manifest.json"
    manifest_path.write_text('{"schema_version": 1, "base_task_count": 2}\n', encoding="utf-8")
    analysis_path = tmp_path / "primary-analysis.json"
    analysis_path.write_text(
        json.dumps(
            {
                "decision_gates": {
                    "candidate_density_behaviorally_material": True,
                    "pagerank_benefit_link_dependent": False,
                    "structural_default_supported": False,
                    "query_specific_beads_ownership_supported": False,
                }
            }
        )
        + "\n"
    )
    monkeypatch.setattr(
        cli,
        "load_density_linkage_manifest",
        lambda *_: {
            f"task-{task}--c150--links-{linkage}": {
                "base_task_id": f"task-{task}",
                "candidate_count": 150,
                "linkage_level": linkage,
            }
            for task in ("a", "b")
            for linkage in ("sparse", "native", "enriched")
        },
    )
    out = tmp_path / "replication-plan.json"

    assert (
        cli.main(
            [
                "beads-ordering-density-linkage-replication-plan",
                "--fixture-dir",
                str(tmp_path),
                "--manifest",
                str(manifest_path),
                "--primary-analysis",
                str(analysis_path),
                "--model",
                "claude-secondary-exact-id",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["selected_cell_count"] == 18
    assert payload["primary_analysis_sha256"]
    assert payload["primary_gate_verdicts"]["candidate_density_behaviorally_material"] is True
    assert payload["secondary_configuration"]["model"] == "claude-secondary-exact-id"


def test_repeat_manifest_mechanically_expands_registered_triggers_without_duplicates() -> None:
    rows = _synthetic_rows()
    analysis = analyze_density_linkage_agents(
        rows,
        _metadata(),
        bootstrap_seed=11,
        bootstrap_resamples=200,
    )

    manifest = build_density_linkage_repeat_manifest(rows, _metadata(), analysis)

    run_ids = manifest["run_ids"]
    assert isinstance(run_ids, list)
    assert run_ids == sorted(set(run_ids))
    assert all(run_id.endswith((":r1", ":r2")) for run_id in run_ids)
    assert manifest["selection_rule"] == "density-linkage-preregistration targeted-repeat rule"
    assert manifest["reason_counts"]["policy-task-success-disagreement"] > 0
    assert manifest["reason_counts"]["density-endpoint-disagreement"] > 0


def test_density_linkage_plots_emit_inert_svg_and_png_pairs(tmp_path: Path) -> None:
    analysis = analyze_density_linkage_agents(
        _synthetic_rows(),
        _metadata(),
        bootstrap_seed=11,
        bootstrap_resamples=50,
    )

    outputs = render_density_linkage_plots(analysis, tmp_path)

    assert {path.suffix for path in outputs} == {".svg", ".png"}
    assert len(outputs) == 6
    for path in outputs:
        assert path.exists()
        if path.suffix == ".svg":
            svg = path.read_text(encoding="utf-8")
            assert "<svg" in svg
            assert "<script" not in svg
            assert "onload=" not in svg
        else:
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_cli_renders_density_linkage_plot_pairs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from membench import cli

    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text('{"curves": []}\n', encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_render(analysis: object, out: Path) -> list[Path]:
        seen["analysis"] = analysis
        seen["out"] = out
        return [out / "one.svg", out / "one.png"]

    monkeypatch.setattr(cli, "render_density_linkage_plots", fake_render)
    out = tmp_path / "plots"
    assert (
        cli.main(
            [
                "beads-ordering-density-linkage-plot",
                "--analysis",
                str(analysis_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert seen == {"analysis": {"curves": []}, "out": out}
