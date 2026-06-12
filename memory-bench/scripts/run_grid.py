#!/usr/bin/env python3
"""mem-apg.3 ablation-grid CLI: dual-score the admitted bundle pool, resumable.

Scores the grid from the gate probe's CACHED real agent runs (``.mem/probe/jobs/``)
-- no new Docker/agent execution. Per (admitted bundle, condition) it re-harvests
the candidate diff from the persisted stream and runs the dual verifier with the
LIVE gold-test repro runner (`membench.harbor.repro_live`), persisting every result
immediately to ``.mem/grid/<work_id>.<condition>.json`` (existing files are skipped
on rerun). After the loop it probes the ``ours`` rung's retrieval payload per
bundle (structural-emptiness evidence -- no agent runs) and writes
``.mem/grid/summary.json``: per-bundle paired deltas (efficiency headline +
quality guard) and the rung-availability record.

ZFC: pure plumbing. Run from memory-bench/:

    uv run python scripts/run_grid.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from membench.harbor.bundle_grid import (
    GRID_CONDITIONS,
    GridConditionResult,
    load_grid_ready_work_ids,
    ours_rung_evidence,
    pair_grid,
    score_grid_condition,
    summarize_grid,
)
from membench.harbor.env_recon import DEFAULT_RIG_REPOS
from membench.harbor.probe_gate import sweep_probe_worktrees
from membench.harbor.repro_live import WORKTREE_PREFIX, LiveReproRunner
from membench.schemas.bundle import TaskBundle

# memory-bench/scripts/ -> the mem project root, so defaults track the checkout
# instead of one developer's home directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLES_DIR = PROJECT_ROOT / ".mem/bundles"
DEFAULT_MANIFEST = PROJECT_ROOT / ".mem/grid-ready-pool.json"
DEFAULT_PROBE_JOBS = PROJECT_ROOT / ".mem/probe/jobs"
DEFAULT_GRID_DIR = PROJECT_ROOT / ".mem/grid"
DEFAULT_STORE = PROJECT_ROOT / ".mem/store.db"
DEFAULT_MEM_BIN = str(PROJECT_ROOT / "bin/mem")


def load_admitted_bundles(bundles_dir: Path, manifest: Path) -> list[TaskBundle]:
    """The admitted bundles, in manifest order. A missing bundle JSON is a pool
    integrity error -- raise, never skip."""
    bundles = []
    for work_id in load_grid_ready_work_ids(manifest):
        path = bundles_dir / f"{work_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"admitted bundle {work_id} has no JSON at {path}")
        bundles.append(TaskBundle.model_validate_json(path.read_text(encoding="utf-8")))
    return bundles


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--probe-jobs-dir", type=Path, default=DEFAULT_PROBE_JOBS)
    parser.add_argument("--grid-dir", type=Path, default=DEFAULT_GRID_DIR)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--mem-bin", default=DEFAULT_MEM_BIN)
    args = parser.parse_args(argv)

    bundles = load_admitted_bundles(args.bundles_dir, args.manifest)
    args.grid_dir.mkdir(parents=True, exist_ok=True)
    executed = skipped = 0
    results: dict[tuple[str, str], GridConditionResult] = {}

    # Sweep BEFORE starting: worktrees stranded by a previously KILLED run would
    # otherwise sit on the clone for this whole run (the finally below only covers
    # strays this process leaves beyond its own close()).
    clones = {DEFAULT_RIG_REPOS[b.rig] for b in bundles if b.rig in DEFAULT_RIG_REPOS}
    for clone in sorted(clones):
        sweep_probe_worktrees(clone, prefix=WORKTREE_PREFIX)

    used_clones: set[Path] = set()
    try:
        with LiveReproRunner() as test_runner:
            for bundle in bundles:
                clone = DEFAULT_RIG_REPOS.get(bundle.rig)
                if clone is None:
                    raise RuntimeError(f"no local clone for rig {bundle.rig!r}")
                used_clones.add(clone)
                for condition in GRID_CONDITIONS:
                    out = args.grid_dir / f"{bundle.work_id}.{condition}.json"
                    if out.exists():
                        skipped += 1
                        results[(bundle.work_id, condition)] = (
                            GridConditionResult.model_validate_json(out.read_text(encoding="utf-8"))
                        )
                        print(f"SKIP  {bundle.work_id} {condition}  (result exists)")
                        continue
                    job_dir = args.probe_jobs_dir / f"{bundle.work_id}.{condition}"
                    if not job_dir.is_dir():
                        raise FileNotFoundError(
                            f"no cached run for {bundle.work_id} [{condition}] at {job_dir} -- "
                            "the grid rescoring needs the gate probe's persisted jobs"
                        )
                    result = score_grid_condition(
                        bundle, condition, job_dir, clone=clone, test_runner=test_runner
                    )
                    out.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
                    results[(bundle.work_id, condition)] = result
                    executed += 1
                    print(
                        f"DONE  {bundle.work_id} {condition}  mode={result.direct_mode} "
                        f"direct={result.score_direct} repro={result.repro_passed} "
                        f"out_tokens={result.efficiency.output_tokens}"
                    )
    finally:
        # Exit-sweep (the run_gate_probe discipline): catch repro worktrees stranded
        # by a killed run -- LiveReproRunner.close() only covers this process's own.
        for clone in sorted(used_clones):
            sweep_probe_worktrees(clone, prefix=WORKTREE_PREFIX)

    pairs = [
        pair_grid(results[(bundle.work_id, "none")], results[(bundle.work_id, "oracle")])
        for bundle in bundles
    ]

    evidence = [
        ours_rung_evidence(bundle, mem_bin=args.mem_bin, store_path=args.store)
        for bundle in bundles
    ]
    summary = summarize_grid(pairs, evidence)
    out = args.grid_dir / "summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nexecuted={executed} skipped={skipped}")
    print(f"summary -> {out}  (pairs={summary['n_pairs']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
