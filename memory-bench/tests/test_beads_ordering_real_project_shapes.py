from __future__ import annotations

import json
from pathlib import Path

import pytest

from membench.beads_ordering.models import (
    BM25FConfig,
    CompactMemory,
    DiscoveryPage,
    OrderingArm,
)
from membench.beads_ordering.real_project_shapes import (
    ProjectShape,
    ShapeProvenance,
    ShapeSampling,
    WorkspaceDiscovery,
    collect_real_project_shapes,
    derive_native_probes,
    discover_beads_workspaces,
    measure_project_shape,
    summarize_real_project_shapes,
    write_real_project_shape_evidence,
)


def _item(memory_id: str, key: str, title: str, excerpt: str, rank: int) -> CompactMemory:
    return CompactMemory(
        id=memory_id,
        key=key,
        title=title,
        lifecycle="active",
        excerpt=excerpt,
        rank=rank,
    )


def _page(query: str, items: tuple[CompactMemory, ...], total: int) -> DiscoveryPage:
    return DiscoveryPage(
        items=items,
        query=query,
        order=OrderingArm.KEY,
        page_size=max(1, len(items)),
        unbounded=True,
        total_matched=total,
        complete=True,
        candidate_digest="d" * 64,
        bm25f=BM25FConfig(),
    )


def test_discovery_model_normalizes_null_match_provenance_on_empty_search() -> None:
    item = CompactMemory.model_validate(
        {
            "id": "private-id",
            "key": "private-key",
            "title": "Private title",
            "lifecycle": "active",
            "excerpt": "Private compact text",
            "matched_fields": None,
            "rank": 1,
        }
    )

    assert item.matched_fields == ()


class _FakeClient:
    def __init__(self, inventory: tuple[CompactMemory, ...], match_counts: dict[str, int]):
        self.inventory = inventory
        self.match_counts = match_counts
        self.queries: list[str] = []

    def page(self, query: str, arm: OrderingArm, continuation: str = "") -> DiscoveryPage:
        assert arm is OrderingArm.KEY
        assert continuation == ""
        self.queries.append(query)
        if query == "":
            return _page(query, self.inventory, len(self.inventory))
        return _page(query, (), self.match_counts.get(query, 1))


def _provenance() -> ShapeProvenance:
    return ShapeProvenance(
        mem_git_sha="a" * 40,
        mem_git_diff_sha256="b" * 64,
        beads_git_sha="c" * 40,
        beads_git_diff_sha256="d" * 64,
        beads_bin_sha256="e" * 64,
        collector_source_sha256="f" * 64,
        mem_git_dirty=False,
        beads_git_dirty=True,
    )


def test_native_probe_derivation_is_deterministic_bounded_and_stratified() -> None:
    items = (
        _item(
            "private-id-a",
            "deploy-rollback-policy",
            "Deploy rollback policy",
            "Use the durable release fence before rollback begins.",
            1,
        ),
        _item(
            "private-id-b",
            "release-cursor",
            "Release cursor",
            "Advance the durable cursor only after verification succeeds.",
            2,
        ),
    )

    first = derive_native_probes(items, max_per_kind=2, seed=5879)
    second = derive_native_probes(tuple(reversed(items)), max_per_kind=2, seed=5879)

    assert first == second
    assert set(first) == {"key-token", "content-bigram"}
    assert all(len(values) <= 2 for values in first.values())
    assert all(values for values in first.values())
    assert all("private-id" not in probe for values in first.values() for probe in values)


def test_project_measurement_keeps_queries_private_and_returns_counts_only() -> None:
    secret_key = "customer-secret-rollback"
    secret_body = "The confidential fence token rotates after deployment."
    inventory = (_item("private-id", secret_key, "Confidential rollout", secret_body, 1),)
    probes = derive_native_probes(inventory, max_per_kind=4, seed=7)
    match_counts = {probe: 3 for values in probes.values() for probe in values}
    client = _FakeClient(inventory, match_counts)

    shape = measure_project_shape(client, max_probes_per_kind=4, seed=7)

    assert shape.memory_count == 1
    assert shape.key_match_counts == tuple(3 for _ in probes["key-token"])
    assert shape.content_match_counts == tuple(3 for _ in probes["content-bigram"])
    serialized = json.dumps(shape.to_private_free_dict())
    assert secret_key not in serialized
    assert "confidential" not in serialized.lower()
    assert "private-id" not in serialized
    assert set(shape.to_private_free_dict()) == {
        "memory_count",
        "key_probe_count",
        "content_probe_count",
        "link_observation_count",
    }


