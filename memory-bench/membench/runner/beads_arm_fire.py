"""The three-arm grid's driver: the one path in this package that buys beads-vs-none-vs-builtin.

``beads_arm_grid`` mints an arm and runs one cell; ``beads_arm_plan`` prices the grid and scores
what comes back; this module is what stands between them and an account. It holds the refusal
ladder, the lock, the resume identity, the halts and the artifact — and nothing else, because a
reader auditing what a paid run can and cannot do should not have to read a scorer first.

Three entries, and pricing is never the same keystroke as spending:

    --dry-run    the whole grid on `e1_grid._silent_runner`; spends nothing, proves the plumbing
    --preflight  ONE task x 3 arms x 1 repeat, paid; gate 4's discovery check. Zero is a HALT
    --fire       the grid, paid, resumable against --out

``--n-tasks`` is the staging mechanism (Stephanie's ruling, 2026-09-20, decision 1 option b): the
pilot buys ``--n-tasks 1``, its receipts and per-arm logs are read, and the remaining tasks are
released by re-running with the full count against the SAME ``--out``. ``grid_keys`` is sorted and
capped in one place so the pilot's cells are cells of the widened grid, not a fragment it re-buys.

Reads ``docs/prereg-beads-three-arm.md`` sections 4, 5 and 6.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from membench.runner.bd_build import BdBuild, resolve_bd_build
from membench.runner.beads_arm_grid import ArmCell, run_arm_cell
from membench.runner.beads_arm_plan import (
    PREFLIGHT_TASKS,
    PROTOCOL_VERSION,
    ArmGridKey,
    ArmPlanError,
    cell_from_row,
    cell_key,
    discovery,
    grid_keys,
    priced_plan,
    summarize,
    work_ids_of,
)
from membench.runner.e1_grid import (
    EXIT_HALT,
    EXIT_NO_CORPUS,
    EXIT_OK,
    EXIT_REFUSED,
    QuotaHaltError,
    ResumeMismatchError,
    RigHaltError,
    UnmeasuredStreak,
    _refusal,
    _silent_runner,
    atomic_write_json,
    corpus_fingerprint,
    is_quota_halt,
    out_lock,
    spawn_timeout_of,
    write_json_new,
    write_text_new,
)
from membench.runner.headless_agent import (
    HeadlessAgentError,
    MemoryChannel,
    resolve_cli_version,
    resolve_model,
)
from membench.runner.memory_arm import ARM_NAMES, arm_settings_fingerprint
from membench.runner.tool_surface import (
    RECOGNIZER_IMPLEMENTATION_VERSION,
    MemoryToolError,
    surface_fingerprint,
)
from membench.runner.toolreq_corpus import load_twin_corpus
from membench.runner.toolreq_realagent import ToolReqRealAgentTask
from membench.spawn import Runner

# The framing every leg runs under. One value for all three arms: it is a `shared` field of the
# protocol in everything but name, and an arm that framed its memory differently would be a
# different experiment wearing this one's gates.
CHANNEL = MemoryChannel.TRUSTED

# The corpus §6 names. Stated here rather than defaulted to `toolreq_realagent.DEFAULT_CORPUS`,
# which points at a `fixtures/worlds-tool` that does not exist in this worktree -- a default that
# resolves to nothing is a fire that reports "no corpus" instead of buying the wrong one, but the
# operator should not have to type the right path to avoid that.
DEFAULT_CORPUS = Path("fixtures/worlds-tool-jev32")

HALT_NO_CALL = "NO-MEMORY-CALL"


def resume_identity(
    *, model: str, cli_version: str, corpus: str, work_ids: Sequence[str], bd_build: BdBuild
) -> dict[str, Any]:
    """Everything a partial artifact must match before its cells may be pooled into this fire.

    The arm registry is in here (``arm_settings_fingerprint``) because it is the experiment: a
    cell bought when the floor arm still reached the host toolchain, or when the comparator's
    hook still observed rather than denied, measured a different machine. So is the sorted
    work_id list — §6 buys the whole corpus precisely so the subset carries no researcher degree
    of freedom, and an identity that could not see a re-selection would let a later partial re-buy
    silently choose a friendlier set.

    The bd build is in here (mem-0wpq8.2) because it is the flywheel's one variable per turn: a
    cell bought against last turn's binary measured a different treatment."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        **bd_build.identity(),
        "model": resolve_model(model) or "cli-default",
        "cli_version": cli_version,
        "corpus_fingerprint": corpus,
        "arm_settings_fingerprint": arm_settings_fingerprint(),
        "surface_fingerprint": surface_fingerprint(),
        "recognizer_version": RECOGNIZER_IMPLEMENTATION_VERSION,
        "work_ids": list(work_ids),
    }


