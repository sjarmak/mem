"""mem-rk41.3 — frozen tool-requiring world -> real-agent action-check task adapter.

The staging finding, corrected (mem-rk41.5 already landed the OFFLINE half): the
synthetic tool-requiring corpus reaches ``mem retrieve`` and fires the exact-signature
mechanism gate, but the *paid* ours-vs-builtin verdict still needs an EXECUTION path —
a real ``claude -p`` agent driving the tool action, scored on that action. rk41.5's
``toolreq_bundle_adapter`` produces OFFLINE-ONLY placeholder bundles (synthetic env,
empty ``ReplayResult``), so ``run_grid_3arm_graded`` (Docker checkout + gold-diff repro)
cannot execute them. This module is the missing seam for the real-agent none/oracle
ceiling-at-scale: it projects each frozen sequence onto the mem-rk41.4 ``Write``-bridge
so ``realagent_probe.run_arm`` + ``score_goal_action`` drive and grade it.

Two things this adapter does that the rk41.4 single-goal probe hardcoded:

1. **Generalizes the bridge to the corpus.** It reuses ``toolreq_bundle_adapter``'s goal
   / apply-action extraction and leak firewall (one canonical reader, no drift) and
   rewrites the synthetic ``apply_config`` goal onto the real ``Write`` tool.
2. **Opaquifies the REWARD-BEARING values (mem-rk41.4 discipline).** The frozen corpus
   authors REALISTIC values (``30 days`` / ``90 days``), which a memory-free real agent can
   simply GUESS — a dirty ``none`` floor that makes the sweep uninterpretable. The values the
   action SCORES — the current value the ``Write`` must carry and the stale values it must not
   (``ExpectedAction.arg_values`` / ``forbidden_values``) — are each mapped to a deterministic
   OPAQUE token (unguessable, unique per ``(sequence, value)``), so ``none`` can pass only via a
   real prompt leak (which the firewall forbids) and ``oracle`` only by copying the surfaced
   token. Non-reward facts the oracle arm also surfaces stay realistic — they are never scored,
   so their realism costs nothing. The frozen corpus is untouched; opacity lives on this path
   only, next to the offline discrimination/realism gates that need realism.

ZFC: a deterministic projection of authored ground truth — no model call, no semantic
judgment. The one authored thing is the opaque token (a hash of ``(sequence_id, value)``);
the reward structure is the sequence's own ``requires_action``.

mem-rk41.3.1 adds the ``ours`` arm's seeding half: ``sequence_lessons_opaque`` authors a
lesson per sequence whose facts state the SAME opaque ``oracle_memory`` content this
module already computed, so a store seeded from it and the ``oracle`` arm share one
opaque value space (``scripts/grid_toolreq_realagent.seed_ours_store_and_resolve_payloads``
imports this alongside rk41.5's value-free ``sequence_records``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from membench.generators.toolreq_bundle_adapter import (
    APPLY_TOOL,
    DEFAULT_BASE_STARTED,
    _apply_action,
    _authored_values,
    _goal_step,
    _lifecycle,
)
from membench.metrics.scorers import states_value
from membench.runner.realagent_probe import CONFIG_FILE, REAL_TOOL
from membench.schemas.sequence import (
    BenchmarkSequence,
    ExpectedAction,
    OutcomeCheck,
    SequenceStep,
)

# Opaque-token namespace. The prefix makes tokens greppable in a transcript and lets a
# reader tell a substituted value from an authored one at a glance.
_OPAQUE_PREFIX = "toolreq"


@dataclass(frozen=True)
class ToolReqRealAgentTask:
    """One frozen tool-requiring sequence, ready for the real-agent none/oracle sweep.

    ``goal_step`` is the ``Write``-bridged, OPAQUE-valued goal (``realagent_probe.run_arm``
    runs it, ``score_goal_action`` grades it). ``oracle_memory`` is the id-exact ceiling
    the ``oracle`` arm surfaces (the ``none`` arm surfaces nothing). ``current_opaque_values``
    are exactly the values a passing ``Write`` must carry — the dry-run simulator keys on
    them."""

    work_id: str
    goal_step: SequenceStep
    oracle_memory: dict[str, str]
    current_opaque_values: tuple[str, ...]


def _opaque(sequence_id: str, value: str, kind: str) -> str:
    """A deterministic, unguessable token for ``value`` within ``sequence_id``. Hashing
    ``(sequence_id, value)`` makes the SAME value in different sequences distinct (no
    cross-task collision) while staying reproducible (no wall clock, no RNG). ``kind``
    (CURRENT / STALE) is a human-readable tag only; the hash carries the uniqueness."""
    digest = hashlib.sha1(f"{sequence_id}|{value}".encode()).hexdigest()[:12]
    return f"{_OPAQUE_PREFIX}-{digest}-{kind}"


def _substitute(text: str, value_map: dict[str, str]) -> str:
    """Replace each authored value in ``text`` with its opaque token, word-boundary
    anchored the same way ``states_value`` matches (so ``v2`` never rewrites inside
    ``checkout_v2``). Longest values first, so one value that is a substring of another
    cannot pre-empt the longer match."""
    for value in sorted(value_map, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", value_map[value], text)
    return text


def _bridge_request(original: str, *, sequence_id: str) -> str:
    """Rewrite the synthetic ``apply_config`` goal request onto the real ``Write`` tool.

    The synthetic request names subjects only, never values (the materialiser's
    invariant), so swapping the tool and appending an explicit ``Write``-to-``config.json``
    instruction is leak-safe by construction — and the caller leak-asserts it regardless.
    Every ``apply_config`` mention is stripped so the agent is never told to call a tool it
    does not have."""
    # The materialiser phrases the goal as "Using the tool `apply_config`, ..."; collapse
    # the whole "the tool `apply_config`" phrase first so the swap does not leave a doubled
    # "the tool the Write tool", then mop up any bare/remaining mention.
    rewritten = (
        original.replace(f"the tool `{APPLY_TOOL}`", f"the {REAL_TOOL} tool")
        .replace(f"`{APPLY_TOOL}`", f"the {REAL_TOOL} tool")
        .replace(APPLY_TOOL, REAL_TOOL)
    )
    if APPLY_TOOL in rewritten:  # defensive: an unexpected embedding survived the swap
        raise ValueError(f"{sequence_id}: could not strip {APPLY_TOOL!r} from the goal request")
    return (
        f"{rewritten}\n\nUsing the {REAL_TOOL} tool, write the required current value(s) "
        f"to a file named {CONFIG_FILE} in the current directory. Write only the value(s), "
        "nothing else."
    )


# work_id flows into a filesystem path (the driver's per-task result file), so the
# charset is locked down here — bead-id shaped (the TaskBundle rule), never a path
# separator or a leading dot, so a malformed sequence id cannot escape the out dir.
_WORK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _value_map(sequence_id: str, action: ExpectedAction) -> dict[str, str]:
    """Map each reward-bearing value to its opaque token. A value that is authored as BOTH
    current and stale is a self-contradictory (malformed) reward, so raise rather than
    silently letting the CURRENT tag win — the whole leak firewall rests on this map."""
    value_map: dict[str, str] = {}
    for value in action.arg_values:
        value_map[value] = _opaque(sequence_id, value, "CURRENT")
    for value in action.forbidden_values:
        if value in value_map:
            raise ValueError(
                f"{sequence_id}: value {value!r} is both a current and a superseded (stale) "
                "reward value — the sequence's reward is self-contradictory"
            )
        value_map[value] = _opaque(sequence_id, value, "STALE")
    return value_map


def _oracle_memory(
    seq: BenchmarkSequence, check: OutcomeCheck, value_map: dict[str, str]
) -> dict[str, str]:
    """The id-exact ceiling the ``oracle`` arm surfaces: each required-memory fact, with its
    reward value opaquified. Non-reward facts stay realistic (they are not scored, so they
    need no opacity). Raises if a required id has no backing write — a malformed sequence
    whose ceiling cannot be surfaced."""
    writes: dict[str, str] = {}
    for step in seq.steps:
        writes.update(step.expected_memory_writes)
    oracle_memory: dict[str, str] = {}
    for memory_id in check.requires_memory:
        content = writes.get(memory_id)
        if content is None:
            raise ValueError(
                f"{seq.sequence_id}: goal requires memory id {memory_id!r} that no step "
                "writes — the oracle ceiling cannot be surfaced"
            )
        oracle_memory[memory_id] = _substitute(content, value_map)
    return oracle_memory


def adapt_sequence(seq: BenchmarkSequence) -> ToolReqRealAgentTask:
    """Project a frozen tool-requiring sequence into a real-agent task with opaque REWARD
    values (the current value the ``Write`` must carry + the stale values it must not).

    Raises ``ValueError`` if the sequence is not tool-requiring (no ``apply_config`` action),
    if a required-memory id has no backing write, if the reward is self-contradictory, or if
    a value would leak into the agent-visible request."""
    if not _WORK_ID_RE.match(seq.sequence_id):
        raise ValueError(
            f"unsafe sequence_id {seq.sequence_id!r}: must match {_WORK_ID_RE.pattern}"
        )
    action = _apply_action(seq)  # raises unless tool-requiring
    goal = _goal_step(seq)
    # The apply-action's own check carries the required-memory ids the oracle arm surfaces.
    # Find it by the action, not by position — robust to a multi-check goal step.
    check = next(
        c for c in goal.outcome_checks if any(a.tool == APPLY_TOOL for a in c.requires_action)
    )

    value_map = _value_map(seq.sequence_id, action)
    current_opaque = tuple(value_map[value] for value in action.arg_values)
    forbidden_opaque = tuple(value_map[value] for value in action.forbidden_values)
    oracle_memory = _oracle_memory(seq, check, value_map)

    user_request = _bridge_request(goal.user_request, sequence_id=seq.sequence_id)
    # The agent-visible request may leak NO reward value — realistic (the source) or opaque
    # (the substituted token). Either would turn ``none`` into a free pass.
    for text_value in (*_authored_values(seq), *current_opaque, *forbidden_opaque):
        if states_value(user_request, text_value):
            raise ValueError(
                f"{seq.sequence_id}: bridged request leaks value {text_value!r}: {user_request!r}"
            )

    goal_step = SequenceStep(
        step_id=f"{seq.sequence_id}-goal-write",
        user_request=user_request,
        available_tools=[REAL_TOOL],
        expected_memory_reads=list(check.requires_memory),
        outcome_checks=[
            OutcomeCheck(
                check_id=f"{seq.sequence_id}-goal-write",
                description=(
                    "goal requires a Write carrying the current (opaque) value and never a "
                    "superseded one"
                ),
                requires_memory=list(check.requires_memory),
                requires_action=[
                    ExpectedAction(
                        tool=REAL_TOOL,
                        arg_values=list(current_opaque),
                        forbidden_values=list(forbidden_opaque),
                    )
                ],
            )
        ],
    )

    # The oracle ceiling must surface every current opaque value and no superseded one —
    # else the arm the whole sweep calls the ceiling could not pass, or could pass on a
    # stale value.
    surfaced = " ".join(oracle_memory.values())
    for value in current_opaque:
        if not states_value(surfaced, value):
            raise ValueError(
                f"{seq.sequence_id}: oracle memory does not surface current value {value!r}"
            )
    for value in forbidden_opaque:
        if states_value(surfaced, value):
            raise ValueError(f"{seq.sequence_id}: oracle memory leaks superseded value {value!r}")

    return ToolReqRealAgentTask(
        work_id=seq.sequence_id,
        goal_step=goal_step,
        oracle_memory=oracle_memory,
        current_opaque_values=current_opaque,
    )


def _seed_sort_key(sequences_path: Path) -> tuple[int, str]:
    """Order seed dirs NUMERICALLY where the name is an int (``2`` before ``10``), falling
    back to lexical for non-numeric names — so a ``--limit`` prefix stays stable past nine
    seeds. Numeric dirs sort ahead of non-numeric ones deterministically."""
    name = sequences_path.parent.name
    return (int(name), "") if name.isdigit() else (2**31, name)


def load_corpus_with_sequences(
    corpus_dir: Path,
) -> tuple[list[BenchmarkSequence], list[ToolReqRealAgentTask]]:
    """Adapt every frozen sequence under ``corpus_dir`` (one ``<seed>/sequences.json`` per
    world), seed dirs in numeric order then in-file order, so a ``--limit`` prefix is stable.
    Returns the source sequences alongside their adapted tasks, in the SAME order — the
    pairing ``sequence_lessons_opaque`` needs to author each lesson against its own
    sequence's oracle ceiling."""
    sequences: list[BenchmarkSequence] = []
    tasks: list[ToolReqRealAgentTask] = []
    for sequences_path in sorted(corpus_dir.glob("*/sequences.json"), key=_seed_sort_key):
        raw: Sequence[dict[str, object]] = json.loads(sequences_path.read_text(encoding="utf-8"))
        for entry in raw:
            seq = BenchmarkSequence.model_validate(entry)
            sequences.append(seq)
            tasks.append(adapt_sequence(seq))
    return sequences, tasks


