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
from membench.grading.graded import DEFAULT_JUDGE_ROUNDS, RubricJudge, judge_graded
from membench.grading.probe_direct import ProbeEfficiency
from membench.grading.retrieval_leg import RetrievalLeg
from membench.grading.validity_gate import ValidityResult
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
    # Efficiency headline.
    efficiency: ProbeEfficiency
    candidate_files: tuple[str, ...]
    # Graded side-signals (mem-g6a), additive under the binary repro anchor. All
    # default to absent so cached pre-graded result JSONs still load, and a metric
    # absent on a run is omitted from its paired deltas (never imputed). S1
    # ``test_ratio`` is None on the diff-sim fallback (no tests ran); S2 ``diff_sim``
    # is the ALWAYS-ON bounded structural similarity, computed on every run (on the
    # fallback path it is exactly the value the direct leg fell back to); S3 judge
    # fields are populated only when a judge is wired.
    test_ratio: float | None = None
    diff_sim: float | None = None
    judge_score: float | None = None
    judge_confidence: float | None = None
    judge_divergence: float | None = None
    judge_divergence_flagged: bool = False
    # White-box retrieval-correctness leg (M1/M2), additive + None-defaulted so cached
    # pre-M2 result JSONs still load. ``retrieval_target`` is the declared TIAP scoring
    # target (M1); the four metric fields ride ``metrics()`` SEPARATELY from
    # score_direct/judge_score (never folded into a composite). A retrieval-bearing
    # result (``retrieval_recall is not None``) with no declared target is flagged in
    # summarize, not scored silently.
    retrieval_target: str | None = None
    retrieval_precision: float | None = None
    retrieval_recall: float | None = None
    retrieval_mrr: float | None = None
    retrieval_ndcg: float | None = None

    def with_retrieval_leg(self, leg: RetrievalLeg) -> GridConditionResult:
        """Return a copy carrying the white-box retrieval leg (frozen model)."""
        return self.model_copy(
            update={
                "retrieval_target": leg.retrieval_target,
                "retrieval_precision": leg.precision,
                "retrieval_recall": leg.recall,
                "retrieval_mrr": leg.mrr,
                "retrieval_ndcg": leg.ndcg,
            }
        )

    def metrics(self) -> dict[str, float | None]:
        """The per-condition metric vector the pairing arithmetic consumes. The
        graded signals (``test_ratio``/``diff_sim``/``judge_score``) and the white-box
        retrieval leg ride along and pair automatically; a None metric is omitted from
        a bundle's deltas. Retrieval metrics are their own keys — never folded into
        score_direct/judge_score."""
        return {
            "score_direct": self.score_direct,
            "score_artifact": self.score_artifact,
            "repro_passed": None if self.repro_passed is None else float(self.repro_passed),
            "test_ratio": self.test_ratio,
            "diff_sim": self.diff_sim,
            "judge_score": self.judge_score,
            "retrieval_precision": self.retrieval_precision,
            "retrieval_recall": self.retrieval_recall,
            "retrieval_mrr": self.retrieval_mrr,
            "retrieval_ndcg": self.retrieval_ndcg,
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
    judge: RubricJudge | None = None,
    judge_rounds: int = DEFAULT_JUDGE_ROUNDS,
) -> GridConditionResult:
    """Dual-score one cached (bundle, condition) run: load the persisted stream,
    re-harvest the candidate diff (same replay machinery as the gate), and run
    `score_run` with the live repro runner. When ``judge`` is supplied, the S3
    semantic signal is computed too (mem-g6a); left None (the default) the run carries
    only the mechanical S1/S2 signals -- no model call, the offline path."""
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

    graded = None
    if judge is not None:
        graded = judge_graded(
            judge,
            issue_title=bundle.issue_title,
            issue_body=bundle.issue_body,
            candidate_diff=candidate_diff,
            gold_diff=bundle.output.diff_by_file(),
            mechanical_reference=dual.diff_sim.combined,
            rounds=judge_rounds,
        )
    return GridConditionResult(
        work_id=bundle.work_id,
        condition=condition,
        score_direct=dual.score_direct,
        score_artifact=dual.score_artifact,
        direct_mode=direct.mode,
        repro_passed=None if direct.test_outcome is None else direct.test_outcome.passed,
        repro_error=direct.repro_error,
        efficiency=dual.efficiency,
        candidate_files=candidate_files,
        test_ratio=dual.test_ratio,
        diff_sim=dual.diff_sim.combined,
        judge_score=None if graded is None else graded.judge_score,
        judge_confidence=None if graded is None else graded.judge_confidence,
        judge_divergence=None if graded is None else graded.divergence,
        judge_divergence_flagged=graded is not None and graded.divergence_flagged,
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


def _validity_block(validity: Sequence[ValidityResult]) -> dict[str, Any]:
    """The CSB validity-gate report block (mem-g6a): how many bundles' oracles were
    checked, how many passed, and the work_ids + reasons of those that did not. An
    invalid bundle is named so its exclusion from the graded comparison is never
    silent. ``checked == 0`` means no gate ran (no runner was wired), distinct from
    all-valid."""
    return {
        "checked": len(validity),
        "valid": sum(1 for v in validity if v.valid),
        "invalid": [v.work_id for v in validity if not v.valid],
        "evidence": [v.model_dump() for v in validity],
    }


def summarize_grid(
    pairs: Sequence[GridPair],
    ours_evidence: Sequence[OursRungEvidence],
    validity: Sequence[ValidityResult] = (),
) -> dict[str, Any]:
    """The grid's DATA product (the mem-apg.4 report input): per-bundle paired
    deltas (the headline shape -- never pooled means alone), per-metric gap stats,
    quality-guard pass counts, the rung-availability record, and the CSB validity
    gate block. Verdict prose is the orchestrator's, never computed here."""
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
        "validity_gates": _validity_block(validity),
    }


