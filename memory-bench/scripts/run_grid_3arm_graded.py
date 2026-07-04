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

Repeats doctrine (mem-eacq): anything called a headline runs every arm
``--repeats`` >= 3 times -- a single draw per (bundle, arm) has unknown within-task
noise, and the variance pilot exists precisely because no task had ever run twice
under the same condition. Rep 1 lives at the legacy probe/grid paths so prior
single-run artifacts resume as rep 1 (their instrument pins were asserted when
they executed; any cross-day drift is visible in the repeats block's per-rep
values); reps 2..k live under ``rep<i>/`` subdirs. The headline per-bundle deltas
are read from ``summary["repeats"]`` -- repeats collapsed within task first (the
curve.py M2 doctrine), deltas of arm means with their standard errors -- while the
top-level ``per_bundle``/``gaps`` blocks keep the schema-stable single-rep (rep 1)
view. Reps execute strictly sequentially: the agent-leg harvest and the repro
scorer sweep the shared rig clone's worktrees by global prefix, so a concurrent
rep would be swept mid-replay.

ZFC: pure plumbing. Real run (from memory-bench/, Docker up, valid OAuth token):

    CLAUDE_CODE_OAUTH_TOKEN=... uv run python scripts/run_grid_3arm_graded.py

Dry run (constructs + leak-validates tasks, executes nothing, no token needed):

    uv run python scripts/run_grid_3arm_graded.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from run_gate_probe import run_probe_batch
from run_grid import load_admitted_bundles, score_runs
from run_grid_3arm import (
    assemble_rows,
    builtin_surface_evidence,
    resolve_held_signatures,
    resolve_payloads,
    scrub_unfinished_jobs,
)

from membench.grading.dual_verifier import ReproRunner
from membench.grading.graded import DEFAULT_JUDGE_ROUNDS, ClaudeRubricJudge
from membench.grading.validity_gate import ValidityResult, validity_gate
from membench.grading.variance import delta_of_means, metric_stats_by_key
from membench.harbor.bundle_grid import (
    OursRungEvidence,
    ThreeArmRow,
    ours_rung_evidence,
    signature_overlap_summary,
    summarize_grid_3arm,
)
from membench.harbor.probe_gate import StreamExec, pinned_stream_exec
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

# The headline repeat floor (mem-eacq): below 3 repeats a per-bundle delta has no
# within-task spread estimate and cannot be told from single-draw noise.
MIN_HEADLINE_REPEATS = 3

# (baseline arm, treatment arm, comparison name) -- the same three comparisons
# summarize_grid_3arm reports, collapsed across repeats here.
_REPEAT_COMPARISONS = (
    ("none-clean", "ours", "ours_vs_none_clean"),
    ("none-clean", "builtin", "builtin_vs_none_clean"),
    ("builtin", "ours", "ours_vs_builtin"),
)


def legacy_rep_dir(base: Path, rep: int) -> Path:
    """Rep 1 stays at the legacy path (prior single-run artifacts resume as rep
    1); reps 2..k get ``rep<i>/`` subdirs."""
    return base if rep == 1 else base / f"rep{rep}"


def repeats_block(rows_by_rep: Sequence[Sequence[ThreeArmRow]]) -> dict[str, Any]:
    """The headline read: per-bundle per-arm repeat statistics and the
    repeats-collapsed paired deltas (within-task collapse first, curve.py M2).
    Deltas are of arm MEANS with a standard error -- rep indices are arbitrary,
    so per-rep pairing across arms would be false pairing. Every rep must carry
    the same bundle set; a bundle missing from one rep means the run is
    incomplete and collapsing over it would silently change the sample."""
    if not rows_by_rep:
        raise ValueError("repeats_block needs at least one rep's rows")
    bundle_order = [row.work_id for row in rows_by_rep[0]]
    for rep_rows in rows_by_rep:
        if [row.work_id for row in rep_rows] != bundle_order:
            raise ValueError(
                f"inconsistent bundle sets across reps: {bundle_order} vs "
                f"{[row.work_id for row in rep_rows]}"
            )
    per_bundle: list[dict[str, Any]] = []
    for index, work_id in enumerate(bundle_order):
        rows = [rep_rows[index] for rep_rows in rows_by_rep]
        arm_results = {
            "none-clean": [row.none_clean for row in rows],
            "ours": [row.ours for row in rows],
            "builtin": [row.builtin for row in rows],
        }
        arm_stats = {
            arm: metric_stats_by_key([result.metrics() for result in results])
            for arm, results in arm_results.items()
        }
        deltas: dict[str, dict[str, dict[str, float | None]]] = {}
        for base_arm, treat_arm, name in _REPEAT_COMPARISONS:
            base, treat = arm_stats[base_arm], arm_stats[treat_arm]
            deltas[name] = {}
            for key in sorted(base.keys() & treat.keys()):
                delta, se = delta_of_means(base[key], treat[key])
                deltas[name][key] = {"delta": delta, "se": se}
        per_bundle.append(
            {
                "work_id": work_id,
                "arms": {
                    arm: {key: stat.as_dict() for key, stat in stats.items()}
                    for arm, stats in arm_stats.items()
                },
                "deltas": deltas,
                "ours_retrieval_empty": rows[0].ours_retrieval_empty,
            }
        )
    return {
        "k": len(rows_by_rep),
        "per_bundle": per_bundle,
        "doctrine": (
            "headline per-bundle deltas are read HERE (repeats collapsed within task, "
            "curve.py M2; deltas of arm means +/- se); the top-level per_bundle/gaps "
            "blocks are the schema-stable rep-1 view"
        ),
    }