def admissible_cells(
    summary: Mapping[str, Any], *, identity: Mapping[str, Any], grid: Sequence[ArmGridKey]
) -> list[ArmCell]:
    """The cells a partial ``--out`` contributes to a resumed fire.

    Every identity field must match the rig now running, and a blank field on either side is a
    mismatch rather than a pass: an artifact that cannot say what produced it cannot be shown to
    have been produced by this.

    Rows are then filtered to what is admissible AS EVIDENCE — unmeasured cells are dropped so the
    resume is a chance to buy them, unpaid cells are dropped because a dry-run cell carried into a
    paid grid publishes a simulation as a measurement — and two things REFUSE rather than filter:
    a duplicate key (two fires wrote this file and neither row can be shown to be the one to keep)
    and a key outside the current grid (a cell this fire will not run cannot be pooled into its
    rates)."""
    blank = [field for field, value in identity.items() if not value]
    if blank:
        raise ResumeMismatchError(
            f"this rig cannot state its own {', '.join(blank)}; refusing to match an artifact "
            "against an identity it does not have"
        )
    got = {field: summary.get(field) for field in identity}
    if got != dict(identity):
        differing = {f: (got[f], identity[f]) for f in identity if got[f] != identity[f]}
        detail = "; ".join(f"{f}: artifact={a!r} rig={b!r}" for f, (a, b) in differing.items())
        raise ResumeMismatchError(
            f"partial artifact was produced by a different rig ({detail}). "
            "Not resuming into a different rig's grid."
        )
    allowed = set(grid)
    seen: set[ArmGridKey] = set()
    kept: list[ArmCell] = []
    for row in summary.get("cells", ()):
        cell = cell_from_row(row)
        key = cell_key(cell)
        if key in seen:
            raise ResumeMismatchError(
                f"partial artifact carries {key} twice; two fires wrote it and neither row can "
                "be shown to be the one this grid should keep"
            )
        seen.add(key)
        if key not in allowed:
            raise ResumeMismatchError(
                f"partial artifact carries {key}, which is not a cell of the grid this fire "
                "runs; it cannot be pooled into these rates"
            )
        if cell.status != "ok" or not cell.paid:
            continue
        kept.append(cell)
    return kept


def _unmeasured(key: ArmGridKey, *, status: str, detail: str, paid: bool) -> ArmCell:
    """A cell that bought legs and measured nothing. Explicitly NOT a scored zero (gate 12): it
    carries no pass, is absent from every rate, and counts against the arm's unmeasured budget."""
    arm_name, variant, work_id, repeat = key
    return ArmCell(
        arm=arm_name,
        work_id=work_id,
        variant=variant,
        repeat=repeat,
        passed=False,
        engaged=False,
        leaked=False,
        establish_tool_names=(),
        endogenous_verbs=(),
        establish_outcomes=(),
        goal_outcomes=(),
        native_reaches=0,
        pinned_off=False,
        paid=paid,
        status=status,
        detail=detail,
    )


