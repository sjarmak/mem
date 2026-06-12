"""Ablation-grid dual scoring over the admitted bundle pool (mem-apg.3).

The headline-producing assembly, re-anchored per the resolved decision (mem-bfk /
dec-gck, 2026-06-12): the mem-apg headline is **efficiency-vs-information ONLY**
(both recurrence axes dead -- mem-apg.3.1 INSUFFICIENT_POWER, mem-75t.10 alias
audit), scored on task bundles with the dual verifier (mem-75t.7.5): the
efficiency leg (tokens / turns / tool calls) is the headline metric, the quality
leg (gold-test reproduction) the guard, reported as PER-BUNDLE PAIRED DELTAS,
never pooled means (the mem-75t.7.6 gate instruction).

This module scores the grid from the gate probe's CACHED real runs (the 2026-06-11
Docker/OAuth executions under ``.mem/probe/jobs/``): per (bundle, condition) it
re-harvests the candidate diff from the persisted stream transcript and runs the
dual verifier with the LIVE gold-test repro runner -- new local execution, no new
agent runs. The information ladder it can execute today:

- ``none``   -- stateless floor (cached agent runs);
- ``oracle`` -- gold-diff file-list ceiling (cached agent runs);
- ``ours``   -- NOT runnable: retrieval-v1 over this store yields zero lessons
  (the lessons table is empty corpus-wide; no distiller has run), so the injected
  payload would carry no information. `ours_rung_evidence` measures that
  structural emptiness per bundle instead of burning agent runs on a
  dead-by-construction arm (the mem-apg.3.1 lesson);
- ``builtin`` / ``ours+builtin`` -- deferred to mem-whi (agent's opaque memory).

ZFC: pure plumbing -- file IO, replay, arithmetic pairing. Semantic judgement
lives in the agent runs (cached) and the test runner (delegated).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from membench.grading.dual_verifier import ReproRunner, RunResult, score_run
from membench.grading.probe_direct import ProbeEfficiency
from membench.harbor.probe_gate import (
    CONDITIONS,
    Runner,
    harvest_candidate,
    load_stream,
    metric_gap_stats,
    paired_deltas,
)
from membench.memory_systems.ours_system import OursQuery, RetrieveRunner, _default_runner
from membench.schemas.bundle import TaskBundle

# The grid rescores the gate's executed runs, so its conditions ARE the gate's --
# one shared constant, not a copy to keep in sync.
GRID_CONDITIONS: tuple[str, ...] = CONDITIONS


class GridConditionResult(BaseModel):
    """One (bundle, condition) dual-verifier readout over a cached agent run."""

    model_config = ConfigDict(frozen=True)

    work_id: str
    condition: str
    # Quality guard: the dual verifier's two legs. `repro_passed` is None when the
    # direct leg fell back to diff similarity (`repro_error` says why).
    score_direct: float | None
    score_artifact: float | None
    direct_mode: str
    repro_passed: bool | None
    repro_error: str | None
    diff_sim_combined: float | None
    # Efficiency headline.
    efficiency: ProbeEfficiency
    candidate_files: tuple[str, ...]

    def metrics(self) -> dict[str, float | None]:
        """The per-condition metric vector the pairing arithmetic consumes."""
        return {
            "score_direct": self.score_direct,
            "score_artifact": self.score_artifact,
            "repro_passed": None if self.repro_passed is None else float(self.repro_passed),
            "input_tokens": (
                None
                if self.efficiency.input_tokens is None
                else float(self.efficiency.input_tokens)
            ),
            "output_tokens": (
                None
                if self.efficiency.output_tokens is None
                else float(self.efficiency.output_tokens)
            ),
            "turns": float(self.efficiency.turns),
            "tool_calls": float(self.efficiency.tool_calls),
        }


class GridPair(BaseModel):
    """One bundle's paired grid readout; ``deltas`` is oracle - none per metric
    (a metric absent on either side is omitted, never imputed)."""

    model_config = ConfigDict(frozen=True)

    work_id: str
    none: GridConditionResult
    oracle: GridConditionResult
    deltas: tuple[tuple[str, float], ...]


class OursRungEvidence(BaseModel):
    """Why the ``ours`` rung is not executed: the per-bundle retrieval-v1 readout
    showing the injectable payload carries no lesson content (the rung's
    information source). Recorded as grid provenance, not a score."""

    model_config = ConfigDict(frozen=True)

    work_id: str
    items: int = Field(ge=0)
    items_with_lessons: int = Field(ge=0)
    total_matched: int = Field(ge=0)


def score_grid_condition(
    bundle: TaskBundle,
    condition: str,
    job_dir: Path,
    *,
    clone: Path,
    test_runner: ReproRunner,
    runner: Runner = subprocess.run,
    worktree_root: Path = Path("/tmp"),
) -> GridConditionResult:
    """Dual-score one cached (bundle, condition) run: load the persisted stream,
    re-harvest the candidate diff (same replay machinery as the gate), and run
    `score_run` with the live repro runner."""
    stream = load_stream(job_dir)
    candidate = harvest_candidate(
        stream, bundle, clone=clone, runner=runner, worktree_root=worktree_root
    )
    candidate_diff = candidate.diff_by_file()
    candidate_files = tuple(sorted(candidate_diff))
    run = RunResult(
        candidate_diff=candidate_diff,
        identified_files=candidate_files,
        transcript=stream,
    )
    dual, _ = score_run(bundle, run, test_runner=test_runner)
    direct = dual.direct
    if dual.efficiency is None:
        # Unreachable while `transcript=stream` above: the grid's headline metric
        # must never be silently zero-filled.
        raise ValueError(f"no efficiency readout for {bundle.work_id} [{condition}]")
    return GridConditionResult(
        work_id=bundle.work_id,
        condition=condition,
        score_direct=dual.score_direct,
        score_artifact=dual.score_artifact,
        direct_mode=direct.mode,
        repro_passed=None if direct.test_outcome is None else direct.test_outcome.passed,
        repro_error=direct.repro_error,
        diff_sim_combined=None if direct.diff_sim is None else direct.diff_sim.combined,
        efficiency=dual.efficiency,
        candidate_files=candidate_files,
    )


def pair_grid(none: GridConditionResult, oracle: GridConditionResult) -> GridPair:
    """Pair one bundle's two condition results (oracle - none deltas)."""
    if none.work_id != oracle.work_id:
        raise ValueError(f"work_id mismatch: {none.work_id!r} vs {oracle.work_id!r}")
    if none.condition != "none" or oracle.condition != "oracle":
        raise ValueError(
            f"pair_grid needs (none, oracle), got ({none.condition!r}, {oracle.condition!r})"
        )
    deltas = paired_deltas(none.metrics(), oracle.metrics())
    return GridPair(work_id=none.work_id, none=none, oracle=oracle, deltas=deltas)


