from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from membench.beads_ordering.density_linkage_agent_evidence import hierarchical_summary
from membench.beads_ordering.models import OrderingRunResult

_PROTOCOL_FIELDS = (
    "task_id",
    "query",
    "corpus_size",
    "arm",
    "mode",
    "repeat",
    "page_size",
    "total_matched",
    "primary_rank",
    "primary_page",
    "acceptable_rank",
    "acceptable_page",
    "page_one_acceptable_visible",
    "beads_git_sha",
    "beads_git_dirty",
    "beads_git_diff_sha256",
    "beads_bin_sha256",
    "structural_order_source_git_sha",
    "agent_cli_version",
)
_MANIFEST_HELD_FIELDS = (
    "schema_version",
    "shard_index",
    "shard_count",
    "planned_cell_count",
    "variant_count",
    "variant_ids",
    "mem_git_sha",
    "mem_git_dirty",
    "mem_git_diff_sha256",
    "beads_git_sha",
    "beads_git_dirty",
    "beads_git_diff_sha256",
    "beads_bin_sha256",
    "structural_order_source_git_sha",
    "arms",
    "page_sizes",
    "modes",
    "repeats",
    "repeat_start",
    "order_seed",
    "max_tool_calls",
    "agent_cli_version",
    "agent_settings",
    "sharding_convention",
    "bm25f",
    "selection_run_count",
    "base_fixture_manifest_sha256",
    "density_linkage_manifest_sha256",
    "preregistration_sha256",
    "agent_sharding_amendment_sha256",
    "agent_auth",
)
_MANIFEST_WAVE_SHARED_FIELDS = tuple(
    field
    for field in _MANIFEST_HELD_FIELDS
    if field
    not in {
        "shard_index",
        "planned_cell_count",
        "variant_count",
        "variant_ids",
    }
)
_MANIFEST_REQUIRED_FIELDS = (*_MANIFEST_HELD_FIELDS, "agent_model", "selection_manifest_sha256")


def _index_rows(rows: Sequence[OrderingRunResult], *, wave: str) -> dict[str, OrderingRunResult]:
    index: dict[str, OrderingRunResult] = {}
    for row in rows:
        if row.run_id in index:
            raise ValueError(f"{wave} contains duplicate run ID {row.run_id}")
        index[row.run_id] = row
    if not index:
        raise ValueError(f"{wave} contains no observations")
    return index


def _single_model(rows: Sequence[OrderingRunResult], *, wave: str) -> str:
    models = {row.agent_model for row in rows}
    if len(models) != 1 or not next(iter(models)):
        raise ValueError(f"{wave} must contain exactly one non-empty agent model")
    return next(iter(models))


def _manifest_index(
    manifests: Sequence[Mapping[str, object]], *, wave: str
) -> dict[int, Mapping[str, object]]:
    if not manifests:
        raise ValueError(f"{wave} contains no shard manifests")
    index: dict[int, Mapping[str, object]] = {}
    for manifest in manifests:
        missing = [field for field in _MANIFEST_REQUIRED_FIELDS if field not in manifest]
        if missing:
            raise ValueError(f"{wave} manifest is missing required fields: {', '.join(missing)}")
        shard = manifest["shard_index"]
        if not isinstance(shard, int):
            raise ValueError(f"{wave} manifest shard_index must be an integer")
        if shard in index:
            raise ValueError(f"{wave} contains duplicate shard manifest {shard}")
        index[shard] = manifest
    expected = set(range(len(manifests)))
    if set(index) != expected:
        raise ValueError(f"{wave} shard manifests must cover {sorted(expected)}")
    for manifest in manifests:
        if manifest["shard_count"] != len(manifests):
            raise ValueError(f"{wave} shard_count does not match manifest count")
    return index