def _stream_keeper(
    on_stream: Callable[[ArmGridKey, str, str], None] | None, key: ArmGridKey
) -> Callable[[str, str], None] | None:
    """Bind one cell's key into the per-leg sink `run_arm_cell` calls. Bound here, outside the
    fire loop, so the closure cannot capture the loop variable of a later cell."""
    if on_stream is None:
        return None
    sink = on_stream

    def keep(leg: str, raw_stream: str) -> None:
        sink(key, leg, raw_stream)

    return keep


def fire(
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    model: str,
    runner: Runner,
    n_tasks: int | None = None,
    landed: Sequence[ArmCell] = (),
    on_cell: Callable[[ArmCell], None] | None = None,
    on_stream: Callable[[ArmGridKey, str, str], None] | None = None,
) -> list[ArmCell]:
    """Run every cell of the grid that ``landed`` does not already hold.

    One cell at a time, and ``run_arm_cell`` is atomic: it mints, runs both legs and tears the
    mint down, so a failure anywhere inside it leaves a cell that bought legs and measured
    nothing. That is the granularity gate 12 is enforced at, and saying so is the honest reading —
    a timed-out establish leg does not leave a scorable goal leg behind for this rig to keep.

    Two halts, and they mean different things. A quota refusal is not a defect and not a
    measurement: nothing can be bought until the account resets, so the fire keeps what it has and
    stops rather than burning the rest of the authorization into unmeasured cells. A run of
    consecutive unmeasured cells is a broken rig, and a tolerance applied everywhere is a grid
    that measured nothing while reporting a rate.

    ``on_stream(key, leg, raw_stream)`` receives each leg's verbatim stream as the cell runs,
    keyed by the cell it belongs to. It fires for a cell that then times out or errors, which is
    the point: an unmeasured cell's stream is the only evidence of WHY it measured nothing."""
    by_key = {(task.variant, task.work_id): task for task in tasks}
    done = {cell_key(cell) for cell in landed}
    cells = list(landed)
    streak = UnmeasuredStreak()
    paid = runner is subprocess.run
    for key in grid_keys(tasks, n_tasks=n_tasks):
        if key in done:
            continue
        arm_name, variant, work_id, repeat = key
        task = by_key.get((variant, work_id))
        if task is None:
            raise ArmPlanError(
                f"the grid names cell {key} but the corpus carries no "
                f"({variant!r}, {work_id!r}) task"
            )
        try:
            cell = run_arm_cell(
                task,
                arm_name,
                repeat=repeat,
                model=model,
                channel=CHANNEL,
                runner=runner,
                keep_stream=_stream_keeper(on_stream, key),
            )
        except HeadlessAgentError as exc:
            if is_quota_halt(exc):
                raise QuotaHaltError(
                    f"the account refused at cell {key}: {exc}. {len(cells)} cell(s) kept; "
                    "nothing further can be bought until it resets."
                ) from exc
            timeout = spawn_timeout_of(exc)
            cell = _unmeasured(
                key,
                status="timeout" if timeout is not None else "error",
                detail=str(exc),
                paid=paid,
            )
            cells.append(cell)
            if on_cell is not None:
                on_cell(cell)
            if streak.unmeasured():
                raise RigHaltError(
                    f"{streak.count} consecutive cell(s) measured nothing, through {key}: the rig "
                    "is broken, not flaky. Fix it before spending the rest of the authorization."
                ) from exc
            continue
        streak.measured()
        cells.append(cell)
        if on_cell is not None:
            on_cell(cell)
    return cells


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------

_PLAN_ONLY = (
    "No run requested. This printed the PLAN and spent nothing.\n"
    "  free plumbing proof : python -m membench.runner.beads_arm_fire --dry-run "
    "--corpus-dir <dir> --out <path>\n"
    "  paid discovery gate : python -m membench.runner.beads_arm_fire --preflight "
    "--model <id> --out <path>\n"
    "  buy the pilot       : python -m membench.runner.beads_arm_fire --fire --n-tasks 1 "
    "--model <id> --out <path>\n"
    "  release the rest    : the same --fire --out with the full --n-tasks\n"
    "The three paid lines need CLAUDE_CODE_OAUTH_TOKEN and a pinned --model and spend real money."
)