def ours_rung_evidence(
    bundle: TaskBundle,
    *,
    mem_bin: str,
    store_path: Path,
    scope: str = "same_rig_temporal",
    runner: RetrieveRunner | None = None,
) -> OursRungEvidence:
    """Measure what the ``ours`` rung WOULD inject for this bundle: retrieval-v1's
    item count and how many items carry lesson payloads (the arm's information
    content -- `ours_system` injects citation + lessons only). Zero lessons across
    the pool is the structural evidence the grid report cites for not executing
    the rung. Retrieval goes through the ours ARM's own runner so the evidence
    measures exactly what the arm would inject."""
    run = runner if runner is not None else _default_runner(mem_bin)
    result = run(OursQuery(work_id=bundle.work_id, scope=scope, store_path=str(store_path)))
    items = result.get("items", [])
    return OursRungEvidence(
        work_id=bundle.work_id,
        items=len(items),
        items_with_lessons=sum(1 for item in items if item.get("lessons")),
        total_matched=int(result.get("total_matched", len(items))),
    )


def summarize_grid(
    pairs: Sequence[GridPair],
    ours_evidence: Sequence[OursRungEvidence],
) -> dict[str, Any]:
    """The grid's DATA product (the mem-apg.4 report input): per-bundle paired
    deltas (the headline shape -- never pooled means alone), per-metric gap stats,
    quality-guard pass counts, and the rung-availability record. Verdict prose is
    the orchestrator's, never computed here."""
    if not pairs:
        raise ValueError("summarize_grid needs at least one (none, oracle) pair")
    delta_maps = [dict(pair.deltas) for pair in pairs]
    # The ours-rung verdict is DERIVED from the evidence just gathered, never
    # asserted from a constant -- once a distiller populates lessons, the summary
    # flips to payload_available on its own instead of contradicting its evidence.
    lesson_items = sum(e.items_with_lessons for e in ours_evidence)
    ours_rung: dict[str, Any] = {
        "status": "not_executable" if lesson_items == 0 else "payload_available",
        "reason": (
            f"retrieval-v1 returned {lesson_items} lesson-bearing item(s) across "
            f"{len(ours_evidence)} bundle(s); the ours arm injects citation+lessons only"
        ),
        "evidence": [e.model_dump() for e in ours_evidence],
    }
    per_bundle = [
        {
            "work_id": pair.work_id,
            "none": pair.none.metrics(),
            "oracle": pair.oracle.metrics(),
            "deltas": deltas,
            "direct_mode": {
                "none": pair.none.direct_mode,
                "oracle": pair.oracle.direct_mode,
            },
        }
        for pair, deltas in zip(pairs, delta_maps, strict=True)
    ]
    return {
        "n_pairs": len(pairs),
        "conditions": list(GRID_CONDITIONS),
        "per_bundle": per_bundle,
        "gaps": metric_gap_stats(delta_maps),
        "quality_guard": {
            "repro_scored_pairs": sum(
                1
                for pair in pairs
                if pair.none.repro_passed is not None and pair.oracle.repro_passed is not None
            ),
            "repro_passed": {
                "none": sum(1 for p in pairs if p.none.repro_passed),
                "oracle": sum(1 for p in pairs if p.oracle.repro_passed),
            },
        },
        "rung_availability": {
            "none": "executed (cached gate-probe agent runs)",
            "oracle": "executed (cached gate-probe agent runs; gold-diff file list)",
            "ours": ours_rung,
            "curated": (
                "degenerate: single consensus backend collapses curated context to the "
                "gold-diff file list == the oracle condition (docs/mem-75t.7.3)"
            ),
            "builtin": "deferred to mem-whi (agent built-in memory)",
            "ours+builtin": "deferred to mem-whi (agent built-in memory)",
        },
    }


def load_grid_ready_work_ids(manifest_path: Path) -> tuple[str, ...]:
    """The admitted work_ids from the fanout-guard manifest
    (``.mem/grid-ready-pool.json``, mem-75t.7.7). Rejected bundles never enter the
    grid -- their issue text mismatches their gold diff's scope."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    admitted = manifest.get("admitted")
    if not admitted:
        raise ValueError(f"no admitted work_ids in {manifest_path}")
    return tuple(admitted)
