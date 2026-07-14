#!/usr/bin/env python3
"""mem-rk41.3.2 — builtin native-memory persistent-env arm, staged over the tool-requiring corpus.

The ``builtin`` sibling to ``grid_toolreq_realagent.py``'s none/oracle ceiling sweep:
for every frozen tool-requiring task, run ``membench.runner.toolreq_builtin.run_builtin_arm``
under both memory-trust channels — TWO real ``claude -p`` calls per repeat (establish,
then a bare goal call) sharing one sandbox cwd + one ``CLAUDE_CONFIG_DIR``, so Claude
Code's own native memory is the sole continuity channel. It does NOT run ``none``,
``oracle`` (mem-rk41.3), or ``ours`` (mem-rk41.3.1) — this script is builtin-only.

This is the paid ceiling driver. It STAGES but never over-reaches:

* ``--dry-run`` runs the identical two-call loop with a simulated memory-copying agent
  — no token, no ``claude`` — proving the establish/goal wiring end to end for FREE;
* a real run REFUSES to spend without ``CLAUDE_CODE_OAUTH_TOKEN`` and prints the exact
  ``scix-batch`` go-command + the call count / worst-case wall-clock (double the
  none/oracle cost per repeat: establish + goal), so the paid fire stays an explicit,
  cost-disclosed, per-action decision (Stephanie's call);
* a real run also spends one cheap PREFLIGHT establish+check cycle before the full
  sweep and HALTS if native memory never reached ``MEMORY.md`` — builtin engagement can
  depend on an account/pool-level feature flag this script's env plumbing cannot itself
  turn on (mem-rk41.3.2 bead Q3), so a silent no-op run would produce an uninterpretable
  null instead of a diagnosed refusal;
* per-task results persist to ``--out/<work_id>.json`` and are REUSED on re-run, so a
  token-expiry or OOM mid-sweep does not re-pay for finished tasks.

    # FREE — prove the two-call establish/goal wiring end to end:
    uv run python scripts/grid_toolreq_builtin.py --corpus-dir fixtures/worlds-tool --dry-run

    # PAID — Stephanie's per-action go (wrap in scix-batch; the script prints the command):
    scix-batch -- env CLAUDE_CODE_OAUTH_TOKEN=... \
        uv run python scripts/grid_toolreq_builtin.py --corpus-dir fixtures/worlds-tool
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, get_type_hints

from membench.runner.headless_agent import DEFAULT_TIMEOUT_S, HeadlessAgentError, MemoryChannel
from membench.runner.realagent_probe import ArmOutcome
from membench.runner.toolreq_builtin import BuiltinDiagnostics, run_builtin_arm
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, load_corpus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = PROJECT_ROOT / "memory-bench/fixtures/worlds-tool"
DEFAULT_OUT = PROJECT_ROOT / ".mem/toolreq-builtin"
ENV_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"

CHANNELS = (MemoryChannel.RECALLED, MemoryChannel.TRUSTED)
CALLS_PER_REPEAT = 2  # establish + goal — double none/oracle's 1-call cost


Cell = tuple[ArmOutcome, BuiltinDiagnostics]


def evaluate_task(
    task: ToolReqRealAgentTask, *, repeats: int, model: str, dry_run: bool
) -> list[Cell]:
    """Run every channel cell for one task."""
    return [
        run_builtin_arm(task, repeats=repeats, model=model, dry_run=dry_run, channel=channel)
        for channel in CHANNELS
    ]


def _cell_kind(outcome: ArmOutcome, diag: BuiltinDiagnostics) -> str:
    """Classify one (outcome, diagnostics) cell — the single source of truth `task_verdict`
    (display) and `run_corpus` (summary counts) both read, so a wording change in one can
    never silently desync from what the other counts. Priority: a LEAK (pass without
    engagement) outranks everything else — it means the shared sandbox let a Write
    scavenge a stale file, not that builtin memory worked. Otherwise: full engagement +
    full pass separates; zero engagement means the mechanism never fired (the mem-hb9o
    precedent); partial is WEAK."""
    if diag.leaked:
        return "LEAK"
    if outcome.passes == outcome.runs and diag.engaged == outcome.runs and outcome.runs > 0:
        return "SEPARATES"
    if diag.engaged == 0:
        return "NOT-ENGAGED"
    return "WEAK"


def task_verdict(cells: Sequence[Cell]) -> str:
    """Human-readable per-channel verdict line, built from `_cell_kind`."""
    lines: list[str] = []
    for outcome, diag in cells:
        runs = outcome.runs
        kind = _cell_kind(outcome, diag)
        if kind == "LEAK":
            call = f"LEAK: {diag.leaked}/{runs} passed WITHOUT engaging native memory"
        elif kind == "SEPARATES":
            call = f"SEPARATES: {outcome.passes}/{runs} (engaged {diag.engaged}/{runs})"
        elif kind == "NOT-ENGAGED":
            call = f"NOT-ENGAGED: native memory never reached MEMORY.md (0/{runs})"
        else:
            call = f"WEAK: {outcome.passes}/{runs} passed, engaged {diag.engaged}/{runs}"
        lines.append(f"[{outcome.channel}] {call}")
    return " | ".join(lines)


def _int_fields_ok(obj: ArmOutcome | BuiltinDiagnostics) -> bool:
    """True iff every int-annotated field on ``obj`` actually holds an int.

    Schema-driven (``dataclasses.fields``), not a hand-list of whatever fields the
    scoring path reads today: ``dataclass(**...)`` never type-checks, so a cache with
    (e.g.) string counts constructs fine and only crashes later at ``_cell_kind``'s
    ``runs > 0``. Deriving the check from the dataclass definitions means adding a new
    count field can never silently reopen that path."""
    hints = get_type_hints(type(obj))
    return all(
        isinstance(getattr(obj, field.name), int)
        for field in fields(obj)
        if hints[field.name] is int
    )


def _load_cached_cells(result_path: Path, identity: Mapping[str, object]) -> list[Cell] | None:
    """One persisted per-task result -> its cells, or ``None`` = cache miss.

    ``run_corpus``'s guarantee ("a corrupt or partial cache file is treated as a miss
    and re-executed, never a crash") lives HERE, as explicit miss branches instead of
    an ever-widening except tuple: unreadable or non-UTF-8 bytes, malformed JSON, a
    non-dict top level, an identity mismatch, schema drift, type-hostile counts, and a
    cell grid that does not cover exactly ``CHANNELS`` are all misses.

    That last branch is load-bearing on the PAID path. ``run_corpus`` derives the
    ``separates_all_channels`` headline from whatever cells this returns, so a grid
    missing a channel (truncated write, or a channel added since the file was written)
    or repeating one twice would credit "separates on BOTH channels" off a measurement
    that only ever covered one — full coverage reported for a channel no ``claude -p``
    call ever ran. Comparing the cells' channels against ``CHANNELS`` pins arity,
    identity, and order in one check; a partial grid must be re-executed, not scored."""
    try:
        loaded = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # ValueError subsumes json.JSONDecodeError (malformed JSON text) and
        # UnicodeDecodeError (invalid UTF-8 bytes).
        return None
    if not isinstance(loaded, dict):
        return None
    if any(loaded.get(key) != value for key, value in identity.items()):
        return None
    rows = loaded.get("cells")
    if not isinstance(rows, list):
        # absent (older schema) or not a grid at all — a dict or scalar would otherwise
        # iterate to zero cells and load as a HIT holding no measurement.
        return None
    try:
        cells = [
            (ArmOutcome(**row["outcome"]), BuiltinDiagnostics(**row["diagnostics"])) for row in rows
        ]
    except (KeyError, TypeError):
        # schema drift: missing/renamed keys, non-mapping rows, or an older result
        # written before a field was added.
        return None
    if [outcome.channel for outcome, _ in cells] != [channel.value for channel in CHANNELS]:
        return None
    if not all(_int_fields_ok(outcome) and _int_fields_ok(diag) for outcome, diag in cells):
        return None
    return cells


def run_corpus(
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    out_dir: Path,
    repeats: int,
    model: str,
    dry_run: bool,
    resume: bool = True,
) -> dict[str, Any]:
    """Evaluate every task, persisting one ``<work_id>.json`` each. A persisted result is
    reused only when its ``(repeats, dry_run, model)`` identity matches — a FREE dry-run's
    simulated result can never satisfy a PAID run over the same ``--out``. A corrupt or
    partial cache file is treated as a miss and re-executed, never a crash."""
    out_dir.mkdir(parents=True, exist_ok=True)
    per_task: list[dict[str, Any]] = []
    executed = 0
    reused = 0
    separates = 0
    leaked: list[str] = []
    not_engaged: list[str] = []
    identity = {"repeats": repeats, "dry_run": dry_run, "model": model}
    for task in tasks:
        result_path = out_dir / f"{task.work_id}.json"
        cached_cells: list[Cell] | None = None
        if resume and result_path.is_file():
            cached_cells = _load_cached_cells(result_path, identity)
        if cached_cells is not None:
            cells = cached_cells
            reused += 1
        else:
            cells = evaluate_task(task, repeats=repeats, model=model, dry_run=dry_run)
            executed += 1
        record = {
            "work_id": task.work_id,
            **identity,
            "cells": [
                {"outcome": asdict(outcome), "diagnostics": asdict(diag)} for outcome, diag in cells
            ],
            "verdict": task_verdict(cells),
        }
        if cached_cells is None:
            # Atomic publish: write a sibling temp file then rename, so a kill mid-write
            # leaves either the old result or the new one, never a half-written JSON the
            # next resume trips on. Skipped on a cache hit — the file already holds this
            # exact content (same identity), so rewriting it would just be wasted I/O.
            tmp_path = result_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            tmp_path.replace(result_path)
        per_task.append(record)
        kinds = [_cell_kind(outcome, diag) for outcome, diag in cells]
        if kinds and all(kind == "SEPARATES" for kind in kinds):
            separates += 1
        if "LEAK" in kinds:
            leaked.append(task.work_id)
        if "NOT-ENGAGED" in kinds:
            not_engaged.append(task.work_id)
    return {
        "n_tasks": len(tasks),
        "executed": executed,
        "reused": reused,
        "dry_run": dry_run,
        "repeats": repeats,
        "per_task": per_task,
        "separates_all_channels": separates,
        "leaked": leaked,
        "not_engaged": not_engaged,
    }


def _print_go_command(n_tasks: int, repeats: int, out_dir: Path, corpus_dir: Path) -> None:
    calls = n_tasks * len(CHANNELS) * repeats * CALLS_PER_REPEAT
    worst_hours = calls * DEFAULT_TIMEOUT_S / 3600.0
    print(
        f"REFUSING to spend: {ENV_OAUTH} is unset.\n"
        f"  This paid sweep is {calls} real `claude -p` call(s) "
        f"({n_tasks} task x {len(CHANNELS)} channel x {repeats} repeat x "
        f"{CALLS_PER_REPEAT} calls/repeat [establish+goal] — DOUBLE the none/oracle cost "
        f"per repeat); worst-case wall-clock ~{worst_hours:.1f}h at the "
        f"{DEFAULT_TIMEOUT_S:.0f}s timeout.\n"
        f"  Plus one PREFLIGHT establish+check cycle (2 calls) before the sweep starts.\n"
        f"  Per-task results persist to {out_dir} and are reused on re-run (resumable).\n"
        "  RISK: builtin native-memory engagement depends on an account/pool-level "
        "feature flag this script's CLAUDE_CONFIG_DIR plumbing cannot itself turn on — "
        "the preflight below catches an OFF account before the full spend, but confirm "
        "the OAuth account used has it enabled if the preflight halts (mem-rk41.3.2 Q3).\n"
        "  To fire (Stephanie's per-action go), source the token from an account home and "
        "wrap in scix-batch:\n\n"
        f"    scix-batch -- env {ENV_OAUTH}=... \\\n"
        f"        uv run python scripts/grid_toolreq_builtin.py --corpus-dir {corpus_dir}\n\n"
        "  Or prove the wiring for free first:\n"
        f"    uv run python scripts/grid_toolreq_builtin.py --corpus-dir {corpus_dir} --dry-run"
    )


def preflight(task: ToolReqRealAgentTask, *, model: str) -> BuiltinDiagnostics:
    """One real establish+check cycle (2 calls, repeats=1) BEFORE the full paid sweep.
    Self-diagnoses "is builtin even enabled on this account" (mem-rk41.3.2 Q3 /
    mem-xe2p's enforce_mechanism_fires doctrine) instead of letting a disabled feature
    flag silently produce an uninterpretable all-null sweep."""
    _, diagnostics = run_builtin_arm(
        task, repeats=1, model=model, dry_run=False, channel=CHANNELS[0]
    )
    return diagnostics


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repeats", type=int, default=3, help="runs per (task, channel)")
    parser.add_argument(
        "--model", default="", help="pins --model; empty reads MEMBENCH_AGENT_MODEL"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N tasks (smoke subset)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="simulate the agent; no token, no claude"
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="skip the real preflight establish+check cycle (not recommended)",
    )
    args = parser.parse_args(argv)

    corpus_dir = args.corpus_dir
    tasks = load_corpus(corpus_dir)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    if not tasks:
        print(f"no tool-requiring tasks under {corpus_dir}", file=sys.stderr)
        return 1

    if not args.dry_run and not os.environ.get(ENV_OAUTH):
        _print_go_command(len(tasks), args.repeats, args.out, corpus_dir)
        return 2

    if not args.dry_run and not args.skip_preflight:
        print("PREFLIGHT: one real establish+check cycle before the full sweep...")
        try:
            diag = preflight(tasks[0], model=args.model)
        except HeadlessAgentError as exc:
            print(
                f"PREFLIGHT HALT: the real establish/goal call failed ({exc}) — refusing "
                "to spend on the full sweep. This is a diagnosed halt, not a mechanism-"
                "disabled verdict (mem-rk41.3.2 Q3); retry once the underlying failure "
                "(network, rate limit, CLI issue) is resolved.",
                file=sys.stderr,
            )
            return 3
        if diag.engaged == 0:
            print(
                "PREFLIGHT HALT: the real establish call never reached MEMORY.md — builtin "
                "native memory may be disabled for this OAuth account/pool (mem-rk41.3.2 "
                "Q3). Refusing to spend on the full sweep.",
                file=sys.stderr,
            )
            return 3
        print(f"PREFLIGHT OK: native memory engaged on {tasks[0].work_id}; proceeding.")

    mode = "DRY-RUN (simulated agent, no tokens)" if args.dry_run else "PAID real claude -p"
    print(
        f"toolreq builtin-arm sweep: {mode}; {len(tasks)} task(s) x {len(CHANNELS)} channel x "
        f"{args.repeats} repeat x {CALLS_PER_REPEAT} calls/repeat"
    )
    try:
        summary = run_corpus(
            tasks, out_dir=args.out, repeats=args.repeats, model=args.model, dry_run=args.dry_run
        )
    except HeadlessAgentError as exc:
        # The preflight is not the only paid boundary: a rate-limited/flaky/timed-out
        # `claude -p` mid-sweep gets the same diagnosed-halt treatment, never a raw
        # traceback. Finished tasks are already persisted, so resuming is cheap.
        print(
            f"SWEEP HALT: a real agent call failed mid-sweep ({exc}) — stopping instead "
            f"of spending into a broken link. Finished tasks are persisted under "
            f"{args.out} and are REUSED on re-run: re-run the same command to resume "
            "once the underlying failure (network, rate limit, CLI issue) is resolved.",
            file=sys.stderr,
        )
        return 3
    for record in summary["per_task"]:
        print(f"  {record['work_id']:<24} {record['verdict']}")
    summary_path = args.out / "summary-toolreq-builtin.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n{summary['separates_all_channels']}/{summary['n_tasks']} task(s) separate on both "
        f"channels; leaked={summary['leaked'] or 'none'}; "
        f"not_engaged={summary['not_engaged'] or 'none'}; summary -> {summary_path}"
    )
    if args.dry_run:
        print(
            "(DRY-RUN proves the two-call establish/goal wiring + scoring path ONLY — it "
            "does NOT exercise whether a real claude -p session persists to MEMORY.md.)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