def load_corpus(corpus_dir: Path) -> list[ToolReqRealAgentTask]:
    """Adapt every frozen sequence under ``corpus_dir``. A thin wrapper around
    ``load_corpus_with_sequences`` for callers that need only the tasks."""
    _, tasks = load_corpus_with_sequences(corpus_dir)
    return tasks


# The opaque-value lesson's framing. Distinct from toolreq_bundle_adapter's
# ``_LESSON_SUBTITLE`` (whose facts embed a tier1 failure SIGNATURE): these facts embed
# the actual opaque CONTENT — the ``ours`` arm's seeded store needs the value a
# cross-task retrieval could surface, not a signature string.
_OPAQUE_LESSON_SUBTITLE = "tool-requiring current-value ceiling (opaque)"
_OPAQUE_LESSON_CONCEPTS = ("fact",)


def sequence_lessons_opaque(
    sequences: Sequence[BenchmarkSequence],
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    base_started: str = DEFAULT_BASE_STARTED,
) -> list[dict[str, Any]]:
    """Author one lesson per sequence (the ``mem import-lessons`` JSON shape) whose facts
    state each required-memory id's CURRENT value — reusing ``adapt_sequence``'s already
    opaque + leak-checked ``oracle_memory`` dict VERBATIM (no new value-map logic, so this
    is leak-safe by construction and stays in the SAME opaque token space the ``oracle``
    arm surfaces, the mem-rk41.3.1 invariant).

    Raises ``ValueError`` if ``sequences``/``tasks`` mismatch in length or order — the two
    must be the SAME corpus walk (``load_corpus_with_sequences``), else a lesson would be
    authored against the wrong sequence's oracle ceiling."""
    if len(sequences) != len(tasks):
        raise ValueError(
            f"sequences/tasks length mismatch: {len(sequences)} sequence(s) != "
            f"{len(tasks)} task(s)"
        )
    lessons: list[dict[str, Any]] = []
    for index, (seq, task) in enumerate(zip(sequences, tasks, strict=True)):
        if seq.sequence_id != task.work_id:
            raise ValueError(
                f"sequences/tasks order mismatch at index {index}: "
                f"{seq.sequence_id!r} != {task.work_id!r}"
            )
        facts = list(task.oracle_memory.values())
        _, closed = _lifecycle(base_started, index)
        lessons.append(
            {
                "work_id": seq.sequence_id,
                "extracted_at": closed,
                "payload": {
                    "subtitle": _OPAQUE_LESSON_SUBTITLE,
                    "facts": facts,
                    "concepts": list(_OPAQUE_LESSON_CONCEPTS),
                },
            }
        )
    return lessons