def run_validity_gates(
    bundles: Sequence[TaskBundle],
    *,
    grid_dir: Path,
    runner_factory: Callable[[], AbstractContextManager[ReproRunner]] = LiveReproRunner,
) -> list[ValidityResult]:
    """CSB oracle-validity gate per bundle (mem-g6a): gold diff must reproduce,
    empty diff must fail. Runs the SAME repro runner the graded scoring uses
    (``runner_factory`` -- `LiveReproRunner` for the native pool, `FtpReproRunner`
    for the ftp corpus), so its judgment is the test runner's. A bundle whose oracle
    is broken (gold does not reproduce, or a gold test passes without the fix) is
    reported invalid and excluded from the anchored read rather than silently scored.

    Resumable: each result persists to ``<grid_dir>/<work_id>.validity.json`` and an
    existing file is loaded, never re-executed -- a resume after a token-expiry abort
    in the agent phase does not pay the Docker repro cost again."""
    grid_dir.mkdir(parents=True, exist_ok=True)
    pending = [b for b in bundles if not (grid_dir / f"{b.work_id}.validity.json").is_file()]
    results: list[ValidityResult] = []
    runner_cm = runner_factory() if pending else None
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
        "--repeats",
        type=int,
        default=MIN_HEADLINE_REPEATS,
        help=(
            "identical runs per (bundle, arm); headline floor is "
            f"{MIN_HEADLINE_REPEATS} (mem-eacq), rep 1 resumes from the legacy dirs"
        ),
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
    if args.repeats < MIN_HEADLINE_REPEATS:
        parser.error(
            f"--repeats must be >= {MIN_HEADLINE_REPEATS} for a headline run "
            f"(mem-eacq repeats doctrine), got {args.repeats}"
        )

    candidates = load_admitted_bundles(args.bundles_dir, args.manifest)
    retrieve = _default_runner(args.mem_bin)
    payloads = resolve_payloads(candidates, store_path=args.store, runner=retrieve)

    # All arms and reps share one pinned instrument (`pinned_stream_exec`: dead
    # runs raise the batch-handled EmptyRunError, pin drift raises before persist).
    def make_exec_stream(jobs_dir: Path) -> StreamExec:
        return pinned_stream_exec(
            jobs_dir=jobs_dir,
            model=args.model,
            cli_version=args.cli_version,
            timeout_sec=args.timeout_sec,
        )

    if args.dry_run:
        # Construct + leak-validate every arm's task for every candidate (tasks are
        # byte-identical across reps, so one construction pass validates all); the
        # Docker-backed validity gate and agent runs are skipped (no token needed).
        batch_kwargs = {
            "probe_dir": args.probe_dir,
            "tasks_dir": args.probe_dir / "tasks",
            "exec_stream": make_exec_stream(args.probe_dir / "jobs"),
            "dry_run": True,
        }
        with_payload = [b for b in candidates if payloads[b.work_id]]
        planned = run_probe_batch(candidates, ("none-clean",), **batch_kwargs)["planned"]
        planned += run_probe_batch(candidates, (BUILTIN_CONDITION,), **batch_kwargs)["planned"]
        planned += run_probe_batch(
            with_payload,
            ("ours",),
            ours_payloads_for=lambda bundle: payloads[bundle.work_id],
            **batch_kwargs,
        )["planned"]
        print(
            f"\nDRY RUN: {planned} task(s) constructed + leak-validated "
            f"(x {args.repeats} repeats at execution); nothing executed."
        )
        return 0

    # CSB validity gate PRECEDES the grid (mem-g6a doctrine): a broken oracle is
    # excluded BEFORE any agent run, never silently scored, and the agent budget is
    # not spent on a bundle whose anchored read would be meaningless.
    validity = [] if args.skip_validity else run_validity_gates(candidates, grid_dir=args.grid_dir)
    # --skip-validity admits every candidate (validity is empty -> no exclusions).
    valid_ids = (
        {v.work_id for v in validity if v.valid} if validity else {b.work_id for b in candidates}
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

    # The held record's canonical full+relaxed signatures -- the mem-tnyo H3
    # signature-overlap covariate input, read from the retrieval envelope.
    # Deterministic, so resolved once and shared across reps.
    held_signatures = resolve_held_signatures(bundles, store_path=args.store, runner=retrieve)
    # Reps execute strictly sequentially (shared-clone worktree sweeps are
    # prefix-global). Rep 1 uses the legacy dirs so prior single-run artifacts
    # resume as rep 1; the deterministic stages above (validity, payloads,
    # surface evidence) ran once -- only the agent legs and the judge sample.
    judge = ClaudeRubricJudge(model=args.judge_model)
    rows_by_rep: list[list[ThreeArmRow]] = []
    for rep in range(1, args.repeats + 1):
        probe_rep = legacy_rep_dir(args.probe_dir, rep)
        grid_rep = legacy_rep_dir(args.grid_dir, rep)
        batch_kwargs = {
            "probe_dir": probe_rep,
            "tasks_dir": probe_rep / "tasks",
            "exec_stream": make_exec_stream(probe_rep / "jobs"),
            "dry_run": False,
        }
        scrub_unfinished_jobs(
            bundles, ("none-clean", BUILTIN_CONDITION, "ours"), probe_dir=probe_rep
        )
        # none-clean and builtin (fresh `none`) run every valid bundle; ours runs
        # only payload-bearing bundles -- an empty retrieval makes the ours task
        # byte-identical to none-clean, so assemble_rows reuses that leg (delta 0).
        tally_clean = run_probe_batch(bundles, ("none-clean",), **batch_kwargs)
        tally_builtin = run_probe_batch(bundles, (BUILTIN_CONDITION,), **batch_kwargs)
        tally_ours = run_probe_batch(
            with_payload,
            ("ours",),
            ours_payloads_for=lambda bundle: payloads[bundle.work_id],
            held_signatures_for=lambda bundle: held_signatures[bundle.work_id],
            **batch_kwargs,
        )
        print(
            f"agent runs rep {rep}/{args.repeats}: "
            f"none-clean executed={tally_clean['executed']} "
            f"builtin executed={tally_builtin['executed']} ours executed={tally_ours['executed']}"
        )

        pending = [(bundle, "none-clean") for bundle in bundles]
        pending += [(bundle, BUILTIN_CONDITION) for bundle in bundles]
        pending += [(bundle, "ours") for bundle in with_payload]
        _, tally_scored = score_runs(
            pending,
            probe_jobs_dir=probe_rep / "jobs",
            grid_dir=grid_rep,
            judge=judge,
            judge_rounds=args.judge_rounds,
        )
        print(
            f"scoring rep {rep}/{args.repeats}: executed={tally_scored['executed']} "
            f"skipped={tally_scored['skipped']}"
        )
        rows_by_rep.append(assemble_rows(bundles, payloads, grid_rep))

    rows = rows_by_rep[0]
    evidence: list[OursRungEvidence] = [
        ours_rung_evidence(bundle, mem_bin=args.mem_bin, store_path=args.store, runner=retrieve)
        for bundle in bundles
    ]
    summary = summarize_grid_3arm(rows, evidence, validity=validity)
    summary["repeats"] = repeats_block(rows_by_rep)
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
        "repeats": args.repeats,
    }
    summary["pool"] = {
        "candidates": [b.work_id for b in candidates],
        "admitted": [b.work_id for b in bundles],
        "excluded_by_validity": excluded,
    }
    summary["builtin_surface_evidence"] = surface_evidence
    # mem-tnyo H3 parity: the per-payload signature-overlap covariate (report
    # column) for the ours (oracle-triggered) payloads this grid injected.
    summary["signature_overlap"] = signature_overlap_summary(
        {"ours": {b.work_id: payloads[b.work_id] for b in bundles}}, held_signatures
    )
    out = args.grid_dir / "summary-3arm-graded.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"summary -> {out}  (bundles={summary['n_bundles']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
