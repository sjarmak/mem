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
rk41.5's ``toolreq_bundle_adapter`` substrate (the shared value-free apply_config staleness
signature) plus lessons whose facts state each sequence's CURRENT opaque value — the SAME
opaque token space ``oracle`` surfaces. That shared value space is load-bearing: under a
different value-map, ``ours`` could never satisfy the scorer even if retrieval fired
correctly. Cross-task retrieval will generally NOT surface the queried task's own
sequence-unique opaque value, so ``ours`` scoring near ``none`` rather than near ``oracle``
is the expected, honest substrate finding, not a defect to work around.

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

THE CACHE INVARIANT — stated once here, not re-argued at each check. A resumed paid run may
reuse a persisted cell only when that cell's identity is a total function of what was
measured: the prompts sent, the inputs the scorer grades against, and the run knobs. Every
cache defect this file has shipped had a single shape — the identity hashed a MODEL of the
executed input, the model was one field short, and the task then reported ``reused``, spent
nothing, did NOT crash, and printed a stale or fabricated number as a real measurement. A
green suite is not evidence against that. Each such shape is an executable case in
``tests/test_toolreq_realagent.py``; do not delete an identity field or a validity check
below without a test proving the survivor covers every input the removed one did. Two
collapses in this file have already re-opened a hole that only LOOKED subsumed.

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
from dataclasses import asdict, replace
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
from membench.runner.headless_agent import (
    DEFAULT_TIMEOUT_S,
    ENV_MODEL,
    MemoryChannel,
    build_agent_prompt,
)
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

# The run summary, written into the SAME directory as the per-task `<work_id>.json` results —
# hence a name the tasks are not allowed to claim (see _assert_usable_work_ids).
SUMMARY_NAME = "summary-toolreq-realagent.json"

# Rides in the cache identity to cover what the fingerprints structurally CANNOT: the executing
# and scoring CODE. A change to `run_arm`, `build_agent_prompt`, the stream-json parser,
# `score_goal_action`, or DEFAULT_TIMEOUT_S moves a result without touching any task field, so
# every fingerprint stays identical across it and a resumed sweep would serve pre-change answers
# as if they measured the new protocol.
#
# BUMP THIS when the execution or scoring path changes in a way that could move a result. It is a
# MANUAL gate and that is its weakness: the alternative (hashing those modules' source) would
# invalidate the whole paid grid on any comment edit and re-spend real money. It cannot cover the
# `claude` binary itself (version, PATH, account config) — see the driver docstring.
EXECUTION_PROTOCOL = 1

# Seeds the `ours` store + resolves its payload: (sequences, tasks, store_path, mem_bin)
# -> work_id -> (source work_id -> rendered payload). Injectable so hermetic tests can stub it out
# without a built bin/mem (this codebase's CliRunner/RetrieveRunner injection convention — see
# headless_agent.CliRunner).
SeedFn = Callable[
    [Sequence[BenchmarkSequence], Sequence[ToolReqRealAgentTask], Path, str],
    dict[str, dict[str, str]],
]

# The one arm whose memory can legitimately come back EMPTY: `ours` retrieves, and temporal
# LOO admits no priors for the lifecycle-earliest task. See evaluate_task.
RETRIEVING_ARM = "ours"


