#!/usr/bin/env python3
"""mem-75t.7.6 dynamic-range probe CLI: bundle x condition -> scored, resumable.

Loads the admitted bundles (``.mem/bundles/*.json``), builds one Harbor task dir per
(bundle, condition) via `membench.harbor.probe_gate.build_probe_task`, executes each
through the spike's free OAuth/harbor path (`harbor_stream_exec`), harvests +
scores it, and persists EVERY result immediately to
``.mem/probe/<work_id>.<condition>.json`` -- a result file already on disk is
skipped on rerun, so a multi-hour run survives a crash and is resumed by re-running
the same command. After the loop, every bundle with both condition results gets
paired and ``.mem/probe/summary.json`` is written: per-bundle paired scores, the
per-metric gap stats, and the mechanical ``gap_positive_majority`` flag. The
authored GO/NO-GO verdict is the orchestrator's, recorded on the bead -- NOT here.

``--dry-run`` constructs + leak-validates all task dirs (including the real repo
snapshot baked at each bundle's exact ``base_commit``), prints the plan, and
executes nothing -- no Docker, no agent runs.

Cleanup discipline: candidate-replay checkouts are per-run try/finally inside
`harvest_candidate`; this script additionally exit-sweeps every used clone for
leftover ``probe-cand-`` worktrees (the assemble_batch pattern) -- leftovers raise.

ZFC: pure plumbing -- file IO, subprocess fan-out, arithmetic summary. The agent
does all the reasoning; the verdict author is a human/orchestrator.

Real run (from memory-bench/, Docker up, CLAUDE_CODE_OAUTH_TOKEN exported):

    uv run python scripts/run_gate_probe.py

Dry run:  uv run python scripts/run_gate_probe.py --dry-run
"""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from membench.harbor.env_recon import DEFAULT_RIG_REPOS
from membench.harbor.probe_gate import (
    CONDITIONS,
    EmptyRunError,
    ProbeConditionResult,
    ProbePair,
    Runner,
    StreamExec,
    build_probe_task,
    harbor_stream_exec,
    run_probe,
    score_pair,
    summarize_pairs,
    sweep_probe_worktrees,
)
from membench.schemas.bundle import TaskBundle

DEFAULT_BUNDLES_DIR = Path("/home/ds/projects/mem/.mem/bundles")
DEFAULT_PROBE_DIR = Path("/home/ds/projects/mem/.mem/probe")


