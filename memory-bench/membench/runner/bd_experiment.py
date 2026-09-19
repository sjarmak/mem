"""Bounded, durable bd capture/recall comparisons; default invocation only freezes a plan.

A started pair is never automatically retried: its process may have spent money before
writing a leg. Preserve the directory and reconcile evidence before any replacement trial.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from membench.runner.e1_grid import (
    LegRecord,
    ResumeMismatchError,
    UnmeasuredStreak,
    _refusal,
    assert_scoreable_corpus,
    condition_settings,
    corpus_fingerprint,
    out_lock,
    run_rung_cell,
    write_json_new,
)
from membench.runner.headless_agent import resolve_cli_version
from membench.runner.leg_plans import LEG_PLANS, PAIR_ROLES, plan_name
from membench.runner.resume_cache import digest
from membench.runner.tool_surface import resolve_bd_binary
from membench.runner.toolreq_corpus import load_twin_corpus
from membench.runner.toolreq_realagent import DEFAULT_CORPUS, ToolReqRealAgentTask

# The comparison is a 3x2: how bd is offered (nothing beyond the R4 block / a deployment
# CLAUDE.md / that plus the interception hook) crossed with whether the CLI's OWN memory system
# is left on.
#
# The second axis is not a control, it is the question. Every number this harness has published
# was bought with native memory ON, which is deliberate — the contrast a user actually faces is
# bd against the memory the CLI already ships. But it leaves the hook's job stated and untested:
# the hook exists to catch a reach at the native path and answer it with the bd verbs, and if the
# native path is simply absent, the reach has nowhere else to go and the hook may buy nothing.
# `redirect_native_off` vs `explicit_native_off` is that question with one cell; the `*_native_off`
# vs native-on twins say what turning the CLI's memory off does on its own.
#
# Every key here is a `run_rung_cell` keyword: `_execute_pair` splats the frozen condition
# straight into the runner, so a condition cannot describe one arm and run another.
CONDITIONS: dict[str, dict[str, Any]] = {
    "generic": {
        "rung": "R4",
        "bd_context": False,
        "native_memory_hook_mode": "observe",
        "native_memory_off": False,
    },
    "explicit": {
        "rung": "R4",
        "bd_context": True,
        "native_memory_hook_mode": "observe",
        "native_memory_off": False,
    },
    "redirect": {
        "rung": "R4",
        "bd_context": True,
        "native_memory_hook_mode": "redirect",
        "native_memory_off": False,
    },
    "generic_native_off": {
        "rung": "R4",
        "bd_context": False,
        "native_memory_hook_mode": "observe",
        "native_memory_off": True,
    },
    "explicit_native_off": {
        "rung": "R4",
        "bd_context": True,
        "native_memory_hook_mode": "observe",
        "native_memory_off": True,
    },
    "redirect_native_off": {
        "rung": "R4",
        "bd_context": True,
        "native_memory_hook_mode": "redirect",
        "native_memory_off": True,
    },
}

# The contrasts the design is FOR, as (treatment, baseline) pairs, named once here so the
# scheduler and the analyzer cannot drift into comparing different things. The analyzer keeps
# every condition against `generic` on top of these; these are the ones that carry the design's
# questions, and each is dropped silently when a run's manifest froze without both of its
# conditions (an older three-condition artifact re-analyzes unchanged).
CONDITION_CONTRASTS: tuple[tuple[str, str], ...] = (
    # Does the interception hook add anything over deployment context alone? Once with the CLI's
    # own memory on, once with it off — the same question either side of the second axis.
    ("redirect", "explicit"),
    ("redirect_native_off", "explicit_native_off"),
    # What does turning the CLI's own memory off do to each arm on its own?
    ("generic_native_off", "generic"),
    ("explicit_native_off", "explicit"),
    ("redirect_native_off", "redirect"),
)


def native_memory_settings() -> dict[str, dict[str, Any]]:
    """What each condition SEEDS into its config dir, resolved through the same function the
    runner seeds with. Recorded in the manifest so the frozen identity states the pin per
    condition rather than the empty dict a single-axis design could get away with. It remains a
    statement of intent: what a leg RAN under is the disk read-back on its own record
    (``native_memory_pinned_off``)."""
    return {
        name: dict(
            condition_settings(
                str(condition["rung"]), native_memory_off=bool(condition["native_memory_off"])
            )
        )
        for name, condition in CONDITIONS.items()
    }


class IncompletePairError(RuntimeError):
    """A purchased pair needs reconciliation, never an implicit retry."""


def select_tasks(
    tasks: Sequence[ToolReqRealAgentTask], *, n_tasks: int
) -> list[ToolReqRealAgentTask]:
    if n_tasks < 1:
        raise ValueError("n_tasks must be positive")
    indexed = {(task.work_id, task.variant): task for task in tasks}
    ids = sorted({task.work_id for task in tasks})[:n_tasks]
    if len(ids) != n_tasks or len(indexed) != len(tasks):
        raise ValueError("Insufficient tasks or duplicate task identities")
    expected = [(work_id, variant) for work_id in ids for variant in ("necessary", "unnecessary")]
    if any(key not in indexed for key in expected):
        raise ValueError("Every selected task must have necessary and unnecessary twins")
    return [indexed[key] for key in expected]


def build_manifest(
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    model: str,
    cli_version: str,
    bd_identity: Mapping[str, str],
    source_fingerprint: str,
    repeats: int,
    seed: int,
    timeout_s: float,
    leg_plan: Sequence[str] = PAIR_ROLES,
) -> dict[str, Any]:
    if (
        repeats < 1
        or timeout_s <= 0
        or not all((model, cli_version, source_fingerprint))
        or not all(bd_identity.get(key) for key in ("path", "sha256", "version"))
    ):
        raise ValueError("Pinned identities and positive repeats/timeout are required")
    # The plan is part of the frozen identity: every schedule entry spends len(plan) legs on one
    # store, and the price, the leg files and the scorer all read that length from here.
    plan = tuple(leg_plan)
    plan_name(plan)
    rng = random.Random(seed)
    blocks = [(task, repeat) for repeat in range(repeats) for task in tasks]
    rng.shuffle(blocks)
    schedule = []
    for task, repeat in blocks:
        conditions = list(CONDITIONS)
        rng.shuffle(conditions)
        for condition in conditions:
            schedule.append(
                {
                    "condition": condition,
                    "work_id": task.work_id,
                    "variant": task.variant,
                    "repeat": repeat,
                }
            )
    return {
        "schema_version": 5,
        "leg_plan": list(plan),
        "instrument_bd": True,
        "model": model,
        "cli_version": cli_version,
        "bd_version": bd_identity["version"],
        "bd_identity": dict(bd_identity),
        "source_fingerprint": source_fingerprint,
        "corpus_fingerprint": corpus_fingerprint(tasks),
        "conditions": CONDITIONS,
        "native_memory_settings": native_memory_settings(),
        "repeats": repeats,
        "seed": seed,
        "timeout_s": timeout_s,
        "planned_pairs": len(schedule),
        "planned_calls": len(plan) * len(schedule),
        "schedule": schedule,
        "tasks": [{"work_id": t.work_id, "variant": t.variant} for t in tasks],
    }


def resolve_bd_identity() -> dict[str, str]:
    """Identify the executable provisioning actually resolves, including env overrides."""
    binary = Path(resolve_bd_binary()).resolve(strict=True)
    version = subprocess.run(
        [str(binary), "--version"], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    return {
        "path": str(binary),
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "version": version,
    }


def source_fingerprint() -> str:
    """Hash the executable Python harness, including this scheduler."""
    package = Path(__file__).resolve().parents[1]
    files = {
        str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package.rglob("*.py"))
    }
    return digest(files)


def freeze(out: Path, manifest: Mapping[str, Any]) -> None:
    path = out / "manifest.json"
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise ResumeMismatchError("Frozen experiment identity differs; nothing was purchased")
    else:
        if any(out.iterdir()):
            raise ResumeMismatchError("Nonempty output directory has no manifest")
        write_json_new(path, manifest)


def _pair_dir(out: Path, pair: Mapping[str, Any]) -> Path:
    # Hash user/corpus identifiers so no work_id can become a path traversal.
    task_id = digest({"work_id": pair["work_id"], "variant": pair["variant"]})[:24]
    return out / "pairs" / str(pair["condition"]) / task_id / str(pair["repeat"])


def _execute_pair(
    directory: Path,
    pair: Mapping[str, Any],
    task: ToolReqRealAgentTask,
    manifest: Mapping[str, Any],
    *,
    cell_runner: Callable[..., Any],
    corpus_dir: Path | None,
    streak: UnmeasuredStreak,
) -> None:
    directory.mkdir(parents=True)
    write_json_new(directory / "started.json", {"pair": pair, "manifest_digest": digest(manifest)})
    legs_dir = directory / "legs"
    legs_dir.mkdir()

    def on_leg(leg: LegRecord) -> None:
        write_json_new(legs_dir / leg.filename, leg.row())

    try:
        cell = cell_runner(
            task,
            **manifest["conditions"][pair["condition"]],
            repeats=1,
            instrument_bd=manifest["instrument_bd"],
            model=manifest["model"],
            dry_run=False,
            timeout_s=manifest["timeout_s"],
            on_leg=on_leg,
            streak=streak,
            expect_cli_version=manifest["cli_version"],
            corpus_dir=corpus_dir,
            # A manifest frozen before plans existed ran pairs; it says so by saying nothing.
            leg_plan=tuple(manifest.get("leg_plan") or PAIR_ROLES),
        )
        write_json_new(
            directory / "cell.json",
            {"pair": pair, "cell": cell.row(), "manifest_digest": digest(manifest)},
        )
    except BaseException as exc:
        write_json_new(directory / "halt.json", {"type": type(exc).__name__, "error": str(exc)})
        raise


def execute(
    out: Path,
    manifest: Mapping[str, Any],
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    max_pairs: int,
    cell_runner: Callable[..., Any] = run_rung_cell,
    corpus_dir: Path | None = None,
    bd_identity_reader: Callable[[], Mapping[str, str]] = resolve_bd_identity,
) -> dict[str, int]:
    if max_pairs < 1:
        raise ValueError("max_pairs must be positive")
    if corpus_fingerprint(tasks) != manifest["corpus_fingerprint"]:
        raise ResumeMismatchError("Task corpus differs from frozen manifest")
    out.mkdir(parents=True, exist_ok=True)
    indexed = {(task.work_id, task.variant): task for task in tasks}
    completed = 0
    purchased = 0
    streak = UnmeasuredStreak()
    with out_lock(out):
        freeze(out, manifest)
        # Audit the entire schedule before any new purchase, including later interrupted pairs.
        for pair in manifest["schedule"]:
            directory = _pair_dir(out, pair)
            cell_path = directory / "cell.json"
            if directory.exists() and not cell_path.exists():
                raise IncompletePairError(
                    f"Incomplete pair at {directory}; refusing automatic repurchase"
                )
            if cell_path.exists():
                saved = json.loads(cell_path.read_text())
                if saved["pair"] != pair or saved["manifest_digest"] != digest(manifest):
                    raise ResumeMismatchError(f"Pair identity mismatch at {directory}")
                completed += 1
        for pair in manifest["schedule"]:
            directory = _pair_dir(out, pair)
            if directory.exists():
                continue
            if purchased >= max_pairs:
                break
            if bd_identity_reader() != manifest["bd_identity"]:
                raise ResumeMismatchError("Resolved bd executable differs from frozen identity")
            _execute_pair(
                directory,
                pair,
                indexed[(pair["work_id"], pair["variant"])],
                manifest,
                cell_runner=cell_runner,
                corpus_dir=corpus_dir,
                streak=streak,
            )
            completed += 1
            purchased += 1
    return {
        "completed": completed,
        "new_pairs": purchased,
        "remaining": len(manifest["schedule"]) - completed,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--model", required=True)
    parser.add_argument("--expect-cli-version", required=True)
    parser.add_argument("--tasks", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--timeout-s", type=float, default=600)
    parser.add_argument("--max-pairs", type=int, default=1)
    parser.add_argument(
        "--legs",
        choices=sorted(LEG_PLANS),
        default="pair",
        help="the sessions each schedule entry spends on one store: a pair establishes a fact "
        "and acts on it; a trial establishes the previous version, revises it, acts on the "
        "current one, then lets a session primed on the previous version try to write",
    )
    parser.add_argument("--fire", action="store_true")
    args = parser.parse_args(argv)
    refusal = _refusal(dry_run=not args.fire, model=args.model)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    try:
        _, all_tasks = load_twin_corpus(args.corpus_dir)
        tasks = select_tasks(all_tasks, n_tasks=args.tasks)
        assert_scoreable_corpus(tasks)
        cli_version = resolve_cli_version()
        if cli_version != args.expect_cli_version:
            raise ResumeMismatchError(f"CLI version {cli_version!r} differs from expected pin")
        bd_identity = resolve_bd_identity()
        manifest = build_manifest(
            tasks,
            model=args.model,
            cli_version=cli_version,
            bd_identity=bd_identity,
            source_fingerprint=source_fingerprint(),
            repeats=args.repeats,
            seed=args.seed,
            timeout_s=args.timeout_s,
            leg_plan=LEG_PLANS[args.legs],
        )
        if args.fire:
            result: Mapping[str, Any] = execute(
                args.out, manifest, tasks, max_pairs=args.max_pairs, corpus_dir=args.corpus_dir
            )
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            with out_lock(args.out):
                freeze(args.out, manifest)
            result = manifest
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Experiment stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