def validate_replication_wave_manifests(
    bridge_manifests: Sequence[Mapping[str, object]],
    secondary_manifests: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Prove that model and selection-manifest digest are the only wave differences."""

    bridge = _manifest_index(bridge_manifests, wave="bridge")
    secondary = _manifest_index(secondary_manifests, wave="secondary")
    if set(bridge) != set(secondary):
        raise ValueError("replication waves must contain the same shard manifests")

    def wave_model(index: Mapping[int, Mapping[str, object]], wave: str) -> str:
        models = {str(manifest["agent_model"]) for manifest in index.values()}
        if len(models) != 1 or not next(iter(models)):
            raise ValueError(f"{wave} manifests must contain one non-empty agent model")
        return next(iter(models))

    bridge_model = wave_model(bridge, "bridge")
    secondary_model = wave_model(secondary, "secondary")
    if bridge_model == secondary_model:
        raise ValueError("replication manifest waves must use different agent models")

    for wave, index in (("bridge", bridge), ("secondary", secondary)):
        first = index[min(index)]
        for field in (*_MANIFEST_WAVE_SHARED_FIELDS, "selection_manifest_sha256"):
            if any(manifest[field] != first[field] for manifest in index.values()):
                raise ValueError(f"{wave} manifests disagree on shared field {field}")
        selection_sha = first["selection_manifest_sha256"]
        if not isinstance(selection_sha, str) or len(selection_sha) != 64:
            raise ValueError(f"{wave} selection manifest SHA-256 is invalid")

    for shard in sorted(bridge):
        mismatches = [
            field
            for field in _MANIFEST_HELD_FIELDS
            if bridge[shard][field] != secondary[shard][field]
        ]
        if mismatches:
            raise ValueError(
                f"manifest protocol mismatch for shard {shard}: {', '.join(mismatches)}"
            )

    cells_per_wave = 0
    for manifest in bridge.values():
        planned = manifest["planned_cell_count"]
        if not isinstance(planned, int):
            raise ValueError("replication planned_cell_count must be an integer")
        cells_per_wave += planned
    return {
        "valid": True,
        "bridge_model": bridge_model,
        "secondary_model": secondary_model,
        "shard_count": len(bridge),
        "cells_per_wave": cells_per_wave,
        "held_constant_fields": list(_MANIFEST_HELD_FIELDS),
        "allowed_wave_differences": ["agent_model", "selection_manifest_sha256", "started_at"],
    }


def _validate_protocol_pair(bridge: OrderingRunResult, secondary: OrderingRunResult) -> None:
    mismatches = [
        field for field in _PROTOCOL_FIELDS if getattr(bridge, field) != getattr(secondary, field)
    ]
    if mismatches:
        raise ValueError(
            f"held-constant protocol mismatch for {bridge.run_id}: {', '.join(mismatches)}"
        )


def _fraction_saved(reference: int, contender: int) -> float:
    return (reference - contender) / max(1, reference)


def _paired_metrics(
    bridge: OrderingRunResult,
    secondary: OrderingRunResult,
) -> dict[str, float]:
    metrics = {
        "task_success_delta": float(secondary.task_success) - float(bridge.task_success),
        "compact_tokens_to_first_useful_reduction_fraction": _fraction_saved(
            bridge.compact_tokens_to_first_useful,
            secondary.compact_tokens_to_first_useful,
        ),
        "retrieval_tokens_to_first_useful_saved": float(
            bridge.retrieval_tokens_to_first_useful - secondary.retrieval_tokens_to_first_useful
        ),
        "tool_calls_to_first_useful_saved": float(
            bridge.tool_calls_to_first_useful - secondary.tool_calls_to_first_useful
        ),
        "retrieval_tool_calls_saved": float(
            bridge.retrieval_tool_calls - secondary.retrieval_tool_calls
        ),
        "full_recalls_saved": float(bridge.full_recalls - secondary.full_recalls),
        "graph_hops_delta": float(secondary.graph_hops_total - bridge.graph_hops_total),
        "retrieval_latency_ms_saved": bridge.retrieval_latency_ms - secondary.retrieval_latency_ms,
        "server_ordering_ms_delta": secondary.server_ordering_ms - bridge.server_ordering_ms,
        "end_to_end_ms_saved": bridge.end_to_end_ms - secondary.end_to_end_ms,
        "abstention_reduction": float(bridge.abstained) - float(secondary.abstained),
        "premature_stop_reduction": float(bridge.premature_stop) - float(secondary.premature_stop),
    }
    if bridge.pages_to_first_useful is not None and secondary.pages_to_first_useful is not None:
        metrics["pages_to_first_useful_saved"] = float(
            bridge.pages_to_first_useful - secondary.pages_to_first_useful
        )
    if bridge.time_to_first_useful_ms is not None and secondary.time_to_first_useful_ms is not None:
        metrics["time_to_first_useful_ms_saved"] = (
            bridge.time_to_first_useful_ms - secondary.time_to_first_useful_ms
        )
    return metrics


def _failures(rows: Sequence[OrderingRunResult]) -> dict[str, object]:
    run_ids = sorted(row.run_id for row in rows if row.failure is not None)
    return {"count": len(run_ids), "run_ids": run_ids}


def compare_density_linkage_replication(
    bridge_rows: Sequence[OrderingRunResult],
    secondary_rows: Sequence[OrderingRunResult],
    task_metadata: Mapping[str, Mapping[str, object]],
    *,
    bootstrap_seed: int = 5880,
    bootstrap_resamples: int = 5000,
) -> dict[str, object]:
    """Compare two complete decision-edge waves while varying only the model."""

    if bootstrap_resamples < 1:
        raise ValueError("bootstrap resamples must be positive")
    bridge_index = _index_rows(bridge_rows, wave="bridge")
    secondary_index = _index_rows(secondary_rows, wave="secondary")
    bridge_ids = set(bridge_index)
    secondary_ids = set(secondary_index)
    missing = {
        "bridge": sorted(secondary_ids - bridge_ids),
        "secondary": sorted(bridge_ids - secondary_ids),
    }
    if missing["bridge"] or missing["secondary"]:
        raise ValueError("replication waves must contain identical run ID sets")

    bridge_model = _single_model(bridge_rows, wave="bridge")
    secondary_model = _single_model(secondary_rows, wave="secondary")
    if bridge_model == secondary_model:
        raise ValueError("replication waves must use different agent models")

    grouped: dict[tuple[str, str], dict[str, list[tuple[str, str, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    comparable_pairs = 0
    for run_id in sorted(bridge_ids):
        bridge = bridge_index[run_id]
        secondary = secondary_index[run_id]
        _validate_protocol_pair(bridge, secondary)
        metadata = task_metadata.get(bridge.task_id)
        if metadata is None:
            raise ValueError(f"missing density/linkage metadata for {bridge.task_id}")
        if bridge.failure is not None or secondary.failure is not None:
            continue
        comparable_pairs += 1
        linkage = str(metadata["linkage_level"])
        family = str(metadata["graph_family"])
        task = str(metadata["base_task_id"])
        for metric, value in _paired_metrics(bridge, secondary).items():
            grouped[(linkage, bridge.arm.value)][metric].append((family, task, value))

    mean_metrics = {
        "task_success_delta",
        "abstention_reduction",
        "premature_stop_reduction",
        "graph_hops_delta",
    }
    contrasts: list[dict[str, object]] = []
    for group_index, ((linkage, arm), metrics) in enumerate(sorted(grouped.items())):
        contrasts.append(
            {
                "linkage_level": linkage,
                "arm": arm,
                "pair_count": len(metrics["task_success_delta"]),
                **{
                    metric: hierarchical_summary(
                        values,
                        statistic="mean" if metric in mean_metrics else "median",
                        seed=bootstrap_seed + group_index * 100 + metric_index,
                        resamples=bootstrap_resamples,
                    )
                    for metric_index, (metric, values) in enumerate(sorted(metrics.items()))
                },
            }
        )

    return {
        "schema_version": 1,
        "evidence_kind": "paired same-protocol bridge versus secondary-model outcomes",
        "bridge_model": bridge_model,
        "secondary_model": secondary_model,
        "expected_pair_count": len(bridge_ids),
        "comparable_pair_count": comparable_pairs,
        "missing_run_ids": missing,
        "infrastructure_failures": {
            "bridge": _failures(bridge_rows),
            "secondary": _failures(secondary_rows),
        },
        "bootstrap": {
            "confidence_level": 0.9,
            "seed": bootstrap_seed,
            "resamples": bootstrap_resamples,
            "cluster_order": ["graph_family", "base_task_id"],
        },
        "sign_convention": {
            "saved_or_reduction": "positive values favor the secondary model",
            "task_success_delta": "secondary minus bridge",
            "graph_hops_delta": "secondary minus bridge; direction is descriptive",
            "server_ordering_ms_delta": "secondary minus bridge; model should not affect it",
        },
        "contrasts": contrasts,
    }


def _format_interval(summary: object, *, percent: bool = False) -> str:
    if not isinstance(summary, Mapping):
        return "n/a"
    estimate = summary.get("estimate")
    low = summary.get("low")
    high = summary.get("high")
    if not isinstance(estimate, (int, float)):
        return "n/a"
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return "n/a"
    scale = 100 if percent else 1
    suffix = "%" if percent else ""
    return (
        f"{float(estimate) * scale:+.1f}{suffix} "
        f"[{float(low) * scale:+.1f}, {float(high) * scale:+.1f}]{suffix}"
    )


def _render_report(analysis: Mapping[str, object]) -> str:
    failures = analysis["infrastructure_failures"]
    assert isinstance(failures, Mapping)
    bridge_failures = failures["bridge"]
    secondary_failures = failures["secondary"]
    assert isinstance(bridge_failures, Mapping)
    assert isinstance(secondary_failures, Mapping)
    lines = [
        "# Density/linkage model replication",
        "",
        (
            f"{analysis['comparable_pair_count']}/{analysis['expected_pair_count']} "
            "paired cells were comparable. "
            f"Infrastructure failures: bridge={bridge_failures['count']}, "
            f"secondary={secondary_failures['count']}."
        ),
        "",
        "Positive savings/reductions favor the secondary model. Intervals are 90% "
        "hierarchical cluster bootstraps (graph family, then task).",
        "",
        "| links | arm | pairs | success delta [90% CI] | pages saved [90% CI] | "
        "compact-token reduction [90% CI] |",
        "|---|---|---:|---:|---:|---:|",
    ]
    contrasts = analysis["contrasts"]
    assert isinstance(contrasts, Sequence)
    for contrast in contrasts:
        assert isinstance(contrast, Mapping)
        compact_reduction = _format_interval(
            contrast.get("compact_tokens_to_first_useful_reduction_fraction"), percent=True
        )
        lines.append(
            f"| {contrast['linkage_level']} | {contrast['arm']} | {contrast['pair_count']} | "
            f"{_format_interval(contrast.get('task_success_delta'), percent=True)} | "
            f"{_format_interval(contrast.get('pages_to_first_useful_saved'))} | "
            f"{compact_reduction} |"
        )
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_density_linkage_replication_comparison(
    bridge_rows: Sequence[OrderingRunResult],
    secondary_rows: Sequence[OrderingRunResult],
    task_metadata: Mapping[str, Mapping[str, object]],
    out: Path,
    *,
    provenance: Mapping[str, object],
    bootstrap_seed: int = 5880,
    bootstrap_resamples: int = 5000,
) -> dict[str, object]:
    analysis = compare_density_linkage_replication(
        bridge_rows,
        secondary_rows,
        task_metadata,
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    out.mkdir(parents=True, exist_ok=True)
    analysis_path = out / "analysis.json"
    report_path = out / "report.md"
    analysis_path.write_text(
        json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path.write_text(_render_report(analysis), encoding="utf-8")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "privacy_projection": (
            "paired metrics only; excludes queries, model text, failure diagnostics, "
            "credentials, and local paths"
        ),
        "artifact_sha256s": {
            "analysis.json": _sha256(analysis_path),
            "report.md": _sha256(report_path),
        },
        "provenance": dict(provenance),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