def _summary(
    cells: Sequence[ArmCell],
    *,
    args: argparse.Namespace,
    identity: Mapping[str, Any],
    dry_run: bool,
    n_tasks: int | None,
) -> dict[str, Any]:
    return dict(identity) | summarize(
        cells,
        model=args.model,
        dry_run=dry_run,
        n_tasks=n_tasks,
        work_ids=identity["work_ids"],
        cli_version=identity["cli_version"],
        corpus=identity["corpus_fingerprint"],
    )


def _run(
    args: argparse.Namespace,
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    dry_run: bool,
    n_tasks: int | None,
    runner: Runner,
) -> int:
    out: Path = args.out
    corpus = corpus_fingerprint(tasks)
    try:
        cli_version = resolve_cli_version()
        bd_build = resolve_bd_build()
    except (HeadlessAgentError, MemoryToolError) as exc:
        print(f"REFUSING to run: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    identity = resume_identity(
        model=args.model,
        cli_version=cli_version,
        corpus=corpus,
        work_ids=work_ids_of(tasks, n_tasks=n_tasks),
        bd_build=bd_build,
    )
    grid = grid_keys(tasks, n_tasks=n_tasks)
    plan = priced_plan(tasks, n_tasks=n_tasks)

    landed: list[ArmCell] = []
    if out.exists():
        try:
            prior = json.loads(out.read_text(encoding="utf-8"))
            landed = admissible_cells(prior, identity=identity, grid=grid)
        except ResumeMismatchError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return EXIT_REFUSED
        except (ArmPlanError, ValueError, KeyError, TypeError, AttributeError) as exc:
            print(f"{out}: not a readable partial artifact: {exc}", file=sys.stderr)
            return EXIT_REFUSED

    print(
        json.dumps(
            {
                "running": plan,
                "dry_run": dry_run,
                "resumed_cells": len(landed),
                "remaining_cells": len(grid) - len(landed),
                "cli_version": cli_version,
                "corpus_fingerprint": corpus,
                "bd_binary": bd_build.binary,
                "bd_version": bd_build.version,
                **bd_build.identity(),
            },
            indent=2,
        ),
        file=sys.stderr,
    )

    cells_dir = out.with_name(f"{out.name}.cells")
    cells_dir.mkdir(parents=True, exist_ok=True)
    kept: list[ArmCell] = list(landed)

    def _artifact(rows: Sequence[ArmCell]) -> dict[str, Any]:
        return _summary(rows, args=args, identity=identity, dry_run=dry_run, n_tasks=n_tasks)

    def _persist() -> None:
        atomic_write_json(out, _artifact(kept))

    def _cell_stem(key: ArmGridKey) -> str:
        arm_name, variant, work_id, repeat = key
        return f"{arm_name}-{variant}-{work_id}-{repeat}"

    def _keep_stream(key: ArmGridKey, leg: str, raw_stream: str) -> None:
        # Beside the cell file, under the cell's own key: `<stem>.establish.jsonl` and
        # `<stem>.goal.jsonl`. Never over an existing one (`write_text_new`), for the reason the
        # cell files are never overwritten. An empty stream is a stand-in's absence and gets no
        # file: a zero-byte `.jsonl` would read as a paid agent that said nothing.
        if raw_stream:
            write_text_new(cells_dir / f"{_cell_stem(key)}.{leg}.jsonl", raw_stream)

    def _record(cell: ArmCell) -> None:
        if cell not in kept:
            kept.append(cell)
        key = cell_key(cell)
        write_json_new(
            cells_dir / f"{_cell_stem(key)}.json",
            {"key": list(key)} | _artifact([cell])["cells"][0],
        )
        print(
            f"[{len(kept)}/{len(grid)}] {cell.arm}/{cell.variant}/{cell.work_id}#{cell.repeat} "
            f"{cell.status} passed={cell.passed} engaged={cell.engaged} "
            f"reaches={cell.native_reaches}",
            file=sys.stderr,
            flush=True,
        )
        _persist()

    try:
        cells = fire(
            tasks,
            model=args.model,
            runner=runner,
            n_tasks=n_tasks,
            landed=landed,
            on_cell=_record,
            on_stream=_keep_stream,
        )
    except (QuotaHaltError, RigHaltError) as exc:
        _persist()
        print(f"HALT: {exc}", file=sys.stderr)
        print(
            f"{len(kept)} cell(s) kept in {out}; re-run the same command to resume.",
            file=sys.stderr,
        )
        return EXIT_HALT
    except ArmPlanError as exc:
        _persist()
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    summary = _artifact(cells)
    atomic_write_json(out, summary)
    print(json.dumps(summary, indent=2))

    if args.preflight:
        reached = discovery(cells)
        if not reached["passed"]:
            print(
                f"HALT {HALT_NO_CALL}: {reached['verdict']} ({json.dumps(reached)}). "
                "The grid is NOT bought.",
                file=sys.stderr,
            )
            return EXIT_HALT
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--model", default="")
    ap.add_argument(
        "--n-tasks",
        type=int,
        default=None,
        help=(
            "how many work_ids (sorted) this run buys; the staging mechanism -- 1 for the pilot, "
            "then the full count against the same --out to release the rest. Default: all of them"
        ),
    )
    ap.add_argument(
        "--plan", action="store_true", help="price the grid and print it; spends nothing"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "run the whole grid on an agent that makes no tool call; proves the plumbing, the "
            "argv, the surfaces and the gate arithmetic, and buys nothing"
        ),
    )
    ap.add_argument(
        "--preflight",
        action="store_true",
        help=(
            f"PAID: {PREFLIGHT_TASKS} task x {len(ARM_NAMES)} arms x 1 repeat, then gate 4's "
            "discovery check. Zero reaches is a HALT and the grid is not bought."
        ),
    )
    ap.add_argument(
        "--fire",
        action="store_true",
        help=(
            "PAID: execute the grid priced by --plan. Separate from --plan on purpose: pricing "
            "and spending must not be the same keystroke."
        ),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "where the summary is written; cells land as they are bought, and an existing file "
            "here is RESUMED (its cells are kept, not re-bought) when it matches this rig"
        ),
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.preflight and args.fire:
        ap.error("--preflight and --fire are two different spends; run them one at a time")
    if args.preflight and args.n_tasks is not None:
        ap.error(
            f"--preflight buys {PREFLIGHT_TASKS} task by pre-registration (§6); --n-tasks here "
            "would price one grid and buy another"
        )

    paid = args.preflight or args.fire
    refusal = _refusal(dry_run=not paid, model=args.model)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED

    _, tasks = load_twin_corpus(args.corpus_dir)
    if not tasks:
        print(
            f"no tool-requiring tasks under {args.corpus_dir}: the corpus is missing or empty, so "
            "there is nothing to measure (this is NOT a result)",
            file=sys.stderr,
        )
        return EXIT_NO_CORPUS

    n_tasks = PREFLIGHT_TASKS if args.preflight else args.n_tasks

    if not (args.dry_run or paid):
        print(json.dumps(priced_plan(tasks, n_tasks=n_tasks), indent=2))
        if not args.plan:
            print(_PLAN_ONLY, file=sys.stderr)
        return EXIT_OK

    if args.out is None:
        print(
            "REFUSING to run: --out is required. It is the resume artifact, the lock and the "
            "parent of the per-cell evidence directory; without it a halt loses every cell bought "
            "so far and a re-run re-buys the whole grid.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    runner: Runner = _silent_runner if args.dry_run else subprocess.run
    try:
        with out_lock(args.out):
            return _run(args, tasks, dry_run=args.dry_run, n_tasks=n_tasks, runner=runner)
    except ResumeMismatchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CHANNEL",
    "DEFAULT_CORPUS",
    "HALT_NO_CALL",
    "admissible_cells",
    "fire",
    "main",
    "resume_identity",
]
