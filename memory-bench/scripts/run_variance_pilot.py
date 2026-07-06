#!/usr/bin/env python3
"""mem-eacq variance pilot: N bundles x ONE condition x k identical fresh runs.

No grid task has ever run twice under the same condition, so every delta
reported so far is a single-draw read with unknown within-task noise. This
pilot measures that noise directly: each admitted bundle's ``none-clean`` task
is executed ``--repeats`` times under the same pinned instrument (fresh every
time — no cached leg is ever reused as a repeat), scored with the existing
graded instrument (S1 test ratio, S2 bounded diff-sim, S3 rubric judge under
the S0 repro anchor), and the per-metric within-task SD, its df-weighted pool
across bundles, and the paired MDE *sampling-noise floor* at candidate pool
sizes are reported (``membench.grading.variance``).

Isolation: every artifact lives under rep-scoped dirs
(``<probe-dir>/rep<i>/{tasks,jobs}``, ``<grid-dir>/rep<i>/``) so no result
collides with the headline ``probe-ce``/``grid-ce`` caches and a crashed run
resumes per (rep, bundle).

SEQUENTIAL-ONLY (load-bearing): reps share one rig clone, and both the
agent-leg harvest and the repro scorer sweep that clone's worktrees by global
prefix (``probe-cand-``/``repro-``) before and after each batch. A second
concurrent rep — or any other grid/probe process on the same clone — would be
swept mid-replay and scored from a garbled checkout. Run one rep at a time and
keep the clone otherwise idle for the pilot's duration.

ZFC: pure plumbing — the agent and judge do all the reasoning; the ~0.05
noise-threshold comparison is transparent arithmetic against the bead's
documented constant, and the authored verdict stays with the orchestrator.

Real run (from memory-bench/, Docker up, CLAUDE_CODE_OAUTH_TOKEN exported;
wrap in scix-batch per the gas-city memory-ceiling rule):

    uv run python scripts/run_variance_pilot.py \
        --bundles-dir /home/ds/projects/mem/.mem/bundles-ce \
        --probe-dir /home/ds/projects/mem/.mem/probe-eacq \
        --grid-dir /home/ds/projects/mem/.mem/grid-eacq

Dry run (constructs + leak-validates every rep's task, executes nothing):

    uv run python scripts/run_variance_pilot.py --dry-run [--dirs as above]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from run_gate_probe import run_probe_batch
from run_grid import score_runs
from run_grid_3arm import scrub_unfinished_jobs

from membench.grading.graded import DEFAULT_JUDGE_ROUNDS, ClaudeRubricJudge
from membench.grading.variance import (
    mde_paired_floor,
    metric_stats_by_key,
    pooled_within_sd,
)
from membench.harbor.bundle_grid import GridConditionResult
from membench.harbor.probe_gate import pinned_stream_exec
from membench.schemas.bundle import TaskBundle

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLES_DIR = PROJECT_ROOT / ".mem/bundles-ce"
DEFAULT_PROBE_DIR = PROJECT_ROOT / ".mem/probe-eacq"
DEFAULT_GRID_DIR = PROJECT_ROOT / ".mem/grid-eacq"

# Same pinned instrument as the graded headline runner (run_grid_3arm_graded).
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_CLI_VERSION = "2.1.173"

# The identical condition under repeat. none-clean is the clean-room floor every
# headline delta is read against — its noise bounds every delta's noise.
CONDITION = "none-clean"

# The pilot pool (mem-eacq): the 2 CSB-valid headline admits carry the live S0
# repro anchor; codeprobe-3l6tb (6-file src+test change, ubuntu-fallback image)
# carries the graded S2/S3 signals on a real non-degenerate task. km0wj was
# rejected: its broken oracle pins the anchor ~1.0 (SD ~0 by construction) and
# makes the judge task degenerate.
DEFAULT_BUNDLE_IDS = (
    "gascity-dashboard-2a7lh",
    "gascity-dashboard-4lf62",
    "codeprobe-3l6tb",
)
DEFAULT_REPEATS = 5

# MDE floor grid: N spans the current admitted pool (2), this pilot (3), and the
# full anchorable candidate set (5); k spans single-run practice and the
# headline repeat floors.
MDE_POOL_SIZES = (2, 3, 5)
MDE_REPEAT_GRID = (1, 3, 5)

# The bead's kill-shot constant: pooled SD above this on the [0, 1] graded scale
# means every single-run delta reported so far is noise-indistinguishable.
NOISE_THRESHOLD = 0.05

# The [0, 1]-scale graded signals the threshold applies to; efficiency counters
# (tokens/turns) live on their own scales and are reported but never thresholded.
REWARD_SCALE_METRICS = (
    "score_direct",
    "score_artifact",
    "repro_passed",
    "test_ratio",
    "diff_sim",
    "judge_score",
)


def rep_dir(base: Path, rep: int) -> Path:
    """The rep-scoped artifact dir: ``<base>/rep<i>`` (1-indexed)."""
    return base / f"rep{rep}"


def load_pilot_bundles(bundles_dir: Path, work_ids: Sequence[str]) -> list[TaskBundle]:
    """The pilot bundles in the given order; a missing JSON is a pool-integrity
    error — raise, never skip."""
    bundles = []
    for work_id in work_ids:
        path = bundles_dir / f"{work_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"pilot bundle {work_id} has no JSON at {path}")
        bundles.append(TaskBundle.model_validate_json(path.read_text(encoding="utf-8")))
    return bundles


def collect_rep_metrics(
    grid_dir: Path, work_ids: Sequence[str], *, repeats: int, condition: str
) -> dict[str, list[dict[str, float | None]]]:
    """work_id -> per-rep metric vectors from the scored rep dirs, in rep order.
    Every (rep, bundle) result must exist — a gap means the run is incomplete
    and the SD would silently be computed over fewer draws than claimed."""
    metrics: dict[str, list[dict[str, float | None]]] = {}
    for work_id in work_ids:
        rows: list[dict[str, float | None]] = []
        for rep in range(1, repeats + 1):
            path = rep_dir(grid_dir, rep) / f"{work_id}.{condition}.json"
            if not path.is_file():
                raise FileNotFoundError(
                    f"no scored result for {work_id} [{condition}] rep {rep} at {path} "
                    "-- the pilot is incomplete; rerun the same command to resume"
                )
            result = GridConditionResult.model_validate_json(path.read_text(encoding="utf-8"))
            rows.append(result.metrics())
        metrics[work_id] = rows
    return metrics


def pilot_report(
    metrics_by_bundle: Mapping[str, Sequence[Mapping[str, float | None]]],
    *,
    condition: str,
    noise_threshold: float = NOISE_THRESHOLD,
    pool_sizes: Sequence[int] = MDE_POOL_SIZES,
    repeat_grid: Sequence[int] = MDE_REPEAT_GRID,
) -> dict[str, Any]:
    """The pilot's data product: per-bundle per-metric repeat stats, the pooled
    within-task SD per metric, the paired MDE sampling-noise floor at each
    (pool size, repeats) point, and the mechanical threshold read. The MDE is a
    LOWER BOUND — same-condition repeats cannot see the task-x-arm interaction."""
    if not metrics_by_bundle:
        raise ValueError("pilot_report needs at least one bundle's metrics")

    stats_by_bundle = {
        work_id: metric_stats_by_key(maps) for work_id, maps in metrics_by_bundle.items()
    }
    keys = sorted({key for stats in stats_by_bundle.values() for key in stats})
    pooled: dict[str, float | None] = {
        key: pooled_within_sd([stats[key] for stats in stats_by_bundle.values() if key in stats])
        for key in keys
    }

    mde_floor = {
        key: {
            f"n{n}_k{k}": mde_paired_floor(sd, n_tasks=n, k_repeats=k)
            for n in pool_sizes
            for k in repeat_grid
        }
        for key, sd in pooled.items()
        if sd is not None
    }
    return {
        "condition": condition,
        "n_bundles": len(stats_by_bundle),
        "repeats": {work_id: len(maps) for work_id, maps in metrics_by_bundle.items()},
        "per_bundle": {
            work_id: {key: stat.as_dict() for key, stat in stats.items()}
            for work_id, stats in stats_by_bundle.items()
        },
        "pooled_within_sd": pooled,
        "mde_floor": mde_floor,
        "mde_model": (
            "sampling-noise LOWER BOUND: (t_{1-a/2,N-1} + t_{power,N-1}) * sqrt(2/k) * "
            "sd_within / sqrt(N), alpha=0.05, power=0.8; the task-x-arm interaction is "
            "unmeasurable from same-condition repeats and inflates the true MDE above this"
        ),
        "noise_read": {
            "threshold": noise_threshold,
            "exceeds": {
                key: sd > noise_threshold
                for key in REWARD_SCALE_METRICS
                if (sd := pooled.get(key)) is not None
            },
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", nargs="+", default=list(DEFAULT_BUNDLE_IDS))
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    parser.add_argument("--probe-dir", type=Path, default=DEFAULT_PROBE_DIR)
    parser.add_argument("--grid-dir", type=Path, default=DEFAULT_GRID_DIR)
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help="identical runs per bundle (>= 2; one run has no spread to measure)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="harbor agent model_name")
    parser.add_argument(
        "--cli-version",
        default=DEFAULT_CLI_VERSION,
        help="claude CLI version pinned in-container (shared across every rep)",
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_MODEL,
        help="rubric-judge model (mem-r5y S3); same-family with the agent by design",
    )
    parser.add_argument(
        "--judge-rounds", type=int, default=DEFAULT_JUDGE_ROUNDS, help="median-vote rounds"
    )
    parser.add_argument(
        "--timeout-sec", type=float, default=None, help="per-run harbor subprocess timeout"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="construct + leak-validate every rep's task, print the plan, execute nothing",
    )
    args = parser.parse_args(argv)
    if args.repeats < 2:
        parser.error(f"--repeats must be >= 2 to measure spread, got {args.repeats}")

    bundles = load_pilot_bundles(args.bundles_dir, args.bundles)
    print(
        f"variance pilot: {len(bundles)} bundle(s) x {CONDITION} x {args.repeats} rep(s); "
        "SEQUENTIAL-ONLY -- keep the rig clone(s) free of other probe/grid work"
    )

    planned = 0
    for rep in range(1, args.repeats + 1):
        probe_rep = rep_dir(args.probe_dir, rep)
        if not args.dry_run:
            scrub_unfinished_jobs(bundles, (CONDITION,), probe_dir=probe_rep)
        tally = run_probe_batch(
            bundles,
            (CONDITION,),
            probe_dir=probe_rep,
            tasks_dir=probe_rep / "tasks",
            # Every rep pinned to one instrument; dead runs raise the batch-handled
            # EmptyRunError, pin drift raises before anything persists.
            exec_stream=pinned_stream_exec(
                jobs_dir=probe_rep / "jobs",
                model=args.model,
                cli_version=args.cli_version,
                timeout_sec=args.timeout_sec,
            ),
            dry_run=args.dry_run,
        )
        if args.dry_run:
            planned += tally["planned"]
        else:
            print(
                f"rep {rep}/{args.repeats}: executed={tally['executed']} "
                f"skipped={tally['skipped']}"
            )

    if args.dry_run:
        print(f"\nDRY RUN: {planned} task(s) constructed + leak-validated; nothing executed.")
        return 0

    judge = ClaudeRubricJudge(model=args.judge_model)
    for rep in range(1, args.repeats + 1):
        _, tally_scored = score_runs(
            [(bundle, CONDITION) for bundle in bundles],
            probe_jobs_dir=rep_dir(args.probe_dir, rep) / "jobs",
            grid_dir=rep_dir(args.grid_dir, rep),
            judge=judge,
            judge_rounds=args.judge_rounds,
        )
        print(
            f"scoring rep {rep}/{args.repeats}: executed={tally_scored['executed']} "
            f"skipped={tally_scored['skipped']}"
        )

    metrics = collect_rep_metrics(
        args.grid_dir,
        [bundle.work_id for bundle in bundles],
        repeats=args.repeats,
        condition=CONDITION,
    )
    report = pilot_report(metrics, condition=CONDITION)
    report["pins"] = {
        "model": args.model,
        "cli_version": args.cli_version,
        "judge_model": args.judge_model,
        "judge_rounds": args.judge_rounds,
        "judge_isolation": judge.isolation_marker,  # mem-9ld4 clean-config marker
        "repeats": args.repeats,
    }
    out = args.grid_dir / "variance-pilot.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"\nwithin-task SD (pooled, df-weighted) vs threshold {NOISE_THRESHOLD}:")
    for key, sd in report["pooled_within_sd"].items():
        flag = ""
        if key in report["noise_read"]["exceeds"]:
            flag = "  EXCEEDS" if report["noise_read"]["exceeds"][key] else "  ok"
        print(f"  {key:<22} {'n/a' if sd is None else f'{sd:.4f}'}{flag}")
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
