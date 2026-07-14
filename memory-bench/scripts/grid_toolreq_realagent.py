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
  run on every invocation, dry-run or paid alike — only ``claude -p`` is spend-gated;
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
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
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
from membench.runner.headless_agent import DEFAULT_TIMEOUT_S, ENV_MODEL, MemoryChannel
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

# Rides in the cache identity to cover what the fingerprints structurally CANNOT: the
# executing and scoring CODE. `task_fingerprint` and `payload_fingerprint` hash the DATA that
# reaches the prompt, but a cached cell is equally invalidated by a change to how that data is
# executed or graded — `run_arm`, `build_agent_prompt`, the stream-json parser,
# `score_goal_action`, or DEFAULT_TIMEOUT_S. None of those touch any task field, so every
# fingerprint here stays identical across such a change and a resumed sweep silently serves
# pre-change answers as if they measured the new protocol.
#
# BUMP THIS when the execution or scoring path changes in a way that could move a result.
# It is a MANUAL gate, and that is a real weakness worth naming: the alternative (hashing
# those modules' source) would invalidate the whole paid grid on any comment edit and
# re-spend real money, so the cheap-but-disciplined option is the deliberate trade. What it
# does NOT and cannot cover: the `claude` binary itself (version, PATH, account config) —
# see the driver docstring.
EXECUTION_PROTOCOL = 1

