#!/usr/bin/env python3
"""mem-rk41.3 / .3.1 — real-agent none/oracle/ours ceiling-at-scale over the
tool-requiring corpus.

The corpus-wide generalization of the mem-rk41.4 single-goal probe: it adapts every frozen
tool-requiring world (``membench.runner.toolreq_realagent``) onto the ``Write``-bridge and
runs the SAME arm loop + external scorer the probe uses (``realagent_probe.run_arm`` /
``score_goal_action``), across ``none`` (empty memory, a leak detector), ``oracle`` (the
id-exact ceiling), and ``ours`` (a genuine ``mem retrieve`` payload), under both
memory-trust channels. Values are OPAQUE on this path, so ``none`` can pass only on a
genuine leak — the verdict rule is the probe's, restored.

``ours`` (mem-rk41.3.1) seeds a fresh ``.mem/store.db`` from the SAME corpus, using
rk41.5's ``toolreq_bundle_adapter`` substrate (the shared value-free apply_config
staleness signature) plus lessons whose facts state each sequence's CURRENT opaque
value — the SAME opaque token space ``oracle`` surfaces (the load-bearing invariant: if
``ours`` used a different value-map, its payload could never satisfy the scorer even if
retrieval fired correctly). Cross-task retrieval will generally NOT surface the queried
task's own sequence-unique opaque value — ``ours`` scoring near ``none`` rather than near
``oracle`` is the expected, honest substrate finding, not a defect to work around.

This is the paid ceiling driver. It STAGES but never over-reaches:

* ``--dry-run`` runs the identical loop with a simulated memory-copying agent — no token,
  no ``claude`` — proving the arms separate end to end for FREE. Seeding the ``ours``
  store and resolving its payload are ALSO free (real ``mem`` CLI calls, no agent turn) and
  run dry-run or paid alike (skipped only when every task is already cache-served) — only
  ``claude -p`` is spend-gated;
* a real run REFUSES to spend without ``CLAUDE_CODE_OAUTH_TOKEN`` and prints the exact
  ``scix-batch`` go-command + the run count / worst-case wall-clock, so the paid fire stays
  an explicit, cost-disclosed, per-action decision (Stephanie's call);
* per-task results persist to ``--out/<work_id>.json`` and are REUSED on re-run, so a
  token-expiry or OOM mid-sweep does not re-pay for finished tasks.

It does NOT run the ``builtin`` arm (mem-rk41.3.2): that needs a persistent native-memory
env, separately substantial.

    # FREE — prove the wiring over the whole corpus (still needs bin/mem built):
    uv run python scripts/grid_toolreq_realagent.py --corpus-dir fixtures/worlds-tool --dry-run

    # PAID — Stephanie's per-action go (wrap in scix-batch; the script prints the command):
    scix-batch -- env CLAUDE_CODE_OAUTH_TOKEN=... \
        uv run python scripts/grid_toolreq_realagent.py --corpus-dir fixtures/worlds-tool
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

# Sibling-script reuse: the SAME payload resolver the paid grid already uses — no
# reimplementation. (The NDJSON writer is two lines and lives below instead of being
# imported from gate_toolreq_offline, whose module body drags the NeMo import graph into
# this driver's startup for no reason.)
from run_grid_3arm import resolve_payloads

from membench.generators.toolreq_bundle_adapter import sequence_bundles, sequence_records
from membench.mem_cli import run_mem_json
from membench.memory_systems.ours_system import _default_runner
from membench.runner.headless_agent import DEFAULT_TIMEOUT_S, MemoryChannel
from membench.runner.realagent_probe import ArmOutcome, run_arm
from membench.runner.toolreq_realagent import (
    ToolReqRealAgentTask,
    load_corpus_with_sequences,
    sequence_lessons_opaque,
)
from membench.schemas.sequence import BenchmarkSequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = PROJECT_ROOT / "memory-bench/fixtures/worlds-tool"
DEFAULT_OUT = PROJECT_ROOT / ".mem/toolreq-realagent"
DEFAULT_MEM_BIN = str(PROJECT_ROOT / "bin/mem")
ENV_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"

ARMS = ("none", "oracle", "ours")
CHANNELS = (MemoryChannel.RECALLED, MemoryChannel.TRUSTED)

# Seeds the `ours` store + resolves its payload: (sequences, tasks, store_path, mem_bin,
# resolve_ids) -> work_id -> (source work_id -> rendered payload). `resolve_ids` narrows the
# resolution step to the tasks actually being executed; seeding stays full-corpus. Injectable
# so hermetic tests can stub it out without a built bin/mem (matching this codebase's
# CliRunner/RetrieveRunner injection convention — see headless_agent.CliRunner).
SeedFn = Callable[
    [
        Sequence[BenchmarkSequence],
        Sequence[ToolReqRealAgentTask],
        Path,
        str,
        Collection[str],
    ],
    dict[str, dict[str, str]],
]

# The one arm whose memory can legitimately come back EMPTY: `ours` retrieves, and temporal
# LOO admits no priors for the lifecycle-earliest task. See evaluate_task.
RETRIEVING_ARM = "ours"


def _write_ndjson(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """One JSON object per line — the import format `mem import-records/-lessons` reads."""
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def arm_memories(
    task: ToolReqRealAgentTask, ours_payload: Mapping[str, str] | None = None
) -> dict[str, dict[str, str]]:
    """The surfaced memory per arm, resolved once per task: nothing for ``none`` (the leak
    detector), the id-exact opaque ceiling for ``oracle``, the genuine ``mem retrieve``
    payload (rendered citation+lessons text, keyed by source work_id) for ``ours``."""
    memories = {
        "none": {},
        "oracle": dict(task.oracle_memory),
        "ours": dict(ours_payload or {}),
    }
    if set(memories) != set(ARMS):
        raise ValueError(f"arm memories {sorted(memories)} out of sync with ARMS {ARMS}")
    return memories


def evaluate_task(
    task: ToolReqRealAgentTask,
    *,
    repeats: int,
    model: str,
    dry_run: bool,
    ours_payload: Mapping[str, str] | None = None,
    channels: Sequence[MemoryChannel] = CHANNELS,
) -> list[ArmOutcome]:
    """Run every (arm, channel) cell for one task.

    One cell is never run: ``ours`` when its retrieval came back EMPTY. That is not an edge
    case but a guarantee for the lifecycle-earliest task, which temporal LOO leaves with no
    priors to retrieve. An empty payload makes the ``ours`` prompt byte-identical to ``none``,
    so the cell is none-equivalent by construction (delta exactly 0) — running it would spend
    ``repeats`` real ``claude -p`` turns per channel to re-measure ``none``, and would leave
    the headline ``(ours 0/N)`` unattributable: a retrieval miss reads exactly like
    memory-did-not-help. The ``none`` cell is relabeled instead, matching
    ``run_grid_3arm``'s empty-retrieval convention, and ``run_corpus`` records the
    ``ours_retrieval_empty`` flag so the two causes stay distinguishable in the results."""
    memories = arm_memories(task, ours_payload)
    outcomes: list[ArmOutcome] = []
    for channel in channels:
        cells: dict[str, ArmOutcome] = {}
        for arm in ARMS:
            if arm == RETRIEVING_ARM and not memories[arm]:
                continue  # filled from `none` below — never spent on
            cells[arm] = run_arm(
                arm=arm,
                step=task.goal_step,
                memory=memories[arm],
                channel=channel,
                repeats=repeats,
                model=model,
                dry_run=dry_run,
                current_values=task.current_opaque_values,
            )
        if RETRIEVING_ARM not in cells:
            cells[RETRIEVING_ARM] = replace(cells["none"], arm=RETRIEVING_ARM)
        outcomes.extend(cells[arm] for arm in ARMS)
    return outcomes


# The verdict is decided by these two arms alone; every other ARMS member rides along as
# an informational suffix (see task_verdict).
_GATING_ARMS = ("none", "oracle")


def task_verdict(outcomes: Sequence[ArmOutcome]) -> str:
    """The probe's per-channel verdict rule (valid again under opaque values): ``none``
    passing means a leak; ``none`` 0 + ``oracle`` ceiling means the arms separate. Arms
    beyond ``_GATING_ARMS`` (today: ``ours``) never gate this call — each rides along as
    an informational suffix (cross-task retrieval will generally NOT surface the queried
    task's own sequence-unique opaque value, so ``ours`` scoring near ``none`` is the
    expected, honest substrate finding)."""
    by = {(o.arm, o.channel): o for o in outcomes}
    lines: list[str] = []
    for channel in (c.value for c in CHANNELS):
        none_o = by.get(("none", channel))
        oracle_o = by.get(("oracle", channel))
        if none_o is None or oracle_o is None:
            continue
        runs = oracle_o.runs
        if none_o.passes > 0:
            call = f"LEAK: none {none_o.passes}/{none_o.runs} — value reached the prompt"
        elif oracle_o.passes == runs and runs > 0:
            call = f"SEPARATES: none 0/{runs}, oracle {oracle_o.passes}/{runs}"
        elif oracle_o.passes == 0:
            call = f"KILL: oracle ceiling 0/{runs} — no separation"
        else:
            call = f"WEAK: none 0/{runs}, oracle {oracle_o.passes}/{runs} — add repeats"
        for arm in ARMS:
            if arm in _GATING_ARMS:
                continue
            extra = by.get((arm, channel))
            if extra is not None:
                call += f" ({arm} {extra.passes}/{extra.runs})"
        lines.append(f"[{channel}] {call}")
    return " | ".join(lines)


def _reset_store(store_path: Path) -> None:
    """Delete any store already at ``store_path``, SQLite sidecars included, so the seed that
    follows is genuinely FRESH.

    Load-bearing, not hygiene: ``lessons`` is append-only (see CLAUDE.md — three tables have
    no FK to ``work_records`` and are never rewritten). Importing into a store left behind by
    an EARLIER corpus therefore keeps that corpus's opaque tokens retrievable, and
    ``resolve_payloads`` renders them straight into the live cross-task payloads — a paid
    ``ours`` measurement quietly carrying values that are no longer in the world. The store is
    a derived artifact of the corpus and is cheap to rebuild (FREE), so rebuild it."""
    for suffix in ("", "-wal", "-shm"):
        store_path.with_name(store_path.name + suffix).unlink(missing_ok=True)


def seed_ours_store_and_resolve_payloads(
    sequences: Sequence[BenchmarkSequence],
    tasks: Sequence[ToolReqRealAgentTask],
    store_path: Path,
    mem_bin: str,
    resolve_ids: Collection[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Seed a fresh ``ours`` store with the SAME substrate the mem-rk41.5 offline gate
    builds (``sequence_records`` — the shared value-free apply_config staleness trace
    error) plus opaque-valued lessons (``sequence_lessons_opaque`` — the SAME opaque token
    space ``oracle`` surfaces, the mem-rk41.3.1 invariant), then resolves the ``ours``
    arm's real retrieval payload via ``run_grid_3arm.resolve_payloads`` — the SAME
    function the paid ours-vs-builtin grid uses, no reimplementation.

    The store is rebuilt from scratch on every call (``_reset_store``): seeding is FREE and
    the store is a pure function of the corpus, so a regenerated corpus must never inherit
    the previous one's append-only lessons.

    Seeding always covers ALL sequences (cross-task retrieval needs every lesson in the
    store); ``resolve_ids`` narrows only the RESOLUTION step to the tasks that will actually
    be executed, so a resumed sweep does not re-query the store for cache-served tasks.

    FREE: this never spends an agent turn regardless of ``--dry-run`` (only ``claude -p``
    is spend-gated, in ``run_corpus``, which also skips this seed entirely when every task
    is cache-served) — it does need a built ``bin/mem``."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    _reset_store(store_path)
    records = sequence_records(sequences)
    lessons = sequence_lessons_opaque(sequences, tasks)
    with tempfile.TemporaryDirectory(prefix="toolreq-ours-seed-") as workspace:
        workspace_path = Path(workspace)
        records_path = workspace_path / "records.ndjson"
        lessons_path = workspace_path / "lessons.ndjson"
        _write_ndjson(records_path, records)
        _write_ndjson(lessons_path, lessons)
        run_mem_json(
            [mem_bin, "import-records", "--file", str(records_path), "--store", str(store_path)]
        )
        run_mem_json(
            [mem_bin, "import-lessons", "--file", str(lessons_path), "--store", str(store_path)]
        )
    bundles = sequence_bundles(sequences)
    if resolve_ids is not None:
        bundles = [bundle for bundle in bundles if bundle.work_id in resolve_ids]
    runner = _default_runner(mem_bin)
    return resolve_payloads(bundles, store_path=store_path, runner=runner)


@dataclass(frozen=True)
class _CachedTask:
    """A persisted per-task result that passed every validity check in ``_load_cached``."""

    outcomes: list[ArmOutcome]
    ours_retrieval_empty: bool


def expected_cells() -> set[tuple[str, str]]:
    """The full (arm, channel) grid one task must cover to be scored as complete."""
    return {(arm, channel.value) for arm in ARMS for channel in CHANNELS}


def task_fingerprint(task: ToolReqRealAgentTask) -> str:
    """Identifies the WORLD a cached result was measured against, not just the run's knobs.

    Without this, the cache identity is only ``(repeats, dry_run, model, arms)`` — none of
    which change when the CORPUS is regenerated. Work ids are positional (``w-0``, ``w-1``),
    so a fresh world reuses them, and a re-run over the same ``--out`` reports the PREVIOUS
    world's numbers with zero cells evaluated. Reproduced before this was added: a corpus
    with entirely different authored values returned executed=0 reused=3 and printed the old
    world's verdicts as the new world's.

    The reward-bearing content IS the world: the opaque values the action must carry and the
    oracle memory that surfaces them. Hash those and a regenerated corpus invalidates its
    stale caches automatically."""
    payload = json.dumps(
        {
            "work_id": task.work_id,
            "oracle_memory": sorted(task.oracle_memory.items()),
            "current_opaque_values": sorted(task.current_opaque_values),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _valid_cell(row: Any, repeats: int) -> bool:
    """Is one persisted ``outcomes`` row a faithful ArmOutcome measured at THIS run's repeats?

    JSON has no int/str distinction at the schema level and ``ArmOutcome`` is a plain frozen
    dataclass that does not type-check its fields, so ``{"passes": "0"}`` constructs happily
    and only blows up LATER, in ``task_verdict``'s ``passes > 0`` — an unhandled TypeError
    escaping mid-resume and killing a paid sweep. Validate the VALUES here, where a bad row
    is still just a miss. ``bool`` is excluded explicitly because it is an ``int`` subclass
    and would otherwise sail through.

    ``runs`` must equal ``repeats``. Every real write path sets it that way
    (``run_arm`` loops ``range(repeats)``), so anything else is a corrupted record — and the
    degenerate ``runs=0`` case is the one that bites: it satisfies ``0 <= passes <= runs``,
    and ``task_verdict`` then reads ``oracle 0/0`` and fabricates a confident
    "KILL: no separation" for a task that was never evaluated, counted as ``reused``.
    ``passes > runs`` is rejected for the mirror reason: no real run produces it, and left in
    place it inflates the oracle ceiling."""
    if not isinstance(row, Mapping):
        return False
    arm, channel = row.get("arm"), row.get("channel")
    passes, runs = row.get("passes"), row.get("runs")
    if not isinstance(arm, str) or not isinstance(channel, str):
        return False
    if isinstance(passes, bool) or isinstance(runs, bool):
        return False
    if not isinstance(passes, int) or not isinstance(runs, int):
        return False
    return runs == repeats and 0 <= passes <= runs


def _load_cached(result_path: Path, identity: Mapping[str, Any]) -> _CachedTask | None:
    """A persisted per-task result, or ``None`` meaning MISS — re-execute this task.

    Every rejection below is a miss, never a crash, and never a partial acceptance. These
    files are written by a sweep that can be killed mid-run and re-read by a PAID resume, so
    the two failure modes to design against are (a) an exception escaping and killing the
    whole sweep on one bad file, and (b) a degenerate or FOREIGN file being scored as a
    complete task. ``identity`` carries the run's knobs AND the per-task world fingerprint
    (see ``task_fingerprint``), so a regenerated corpus misses rather than silently reporting
    the previous world's numbers."""
    try:
        loaded = json.loads(result_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, RecursionError):
        # This is a PARSE BOUNDARY over an untrusted file, so it catches by blast radius, not
        # by enumerating what json.loads is known to raise — enumerating is what let the last
        # two rounds through. ValueError covers JSONDecodeError and UnicodeDecodeError (both
        # subclasses) AND the >4300-digit int-literal limit; RecursionError covers deeply
        # nested JSON; OSError covers unreadable/directory/permission. A corrupt file is a
        # miss; it must never kill a resumed paid sweep.
        return None
    if not isinstance(loaded, dict):
        return None  # valid JSON of the wrong shape: null, 3, [], "cached"
    if any(loaded.get(key) != value for key, value in identity.items()):
        return None  # another run's identity (dry-run vs paid, model, repeats, arms, world)
    if not isinstance(loaded.get("ours_retrieval_empty"), bool):
        return None  # written before the flag existed, or hand-edited
    repeats = identity["repeats"]
    rows = loaded.get("outcomes")
    if not isinstance(rows, list) or not all(_valid_cell(row, repeats) for row in rows):
        return None  # not a list, or a row whose fields drifted in TYPE or in VALUE
    try:
        outcomes = [ArmOutcome(**row) for row in rows]
    except TypeError:
        return None  # schema-drifted row (missing/extra field)
    # The cells must cover the grid EXACTLY — one per (arm, channel), no dupes, no strays.
    # A row-count check is not enough: six copies of the same cell has the right arity, and
    # would be accepted as a complete task. task_verdict keys by (arm, channel), so it would
    # then emit an EMPTY verdict, and the task would vanish from the separates/leaked
    # accounting while still counting as `reused` — a partial run reading as a full one.
    if {(o.arm, o.channel) for o in outcomes} != expected_cells():
        return None
    return _CachedTask(outcomes=outcomes, ours_retrieval_empty=loaded["ours_retrieval_empty"])


