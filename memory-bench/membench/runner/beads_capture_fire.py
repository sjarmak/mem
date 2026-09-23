"""Buy one capture turn: the establish leg alone, on the beads build the turn names.

    --plan       price the turn and print it; spends nothing
    --dry-run    the whole turn on `e1_grid._silent_runner`; spends nothing, proves the plumbing
    --fire       PAID: run it

Reads ``docs/prereg-beads-capture.md``. One turn = one candidate = one ``--bd-ref <remote> <sha>``,
and the artifact is identified by the build it measured, so two turns cannot pool. The floor and
the comparator are bought once per runtime version with ``--arms none builtin`` and reused; a
candidate turn buys ``beads`` alone.

The spending loop, the halts and the resume are ``beads_arm_fire``'s, unchanged. What is local
here is the derivation of the grid and the identity that grid is pooled under.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from membench.runner.agent_harness import (
    HARNESS_CLAUDE_CODE,
    AgentHarness,
    HarnessError,
    claude_code_harness,
    command_harness,
)
from membench.runner.bd_build import BdBuild, resolve_bd_build
from membench.runner.bd_ref import build_bd_ref
from membench.runner.beads_arm_fire import DEFAULT_CORPUS, admissible_cells, run_grid
from membench.runner.beads_arm_grid import PROTOCOL_CAPTURE, ArmCell
from membench.runner.beads_arm_plan import (
    PROTOCOL_VERSION,
    ArmGridKey,
    ArmPlanError,
    cell_key,
    cell_row,
)
from membench.runner.beads_capture import (
    CAPTURE_ARMS,
    CAPTURE_SEED,
    capture_keys,
    capture_plan,
    capture_summary,
    capture_work_ids,
)
from membench.runner.e1_grid import (
    EXIT_HALT,
    EXIT_NO_CORPUS,
    EXIT_OK,
    EXIT_REFUSED,
    QuotaHaltError,
    ResumeMismatchError,
    RigHaltError,
    _refusal,
    _silent_runner,
    atomic_write_json,
    corpus_fingerprint,
    out_lock,
    write_json_new,
    write_text_new,
)
from membench.runner.headless_agent import (
    REFUSE_UNPINNED_MODEL,
    HeadlessAgentError,
    MemoryChannel,
    a_paid_run_needs_a_model,
    resolve_cli_version,
    resolve_model,
)
from membench.runner.memory_arm import ARM_BUILTIN, arm_settings_fingerprint
from membench.runner.tool_surface import (
    RECOGNIZER_IMPLEMENTATION_VERSION,
    MemoryToolError,
    surface_fingerprint,
)
from membench.runner.toolreq_corpus import load_twin_corpus
from membench.runner.toolreq_realagent import ToolReqRealAgentTask
from membench.spawn import Runner

CHANNEL = MemoryChannel.TRUSTED

_PLAN_ONLY = (
    "No run requested. This printed the PLAN and spent nothing.\n"
    "  free plumbing proof : python -m membench.runner.beads_capture_fire --dry-run "
    "--corpus-dir <dir> --out <path>\n"
    "  buy a candidate     : python -m membench.runner.beads_capture_fire --fire "
    "--bd-ref <remote> <sha> --model <id> --out <path>\n"
    "  buy the reused arms : the same, with --arms none builtin, once per runtime version\n"
    "The paid lines need CLAUDE_CODE_OAUTH_TOKEN and a pinned --model and spend real money."
)


def capture_identity(
    *,
    model: str,
    corpus: str,
    arms: Sequence[str],
    work_ids: Sequence[str],
    bd_build: BdBuild,
    harness: AgentHarness,
) -> dict[str, Any]:
    """Everything a partial artifact must match before its cells may be pooled into this turn.

    The three-arm identity's fields, plus two the capture registration adds. ``protocol`` is in
    here because the two registrations write the same row shape and a capture cell pooled into a
    contrast grid would enter its rates as a zero on a leg it never bought. ``arms`` is in here
    because §6 buys the treatment every turn and REUSES the floor and comparator: an artifact that
    could not say which arms it bought would let a candidate-only file be resumed as though it
    already held the reused pair.

    ``harness`` names the runtime and its conditions: ``cli_version`` is that runtime's version,
    and the harness name and conditions fingerprint sit beside it, so a turn bought on one
    runtime, or under one set of conditions, never pools with a turn bought on another."""
    return {
        "protocol": PROTOCOL_CAPTURE,
        "protocol_version": PROTOCOL_VERSION,
        **bd_build.identity(),
        "model": resolve_model(model) or "cli-default",
        "cli_version": harness.version,
        **harness.identity(),
        "corpus_fingerprint": corpus,
        "arm_settings_fingerprint": arm_settings_fingerprint(),
        "surface_fingerprint": surface_fingerprint(),
        "recognizer_version": RECOGNIZER_IMPLEMENTATION_VERSION,
        "sample_seed": CAPTURE_SEED,
        "arms": list(arms),
        "work_ids": list(work_ids),
    }


def parse_conditions(pairs: Sequence[str]) -> dict[str, str]:
    """``KEY=VALUE`` pairs from the command line into the conditions the harness exports."""
    conditions: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key:
            raise HarnessError(f"--condition takes KEY=VALUE, got {pair!r}")
        conditions[key] = value
    return conditions


def harness_of(
    *,
    name: str,
    version: str | None,
    command: Sequence[str] | None,
    conditions: Mapping[str, str],
    home_seed: Path | None,
    cli_version: Callable[[], str],
) -> AgentHarness:
    """The runtime this turn spawns on. Claude Code by name, its version read off the binary;
    any other name is a command harness and must declare both its command and its version,
    because this rig cannot read a version off a binary it does not know."""
    if name == HARNESS_CLAUDE_CODE:
        if command is not None:
            raise HarnessError(f"--harness-command is not for the {HARNESS_CLAUDE_CODE} harness")
        if home_seed is not None:
            raise HarnessError(f"--harness-home-seed is not for the {HARNESS_CLAUDE_CODE} harness")
        return claude_code_harness(
            version=version if version is not None else cli_version(), conditions=conditions
        )
    if command is None:
        raise HarnessError(f"harness {name!r} needs --harness-command <argv...> with a {{prompt}}")
    if version is None:
        raise HarnessError(f"harness {name!r} needs --harness-version; it cannot be read off")
    return command_harness(
        name=name,
        version=version,
        argv_template=command,
        conditions=conditions,
        home_seed=home_seed,
    )


def _bd_build_of(args: argparse.Namespace, *, runner: Runner) -> BdBuild:
    """The bd this turn measures. Same resolution as the three-arm fire: the pinned commit when
    ``--bd-ref`` names one, else the ambient binary for a local plumbing run."""
    if args.bd_ref is None:
        return resolve_bd_build(runner=runner)
    remote, sha = args.bd_ref
    return build_bd_ref(remote, sha, runner=runner)


def _artifact(
    cells: Sequence[ArmCell], *, identity: Mapping[str, Any], dry_run: bool, arms: Sequence[str]
) -> dict[str, Any]:
    return (
        dict(identity)
        | capture_summary(cells, arms=arms)
        | {"dry_run": dry_run, "cells": [cell_row(cell) for cell in cells]}
    )


def _run(
    args: argparse.Namespace,
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    dry_run: bool,
    arms: Sequence[str],
    runner: Runner,
) -> int:
    out: Path = args.out
    corpus = corpus_fingerprint(tasks)
    try:
        harness = harness_of(
            name=args.harness,
            version=args.harness_version,
            command=(
                shlex.split(args.harness_command) if args.harness_command is not None else None
            ),
            conditions=parse_conditions(args.condition),
            home_seed=args.harness_home_seed,
            cli_version=resolve_cli_version,
        )
        # `subprocess.run`, never the run's own `runner`. A dry run swaps in a stand-in that
        # answers every spawn with a fixed agent result, and handing it `bd version --json` makes
        # the binary unidentifiable and refuses the run -- which is what `--dry-run` did from the
        # moment the build was added to the identity. Identifying and building bd is FREE (it
        # prints and exits) and it is part of what a dry run is for: proving the pinned commit
        # fetches, builds, and reports itself before a paid run depends on it.
        bd_build = _bd_build_of(args, runner=subprocess.run)
    except (HeadlessAgentError, MemoryToolError, HarnessError) as exc:
        print(f"REFUSING to run: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if ARM_BUILTIN in arms and not harness.native_memory:
        # Before the first cell, not at the first builtin cell: the grid is work_id-major, so
        # the mint would otherwise refuse after the treatment cells of the first task were bought.
        print(
            f"REFUSING to run: the {ARM_BUILTIN!r} arm is the runtime's own native memory and "
            f"the {harness.name!r} harness has none; buy '--arms beads none' there instead",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    try:
        grid = capture_keys(tasks, arms=arms)
        plan = capture_plan(tasks, arms=arms)
        identity = capture_identity(
            model=args.model,
            corpus=corpus,
            arms=arms,
            work_ids=capture_work_ids(tasks),
            bd_build=bd_build,
            harness=harness,
        )
    except ArmPlanError as exc:
        print(f"REFUSING to run: {exc}", file=sys.stderr)
        return EXIT_REFUSED

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
                "cli_version": harness.version,
                **harness.identity(),
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

    def _persist() -> None:
        atomic_write_json(out, _artifact(kept, identity=identity, dry_run=dry_run, arms=arms))

    def _stem(key: ArmGridKey) -> str:
        arm_name, variant, work_id, repeat = key
        return f"{arm_name}-{variant}-{work_id}-{repeat}"

    def _keep_stream(key: ArmGridKey, leg: str, raw_stream: str) -> None:
        if raw_stream:
            write_text_new(cells_dir / f"{_stem(key)}.{leg}.jsonl", raw_stream)

    def _record(cell: ArmCell) -> None:
        if cell not in kept:
            kept.append(cell)
        key = cell_key(cell)
        write_json_new(cells_dir / f"{_stem(key)}.json", {"key": list(key)} | cell_row(cell))
        print(
            f"[{len(kept)}/{len(grid)}] {cell.arm}/{cell.work_id}#{cell.repeat} {cell.status} "
            f"engaged={cell.engaged} bd_invocations={cell.bd_invocations} "
            f"verbs={list(cell.endogenous_verbs)} reaches={cell.native_reaches}",
            file=sys.stderr,
            flush=True,
        )
        _persist()

    try:
        cells = run_grid(
            tasks,
            grid,
            model=args.model,
            runner=runner,
            protocol=PROTOCOL_CAPTURE,
            landed=landed,
            on_cell=_record,
            on_stream=_keep_stream,
            bd_binary=bd_build.binary,
            harness=harness,
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

    summary = _artifact(cells, identity=identity, dry_run=dry_run, arms=arms)
    atomic_write_json(out, summary)
    print(json.dumps(summary, indent=2))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--model", default="")
    ap.add_argument(
        "--bd-ref",
        nargs=2,
        metavar=("REMOTE", "SHA"),
        default=None,
        help=(
            "the beads commit this turn measures: a git remote (URL or name) and a COMMIT HASH, "
            "fetched and built with the project's own Makefile and cached by sha. One turn is "
            "one of these. A branch or tag is refused -- it names different code on different "
            "days. Omit to use MEMBENCH_BD_BINARY or the ambient bd"
        ),
    )
    ap.add_argument(
        "--arms",
        nargs="+",
        default=["beads"],
        choices=list(CAPTURE_ARMS),
        help=(
            "which arms this turn buys. Default: the treatment alone, which is what a candidate "
            "turn measures. Pass 'none builtin' once per runtime version to buy the floor and "
            "comparator that every candidate on that runtime is then read against"
        ),
    )
    ap.add_argument(
        "--harness",
        default=HARNESS_CLAUDE_CODE,
        help=(
            f"the agent runtime the legs spawn on. Default {HARNESS_CLAUDE_CODE!r}, whose "
            "version is read off the binary and which alone can buy the builtin arm. Any other "
            "name is a command harness and needs --harness-command and --harness-version"
        ),
    )
    ap.add_argument(
        "--harness-command",
        metavar="'ARGV ...'",
        default=None,
        help=(
            "how to spawn a non-Claude harness: ONE shell-quoted string, split with shlex, with "
            "the prompt as one whole word spelled {prompt} and, optionally, the model as "
            "{model}. The turn reads what the agent did from bd's own receipts, so the runtime "
            "need emit no transcript"
        ),
    )
    ap.add_argument(
        "--harness-version",
        default=None,
        help="the runtime's version, recorded in the resume identity; required off Claude Code",
    )
    ap.add_argument(
        "--harness-home-seed",
        type=Path,
        default=None,
        help=(
            "directory containing only login/config material to copy into each non-Claude "
            "harness cell's private HOME; the operator's HOME is never inherited"
        ),
    )
    ap.add_argument(
        "--condition",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "an environment variable exported to the spawned agent, repeatable: the config / "
            "settings / conditions axis under test. Fingerprinted into the identity, so turns "
            "under different conditions never pool"
        ),
    )
    ap.add_argument(
        "--plan", action="store_true", help="price the turn and print it; spends nothing"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "run the whole turn on an agent that makes no tool call; proves the plumbing, the "
            "argv, the surfaces and the arithmetic, and buys nothing"
        ),
    )
    ap.add_argument(
        "--fire",
        action="store_true",
        help=(
            "PAID: execute the turn priced by --plan. Separate from --plan on purpose: pricing "
            "and spending must not be the same keystroke."
        ),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "where the summary is written; cells land as they are bought, and an existing file "
            "here is RESUMED (its cells are kept, not re-bought) when it matches this turn"
        ),
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    arms = [one for one in CAPTURE_ARMS if one in set(args.arms)]

    # The OAuth and metered-key gates are Claude Code's; a command harness carries its own
    # account, and what this rig still requires of a paid turn there is the pinned model.
    if args.harness == HARNESS_CLAUDE_CODE:
        refusal = _refusal(dry_run=not args.fire, model=args.model)
    elif a_paid_run_needs_a_model(args.model, dry_run=not args.fire):
        refusal = REFUSE_UNPINNED_MODEL
    else:
        refusal = None
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

    if not (args.dry_run or args.fire):
        try:
            print(json.dumps(capture_plan(tasks, arms=arms), indent=2))
        except ArmPlanError as exc:
            print(f"REFUSING to price: {exc}", file=sys.stderr)
            return EXIT_REFUSED
        if not args.plan:
            print(_PLAN_ONLY, file=sys.stderr)
        return EXIT_OK

    if args.out is None:
        print(
            "REFUSING to run: --out is required. It is the resume artifact, the lock and the "
            "parent of the per-cell evidence directory; without it a halt loses every cell bought "
            "so far and a re-run re-buys the whole turn.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    runner: Runner = _silent_runner if args.dry_run else subprocess.run
    try:
        with out_lock(args.out):
            return _run(args, tasks, dry_run=args.dry_run, arms=arms, runner=runner)
    except ResumeMismatchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["CHANNEL", "capture_identity", "harness_of", "main", "parse_conditions"]
