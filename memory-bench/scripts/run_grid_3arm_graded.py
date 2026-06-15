#!/usr/bin/env python3
"""mem-apg.9 graded 3-arm grid: none-clean / ours / builtin, FRESH on every arm,
scored with the mem-r5y graded signal vector over the convoy/epic-carved native
pool (mem-apg.7).

This is the headline EXECUTION of Decision C (mem-cg9h): the N=4-5 gold-test-
anchorable convoy/epic carves run as a clean-room 3-arm grid and scored with the
graded instrument, not the binary anchor alone. It is the same clean-room control
as the mem-p3w pilot (`run_grid_3arm.py`), with TWO deliberate differences the
bead requires:

1. **The builtin arm is FRESH, not a cached relabel.** mem-p3w relabeled the
   2026-06-11 gate-probe ``none`` runs as ``builtin`` -- a cross-day instrument
   confound (the pilot's caveat 5). Here ``builtin`` is the ``none`` condition
   (native project memory present, our system off) RUN FRESH under the same pins
   as ``none-clean`` and ``ours``, so all three arms share one instrument and one
   day. Isolated ``--probe-dir`` / ``--grid-dir`` keep these fresh runs disjoint
   from the pilot's cached ``.mem/grid`` results (overlapping bundles 4lf62/km0wj
   carry stale pilot ``none`` legs that resumability would otherwise reuse).

2. **The graded signal vector is computed.** A Claude Sonnet 4.6 rubric judge
   (mem-r5y) is injected into scoring, so every run carries the S1 per-test-file
   ratio, S2 bounded diff-sim, and S3 judge signals underneath the S0 binary
   repro anchor -- the resolution the binary metric cannot see in the fail region.
   The CSB validity gate (gold->1.0 / empty->0.0) runs per bundle and its
   exclusions are reported, never silent.

The judge is same-family with the agent under test (both Sonnet 4.6); per mem-r5y
the kappa gate is replaced by out-of-band Opus/Codex calibration, and the judge
score is a reported side signal, never a gate or a composite. NO pooled means, NO
single composite -- the per-signal paired per-bundle deltas are the headline shape
(the mem-75t.7.6 reporting doctrine, inherited from ``summarize_grid_3arm``).

ZFC: pure plumbing. Real run (from memory-bench/, Docker up, valid OAuth token):

    CLAUDE_CODE_OAUTH_TOKEN=... uv run python scripts/run_grid_3arm_graded.py

Dry run (constructs + leak-validates tasks, executes nothing, no token needed):

    uv run python scripts/run_grid_3arm_graded.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from run_gate_probe import run_probe_batch
from run_grid import load_admitted_bundles, score_runs
from run_grid_3arm import (
    assemble_rows,
    builtin_surface_evidence,
    resolve_payloads,
    scrub_unfinished_jobs,
)

from membench.grading.graded import DEFAULT_JUDGE_ROUNDS, ClaudeRubricJudge
from membench.grading.validity_gate import ValidityResult, validity_gate
from membench.harbor.bundle_grid import OursRungEvidence, ours_rung_evidence, summarize_grid_3arm
from membench.harbor.probe_gate import (
    EmptyRunError,
    assert_run_pins,
    detect_run_failure,
    harbor_stream_exec,
)
from membench.harbor.repro_live import LiveReproRunner
from membench.memory_systems.ours_system import _default_runner
from membench.schemas.bundle import TaskBundle

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The gold-test-anchorable convoy/epic-carved native candidates (mem-apg.7): the 4
# stable gascity_dashboard carves + the km0wj swing 5th. The 2 codeprobe admits are
# NOT gold-test-anchorable (ubuntu fallback / docs-only) and are out of this bead's
# scope. N=4-5 is resolved mechanically by the validity gate below, never re-opened.
DEFAULT_BUNDLES_DIR = PROJECT_ROOT / ".mem/bundles-ce"
DEFAULT_MANIFEST = PROJECT_ROOT / ".mem/grid-ready-pool-anchorable.json"
# Isolated from the pilot's .mem/probe and .mem/grid so no stale pilot leg is
# reused for an overlapping bundle -- every arm here is fresh.
DEFAULT_PROBE_DIR = PROJECT_ROOT / ".mem/probe-ce"
DEFAULT_GRID_DIR = PROJECT_ROOT / ".mem/grid-ce"
DEFAULT_STORE = PROJECT_ROOT / ".mem/store.db"
DEFAULT_MEM_BIN = str(PROJECT_ROOT / "bin/mem")

# Same pinned instrument across all three fresh arms (the pin parity the cached
# relabel could not give). Verified post-run per stream by `assert_run_pins`.
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_CLI_VERSION = "2.1.173"
# The builtin arm IS the `none` condition (native memory present, our system off)
# run fresh; assemble_rows relabels the scored `none` leg to `builtin`.
BUILTIN_CONDITION = "none"


def run_validity_gates(
    bundles: Sequence[TaskBundle], *, grid_dir: Path
) -> list[ValidityResult]:
    """CSB oracle-validity gate per bundle (mem-g6a): gold diff must reproduce,
    empty diff must fail. Runs the SAME LiveReproRunner the graded scoring uses, so
    its judgment is the test runner's. A bundle whose oracle is broken (gold does
    not reproduce, or a gold test passes without the fix) is reported invalid and
    excluded from the anchored read rather than silently scored.

    Resumable: each result persists to ``<grid_dir>/<work_id>.validity.json`` and an
    existing file is loaded, never re-executed -- a resume after a token-expiry abort
    in the agent phase does not pay the Docker repro cost again."""
    grid_dir.mkdir(parents=True, exist_ok=True)
    pending = [b for b in bundles if not (grid_dir / f"{b.work_id}.validity.json").is_file()]
    results: list[ValidityResult] = []
    runner_cm = LiveReproRunner() if pending else None
    test_runner = runner_cm.__enter__() if runner_cm is not None else None
    try:
        for bundle in bundles:
            out = grid_dir / f"{bundle.work_id}.validity.json"
            if out.is_file():
                result = ValidityResult.model_validate_json(out.read_text(encoding="utf-8"))
                print(f"VALIDITY  {bundle.work_id}  valid={result.valid}  (cached)")
            else:
                assert test_runner is not None  # pending non-empty -> runner opened
                result = validity_gate(bundle, test_runner=test_runner)
                out.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
                print(
                    f"VALIDITY  {bundle.work_id}  valid={result.valid}  "
                    f"gold_repro={result.gold_repro_passed} empty_repro={result.empty_repro_passed}"
                    + ("" if result.valid else f"  ({result.reason})")
                )
            results.append(result)
    finally:
        if runner_cm is not None:
            runner_cm.__exit__(None, None, None)
    return results


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
        help="claude CLI version pinned in-container (shared across all three fresh arms)",
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_MODEL,
        help="rubric-judge model (mem-r5y S3); same-family with the agent under test by design",
    )
    parser.add_argument(
        "--judge-rounds", type=int, default=DEFAULT_JUDGE_ROUNDS, help="median-vote rounds"
    )
    parser.add_argument(
        "--timeout-sec", type=float, default=None, help="per-run harbor subprocess timeout"
    )
    parser.add_argument(
        "--skip-validity",
        action="store_true",
        help="skip the CSB validity gate (it runs the gold/empty repro per bundle)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="construct + leak-validate all tasks, print the plan, execute nothing",
    )
    args = parser.parse_args(argv)

    candidates = load_admitted_bundles(args.bundles_dir, args.manifest)
    retrieve = _default_runner(args.mem_bin)
    payloads = resolve_payloads(candidates, store_path=args.store, runner=retrieve)

    def exec_stream(task_dir: Path) -> str:
        """All three arms pinned to one instrument; dead runs (401 / usage-limit /
        zero-output) classified FIRST so they raise the batch-handled EmptyRunError
        rather than a misleading PinMismatchError, and drift raises before persist."""
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

    if args.dry_run:
        # Construct + leak-validate every arm's task for every candidate; the
        # Docker-backed validity gate and agent runs are skipped (no token needed).
        with_payload = [b for b in candidates if payloads[b.work_id]]
        planned = run_probe_batch(candidates, ("none-clean",), **batch_kwargs)["planned"]
        planned += run_probe_batch(candidates, (BUILTIN_CONDITION,), **batch_kwargs)["planned"]
        planned += run_probe_batch(
            with_payload,
            ("ours",),
            ours_payloads_for=lambda bundle: payloads[bundle.work_id],
            **batch_kwargs,
        )["planned"]
        print(f"\nDRY RUN: {planned} task(s) constructed + leak-validated; nothing executed.")
        return 0

    # CSB validity gate PRECEDES the grid (mem-g6a doctrine): a broken oracle is
    # excluded BEFORE any agent run, never silently scored, and the agent budget is
    # not spent on a bundle whose anchored read would be meaningless.
    validity = (
        [] if args.skip_validity else run_validity_gates(candidates, grid_dir=args.grid_dir)
    )
    # --skip-validity admits every candidate (validity is empty -> no exclusions).
    valid_ids = (
        {v.work_id for v in validity if v.valid}
        if validity
        else {b.work_id for b in candidates}
    )
    bundles = [b for b in candidates if b.work_id in valid_ids]
    excluded = [b.work_id for b in candidates if b.work_id not in valid_ids]
    if not bundles:
        raise RuntimeError(
            f"every candidate failed the validity gate (excluded: {excluded}) -- no "
            "constructible grid on this pool; widen it (mem-e3h2 clone-wiring)"
        )
    # Native-memory surface evidence (valid bundles only): proves each builtin
    # (fresh `none`) image carries project memory at its base_commit AND that the
    # none-clean/ours clean-room strip is meaningful. Fails before any agent run.
    surface_evidence = builtin_surface_evidence(bundles)
    with_payload = [bundle for bundle in bundles if payloads[bundle.work_id]]
    print(
        f"pool: {len(bundles)}/{len(candidates)} candidate(s) admitted by validity"
        + (f"; excluded {excluded}" if excluded else "")
        + f"; retrieval coverage {len(with_payload)}/{len(bundles)} "
        f"({', '.join(b.work_id for b in with_payload) or 'none'})"
    )

    scrub_unfinished_jobs(
        bundles, ("none-clean", BUILTIN_CONDITION, "ours"), probe_dir=args.probe_dir
    )
    # none-clean and builtin (fresh `none`) run every valid bundle; ours runs only
    # payload-bearing bundles -- an empty retrieval makes the ours task byte-
    # identical to none-clean, so assemble_rows reuses that leg (delta 0).
    tally_clean = run_probe_batch(bundles, ("none-clean",), **batch_kwargs)
    tally_builtin = run_probe_batch(bundles, (BUILTIN_CONDITION,), **batch_kwargs)
    tally_ours = run_probe_batch(
        with_payload,
        ("ours",),
        ours_payloads_for=lambda bundle: payloads[bundle.work_id],
        **batch_kwargs,
    )
    print(
        f"agent runs: none-clean executed={tally_clean['executed']} "
        f"builtin executed={tally_builtin['executed']} ours executed={tally_ours['executed']}"
    )

    judge = ClaudeRubricJudge(model=args.judge_model)
    pending = [(bundle, "none-clean") for bundle in bundles]
    pending += [(bundle, BUILTIN_CONDITION) for bundle in bundles]
    pending += [(bundle, "ours") for bundle in with_payload]
    _, tally_scored = score_runs(
        pending,
        probe_jobs_dir=args.probe_dir / "jobs",
        grid_dir=args.grid_dir,
        judge=judge,
        judge_rounds=args.judge_rounds,
    )
    print(f"scoring: executed={tally_scored['executed']} skipped={tally_scored['skipped']}")

    rows = assemble_rows(bundles, payloads, args.grid_dir)
    evidence: list[OursRungEvidence] = [
        ours_rung_evidence(bundle, mem_bin=args.mem_bin, store_path=args.store, runner=retrieve)
        for bundle in bundles
    ]
    summary = summarize_grid_3arm(rows, evidence, validity=validity)
    # The builtin arm here is FRESH -- override the shared cached-relabel provenance
    # string (the pilot's, asserted by test_bundle_grid) for this graded summary.
    summary["arm_provenance"]["builtin"] = (
        "fresh agent runs under the same pinned instrument as the clean arms: "
        "native project memory present in the image (the `none` condition run fresh), "
        "our system off -- the baseline-to-beat (mem-whi), NOT the cached 2026-06-11 "
        "relabel that cross-day-confounded the mem-p3w pilot"
    )
    summary["pins"] = {
        "model": args.model,
        "cli_version": args.cli_version,
        "judge_model": args.judge_model,
        "judge_rounds": args.judge_rounds,
        "builtin_arm": "fresh",
    }
    summary["pool"] = {
        "candidates": [b.work_id for b in candidates],
        "admitted": [b.work_id for b in bundles],
        "excluded_by_validity": excluded,
    }
    summary["builtin_surface_evidence"] = surface_evidence
    out = args.grid_dir / "summary-3arm-graded.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"summary -> {out}  (bundles={summary['n_bundles']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