def run_corpus(
    tasks: Sequence[ToolReqRealAgentTask],
    sequences: Sequence[BenchmarkSequence],
    *,
    out_dir: Path,
    repeats: int,
    model: str,
    dry_run: bool,
    store_path: Path,
    mem_bin: str = DEFAULT_MEM_BIN,
    seed_fn: SeedFn = seed_ours_store_and_resolve_payloads,
    resume: bool = True,
) -> dict[str, Any]:
    """Evaluate every task, persisting one ``<work_id>.json`` each. A persisted result is
    reused only when its ``(repeats, dry_run, model, arms, task_fingerprint)`` identity
    matches — so a FREE
    dry-run's simulated result can never satisfy a PAID run over the same ``--out`` (the
    highest-severity confound: a paid ceiling silently reporting a fabricated pass), and a
    pre-``ours`` cache is correctly invalidated rather than silently reused missing the new
    arm. A corrupt or partial cache file (a process killed mid-write) is treated as a miss
    and re-executed, never a crash.

    ``seed_fn`` seeds the ``ours`` store + resolves its payload ONCE for the whole corpus
    (FREE — see ``seed_ours_store_and_resolve_payloads`` — and always over ALL sequences,
    dry-run or paid alike, since cross-task retrieval needs every lesson in the store);
    it is skipped entirely when every task is served from cache, honoring the same
    resumability contract as ``evaluate_task``. It is injectable so a hermetic test can
    stub it out without a built ``bin/mem``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run_identity = {"repeats": repeats, "dry_run": dry_run, "model": model, "arms": list(ARMS)}
    # Per-task, because the world fingerprint is per-task: same knobs, different corpus -> miss.
    identity_of = {
        task.work_id: {**run_identity, "task_fingerprint": task_fingerprint(task)} for task in tasks
    }
    cached_by_id: dict[str, _CachedTask] = {}
    if resume:
        for task in tasks:
            result_path = out_dir / f"{task.work_id}.json"
            if not result_path.is_file():
                continue
            cached = _load_cached(result_path, identity_of[task.work_id])
            if cached is not None:
                cached_by_id[task.work_id] = cached
    pending = [task for task in tasks if task.work_id not in cached_by_id]
    ours_payloads = (
        seed_fn(sequences, tasks, store_path, mem_bin, {task.work_id for task in pending})
        if pending
        else {}
    )
    per_task: list[dict[str, Any]] = []
    executed = 0
    reused = 0
    for task in tasks:
        result_path = out_dir / f"{task.work_id}.json"
        cached = cached_by_id.get(task.work_id)
        if cached is not None:
            outcomes = cached.outcomes
            retrieval_empty = cached.ours_retrieval_empty
            reused += 1
        else:
            ours_payload = ours_payloads.get(task.work_id, {})
            retrieval_empty = not ours_payload
            outcomes = evaluate_task(
                task,
                repeats=repeats,
                model=model,
                dry_run=dry_run,
                ours_payload=ours_payload,
            )
            executed += 1
        record = {
            "work_id": task.work_id,
            **identity_of[task.work_id],
            "ours_retrieval_empty": retrieval_empty,
            "outcomes": [asdict(o) for o in outcomes],
            "verdict": task_verdict(outcomes),
        }
        # Atomic publish: write a sibling temp file then rename, so a kill mid-write leaves
        # either the old result or the new one, never a half-written JSON the next resume trips on.
        tmp_path = result_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(result_path)
        per_task.append(record)
    separates = sum(1 for r in per_task if r["verdict"].count("SEPARATES") == len(CHANNELS))
    leaked = [r["work_id"] for r in per_task if "LEAK" in r["verdict"]]
    # Attribution, not trivia: for these tasks `ours` was never actually run (empty retrieval,
    # scored none-equivalent), so a flat ours-vs-none result over them means "retrieval
    # surfaced nothing", NOT "memory did not help". Reading the arm without this denominator
    # is how a retrieval miss gets misreported as a null memory effect.
    empty_retrieval = [r["work_id"] for r in per_task if r["ours_retrieval_empty"]]
    return {
        "n_tasks": len(tasks),
        "executed": executed,
        "reused": reused,
        "dry_run": dry_run,
        "repeats": repeats,
        "per_task": per_task,
        "separates_all_channels": separates,
        "leaked": leaked,
        "ours_empty_retrieval": empty_retrieval,
    }


def _print_go_command(n_tasks: int, repeats: int, out_dir: Path, corpus_dir: Path) -> None:
    runs = n_tasks * len(ARMS) * len(CHANNELS) * repeats
    worst_hours = runs * DEFAULT_TIMEOUT_S / 3600.0
    print(
        f"REFUSING to spend: {ENV_OAUTH} is unset.\n"
        f"  This paid sweep is {runs} real `claude -p` run(s) "
        f"({n_tasks} task x {len(ARMS)} arm x {len(CHANNELS)} channel x {repeats} repeat); "
        f"worst-case wall-clock ~{worst_hours:.1f}h at the {DEFAULT_TIMEOUT_S:.0f}s timeout.\n"
        f"  Per-task results persist to {out_dir} and are reused on re-run (resumable).\n"
        "  To fire (Stephanie's per-action go), source the token from an account home and "
        "wrap in scix-batch:\n\n"
        f"    scix-batch -- env {ENV_OAUTH}=... \\\n"
        f"        uv run python scripts/grid_toolreq_realagent.py --corpus-dir {corpus_dir}\n\n"
        "  Or prove the wiring for free first:\n"
        f"    uv run python scripts/grid_toolreq_realagent.py --corpus-dir {corpus_dir} --dry-run"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repeats", type=int, default=3, help="runs per (task, arm, channel)")
    parser.add_argument(
        "--model", default="", help="pins --model; empty reads MEMBENCH_AGENT_MODEL"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N tasks (smoke subset)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="simulate the agent; no token, no claude"
    )
    parser.add_argument("--mem-bin", default=DEFAULT_MEM_BIN, help="the built mem CLI binary")
    parser.add_argument(
        "--store", type=Path, default=None, help="ours store path (default <out>/store.db)"
    )
    args = parser.parse_args(argv)

    corpus_dir = args.corpus_dir
    sequences, tasks = load_corpus_with_sequences(corpus_dir)
    if args.limit is not None:
        sequences = sequences[: args.limit]
        tasks = tasks[: args.limit]
    if not tasks:
        print(f"no tool-requiring tasks under {corpus_dir}", file=sys.stderr)
        return 1

    if not args.dry_run and not os.environ.get(ENV_OAUTH):
        _print_go_command(len(tasks), args.repeats, args.out, corpus_dir)
        return 2

    mode = "DRY-RUN (simulated agent, no tokens)" if args.dry_run else "PAID real claude -p"
    print(
        f"toolreq real-agent sweep: {mode}; {len(tasks)} task(s) x {len(ARMS)} arm x "
        f"{len(CHANNELS)} channel x {args.repeats} repeat"
    )
    store_path = args.store if args.store is not None else args.out / "store.db"
    summary = run_corpus(
        tasks,
        sequences,
        out_dir=args.out,
        repeats=args.repeats,
        model=args.model,
        dry_run=args.dry_run,
        store_path=store_path,
        mem_bin=args.mem_bin,
    )
    for record in summary["per_task"]:
        print(f"  {record['work_id']:<24} {record['verdict']}")
    summary_path = args.out / "summary-toolreq-realagent.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n{summary['separates_all_channels']}/{summary['n_tasks']} task(s) separate on both "
        f"channels; leaked={summary['leaked'] or 'none'}; summary -> {summary_path}"
    )
    if args.dry_run:
        print("(DRY-RUN proves arm wiring + scorer discriminate; real behaviour is the paid run.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