def test_aggregate_evidence_reports_distributions_without_project_rows() -> None:
    shapes = [
        ProjectShape(memory_count=4, key_match_counts=(1, 2), content_match_counts=(3, 4)),
        ProjectShape(memory_count=80, key_match_counts=(6, 12), content_match_counts=(20, 60)),
    ]
    sampling = ShapeSampling(
        workspace_candidates=7,
        unique_workspaces=5,
        workspaces_scanned=4,
        workspaces_with_memories=2,
        unique_memory_snapshots=2,
        duplicate_memory_snapshots=0,
        failures_by_code={"unsupported-memory-surface": 1},
    )

    evidence = summarize_real_project_shapes(
        shapes,
        sampling=sampling,
        provenance=_provenance(),
        experimental_candidate_levels=(10, 40, 150),
        page_sizes=(5, 10, 20, 50),
    )

    assert evidence["privacy_projection"] == "aggregate counts and distributions only"
    assert evidence["sampling_frame"] == {
        "discovery": "recursive .beads workspaces beneath operator-supplied roots",
        "exclusions": [
            ".git",
            ".mem",
            ".venv",
            "node_modules",
            "__pycache__",
            "memory-bench/results",
        ],
        "deduplication": "identical compact candidate snapshots counted once",
        "failure_handling": "fixed aggregate categories only; diagnostics discarded",
    }
    assert "projects" not in evidence
    assert evidence["corpus_size"]["p50"] == 42.0
    assert evidence["match_set_size"]["all_native_probes"]["p90"] == pytest.approx(32.0)
    assert evidence["match_set_size"]["all_native_probes"]["fraction_gt_page_size"]["5"] == 0.5
    assert evidence["link_density"]["available"] is False
    assert evidence["link_density"]["reason"] == "canonical-memory-references-not-observable"
    assert (
        evidence["experimental_regime_comparison"]["40"]["fraction_observed_at_or_below"] == 0.875
    )


def test_writer_never_serializes_memory_content_queries_paths_or_project_ids(
    tmp_path: Path,
) -> None:
    secret = "DO-NOT-SERIALIZE-personal-message"
    shapes = [ProjectShape(memory_count=5, key_match_counts=(1, 5), content_match_counts=(2, 4))]
    sampling = ShapeSampling(
        workspace_candidates=1,
        unique_workspaces=1,
        workspaces_scanned=1,
        workspaces_with_memories=1,
        unique_memory_snapshots=1,
        duplicate_memory_snapshots=0,
        failures_by_code={secret: 1},
    )

    manifest = write_real_project_shape_evidence(
        shapes,
        tmp_path,
        sampling=sampling,
        provenance=_provenance(),
    )

    assert manifest["artifact_names"] == ["analysis.json", "report.md"]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    assert secret not in combined
    assert "/home/" not in combined
    assert "query" not in combined.lower()
    assert "memory body" not in combined.lower()
    assert "Sampling frame" in combined
    assert "Probe derivation" in combined
    assert "Snapshot deduplication" in combined
    assert not (tmp_path / "raw.jsonl").exists()