def load_bundles(bundles_dir: Path, limit: int | None = None) -> list[TaskBundle]:
    """Every ``*.json`` bundle under ``bundles_dir``, sorted by filename (stable
    plan order); ``limit`` truncates AFTER sorting. Read-only."""
    paths = sorted(bundles_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no bundle JSONs under {bundles_dir}")
    if limit is not None:
        paths = paths[:limit]
    return [TaskBundle.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]


def result_path(probe_dir: Path, work_id: str, condition: str) -> Path:
    return probe_dir / f"{work_id}.{condition}.json"


def _dry_run_row(bundle: TaskBundle, condition: str, task_dir: Path) -> str:
    oracle_n = len(bundle.output.file_diffs) if condition == "oracle" else 0
    return (
        f"PLAN  {bundle.work_id:<28} {condition:<7} base={bundle.env.base_commit[:12]} "
        f"image={bundle.env.base_image} oracle_files={oracle_n} task={task_dir}"
    )


def run_probe_batch(
    bundles: Sequence[TaskBundle],
    conditions: Sequence[str],
    *,
    probe_dir: Path,
    tasks_dir: Path,
    rig_repos: Mapping[str, Path] = DEFAULT_RIG_REPOS,
    exec_stream: StreamExec = harbor_stream_exec,
    runner: Runner = subprocess.run,
    worktree_root: Path = Path("/tmp"),
    dry_run: bool = False,
) -> dict[str, int]:
    """The resumable bundle x condition loop. Each scored result persists to
    ``<probe_dir>/<work_id>.<condition>.json`` IMMEDIATELY; existing result files
    are skipped. Returns the ``{"executed": n, "skipped": n, "planned": n}`` tally.
    Used clones are exit-swept for leftover probe worktrees (leftovers raise).

    A dead run (`EmptyRunError` -- auth/usage-limit failure or zero-output transcript)
    is FATAL: no result file is written (so a rerun re-executes it) and the batch
    aborts loudly, because such failures cascade to every subsequent run in the same
    session -- persisting them as 0.0 silently corrupted the gate (mem-75t.7.6
    incident). Results already on disk survive; rerun with a fresh token to resume."""
    probe_dir.mkdir(parents=True, exist_ok=True)
    executed = skipped = planned = 0
    used_clones: set[Path] = set()
    try:
        for bundle in bundles:
            clone = rig_repos.get(bundle.rig)
            if clone is None:
                raise RuntimeError(
                    f"no local clone for rig {bundle.rig!r} (known: {sorted(rig_repos)})"
                )
            for condition in conditions:
                out = result_path(probe_dir, bundle.work_id, condition)
                if not dry_run and out.exists():
                    skipped += 1
                    print(f"SKIP  {bundle.work_id} {condition}  (result exists: {out})")
                    continue
                task_dir = tasks_dir / f"{bundle.work_id}.{condition}"
                if task_dir.exists():  # rebuild fresh -- bundles are the source of truth
                    shutil.rmtree(task_dir)
                build_probe_task(bundle, condition, task_dir, rig_repos=rig_repos, runner=runner)
                if dry_run:
                    planned += 1
                    print(_dry_run_row(bundle, condition, task_dir))
                    continue
                used_clones.add(clone)
                try:
                    result = run_probe(
                        bundle,
                        condition,
                        task_dir,
                        clone=clone,
                        exec_stream=exec_stream,
                        runner=runner,
                        worktree_root=worktree_root,
                    )
                except EmptyRunError as exc:
                    print(
                        f"\n*** EMPTY RUN -- {exc}\n"
                        f"*** No result file written ({out}); the run will re-execute on resume.\n"
                        f"*** Aborting batch: this failure cascades to every subsequent run "
                        f"(check the OAuth token / usage limit, then rerun the same command).",
                        file=sys.stderr,
                    )
                    raise
                out.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
                executed += 1
                print(
                    f"DONE  {bundle.work_id} {condition}  combined={result.score.combined:.3f} "
                    f"file_f1={result.score.file_f1:.3f} turns={result.efficiency.turns} "
                    f"-> {out}"
                )
    finally:
        for clone in sorted(used_clones):
            sweep_probe_worktrees(clone, runner=runner)
    return {"executed": executed, "skipped": skipped, "planned": planned}


def load_pairs(probe_dir: Path, bundles: Sequence[TaskBundle]) -> list[ProbePair]:
    """Pair every bundle whose BOTH condition result files exist on disk."""
    pairs: list[ProbePair] = []
    for bundle in bundles:
        sides: dict[str, ProbeConditionResult] = {}
        for condition in CONDITIONS:
            path = result_path(probe_dir, bundle.work_id, condition)
            if path.exists():
                sides[condition] = ProbeConditionResult.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
        if set(sides) == set(CONDITIONS):
            pairs.append(score_pair(sides["none"], sides["oracle"]))
    return pairs


def write_summary(probe_dir: Path, bundles: Sequence[TaskBundle]) -> Path | None:
    """Write ``summary.json`` over every complete pair on disk; None when no
    bundle has both condition results yet (e.g. a single-condition run)."""
    pairs = load_pairs(probe_dir, bundles)
    if not pairs:
        print("no complete (none, oracle) pairs on disk -- summary not written")
        return None
    summary = summarize_pairs(pairs)
    out = probe_dir / "summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"summary -> {out}  (pairs={summary['n_pairs']} "
        f"gap_positive_majority={summary['gap_positive_majority']})"
    )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    parser.add_argument("--probe-dir", type=Path, default=DEFAULT_PROBE_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--condition", choices=["none", "oracle", "both"], default="both")
    parser.add_argument("--model", default=None, help="harbor agent model_name override")
    parser.add_argument(
        "--timeout-sec", type=float, default=None, help="per-run harbor subprocess timeout"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="construct + leak-validate all tasks, print the plan, execute nothing",
    )
    args = parser.parse_args(argv)

    bundles = load_bundles(args.bundles_dir, args.limit)
    conditions = list(CONDITIONS) if args.condition == "both" else [args.condition]
    tasks_dir = args.probe_dir / "tasks"
    exec_stream: StreamExec = functools.partial(
        harbor_stream_exec,
        jobs_dir=args.probe_dir / "jobs",
        model=args.model,
        timeout_sec=args.timeout_sec,
    )

    tally = run_probe_batch(
        bundles,
        conditions,
        probe_dir=args.probe_dir,
        tasks_dir=tasks_dir,
        exec_stream=exec_stream,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(
            f"\nDRY RUN: {tally['planned']} task(s) constructed + leak-validated "
            f"({len(bundles)} bundle(s) x {len(conditions)} condition(s)); nothing executed."
        )
        return 0
    print(f"\nexecuted={tally['executed']} skipped={tally['skipped']}")
    write_summary(args.probe_dir, bundles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
