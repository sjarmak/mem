#!/usr/bin/env python3
"""mem-bxhh.3.3 graded 3-arm grid over the codeprobe ftp-oracle corpus.

The native graded driver (`run_grid_3arm_graded.py`) scores host gold-test FILES;
this one scores the curated red->green NODE-ID subset on each bundle's
``verification.ftp_oracle`` by injecting `FtpReproRunner` into BOTH the validity gate
and the scorer. Everything else is the same instrument: three FRESH arms under one
pin -- ``none-clean`` (native memory stripped, our system off), ``builtin`` (the
``none`` condition: codeprobe's ``.claude/`` native memory present, our system off),
and ``ours`` (our retrieval payload injected) -- the per-signal paired per-bundle
deltas are the headline (no pooled mean, no composite), with the Sonnet rubric judge
(mem-r5y) riding along as a reported side signal.

The CSB validity gate runs FIRST with the SAME `FtpReproRunner`, so a bundle whose
ftp oracle does not hold (gold not all-green, or empty not all-red) is excluded
before a single agent token is spent -- and that gate is FREE (Docker pytest only),
so ``--validity-only`` is the pre-flight that proves the corpus + wiring with no
paid run at all.

PAID + EXTERNAL: the agent arms call ``claude -p`` against the
``/home/ds/.claude-homes`` pool. HALT branch-ready -- run the full grid only with
explicit approval. Free paths need no token:

    uv run python scripts/run_grid_3arm_ftp.py --validity-only   # Docker pytest, no agent
    uv run python scripts/run_grid_3arm_ftp.py --dry-run         # construct+leak-check tasks

The paid smoke run (one bundle is enough to satisfy "1 smoke run executes green"):

    CLAUDE_CODE_OAUTH_TOKEN=... uv run python scripts/run_grid_3arm_ftp.py --limit 1

ZFC: pure plumbing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from run_gate_probe import run_probe_batch
from run_grid import score_runs
from run_grid_3arm import (
    assemble_rows,
    builtin_surface_evidence,
    resolve_payloads,
    scrub_unfinished_jobs,
)
from run_grid_3arm_graded import run_validity_gates

from membench.grading.graded import DEFAULT_JUDGE_ROUNDS, ClaudeRubricJudge
from membench.grading.validity_gate import ValidityResult
from membench.harbor.bundle_grid import OursRungEvidence, ours_rung_evidence, summarize_grid_3arm
from membench.harbor.ftp_repro import FTP_REPRO_WORKTREE_PREFIX, FtpReproRunner
from membench.harbor.probe_gate import (
    EmptyRunError,
    assert_run_pins,
    detect_run_failure,
    harbor_stream_exec,
)
from membench.memory_systems.ours_system import _default_runner
from membench.schemas.bundle import TaskBundle

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The materialized codeprobe ftp anchor bundles (mem-bxhh.3.2): gitignored data,
# regenerable via `scripts/materialize_codeprobe_anchors.py`.
DEFAULT_BUNDLES_DIR = PROJECT_ROOT / ".mem/bundles-codeprobe"
# Isolated probe/grid dirs so an ftp run never collides with the native pool's.
DEFAULT_PROBE_DIR = PROJECT_ROOT / ".mem/probe-codeprobe"
DEFAULT_GRID_DIR = PROJECT_ROOT / ".mem/grid-codeprobe"
DEFAULT_STORE = PROJECT_ROOT / ".mem/store.db"
DEFAULT_MEM_BIN = str(PROJECT_ROOT / "bin/mem")

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_CLI_VERSION = "2.1.173"
# The builtin arm IS the `none` condition (codeprobe `.claude/` native memory present,
# our system off) run fresh; assemble_rows relabels the scored `none` leg to `builtin`.
BUILTIN_CONDITION = "none"


def load_ftp_bundles(bundles_dir: Path, *, limit: int | None = None) -> list[TaskBundle]:
    """Every ``*.json`` anchor bundle carrying an ftp oracle, sorted by work_id,
    optionally capped to the first ``limit`` (the smoke-run knob). A bundle without
    ``verification.ftp_oracle`` is not an ftp anchor and is skipped."""
    bundles: list[TaskBundle] = []
    for path in sorted(bundles_dir.glob("*.json")):
        bundle = TaskBundle.model_validate_json(path.read_text(encoding="utf-8"))
        if bundle.verification.ftp_oracle is not None:
            bundles.append(bundle)
    bundles.sort(key=lambda b: b.work_id)
    return bundles[:limit] if limit is not None else bundles


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    parser.add_argument("--probe-dir", type=Path, default=DEFAULT_PROBE_DIR)
    parser.add_argument("--grid-dir", type=Path, default=DEFAULT_GRID_DIR)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--mem-bin", default=DEFAULT_MEM_BIN)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="harbor agent model_name")
    parser.add_argument("--cli-version", default=DEFAULT_CLI_VERSION)
    parser.add_argument("--judge-model", default=DEFAULT_MODEL)
    parser.add_argument("--judge-rounds", type=int, default=DEFAULT_JUDGE_ROUNDS)
    parser.add_argument("--timeout-sec", type=float, default=None)
    parser.add_argument(
        "--limit", type=int, default=None, help="cap to the first N ftp bundles (smoke run: 1)"
    )
    parser.add_argument(
        "--validity-only",
        action="store_true",
        help="run only the FREE CSB validity gate (Docker pytest, no agent) and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="construct + leak-validate all tasks, print the plan, execute nothing",
    )
    args = parser.parse_args(argv)

    candidates = load_ftp_bundles(args.bundles_dir, limit=args.limit)
    if not candidates:
        print(f"error: no ftp-oracle bundles under {args.bundles_dir}", file=sys.stderr)
        return 1
    print(f"ftp corpus: {len(candidates)} bundle(s) [{', '.join(b.work_id for b in candidates)}]")

    # CSB validity gate (mem-g6a) with the SAME FtpReproRunner the scorer uses -- FREE
    # (Docker pytest only), the corpus + wiring proof before any paid token. Skipped
    # under --dry-run, which only constructs + leak-checks tasks and must touch no
    # container; --dry-run scores nothing, so the gate's exclusions are moot there.
    validity: list[ValidityResult] = []
    excluded: list[str] = []
    bundles = candidates
    if not args.dry_run:
        validity = run_validity_gates(
            candidates, grid_dir=args.grid_dir, runner_factory=FtpReproRunner
        )
        valid_ids = {v.work_id for v in validity if v.valid}
        bundles = [b for b in candidates if b.work_id in valid_ids]
        excluded = [b.work_id for b in candidates if b.work_id not in valid_ids]
        if not bundles:
            raise RuntimeError(
                f"every candidate failed the validity gate (excluded: {excluded}) -- the ftp "
                "oracle does not hold under the runner; fix the corpus, not the grid"
            )
        print(
            f"validity: {len(bundles)}/{len(candidates)} sound ftp oracle(s)"
            + (f"; excluded {excluded}" if excluded else "")
        )
        if args.validity_only:
            print("validity-only: no agent run requested.")
            return 0

    # Native-memory surface proof: each builtin (fresh `none`) image must carry
    # codeprobe's `.claude/` memory at base_commit, else the clean-room strip is a
    # no-op and the builtin relabel would be false. Fails before any agent run.
    surface_evidence = builtin_surface_evidence(bundles)
    retrieve = _default_runner(args.mem_bin)
    payloads = resolve_payloads(bundles, store_path=args.store, runner=retrieve)
    with_payload = [b for b in bundles if payloads[b.work_id]]
    print(
        f"retrieval coverage: {len(with_payload)}/{len(bundles)} bundle(s) with a "
        f"non-empty ours payload ({', '.join(b.work_id for b in with_payload) or 'none'})"
    )

    def exec_stream(task_dir: Path) -> str:
        """One pinned instrument across all three fresh arms; dead runs classified
        FIRST (so a 401/usage-limit raises the batch-handled EmptyRunError, not a
        misleading PinMismatchError), drift raises before persist."""
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
        planned = run_probe_batch(bundles, ("none-clean",), **batch_kwargs)["planned"]
        planned += run_probe_batch(bundles, (BUILTIN_CONDITION,), **batch_kwargs)["planned"]
        planned += run_probe_batch(
            with_payload,
            ("ours",),
            ours_payloads_for=lambda bundle: payloads[bundle.work_id],
            **batch_kwargs,
        )["planned"]
        print(f"\nDRY RUN: {planned} task(s) constructed + leak-validated; nothing executed.")
        return 0

    scrub_unfinished_jobs(
        bundles, ("none-clean", BUILTIN_CONDITION, "ours"), probe_dir=args.probe_dir
    )
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
    pending = [(b, "none-clean") for b in bundles]
    pending += [(b, BUILTIN_CONDITION) for b in bundles]
    pending += [(b, "ours") for b in with_payload]
    _, tally_scored = score_runs(
        pending,
        probe_jobs_dir=args.probe_dir / "jobs",
        grid_dir=args.grid_dir,
        judge=judge,
        judge_rounds=args.judge_rounds,
        runner_factory=FtpReproRunner,
        worktree_prefix=FTP_REPRO_WORKTREE_PREFIX,
    )
    print(f"scoring: executed={tally_scored['executed']} skipped={tally_scored['skipped']}")

    rows = assemble_rows(bundles, payloads, args.grid_dir)
    evidence: list[OursRungEvidence] = [
        ours_rung_evidence(b, mem_bin=args.mem_bin, store_path=args.store, runner=retrieve)
        for b in bundles
    ]
    summary = summarize_grid_3arm(rows, evidence, validity=validity)
    summary["arm_provenance"]["builtin"] = (
        "fresh agent runs under the same pinned instrument as the clean arms: "
        "codeprobe `.claude/` native memory present in the image (the `none` condition "
        "run fresh), our system off -- the baseline-to-beat, scored on the ftp node-id "
        "subset by FtpReproRunner"
    )
    summary["pins"] = {
        "model": args.model,
        "cli_version": args.cli_version,
        "judge_model": args.judge_model,
        "judge_rounds": args.judge_rounds,
        "repro_runner": "FtpReproRunner",
        "builtin_arm": "fresh",
    }
    summary["pool"] = {
        "candidates": [b.work_id for b in candidates],
        "admitted": [b.work_id for b in bundles],
        "excluded_by_validity": excluded,
    }
    summary["builtin_surface_evidence"] = surface_evidence
    out = args.grid_dir / "summary-3arm-ftp.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"summary -> {out}  (bundles={summary['n_bundles']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