def _digest(payload: object) -> str:
    """The one hash used by every fingerprint below — same encoding, same width, one place."""
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()[:16]


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

    One cell is never run: ``ours`` when its retrieval came back EMPTY — not an edge case but a
    guarantee for the lifecycle-earliest task, which temporal LOO leaves with no priors. An empty
    payload makes the ``ours`` prompt byte-identical to ``none``, so the cell is none-equivalent
    by construction (delta exactly 0); running it would spend ``repeats`` real ``claude -p`` turns
    per channel to re-measure ``none``, and would leave a flat ``(ours 0/N)`` unattributable — a
    retrieval miss reads exactly like memory-did-not-help. The ``none`` cell is relabeled instead
    (``run_grid_3arm``'s empty-retrieval convention) and ``run_corpus`` records the
    ``ours_retrieval_empty`` flag so the two causes stay distinguishable."""
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
    passing means a leak; ``none`` 0 + ``oracle`` ceiling means the arms separate. Arms beyond
    ``_GATING_ARMS`` (today: ``ours``) never gate the call — each rides along as an
    informational suffix, because ``ours`` scoring near ``none`` is the expected substrate
    finding, not a failure of the grid."""
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

    Load-bearing, not hygiene: ``lessons`` is append-only (CLAUDE.md), so importing into a store
    left behind by an EARLIER corpus keeps that corpus's opaque tokens retrievable, and
    ``resolve_payloads`` renders them straight into the live cross-task payloads — a paid ``ours``
    measurement quietly carrying values no longer in the world. The store is a derived artifact of
    the corpus and FREE to rebuild, so rebuild it."""
    for suffix in ("", "-wal", "-shm"):
        store_path.with_name(store_path.name + suffix).unlink(missing_ok=True)


def seed_ours_store_and_resolve_payloads(
    sequences: Sequence[BenchmarkSequence],
    tasks: Sequence[ToolReqRealAgentTask],
    store_path: Path,
    mem_bin: str,
) -> dict[str, dict[str, str]]:
    """Seed a fresh ``ours`` store with the SAME substrate the mem-rk41.5 offline gate builds
    (``sequence_records``) plus opaque-valued lessons (``sequence_lessons_opaque`` — the SAME
    opaque token space ``oracle`` surfaces, the mem-rk41.3.1 invariant), then resolve the ``ours``
    arm's real retrieval payload via ``run_grid_3arm.resolve_payloads``, the SAME function the
    paid ours-vs-builtin grid uses.

    FREE — real ``mem`` CLI calls, never an agent turn — and so it runs on EVERY invocation,
    dry-run or paid, cache-served tasks included. It covers ALL sequences and is never narrowed to
    pending tasks: cross-task retrieval needs every lesson in the store, and the resolved payload
    is itself a measured input that rides in the cache identity (``_task_identities``), so it must
    be recomputed to know whether a cached cell is still current. It does need a built
    ``bin/mem``."""
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


def expected_cells() -> set[tuple[str, str]]:
    """The full (arm, channel) grid one task must cover to be scored as complete."""
    return {(arm, channel.value) for arm in ARMS for channel in CHANNELS}


def task_fingerprint(task: ToolReqRealAgentTask) -> str:
    """Identifies the WORLD a cached result was measured against — everything that determines what
    is EXECUTED and how it is SCORED, not merely the authored values.

    Work ids are positional (``w-0``, ``w-1``), so a regenerated corpus reuses them and, without
    this, a re-run over the same ``--out`` reports the PREVIOUS world's numbers at executed=0.
    ``goal_step`` is hashed whole (a pydantic ``model_dump`` covers new fields automatically): it
    is the prompt actually sent AND carries the ``outcome_checks`` the run is graded against, so
    an adapter change that leaves the authored values untouched still moves the measurement.

    ``oracle_memory`` is hashed in ITS OWN ORDER, deliberately: ``build_agent_prompt`` renders
    ``available_memory.items()``, so insertion order IS the order of lines in the prompt, and a
    sorted hash would call two different ceiling-arm prompts identical. ``current_opaque_values``
    IS sorted, and that is consistent rather than contradictory — it never reaches a prompt, only
    the scorer's membership test, so its order is not a measured input."""
    return _digest(
        {
            "work_id": task.work_id,
            "oracle_memory": list(task.oracle_memory.items()),
            "current_opaque_values": sorted(task.current_opaque_values),
            "goal_step": task.goal_step.model_dump(mode="json"),
        }
    )


def payload_fingerprint(ours_payload: Mapping[str, str]) -> str:
    """Identifies the ``ours`` arm's actual MEASURED INPUT — the text retrieval surfaced.

    ``task_fingerprint`` is task-LOCAL; this payload is not. It comes from CROSS-TASK retrieval
    over the whole seeded store, so it moves when a sibling sequence is added, when the corpus is
    regenerated, or when ``bin/mem``'s retrieval changes — none of which touch the queried task's
    own fields. Hashing the payload puts all three in the identity without modelling any of them.

    ORDER-PRESERVING, for the same reason ``oracle_memory`` is: ``resolve_payloads`` inserts in
    ``mem retrieve``'s RANK order and the prompt renders that order, so the same SET of lessons
    ranked differently is a different prompt and must miss."""
    return _digest(list(ours_payload.items()))


def prompt_fingerprint(task: ToolReqRealAgentTask, ours_payload: Mapping[str, str]) -> str:
    """Hashes the PROMPTS THEMSELVES — the exact text every (arm, channel) cell will send to
    ``claude -p`` — rather than a model of what goes into them, and so it cannot be incomplete
    *about the prompt*, because it IS the prompt. Memory content, memory order, the trust
    channel's framing, the user request, and every prompt-visible field of ``goal_step`` collapse
    into one digest whether or not anyone remembered to enumerate them. Building a prompt is
    string concatenation: FREE, no agent turn.

    It does NOT subsume the other fingerprints and is added ALONGSIDE them, never in place of
    them: the scorer grades ``outcome_checks`` and ``current_opaque_values``, which the prompt
    never mentions."""
    memories = arm_memories(task, ours_payload)
    cells = [
        (arm, channel.value, build_agent_prompt(task.goal_step, memories[arm], channel))
        for channel in CHANNELS
        for arm in ARMS
    ]
    return _digest(cells)


def _valid_cell(row: Any, repeats: int) -> bool:
    """Is one persisted ``outcomes`` row a faithful ArmOutcome measured at THIS run's repeats?

    ``ArmOutcome`` is a plain frozen dataclass that does not type-check its fields, so
    ``{"passes": "0"}`` constructs happily and only blows up LATER in ``task_verdict``'s
    ``passes > 0`` — an unhandled TypeError escaping mid-resume and killing a paid sweep. Validate
    the VALUES here, where a bad row is still just a miss. ``bool`` is excluded explicitly: it is
    an ``int`` subclass and would otherwise sail through.

    ``runs > 0`` is checked SEPARATELY from ``runs == repeats`` and does not restate it: under
    ``--repeats 0`` the equality is vacuously true, and ``0/0`` rows make ``task_verdict``
    fabricate a confident "KILL: no separation" for a task that was never evaluated. ``main``
    rejects ``--repeats 0`` too; this is the backstop, and both are deliberate."""
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


def _load_cached(result_path: Path, identity: Mapping[str, Any]) -> list[ArmOutcome] | None:
    """The persisted outcomes of a task whose identity matches this run, or ``None`` meaning MISS.

    Every rejection is a miss, never a crash and never a partial acceptance. These files are
    written by a sweep that can be killed mid-run and re-read by a PAID resume, so the two failure
    modes to design against are an exception escaping and killing the whole sweep on one bad file,
    and a degenerate or FOREIGN file being scored as a complete task."""
    try:
        loaded = json.loads(result_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, RecursionError):
        # A PARSE BOUNDARY over an untrusted file: catch by blast radius, not by enumerating what
        # json.loads is known to raise. ValueError covers JSONDecodeError, UnicodeDecodeError and
        # the >4300-digit int-literal limit; RecursionError covers deeply nested JSON; OSError
        # covers unreadable/directory/permission.
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
    # BOTH halves are load-bearing and each alone has already shipped a bug: six copies of one
    # cell has the right ARITY but wrong coverage (empty verdict, task vanishes from the
    # accounting, still `reused`); the six correct rows PLUS a duplicate cover the grid as a SET
    # but `task_verdict` keys by (arm, channel), so the last duplicate overwrites the real
    # measurement — a genuine SEPARATES rewritten into a fabricated KILL.
    expected = expected_cells()
    if len(outcomes) != len(expected) or {(o.arm, o.channel) for o in outcomes} != expected:
        return None
    # The flag must AGREE with the rows filed next to it. `ours_retrieval_empty` asserts the `ours`
    # cell was never run — it was relabeled from `none` (evaluate_task) and so is none-equal by
    # construction. A file claiming the flag while carrying an `ours` row that DIFFERS from its
    # channel's `none` row is self-contradictory: one of the two is fabricated. Ordinary runs
    # cannot produce the mismatch (both derive from the same payload object), so this fires only
    # on a corrupted or hand-edited file — the input class every check here exists to reject.
    if loaded["ours_retrieval_empty"]:
        by_cell = {(o.arm, o.channel): o for o in outcomes}
        for channel in (c.value for c in CHANNELS):
            ours_o, none_o = by_cell[(RETRIEVING_ARM, channel)], by_cell[("none", channel)]
            if replace(ours_o, arm="none") != none_o:
                return None
    return outcomes


def _assert_usable_work_ids(tasks: Sequence[ToolReqRealAgentTask]) -> None:
    """A work_id must be unique and a safe, unclaimed filename — it keys both the identity map and
    the ``<work_id>.json`` result path.

    A DUPLICATE silently aliases two different tasks onto one cache file: the second overwrites
    the first and, on resume, that single record is served for both. An UNSAFE id is corpus data
    used to build a filesystem path before being checked as one — it either claims the summary's
    name (``main`` writes the summary into the same directory AFTERWARDS, overwriting that task's
    result, which then misses forever) or carries a separator/traversal that writes outside
    ``--out``. Corpus ids are sequence-derived and a regenerated or hand-assembled corpus can
    produce either, so refuse rather than measure."""
    duplicates = sorted(id_ for id_, n in Counter(t.work_id for t in tasks).items() if n > 1)
    if duplicates:
        raise ValueError(
            f"duplicate work_id(s) in the corpus: {duplicates} — each task must map to exactly "
            "one <work_id>.json, or a resumed run serves one task's measurement for another"
        )
    unsafe = sorted(
        t.work_id
        for t in tasks
        if f"{t.work_id}.json" == SUMMARY_NAME or Path(t.work_id).name != t.work_id or not t.work_id
    )
    if unsafe:
        raise ValueError(
            f"unsafe work_id(s) for a result filename: {unsafe} — a work_id must be a plain "
            f"filename component and must not claim the summary's name ({SUMMARY_NAME})"
        )


def _task_identities(
    tasks: Sequence[ToolReqRealAgentTask],
    ours_payloads: Mapping[str, Mapping[str, str]],
    run_identity: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """The cache identity per task: the run's knobs plus every measured input.

    ``ours_retrieval_empty`` is a first-class identity field, live-computed like the fingerprints,
    not a value carried forward from the file. It says whether the ``ours`` cell was measured at
    all or relabeled from ``none``, which makes it the denominator the headline is read through.
    ``_load_cached`` cross-checks a True flag against the rows; comparing the flag HERE closes the
    other direction, where a file claiming False carries a fabricated ``ours 2/2``. A value
    outside the identity is not defended by the checks that surround it."""
    return {
        task.work_id: {
            **run_identity,
            "task_fingerprint": task_fingerprint(task),
            "ours_payload_fingerprint": payload_fingerprint(ours_payloads.get(task.work_id, {})),
            "prompt_fingerprint": prompt_fingerprint(task, ours_payloads.get(task.work_id, {})),
            "ours_retrieval_empty": not ours_payloads.get(task.work_id, {}),
        }
        for task in tasks
    }


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
    """Evaluate every task, persisting one ``<work_id>.json`` each, and reuse a persisted result
    only when its identity (see ``_task_identities``) matches this run's — so a FREE dry-run's
    simulated result can never satisfy a PAID run over the same ``--out``, and a corrupt or
    partial file is a miss rather than a crash.

    ``seed_fn`` runs BEFORE the cache is consulted and over the WHOLE corpus, every invocation,
    even when every task is cache-served: it is free, and the payload it resolves rides in the
    identity, so it has to be recomputed to know whether a cached cell is still current. It is
    injectable so a hermetic test can stub it out without a built ``bin/mem``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # The model the agent will ACTUALLY run, not the raw flag. `--model` defaults to "" and
    # HeadlessClaudeAgent then resolves it from MEMBENCH_AGENT_MODEL, so caching the raw ""
    # makes the driver's primary independent variable invisible: run the sweep under one model,
    # point the env var at another, resume, and every cached task is served as `reused` with the
    # FIRST model's numbers relabelled as the second's.
    resolved_model = model or os.environ.get(ENV_MODEL, "")
    run_identity = {
        "repeats": repeats,
        "dry_run": dry_run,
        "model": resolved_model,
        "arms": list(ARMS),
        "protocol": EXECUTION_PROTOCOL,
    }
    _assert_usable_work_ids(tasks)
    ours_payloads = seed_fn(sequences, tasks, store_path, mem_bin)
    identity_of = _task_identities(tasks, ours_payloads, run_identity)

    per_task: list[dict[str, Any]] = []
    executed = 0
    reused = 0
    for task in tasks:
        result_path = out_dir / f"{task.work_id}.json"
        identity = identity_of[task.work_id]
        cached = _load_cached(result_path, identity) if resume and result_path.is_file() else None
        if cached is not None:
            outcomes = cached
            reused += 1
        else:
            outcomes = evaluate_task(
                task,
                repeats=repeats,
                model=model,
                dry_run=dry_run,
                ours_payload=ours_payloads.get(task.work_id, {}),
            )
            executed += 1
        # `ours_retrieval_empty` is written by the identity spread and never set again here: the
        # identity is its single source, and a second writer could drift from the value the cache
        # is validated against.
        record = {
            "work_id": task.work_id,
            **identity,
            "outcomes": [asdict(o) for o in outcomes],
            "verdict": task_verdict(outcomes),
        }
        # Atomic publish: write a sibling temp file then rename, so a kill mid-write leaves either
        # the old result or the new one, never a half-written JSON the next resume trips on.
        tmp_path = result_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(result_path)
        per_task.append(record)
    separates = sum(1 for r in per_task if r["verdict"].count("SEPARATES") == len(CHANNELS))
    leaked = [r["work_id"] for r in per_task if "LEAK" in r["verdict"]]
    # Attribution, not trivia: for these tasks `ours` was never actually run (empty retrieval,
    # scored none-equivalent), so a flat ours-vs-none result over them means "retrieval surfaced
    # nothing", NOT "memory did not help".
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
    summary_path = args.out / SUMMARY_NAME
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
