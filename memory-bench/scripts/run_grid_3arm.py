#!/usr/bin/env python3
"""mem-p3w 3-arm pilot CLI: clean-room none / ours / builtin over the admitted pool.

Executes the ablation grid's OURS rung under the clean-room control Stephanie
confirmed (2026-06-12): the agent's native project memory (the repo-shipped
CLAUDE.md / AGENTS.md / .claude / .agents that Claude Code auto-loads) is REMOVED
from the image in both the ``none-clean`` and ``ours`` arms, so the only memory
variable between them is OUR system's injected retrieval payload. The third arm,
``builtin``, is the baseline-to-beat (mem-whi): native memory ON, our system OFF --
which is EXACTLY the gate probe's cached ``none`` runs (the 2026-06-11 Docker/OAuth
executions ran with the repo's native memory present), so it is relabeled from the
existing ``.mem/grid/<work_id>.none.json`` scores at zero new agent cost.

Agent-run economy: ``none-clean`` runs every admitted bundle; ``ours`` runs ONLY
bundles whose retrieval payload is non-empty. An empty retrieval would make the
ours task byte-identical to none-clean -- a fresh run would measure sampling noise
and attribute it to the memory system -- so those bundles REUSE the none-clean
result (delta exactly 0, flagged ``ours_retrieval_empty``). Retrieval coverage is
part of the system under test and is reported, never hidden.

Execution mirrors `run_gate_probe` (resumable result files, EmptyRunError aborts
loudly with nothing persisted); scoring mirrors `run_grid` (dual verifier with the
LIVE gold-test repro runner, results under ``.mem/grid/``). The model is PINNED to
the cached gate runs' resolved model so the builtin arm stays comparable.

ZFC: pure plumbing. Real run (from memory-bench/, Docker up,
CLAUDE_CODE_OAUTH_TOKEN exported):

    uv run python scripts/run_grid_3arm.py

Dry run (constructs + leak-validates tasks, executes nothing):

    uv run python scripts/run_grid_3arm.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from run_gate_probe import run_probe_batch
from run_grid import load_admitted_bundles, score_runs

from membench.harbor.bundle_grid import (
    GridConditionResult,
    OursRungEvidence,
    ThreeArmRow,
    as_condition,
    ours_rung_evidence,
    summarize_grid_3arm,
    three_arm_row,
)
from membench.harbor.env_recon import DEFAULT_RIG_REPOS
from membench.harbor.probe_gate import (
    EmptyRunError,
    assert_run_pins,
    detect_run_failure,
    harbor_stream_exec,
    touches_native_memory,
)
from membench.memory_systems.ours_system import (
    OursQuery,
    RetrieveRunner,
    _default_runner,
    _render_payload,
)
from membench.schemas.bundle import TaskBundle

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLES_DIR = PROJECT_ROOT / ".mem/bundles"
DEFAULT_MANIFEST = PROJECT_ROOT / ".mem/grid-ready-pool.json"
DEFAULT_PROBE_DIR = PROJECT_ROOT / ".mem/probe"
DEFAULT_GRID_DIR = PROJECT_ROOT / ".mem/grid"
DEFAULT_STORE = PROJECT_ROOT / ".mem/store.db"
DEFAULT_MEM_BIN = str(PROJECT_ROOT / "bin/mem")

# The cached gate-probe runs resolved to this model + claude CLI version (uniform
# across all 10 cached stream-json init events); the fresh clean-room runs pin BOTH
# so the builtin arm stays the same instrument. `assert_run_pins` verifies each
# fresh run's stream post-hoc -- a drifted run aborts with nothing persisted.
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_CLI_VERSION = "2.1.173"

# The realistic dual-track scope (D7) -- same-rig prior work, temporally bounded.
RETRIEVAL_SCOPE = "same_rig_temporal"


def resolve_payloads(
    bundles: Sequence[TaskBundle],
    *,
    store_path: Path,
    runner: RetrieveRunner,
) -> dict[str, dict[str, str]]:
    """work_id -> (source work_id -> rendered citation+lessons payload) via the
    ours ARM's own retrieval runner, so the injected text is exactly what the arm
    would inject. Items without lessons are dropped -- the arm's information
    content is the lesson payload (D9); a bare citation carries none. Every item
    is checked against the bundle's LOO exclusion set (D6): retrieval-v1 is
    contracted to enforce that boundary, but a leak here would hand the agent its
    own work record, so the driver re-asserts rather than assumes."""
    payloads: dict[str, dict[str, str]] = {}
    for bundle in bundles:
        result = runner(
            OursQuery(work_id=bundle.work_id, scope=RETRIEVAL_SCOPE, store_path=str(store_path))
        )
        items = [item for item in result.get("items", []) if item.get("lessons")]
        leaked = sorted(
            {item["work_id"] for item in items} & set(bundle.loo_excluded_work_ids)
        )
        if leaked:
            raise RuntimeError(
                f"{bundle.work_id}: retrieval returned LOO-excluded work id(s) {leaked} -- "
                "the D6 boundary is broken; refusing to inject"
            )
        payloads[bundle.work_id] = {item["work_id"]: _render_payload(item) for item in items}
    return payloads


def builtin_surface_evidence(
    bundles: Sequence[TaskBundle],
) -> dict[str, list[str]]:
    """Per-bundle proof the cached ``none`` runs HAD native memory in the image:
    the tracked project-memory paths at each bundle's exact base_commit (what
    ``git archive`` emitted to /app). An empty surface for any bundle invalidates
    its builtin relabel -- abort rather than mislabel."""
    evidence: dict[str, list[str]] = {}
    for bundle in bundles:
        clone = DEFAULT_RIG_REPOS[bundle.rig]
        completed = subprocess.run(
            ["git", "-C", str(clone), "ls-tree", "-r", "--name-only", bundle.env.base_commit],
            capture_output=True,
            text=True,
            check=True,
        )
        surface = [path for path in completed.stdout.splitlines() if touches_native_memory(path)]
        if not surface:
            raise RuntimeError(
                f"{bundle.work_id}: no native project-memory surface tracked at "
                f"{bundle.env.base_commit[:12]} -- the cached `none` run was ALREADY "
                "clean-room, so relabeling it `builtin` would be false"
            )
        evidence[bundle.work_id] = surface
    return evidence


def scrub_unfinished_jobs(
    bundles: Sequence[TaskBundle],
    conditions: Sequence[str],
    *,
    probe_dir: Path,
) -> None:
    """Remove job dirs whose probe result file is MISSING (a previous run died
    mid-execution). Harbor does not re-run an existing job dir -- a resumed batch
    would harvest the stale dead transcript and fail again on its old error (the
    2026-06-11 ghost-401 trap). Scoped strictly to THIS driver's conditions; the
    cached none/oracle jobs are never touched."""
    jobs_dir = probe_dir / "jobs"
    for bundle in bundles:
        for condition in conditions:
            name = f"{bundle.work_id}.{condition}"
            if (probe_dir / f"{name}.json").exists():
                continue
            stale_job = jobs_dir / name
            if stale_job.is_dir():
                shutil.rmtree(stale_job)
                print(f"SCRUB {name}  (unfinished job dir removed)")
            (jobs_dir / f"{name}.job.json").unlink(missing_ok=True)


def load_grid_result(grid_dir: Path, work_id: str, condition: str) -> GridConditionResult:
    path = grid_dir / f"{work_id}.{condition}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"no scored grid result for {work_id} [{condition}] at {path} -- "
            "the 3-arm assembly needs every leg scored first"
        )
    return GridConditionResult.model_validate_json(path.read_text(encoding="utf-8"))


def assemble_rows(
    bundles: Sequence[TaskBundle],
    payloads: dict[str, dict[str, str]],
    grid_dir: Path,
) -> list[ThreeArmRow]:
    """One `ThreeArmRow` per bundle from the scored grid results on disk:
    ``none-clean`` and (payload-bearing) ``ours`` from the fresh clean-room runs;
    ``builtin`` relabeled from the cached gate-probe ``none`` scoring; empty-
    retrieval bundles reuse the none-clean leg as ``ours`` (delta 0)."""
    rows: list[ThreeArmRow] = []
    for bundle in bundles:
        none_clean = load_grid_result(grid_dir, bundle.work_id, "none-clean")
        builtin = as_condition(load_grid_result(grid_dir, bundle.work_id, "none"), "builtin")
        retrieval_empty = not payloads.get(bundle.work_id)
        ours = (
            as_condition(none_clean, "ours")
            if retrieval_empty
            else load_grid_result(grid_dir, bundle.work_id, "ours")
        )
        rows.append(
            three_arm_row(none_clean, ours, builtin, ours_retrieval_empty=retrieval_empty)
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--probe-dir", type=Path, default=DEFAULT_PROBE_DIR)
    parser.add_argument("--grid-dir", type=Path, default=DEFAULT_GRID_DIR)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--mem-bin", default=DEFAULT_MEM_BIN)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="harbor agent model_name")
    parser.add_argument(
        "--cli-version",
        default=DEFAULT_CLI_VERSION,
        help="claude CLI version pinned in-container (parity with the cached builtin arm)",
    )
    parser.add_argument(
        "--timeout-sec", type=float, default=None, help="per-run harbor subprocess timeout"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="construct + leak-validate all tasks, print the plan, execute nothing",
    )
    args = parser.parse_args(argv)

    bundles = load_admitted_bundles(args.bundles_dir, args.manifest)
    # Fail BEFORE any agent run if a builtin leg is missing -- the relabel needs
    # every cached `none` scoring on disk, and discovering that after hours of
    # fresh executions would waste the whole batch.
    for bundle in bundles:
        load_grid_result(args.grid_dir, bundle.work_id, "none")
    surface_evidence = builtin_surface_evidence(bundles)
    retrieve = _default_runner(args.mem_bin)
    payloads = resolve_payloads(bundles, store_path=args.store, runner=retrieve)
    with_payload = [bundle for bundle in bundles if payloads[bundle.work_id]]
    print(
        f"retrieval coverage: {len(with_payload)}/{len(bundles)} bundle(s) with a "
        f"non-empty ours payload ({', '.join(b.work_id for b in with_payload) or 'none'})"
    )

    if not args.dry_run:
        scrub_unfinished_jobs(bundles, ("none-clean", "ours"), probe_dir=args.probe_dir)

    def exec_stream(task_dir: Path) -> str:
        """`harbor_stream_exec` pinned to the cached arm's instrument, plus the
        post-run pin assertion: a drifted run raises BEFORE the batch can persist
        its result. Dead runs are classified FIRST -- a 401/usage-limit stream
        carries an init event without model fields, which would otherwise raise a
        misleading `PinMismatchError` instead of the batch-handled `EmptyRunError`."""
        stream = harbor_stream_exec(
            task_dir,
            jobs_dir=args.probe_dir / "jobs",
            model=args.model,
            timeout_sec=args.timeout_sec,
            agent_version=args.cli_version,
        )
        failure = detect_run_failure(stream)
        if failure is not None:
            raise EmptyRunError(f"{task_dir.name}: {failure}")
        assert_run_pins(stream, model=args.model, cli_version=args.cli_version)
        return stream

    batch_kwargs = {
        "probe_dir": args.probe_dir,
        "tasks_dir": args.probe_dir / "tasks",
        "exec_stream": exec_stream,
        "dry_run": args.dry_run,
    }
    tally_clean = run_probe_batch(bundles, ("none-clean",), **batch_kwargs)
    tally_ours = run_probe_batch(
        with_payload,
        ("ours",),
        ours_payloads_for=lambda bundle: payloads[bundle.work_id],
        **batch_kwargs,
    )
    if args.dry_run:
        print(
            f"\nDRY RUN: {tally_clean['planned'] + tally_ours['planned']} task(s) "
            "constructed + leak-validated; nothing executed."
        )
        return 0
    print(
        f"agent runs: none-clean executed={tally_clean['executed']} "
        f"skipped={tally_clean['skipped']}; ours executed={tally_ours['executed']} "
        f"skipped={tally_ours['skipped']}"
    )

    pending = [(bundle, "none-clean") for bundle in bundles]
    pending += [(bundle, "ours") for bundle in with_payload]
    _, tally_scored = score_runs(
        pending, probe_jobs_dir=args.probe_dir / "jobs", grid_dir=args.grid_dir
    )
    print(f"scoring: executed={tally_scored['executed']} skipped={tally_scored['skipped']}")

    rows = assemble_rows(bundles, payloads, args.grid_dir)
    evidence: list[OursRungEvidence] = [
        ours_rung_evidence(bundle, mem_bin=args.mem_bin, store_path=args.store, runner=retrieve)
        for bundle in bundles
    ]
    summary = summarize_grid_3arm(rows, evidence)
    # Driver-level provenance: the instrument pins and the per-bundle proof the
    # cached `none` runs carried native memory (the builtin relabel's evidence).
    summary["pins"] = {"model": args.model, "cli_version": args.cli_version}
    summary["builtin_surface_evidence"] = surface_evidence
    out = args.grid_dir / "summary-3arm.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"summary -> {out}  (bundles={summary['n_bundles']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
