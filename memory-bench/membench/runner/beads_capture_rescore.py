from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from membench.runner.bd_receipt_surface import read_receipts
from membench.runner.beads_arm_plan import ArmGridKey, cell_stem
from membench.runner.e1_grid import corpus_fingerprint
from membench.runner.receipt_engagement import engaged_by_receipts
from membench.runner.toolreq_realagent import ToolReqRealAgentTask

ESTABLISH_LEG = "establish"
RECEIPTS_SUFFIX = "receipts.jsonl"
UNKNOWN = "unknown"


class RescoreError(Exception):
    pass


def cells_dir_of(out: Path) -> Path:
    return out.with_name(f"{out.name}.cells")


def receipts_path(cells_dir: Path, key: ArmGridKey, leg: str) -> Path:
    return cells_dir / f"{cell_stem(key)}.{leg}.{RECEIPTS_SUFFIX}"


def _key_of(row: Mapping[str, Any], path: Path) -> ArmGridKey:
    key = row.get("key")
    if not isinstance(key, list) or len(key) != 4:
        raise RescoreError(f"{path}: no (arm, variant, work_id, repeat) key")
    arm_name, variant, work_id, repeat = key
    if not (
        isinstance(arm_name, str)
        and isinstance(variant, str)
        and isinstance(work_id, str)
        and isinstance(repeat, int)
    ):
        raise RescoreError(f"{path}: key {key!r} is not (str, str, str, int)")
    return (arm_name, variant, work_id, repeat)


def _cell_rows(cells_dir: Path) -> dict[ArmGridKey, dict[str, Any]]:
    if not cells_dir.is_dir():
        raise RescoreError(f"{cells_dir}: no cells directory beside the artifact")
    rows: dict[ArmGridKey, dict[str, Any]] = {}
    files: dict[ArmGridKey, Path] = {}
    for path in sorted(cells_dir.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RescoreError(f"{path}: not a readable cell file: {exc}") from exc
        if not isinstance(row, dict):
            raise RescoreError(f"{path}: a cell file holds one object")
        key = _key_of(row, path)
        if key in files:
            raise RescoreError(
                f"cell {cell_stem(key)} appears twice under {cells_dir}: "
                f"{files[key].name} and {path.name}"
            )
        files[key] = path
        rows[key] = row
    return rows


def rescore_cell(
    key: ArmGridKey, row: Mapping[str, Any], receipts: Path, tokens: Collection[str]
) -> dict[str, Any]:
    recorded = row.get("engaged")
    if not isinstance(recorded, bool):
        raise RescoreError(f"cell {cell_stem(key)}: no recorded engaged verdict")
    kept = receipts.exists()
    by_receipts: bool | str = UNKNOWN
    engaged = recorded
    rule: str | None = None
    row_index: int | None = None
    if kept:
        verdict = engaged_by_receipts(read_receipts(receipts), tokens)
        by_receipts = verdict.engaged
        engaged = verdict.engaged
        rule = verdict.rule
        row_index = verdict.row_index
    return {
        "key": list(key),
        "engaged_as_recorded": recorded,
        "receipts": kept,
        "engaged_by_receipts": by_receipts,
        "rule": rule,
        "row_index": row_index,
        "engaged": engaged,
        "changed": engaged != recorded,
    }


def rescore_artifact(out: Path, tasks: Sequence[ToolReqRealAgentTask]) -> dict[str, Any]:
    try:
        artifact = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RescoreError(f"{out}: not a readable artifact: {exc}") from exc
    recorded = artifact.get("corpus_fingerprint") if isinstance(artifact, dict) else None
    fingerprint = corpus_fingerprint(tasks)
    cells_dir = cells_dir_of(out)
    rows = _cell_rows(cells_dir)
    has_receipts = any(receipts_path(cells_dir, key, ESTABLISH_LEG).exists() for key in rows)
    if has_receipts and recorded != fingerprint:
        raise RescoreError(
            f"{out}: bought on corpus {recorded!r}; the corpus given fingerprints "
            f"{fingerprint!r}, so its tokens are not the ones these cells were asked to store"
        )
    tokens_by = {(task.variant, task.work_id): tuple(task.current_opaque_values) for task in tasks}
    per_cell: list[dict[str, Any]] = []
    for key, row in rows.items():
        _arm_name, variant, work_id, _repeat = key
        tokens = tokens_by.get((variant, work_id))
        if tokens is None:
            raise RescoreError(
                f"{out}: cell {cell_stem(key)} names ({variant!r}, {work_id!r}), which the "
                "corpus does not hold"
            )
        per_cell.append(
            rescore_cell(key, row, receipts_path(cells_dir, key, ESTABLISH_LEG), tokens)
        )
    return {
        "out": str(out),
        "corpus_fingerprint": recorded if not has_receipts else fingerprint,
        "cells": len(per_cell),
        "engaged_as_recorded": sum(1 for one in per_cell if one["engaged_as_recorded"]),
        "with_receipts": sum(1 for one in per_cell if one["receipts"]),
        "engaged_rescored": sum(1 for one in per_cell if one["engaged"]),
        "changed": sum(1 for one in per_cell if one["changed"]),
        "per_cell": per_cell,
    }


__all__ = [
    "ESTABLISH_LEG",
    "RECEIPTS_SUFFIX",
    "UNKNOWN",
    "RescoreError",
    "cells_dir_of",
    "receipts_path",
    "rescore_artifact",
    "rescore_cell",
]