def test_workspace_discovery_deduplicates_overlapping_roots_and_excludes_results(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "nested" / "second"
    excluded = tmp_path / "memory-bench" / "results" / "experiment"
    hidden_generated = tmp_path / ".mem" / "generated-workspace"
    for workspace in (first, second, excluded, hidden_generated):
        (workspace / ".beads").mkdir(parents=True)

    discovery = discover_beads_workspaces((tmp_path, tmp_path / "nested"))

    assert discovery.workspace_candidates == 3
    assert discovery.workspaces == (first.resolve(), second.resolve())


def test_collection_deduplicates_identical_memory_snapshots_and_sanitizes_errors(
    tmp_path: Path,
) -> None:
    workspaces = tuple(tmp_path / name for name in ("one", "two", "three", "broken"))
    inventory = (_item("id", "release-fence", "Release fence", "durable release fence", 1),)

    class Client(_FakeClient):
        def __init__(self, digest: str, *, broken: bool = False):
            super().__init__(inventory, {})
            self.digest = digest
            self.broken = broken

        def page(self, query: str, arm: OrderingArm, continuation: str = "") -> DiscoveryPage:
            if self.broken:
                raise RuntimeError("private path and diagnostic")
            page = super().page(query, arm, continuation)
            return page.model_copy(update={"candidate_digest": self.digest})

    clients = {
        workspaces[0]: Client("a" * 64),
        workspaces[1]: Client("a" * 64),
        workspaces[2]: Client("b" * 64),
        workspaces[3]: Client("c" * 64, broken=True),
    }
    discovery = WorkspaceDiscovery(workspace_candidates=4, workspaces=workspaces)

    shapes, sampling = collect_real_project_shapes(
        discovery,
        client_factory=lambda workspace: clients[workspace],
        max_probes_per_kind=2,
    )

    assert len(shapes) == 2
    assert sampling.workspaces_scanned == 3
    assert sampling.workspaces_with_memories == 3
    assert sampling.unique_memory_snapshots == 2
    assert sampling.duplicate_memory_snapshots == 1
    assert sampling.failures_by_code == {"bd-error": 1}


def test_cli_wires_private_collection_to_aggregate_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from membench import cli

    beads_bin = tmp_path / "bd"
    beads_bin.write_bytes(b"binary")
    beads_repo = tmp_path / "beads-repo"
    beads_repo.mkdir()
    root = tmp_path / "projects"
    root.mkdir()
    out = tmp_path / "evidence"
    discovery = WorkspaceDiscovery(workspace_candidates=1, workspaces=(root,))
    sampling = ShapeSampling(
        workspace_candidates=1,
        unique_workspaces=1,
        workspaces_scanned=1,
        workspaces_with_memories=1,
        unique_memory_snapshots=1,
        duplicate_memory_snapshots=0,
        failures_by_code={},
    )
    shapes = [ProjectShape(memory_count=3, key_match_counts=(1,), content_match_counts=(2,))]
    seen: dict[str, object] = {}

    monkeypatch.setattr(cli, "discover_beads_workspaces", lambda roots: discovery)

    def fake_collect(
        selected: WorkspaceDiscovery, **kwargs: object
    ) -> tuple[list[ProjectShape], ShapeSampling]:
        seen["discovery"] = selected
        seen["collect_kwargs"] = kwargs
        return shapes, sampling

    monkeypatch.setattr(cli, "collect_real_project_shapes", fake_collect)

    def fake_write(
        selected_shapes: list[ProjectShape], selected_out: Path, **kwargs: object
    ) -> dict[str, object]:
        seen["shapes"] = selected_shapes
        seen["out"] = selected_out
        seen["write_kwargs"] = kwargs
        return {"artifact_names": ["analysis.json", "report.md"]}

    monkeypatch.setattr(cli, "write_real_project_shape_evidence", fake_write)
    monkeypatch.setattr(cli, "git_sha", lambda repo: "a" * 40 if repo == beads_repo else "b" * 40)
    monkeypatch.setattr(cli, "git_diff_sha256", lambda repo: "c" * 64)
    monkeypatch.setattr(cli, "git_dirty", lambda repo: repo == beads_repo)
    monkeypatch.setattr(cli, "file_sha256", lambda path: "d" * 64)

    exit_code = cli.main(
        [
            "beads-ordering-real-project-shapes",
            "--root",
            str(root),
            "--beads-repo",
            str(beads_repo),
            "--beads-bin",
            str(beads_bin),
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert seen["discovery"] is discovery
    assert seen["shapes"] == shapes
    assert seen["out"] == out.resolve()
    provenance = seen["write_kwargs"]["provenance"]
    assert isinstance(provenance, ShapeProvenance)
    assert provenance.beads_git_sha == "a" * 40
    assert provenance.mem_git_sha == "b" * 40
    assert provenance.beads_bin_sha256 == "d" * 64