# --- 3-arm pilot (mem-p3w: none-clean / ours / builtin) ----------------------------------

# The pilot's arms, baseline first. ``none-clean`` and ``ours`` are fresh clean-room
# runs (native project memory stripped from the image); ``builtin`` is the cached
# gate-probe ``none`` run relabeled -- the repo-shipped CLAUDE.md/.claude/AGENTS.md
# WERE present in those containers, which is exactly "native memory ON, ours OFF".
THREE_ARM_CONDITIONS: tuple[str, ...] = ("none-clean", "ours", "builtin")

ARM_PROVENANCE: dict[str, str] = {
    "none-clean": (
        "fresh clean-room agent runs: native project memory stripped from the image "
        "(CLAUDE.md, AGENTS.md, .claude, .agents); no injected memory"
    ),
    "ours": (
        "fresh clean-room agent runs + retrieval-v1 citation+lessons injected (D9, "
        "D6 LOO-bounded); bundles whose retrieval is EMPTY reuse the none-clean run "
        "(the task would be byte-identical, so the delta is 0 by construction and a "
        "fresh run would only measure sampling noise)"
    ),
    "builtin": (
        "cached gate-probe `none` runs (2026-06-11 Docker/OAuth executions): the "
        "repo-shipped native project memory (CLAUDE.md, AGENTS.md, .claude, .agents) "
        "was present in the image -- native memory ON, no injected memory"
    ),
}


class ThreeArmRow(BaseModel):
    """One bundle's 3-arm readout. Deltas are arm - none-clean (the clean-room
    baseline) per metric; a metric absent on either side is omitted, never
    imputed. ``ours_retrieval_empty`` marks the reuse case: retrieval returned no
    payload, so the ours leg IS the none-clean run (deltas exactly 0)."""

    model_config = ConfigDict(frozen=True)

    work_id: str
    none_clean: GridConditionResult
    ours: GridConditionResult
    builtin: GridConditionResult
    ours_retrieval_empty: bool
    deltas_ours: tuple[tuple[str, float], ...]
    deltas_builtin: tuple[tuple[str, float], ...]
    # ours - builtin: the bead's headline comparison -- does our system beat the
    # agent's NATIVE memory, not just the clean-room floor.
    deltas_ours_vs_builtin: tuple[tuple[str, float], ...]


def as_condition(result: GridConditionResult, condition: str) -> GridConditionResult:
    """The same scored readout relabeled as another arm -- the builtin relabel
    (cached ``none`` run -> ``builtin``) and the empty-retrieval ours reuse
    (``none-clean`` run -> ``ours``). Frozen model: returns a new instance."""
    return result.model_copy(update={"condition": condition})


def three_arm_row(
    none_clean: GridConditionResult,
    ours: GridConditionResult,
    builtin: GridConditionResult,
    *,
    ours_retrieval_empty: bool,
) -> ThreeArmRow:
    """Assemble one bundle's 3-arm row (arm - none-clean deltas). Mismatched
    work_ids or mislabeled conditions are caller bugs -- raise."""
    arms = (none_clean, ours, builtin)
    got = tuple(arm.condition for arm in arms)
    if got != THREE_ARM_CONDITIONS:
        raise ValueError(f"three_arm_row needs conditions {THREE_ARM_CONDITIONS}, got {got}")
    work_ids = {arm.work_id for arm in arms}
    if len(work_ids) != 1:
        raise ValueError(f"work_id mismatch across arms: {sorted(work_ids)}")
    baseline = none_clean.metrics()
    return ThreeArmRow(
        work_id=none_clean.work_id,
        none_clean=none_clean,
        ours=ours,
        builtin=builtin,
        ours_retrieval_empty=ours_retrieval_empty,
        deltas_ours=paired_deltas(baseline, ours.metrics()),
        deltas_builtin=paired_deltas(baseline, builtin.metrics()),
        deltas_ours_vs_builtin=paired_deltas(builtin.metrics(), ours.metrics()),
    )