# Seeds the `ours` store + resolves its payload: (sequences, tasks, store_path, mem_bin)
# -> work_id -> (source work_id -> rendered payload). Always over ALL tasks — the payload is
# part of the cache identity, so it cannot be narrowed to pending ones (see run_corpus).
# Injectable so hermetic tests can stub it out without a built bin/mem (matching this
# codebase's CliRunner/RetrieveRunner injection convention — see headless_agent.CliRunner).
SeedFn = Callable[
    [Sequence[BenchmarkSequence], Sequence[ToolReqRealAgentTask], Path, str],
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

    Both the seed and the resolution cover ALL sequences, every run. Cross-task retrieval
    needs every lesson in the store, and the resolved payload is part of the cache identity
    (``run_corpus``), so it must be recomputed even for tasks that may end up cache-served.

    FREE: this never spends an agent turn regardless of ``--dry-run`` (only ``claude -p`` is
    spend-gated, in ``run_corpus``) — it does need a built ``bin/mem``. It runs on EVERY
    invocation, cache-served tasks included; see ``run_corpus`` for why it cannot be skipped."""
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

    Hash everything that determines what is EXECUTED and how it is SCORED — not merely the
    authored values. That distinction is the whole bug: an earlier version hashed only the
    reward-bearing content (oracle_memory + the opaque values), which leaves ``goal_step``
    invisible. ``goal_step`` is the prompt actually sent to ``claude -p`` and it carries the
    ``outcome_checks`` the run is graded against, so an ADAPTER change — new bridged wording,
    a different tool, an altered ExpectedAction — that does not happen to move the authored
    values produced an identical fingerprint, and a resumed ``--out`` silently served
    pre-change answers as if they measured the new prompt. That needs no corrupted file, just
    ordinary iteration on ``adapt_sequence`` over the frozen corpus, which is exactly how this
    repo works.

    ``SequenceStep`` is a pydantic model, so ``model_dump(mode="json")`` is a stable,
    deterministic serialization of the whole step — new fields are covered automatically
    rather than needing to be remembered here.

    ``oracle_memory`` is hashed in ITS OWN ORDER, not sorted. ``build_agent_prompt`` renders
    the memory block by iterating ``available_memory.items()``, so the dict's insertion order
    is literally the order the lines appear in the prompt sent to ``claude -p``. Sorting it
    here would normalize away a difference the agent actually sees: reorder ``oracle_memory``
    without changing its content — an ordinary edit to how ``adapt_sequence`` builds the
    dict — and a sorted hash is IDENTICAL while the ceiling arm's prompt is not, so a resumed
    sweep serves the pre-change measurement. The rule for this fingerprint: whatever reaches
    the prompt, hash it the way the prompt sees it.

    ``current_opaque_values`` IS sorted, and that is correct rather than inconsistent — it is
    never rendered into a prompt. It is only membership-tested by the scorer ("does this value
    appear in the output"), so its order is genuinely not a measured input, and sorting keeps a
    meaningless reordering from forcing a spurious re-spend."""
    payload = json.dumps(
        {
            "work_id": task.work_id,
            "oracle_memory": list(task.oracle_memory.items()),
            "current_opaque_values": sorted(task.current_opaque_values),
            "goal_step": task.goal_step.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def payload_fingerprint(ours_payload: Mapping[str, str]) -> str:
    """Identifies the `ours` arm's actual MEASURED INPUT — the text retrieval surfaced.

    ``task_fingerprint`` is task-LOCAL, but the ``ours`` payload is not: it comes from
    CROSS-TASK retrieval over the whole seeded store, so it moves when a sibling sequence is
    added, when the corpus is regenerated, or when ``bin/mem``'s retrieval changes — none of
    which touch the queried task's own fields. Hashing the payload puts all three inside the
    cache identity without having to model any of them: whatever the cause, a different
    injected context is a different measurement, and the cached cell must miss.

    ORDER-PRESERVING, deliberately. ``resolve_payloads`` builds this dict by iterating
    ``mem retrieve``'s RANKED items, and ``build_agent_prompt`` then renders the memory block
    by iterating ``available_memory.items()`` — so the dict's insertion order is retrieval's
    rank order, and rank order is what the agent reads top-to-bottom. Retrieval can return the
    same SET of lessons in a different ORDER (a reseed moving an FTS tiebreak, a ranking
    change in ``bin/mem``), which is a different prompt and therefore a different measurement.
    A sorted hash would call those two runs identical and serve the stale one."""
    return hashlib.sha256(json.dumps(list(ours_payload.items())).encode("utf-8")).hexdigest()[:16]


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
    place it inflates the oracle ceiling.

    ``runs > 0`` is checked SEPARATELY from ``runs == repeats`` and does not merely restate it.
    ``runs == repeats`` closes the degenerate case only while ``repeats`` is itself >= 1; under
    ``--repeats 0`` it is vacuously true (0 == 0) and the ``0/0`` hole reopens exactly as
    before. Guarding it here as well as in ``main`` is deliberate: the argument validation is
    the gate, this is the backstop, and the reason to have both is that an earlier fix in this
    file DELETED a check it believed a newer one subsumed, and re-opened a closed hole."""
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
    return runs == repeats and runs > 0 and 0 <= passes <= runs


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
    # The cells must cover the grid EXACTLY — one row per (arm, channel), no dupes, no strays.
    # BOTH halves are load-bearing, and each one alone has already shipped a bug here:
    #   * count alone: six copies of one cell has the right arity but wrong coverage. Accepted
    #     as complete, task_verdict emits an EMPTY verdict, and the task vanishes from the
    #     separates/leaked accounting while still counting as `reused`.
    #   * coverage alone: the six correct rows PLUS an extra duplicate row still cover the grid
    #     as a SET. Accepted as complete, and since task_verdict keys by (arm, channel), the
    #     LAST duplicate silently overwrites the real measurement — a genuine SEPARATES rewritten
    #     into a fabricated KILL, reused with zero spend and no crash.
    expected = expected_cells()
    if len(outcomes) != len(expected) or {(o.arm, o.channel) for o in outcomes} != expected:
        return None
    retrieval_empty = loaded["ours_retrieval_empty"]
    # The flag must AGREE with the rows it is filed next to. `ours_retrieval_empty` asserts the
    # `ours` cell was never run — it was produced by relabeling `none` (evaluate_task), so it is
    # none-equal by construction. A file claiming the flag while carrying an `ours` row that
    # DIFFERS from its channel's `none` row is therefore self-contradictory: one of the two is
    # fabricated, and both readings are load-bearing. The flag is the denominator that makes a
    # flat `(ours 0/N)` attributable (retrieval surfaced nothing vs memory did not help), and the
    # rows are the measurement. Ordinary runs cannot produce the mismatch — both derive from the
    # same ours_payload object — so this only fires on a corrupted or hand-edited file, which is
    # exactly the input class every other check here exists to reject. Cross-validate rather than
    # trust: a miss costs one re-run, accepting it publishes a number nobody measured.
    if retrieval_empty:
        by_cell = {(o.arm, o.channel): o for o in outcomes}
        for channel in (c.value for c in CHANNELS):
            ours_o, none_o = by_cell[(RETRIEVING_ARM, channel)], by_cell[("none", channel)]
            if replace(ours_o, arm="none") != none_o:
                return None
    return _CachedTask(outcomes=outcomes, ours_retrieval_empty=retrieval_empty)


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

    ``seed_fn`` seeds the ``ours`` store + resolves its payload for the whole corpus, on every
    run, BEFORE the cache is consulted (FREE — no agent turn — see
    ``seed_ours_store_and_resolve_payloads``). It is not skipped when tasks are cache-served
    and it is not narrowed to pending tasks: the resolved payload is a measured input and
    rides in the identity, so it has to be recomputed to know whether a cached cell is still
    current. It is injectable so a hermetic test can stub it out without a built ``bin/mem``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # The model the agent will ACTUALLY run, not the raw flag. `--model` defaults to "" and
    # HeadlessClaudeAgent then resolves it from MEMBENCH_AGENT_MODEL, so caching the raw ""
    # makes the driver's primary independent variable invisible: run the sweep under one
    # model, point the env var at another, resume, and every cached task is served as
    # `reused` with the FIRST model's numbers relabelled as the second's.
    resolved_model = model or os.environ.get(ENV_MODEL, "")
    run_identity = {
        "repeats": repeats,
        "dry_run": dry_run,
        "model": resolved_model,
        "arms": list(ARMS),
        "protocol": EXECUTION_PROTOCOL,
    }
    # Work ids key BOTH the identity map and the <work_id>.json result path, so a duplicate
    # silently aliases two DIFFERENT tasks onto one cache file: the second overwrites the
    # first, and on resume that single record is served for both — the second task's verdict
    # reported as the first task's. Corpus work ids are sequence-derived and a regenerated or
    # hand-assembled corpus can repeat one, so this is a real input, not a hypothetical. The
    # cache is only sound if task identity is one-to-one; refuse rather than measure.
    duplicates = sorted(id_ for id_, n in Counter(t.work_id for t in tasks).items() if n > 1)
    if duplicates:
        raise ValueError(
            f"duplicate work_id(s) in the corpus: {duplicates} — each task must map to exactly "
            "one <work_id>.json, or a resumed run serves one task's measurement for another"
        )
    # Seeding + resolution happen BEFORE the cache is consulted, and cover EVERY task, because
    # the resolved `ours` payload is itself a measured input and therefore belongs in the cache
    # identity. It cannot be narrowed to pending tasks (an earlier revision did exactly that):
    # the payload comes from CROSS-TASK retrieval, so adding a sibling sequence, regenerating
    # the corpus, or rebuilding `bin/mem` after a retrieval fix all change what a given task
    # retrieves while leaving that task's own fields untouched. Narrowing to pending means a
    # cached task is never re-resolved, so the change is never noticed and a stale `ours` cell
    # is served as current. This costs a store rebuild + N `mem retrieve` calls per run; all of
    # it is FREE (no agent turn), and it buys an identity that is a total function of what was
    # actually measured.
    ours_payloads = seed_fn(sequences, tasks, store_path, mem_bin)
    identity_of = {
        task.work_id: {
            **run_identity,
            "task_fingerprint": task_fingerprint(task),
            "ours_payload_fingerprint": payload_fingerprint(ours_payloads.get(task.work_id, {})),
        }
        for task in tasks
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

    # `--repeats 0` runs zero agent turns per cell and persists six 0/0 rows, which
    # `task_verdict` then reads as a confident "KILL: oracle ceiling 0/0 — no separation" for a
    # task that was NEVER EVALUATED. The `_valid_cell` guard (`runs == repeats`) is vacuously
    # true at 0, so the file caches clean and every resume reports it as `reused`. A sweep of
    # nothing must not be able to publish a verdict.
    if args.repeats < 1:
        parser.error("--repeats must be >= 1; 0 evaluates nothing and fabricates a 0/0 verdict")

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