def _arm_gap_stats(delta_maps: Sequence[dict[str, float]]) -> dict[str, Any]:
    """`metric_gap_stats` with its pair-era count key renamed: the 3-arm summary
    compares an arm against the clean-room baseline, not oracle against none."""
    gaps = metric_gap_stats(delta_maps)
    for stats in gaps.values():
        stats["n_arm_gt_baseline"] = stats.pop("n_oracle_gt_none")
    return gaps


def _scoring_target_block(rows: Sequence[ThreeArmRow]) -> dict[str, str]:
    """Validate + collect the declared TIAP scoring targets per arm (M1). A result
    that COMPUTED a retrieval leg (``retrieval_recall is not None``) but declared no
    ``retrieval_target`` is a silent-scoring bug — raise, naming the arm. Returns
    ``{arm: target}`` for every arm that declared one (retrieval-bearing arms only;
    a non-retrieving arm legitimately declares nothing)."""
    targets: dict[str, str] = {}
    for row in rows:
        for result in (row.none_clean, row.ours, row.builtin):
            if result.retrieval_recall is not None and result.retrieval_target is None:
                raise ValueError(
                    f"arm {result.condition!r} (bundle {result.work_id!r}) computed a "
                    "retrieval leg with no declared retrieval_target (M1 TIAP): a "
                    "retrieval-bearing result must declare its scoring target, not "
                    "score silently"
                )
            if result.retrieval_target is not None:
                targets.setdefault(result.condition, result.retrieval_target)
    return targets


def summarize_grid_3arm(
    rows: Sequence[ThreeArmRow],
    ours_evidence: Sequence[OursRungEvidence],
    validity: Sequence[ValidityResult] = (),
) -> dict[str, Any]:
    """The 3-arm pilot's DATA product: per-bundle metrics + paired deltas vs the
    clean-room baseline (never pooled means alone), per-comparison gap stats, the
    quality-guard pass counts per arm, retrieval coverage, the CSB validity gate
    block, and each arm's provenance. Verdict prose is the orchestrator's, never
    computed here."""
    if not rows:
        raise ValueError("summarize_grid_3arm needs at least one three-arm row")
    scoring_target = _scoring_target_block(rows)
    per_bundle = [
        {
            "work_id": row.work_id,
            "none-clean": row.none_clean.metrics(),
            "ours": row.ours.metrics(),
            "builtin": row.builtin.metrics(),
            "deltas": {
                "ours": dict(row.deltas_ours),
                "builtin": dict(row.deltas_builtin),
                "ours_vs_builtin": dict(row.deltas_ours_vs_builtin),
            },
            "ours_retrieval_empty": row.ours_retrieval_empty,
            "direct_mode": {
                "none-clean": row.none_clean.direct_mode,
                "ours": row.ours.direct_mode,
                "builtin": row.builtin.direct_mode,
            },
        }
        for row in rows
    ]
    return {
        "n_bundles": len(rows),
        "conditions": list(THREE_ARM_CONDITIONS),
        "per_bundle": per_bundle,
        "gaps": {
            "ours_vs_none_clean": _arm_gap_stats([dict(row.deltas_ours) for row in rows]),
            "builtin_vs_none_clean": _arm_gap_stats([dict(row.deltas_builtin) for row in rows]),
            "ours_vs_builtin": _arm_gap_stats([dict(row.deltas_ours_vs_builtin) for row in rows]),
        },
        "quality_guard": {
            "repro_scored_rows": sum(
                1
                for row in rows
                if row.none_clean.repro_passed is not None
                and row.ours.repro_passed is not None
                and row.builtin.repro_passed is not None
            ),
            "repro_passed": {
                "none-clean": sum(1 for row in rows if row.none_clean.repro_passed),
                "ours": sum(1 for row in rows if row.ours.repro_passed),
                "builtin": sum(1 for row in rows if row.builtin.repro_passed),
            },
        },
        "retrieval_coverage": {
            "n_bundles": len(rows),
            "n_with_payload": sum(1 for row in rows if not row.ours_retrieval_empty),
            "evidence": [e.model_dump() for e in ours_evidence],
        },
        "arm_provenance": dict(ARM_PROVENANCE),
        "validity_gates": _validity_block(validity),
        # M1: the declared TIAP scoring target per retrieval-bearing arm (raw / source
        # / canonical). Empty when no arm computed a retrieval leg.
        "scoring_target": scoring_target,
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
