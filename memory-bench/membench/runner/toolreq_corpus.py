"""mem-9q8dg — the E1 twin corpus: a matched memory-NECESSARY / memory-UNNECESSARY pair
per frozen tool-requiring sequence.

E1's primary endpoint is a DISCRIMINATION margin,
``d(rung) = P(call | necessary) - P(call | unnecessary)``. Without an unnecessary half it
cannot be computed at all, and a ladder that only raises the call rate proves nothing:
arXiv 2605.09252 shows prompt-only control is blunt — it suppresses NECESSARY calls
alongside unnecessary ones, so "called MORE" and "called BETTER" are separated only by
measuring both halves.

Every ``ToolReqRealAgentTask`` is memory-necessary BY CONSTRUCTION: ``adapt_sequence``
mints opaque tokens that exist only in the establish leg's ``oracle_memory`` and asserts
the goal request leaks none of them. This module authors the missing half as the MINIMAL
contrast: the unnecessary twin is the necessary task with the values it must write INLINED
into the request under a neutral heading. Same tool, same opaque values, same scorer, same
forbidden (stale) values — the single moved variable is whether the value the ``Write`` must
carry is already in context. That claim is enforced, not asserted: off the values themselves the
two requests are byte-identical apart from a fixed scaffold, and no wording in either half tells
the agent whether to consult memory (arXiv 2605.09252 — a prompt-only suppression instruction in
one half manufactures the very margin E1 measures).

Two constraints shape it, both mechanical rather than aesthetic:

1. **Twins share a ``work_id``.** ``grading.paired_ci.paired_delta_ci`` pairs BY KEY: under
   the primary ``itt`` population a key present on one side only contributes an imputed
   0.0 delta. Disjoint halves therefore yield a 0.0 point estimate with a tight interval
   no matter what the truth is — a silent, confident null. Sharing the key is what makes
   ``n_imputed_zero == 0`` achievable, and the test asserts it on the real corpus.
2. **The label is not the evidence.** ``SequenceStep.memory_necessary`` records the CLAIM;
   ``runner.e1_necessity_preflight`` runs an empty-store leg-2-only arm over both halves and
   measures whether a no-memory arm actually solves the unnecessary half and actually fails
   the necessary one.

ZFC: a deterministic projection — string substitution over already-authored, already
leak-checked ground truth. No model call, no semantic judgment, no inspection of body text.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from membench.generators.enterprise_workflow import fact_subject, fact_value
from membench.metrics.scorers import states_value
from membench.runner.realagent_probe import CONFIG_FILE, REAL_TOOL
from membench.runner.toolreq_realagent import (
    DEFAULT_CORPUS,
    VARIANT_NECESSARY,
    VARIANT_PARTIAL,
    VARIANT_UNNECESSARY,
    VARIANT_UNNECESSARY_BY_ABSENCE,
    ToolReqRealAgentTask,
    load_corpus_with_sequences,
)
from membench.schemas.sequence import BenchmarkSequence, ExpectedAction, OutcomeCheck, SequenceStep

# How the unnecessary twin delivers what the necessary twin withholds.
#
# Most of what this block says is load-bearing by being ABSENT. E1's endpoint is P(agent chooses to
# consult memory), and arXiv 2605.09252 shows prompt-only control is BLUNT: a phrase like "no
# recall required" is a direct instruction to suppress the measured behaviour, and placed in ONE
# half of the contrast it manufactures the margin it is meant to measure. So the heading names the
# block and says nothing about what the agent should DO, and the block carries the VALUES ALONE --
# no provenance, no "already established", no memory framing. The twin's request is therefore the
# necessary twin's request plus this fixed scaffold plus the values, which
# ``test_the_non_value_text_of_a_twin_pair_is_identical`` pins byte-for-byte.
CONTEXT_HEADING = "Current state:"

# The separator between the bridged request and the context block. Public and reconstructed by the
# test rather than re-typed there, so the block's SHAPE is asserted in one place -- while the
# heading's WORDING is re-typed as a literal in the test on purpose, so re-introducing a
# behaviour-directing heading reds the suite instead of moving quietly with the constant.
CONTEXT_SEPARATOR = "\n\n"

# The heading the four-leg trial's REVISE leg puts over the version that just stopped being true,
# beside a ``CONTEXT_HEADING`` block carrying the version that replaced it. Names the block and
# says nothing about what to do with it, for the reason ``CONTEXT_HEADING`` says nothing.
PREVIOUS_HEADING = "Previous state:"


def _goal_action(step: SequenceStep) -> ExpectedAction:
    """The bridged goal's required real-tool action — the source of the values the twin must
    state and the values it must not. Raises on a step this module did not get from
    ``adapt_sequence`` (which always mints exactly one check with one action)."""
    for check in step.outcome_checks:
        for action in check.requires_action:
            return action
    raise ValueError(f"{step.step_id}: bridged goal step has no required action")


def _subject_of(task: ToolReqRealAgentTask) -> dict[str, str]:
    """Each required value mapped to the subject the corpus authored FOR it, read off the same
    fact template the value itself is read off — never paired positionally with the request's
    subject list, which is authored separately and would state a mapping nothing guarantees."""
    return {fact_value(content): fact_subject(content) for content in task.oracle_memory.values()}


def context_values(task: ToolReqRealAgentTask) -> list[str]:
    """Every value the context block states, in the canonical order it states them."""
    return sorted({*task.current_opaque_values, *_subject_of(task)})


def context_block(task: ToolReqRealAgentTask) -> str:
    """The ``Current state:`` block for a NECESSARY task: every required value, subject-labelled
    where the corpus authored a subject for it, bare where it did not.

    ONE definition, two consumers. ``unnecessary_twin`` appends it to the twin's request, which is
    what makes that half memory-unnecessary; ``e1_grid`` sends it as the ESTABLISH leg of a
    two-leg cell, where it is the only place the necessary half ever sees these values. Written
    twice, the two would agree until the day a label or a sort order moved on one side, and the
    E1 contrast would then be reading a prompt difference the design says does not exist."""
    subject_of = _subject_of(task)
    lines = sorted(
        f"- {subject_of[value]} is {value}" if value in subject_of else f"- {value}"
        for value in context_values(task)
    )
    return "\n".join([CONTEXT_HEADING, *lines])


def established_context(task: ToolReqRealAgentTask) -> str:
    """The context block for EITHER variant of a twin pair, and the same text for both.

    The necessary half renders it from ``oracle_memory``; the unnecessary half has no
    ``oracle_memory`` left (the twin empties it) and carries the block already appended to its own
    request, so it is read back off that request rather than reconstructed from fields the twin
    does not have. Both paths return what ``context_block`` produced for the necessary half — by
    construction on one side, by extraction on the other — which is what lets a two-leg cell send
    a byte-identical establish leg to both halves and keep the discrimination margin a statement
    about the GOAL leg's context alone.

    Raises on an unnecessary twin whose request carries no block: that task cannot be established
    from, and guessing an empty context would silently make the easy half the hard one."""
    if task.variant == VARIANT_NECESSARY:
        return context_block(task)
    marker = CONTEXT_SEPARATOR + CONTEXT_HEADING
    _head, sep, tail = task.goal_step.user_request.rpartition(marker)
    if not sep:
        raise ValueError(
            f"{task.work_id}: {task.variant!r} task states no {CONTEXT_HEADING!r} block, so the "
            "establish leg of a two-leg cell has nothing to state and would not match its twin's"
        )
    return CONTEXT_HEADING + tail


def superseded_values(task: ToolReqRealAgentTask) -> tuple[str, ...]:
    """Every value the goal action forbids: the supersession chain minus its current head."""
    return tuple(_goal_action(task.goal_step).forbidden_values)


def prior_values(task: ToolReqRealAgentTask) -> dict[str, str]:
    """Each current value mapped to the version it REPLACED — the value a session primed before
    the revision would still hold, and the value a stale write would carry.

    Read positionally off the goal action, because that is the only place the corpus states the
    chain. ``enterprise_workflow`` lays ``forbidden_values`` out as the chain minus its head, in
    chain order, for each current value in turn (``chain_values[:-1]`` per subject), so the last
    forbidden value under each current one is its immediate predecessor. ``oracle_memory`` cannot
    serve here: it carries the facts the goal REQUIRES, which are the current ones only, so a
    subject lookup on a stale token finds nothing.

    Refuses a task with no superseded value — there is no version to be stale on, and a trial run
    on it would report a stale write that could not have happened — and a chain that does not
    divide evenly over the current values, where no positional predecessor exists."""
    action = _goal_action(task.goal_step)
    currents = list(action.arg_values)
    forbidden = list(action.forbidden_values)
    if not currents:
        raise ValueError(f"{task.work_id}: goal action scores no current value")
    if not forbidden:
        raise ValueError(
            f"{task.work_id}: goal action forbids no superseded value, so no session can be "
            "primed on a previous version"
        )
    if len(forbidden) % len(currents):
        raise ValueError(
            f"{task.work_id}: {len(forbidden)} superseded value(s) over {len(currents)} current "
            "value(s) is a ragged chain; no positional predecessor exists"
        )
    depth = len(forbidden) // len(currents)
    return {current: forbidden[(k + 1) * depth - 1] for k, current in enumerate(currents)}


def prior_context(task: ToolReqRealAgentTask, *, heading: str = CONTEXT_HEADING) -> str:
    """``established_context`` with every scored value moved back one version: the same lines,
    the same labels, the same order, and the predecessor where the current value stood.

    This is what a session that ran BEFORE the revision knew. Derived from the block the goal
    leg's twin is established from rather than rendered afresh, so the two halves of a twin pair
    receive byte-identical text here for the same reason they do in ``established_context``.

    Refuses to return a block that still states a current value, or one in which some current
    value was not found to replace: either would make the trial's establish leg an ordinary pair
    establish and the stale writer a session primed on the truth."""
    block = established_context(task)
    replacements = prior_values(task)
    first, *rest = block.splitlines()
    if first != CONTEXT_HEADING:
        raise ValueError(f"{task.work_id}: context block does not open with {CONTEXT_HEADING!r}")
    lines: list[str] = []
    replaced: set[str] = set()
    for line in rest:
        for current, previous in replacements.items():
            if line == f"- {current}" or line.endswith(f" is {current}"):
                line = line[: -len(current)] + previous
                replaced.add(current)
        lines.append(line)
    missing = set(replacements) - replaced
    if missing:
        raise ValueError(
            f"{task.work_id}: current value(s) {sorted(missing)} not stated in the context block, "
            "so no previous version can stand in for them"
        )
    prior = "\n".join([heading, *lines])
    for current in replacements:
        if states_value(prior, current):
            raise ValueError(
                f"{task.work_id}: previous-state block still states current value {current!r}"
            )
    return prior


def unnecessary_twin(task: ToolReqRealAgentTask) -> ToolReqRealAgentTask:
    """The memory-UNNECESSARY twin of an adapted (necessary) task, under the SAME ``work_id``.

    The twin appends the VALUE of every fact the necessary half requires — the opaque
    ``current_opaque_values`` a passing ``Write`` must carry, and the realistic value of each
    other ``oracle_memory`` fact — under a neutral heading, and drops the memory requirement
    (``requires_memory`` / ``expected_memory_reads`` empty, ``oracle_memory`` empty: there is
    nothing left for an arm to surface). Scoring is byte-identical: the same ``arg_values`` and
    ``forbidden_values``, so a twin passes only by writing the current value and never a stale one.

    EVERY required fact, not only the scored one (mem-zfm0m). The generator names three subjects
    in the goal request and scores one; a twin that inlined the scored value alone handed the
    agent one value against a request naming three, so the "memory-unnecessary" half still needed
    memory for two of its subjects and the contrast under-measured itself. The unscored values
    are parsed off the fact template (``fact_value``), never typed here.

    It appends the VALUES, not ``oracle_memory``'s facts, and that is the whole correction of the
    first cut. Those facts carry authored provenance prose (``— by B. Cee in #meeting``) that the
    necessary half never sees, so inlining them moved TWO variables — the value's availability and
    a paragraph of extra text — while the module claimed to move one. Off the values, the twin's
    request is now byte-identical to the necessary twin's plus a fixed scaffold.

    Raises if the constructed request fails to state a current value, or states a superseded
    one — the two ways an unnecessary twin would stop being the contrast it claims to be."""
    if task.variant != VARIANT_NECESSARY:
        raise ValueError(f"{task.work_id}: can only twin a {VARIANT_NECESSARY!r} task")
    action = _goal_action(task.goal_step)
    if not task.current_opaque_values:
        raise ValueError(
            f"{task.work_id}: necessary task scores no current value, so its unnecessary "
            "twin would withhold the same value it is supposed to state"
        )
    # SUBJECT-LABELLED, and each label comes from the FACT that carries the value, never from
    # the position the request happens to name its subjects in. The distinction is the whole
    # correction here. The request's subject list and ``arg_values`` are authored separately, so
    # pairing them positionally would state a mapping nothing guarantees, and a wrong mapping is
    # worse than none. ``fact_subject`` reads the pairing off the same template ``fact_value``
    # reads the value off, so the label is the one the corpus actually authored.
    #
    # Bare values were the first cut, on the argument that a canonical order "states no mapping
    # at all, which is the truth". The truth was not the problem: legibility was. An unlabelled
    # `- toolreq-efc91a631a8d` under "Current state:" does not read as a VALUE, it reads as an
    # identifier FOR one, and the arm built to be the easy half refused 40 legs out of 40. A
    # value the agent will not recognise as a value is not an inlined value.
    #
    # A current value with no backing fact still renders bare — there is no authored subject to
    # name — and the lines are sorted as rendered, so the order stays canonical either way.
    block = context_block(task)
    values = context_values(task)
    request = task.goal_step.user_request + CONTEXT_SEPARATOR + block
    for value in values:
        if not states_value(request, value):
            raise ValueError(
                f"{task.work_id}: unnecessary twin does not state required fact value {value!r}"
            )
    for value in task.current_opaque_values:
        if not states_value(request, value):
            raise ValueError(
                f"{task.work_id}: unnecessary twin does not state current value {value!r}; "
                "a no-memory arm could not solve it and the twin is not memory-unnecessary"
            )
    for value in action.forbidden_values:
        if states_value(request, value):
            raise ValueError(
                f"{task.work_id}: unnecessary twin states superseded value {value!r} — the "
                "twin would reward a stale write"
            )
    step_id = f"{task.goal_step.step_id}-{VARIANT_UNNECESSARY}"
    goal_step = SequenceStep(
        step_id=step_id,
        user_request=request,
        available_tools=list(task.goal_step.available_tools),
        expected_memory_reads=[],
        memory_necessary=False,
        outcome_checks=[
            OutcomeCheck(
                check_id=step_id,
                description=(
                    "matched unnecessary twin: the same Write must carry the current (opaque) "
                    "value and never a superseded one, with the value already in context"
                ),
                requires_memory=[],
                requires_action=[
                    ExpectedAction(
                        tool=action.tool,
                        arg_values=list(action.arg_values),
                        forbidden_values=list(action.forbidden_values),
                    )
                ],
            )
        ],
    )
    return ToolReqRealAgentTask(
        work_id=task.work_id,
        goal_step=goal_step,
        oracle_memory={},
        current_opaque_values=task.current_opaque_values,
        variant=VARIANT_UNNECESSARY,
    )


def scored_value(task: ToolReqRealAgentTask) -> str:
    """The one value a passing ``Write`` must carry — what ``partial`` withholds.

    ``adapt_sequence`` mints exactly one current opaque value per task, so this is a lookup,
    not a choice. It raises rather than picking a first element if that ever stops holding,
    because silently withholding an UNSCORED value would leave the partial class solvable
    without memory and the class would be mislabelled rather than merely odd."""
    if len(task.current_opaque_values) != 1:
        raise ValueError(
            f"{task.work_id}: expected exactly one scored opaque value, got "
            f"{list(task.current_opaque_values)} — partial_twin cannot choose which to withhold"
        )
    return task.current_opaque_values[0]


def partial_context_block(task: ToolReqRealAgentTask) -> str:
    """``context_block`` with the SCORED value's line removed, heading intact.

    The heading is the whole point. A ``partial`` task looks answered — it carries the same
    ``Current state:`` block the memory-unnecessary half carries — and is not, because the one
    value the scorer checks is the one the block does not state. A regex on the heading calls
    it unnecessary and is wrong, which is what makes this the class the experiment rests on."""
    withheld = scored_value(task)
    subject_of = _subject_of(task)
    lines = sorted(
        f"- {subject_of[value]} is {value}" if value in subject_of else f"- {value}"
        for value in context_values(task)
        if value != withheld
    )
    if not lines:
        raise ValueError(
            f"{task.work_id}: withholding the scored value empties the context block, so the "
            "partial task is just the necessary task with a bare heading"
        )
    return "\n".join([CONTEXT_HEADING, *lines])


def partial_twin(task: ToolReqRealAgentTask) -> ToolReqRealAgentTask:
    """The PARTIAL sibling: the unnecessary twin's request minus the scored value's line.

    Memory is still required — the scorer checks a value the prompt never states — so the
    label stays ``memory_necessary=True`` and ``oracle_memory`` keeps exactly the facts that
    carry the withheld value. Everything else matches the unnecessary twin byte for byte:
    same tool, same ``arg_values``, same ``forbidden_values``, same heading, same sort order.

    Raises if the constructed request states the withheld value (the class would not need
    memory) or a superseded one (it would reward a stale write)."""
    if task.variant != VARIANT_NECESSARY:
        raise ValueError(
            f"{task.work_id}: can only derive a partial sibling"
            f" from a {VARIANT_NECESSARY!r} task"
        )
    action = _goal_action(task.goal_step)
    withheld = scored_value(task)
    block = partial_context_block(task)
    request = task.goal_step.user_request + CONTEXT_SEPARATOR + block
    if states_value(request, withheld):
        raise ValueError(
            f"{task.work_id}: partial sibling states the withheld scored value {withheld!r}, "
            "so it does not need memory and is not partial"
        )
    for value in context_values(task):
        if value != withheld and not states_value(request, value):
            raise ValueError(
                f"{task.work_id}: partial sibling does not state retained value {value!r}"
            )
    for value in action.forbidden_values:
        if states_value(request, value):
            raise ValueError(
                f"{task.work_id}: partial sibling states superseded value {value!r} — it "
                "would reward a stale write"
            )
    # Exactly the facts carrying the withheld value: what a correct arm must surface, and
    # nothing else, so the oracle ceiling for this class is the withheld value alone.
    oracle_memory = {
        key: content
        for key, content in task.oracle_memory.items()
        if fact_value(content) == withheld
    }
    if not oracle_memory:
        raise ValueError(
            f"{task.work_id}: no authored fact carries the withheld value {withheld!r}, so no "
            "arm could recover it and the partial task would be unsolvable rather than hard"
        )
    step_id = f"{task.goal_step.step_id}-{VARIANT_PARTIAL}"
    goal_step = SequenceStep(
        step_id=step_id,
        user_request=request,
        available_tools=list(task.goal_step.available_tools),
        expected_memory_reads=list(oracle_memory),
        memory_necessary=True,
        outcome_checks=[
            OutcomeCheck(
                check_id=step_id,
                description=(
                    "partial sibling: the state block states every required value except the "
                    "scored one, which the Write must still carry and only memory supplies"
                ),
                requires_memory=list(oracle_memory),
                requires_action=[
                    ExpectedAction(
                        tool=action.tool,
                        arg_values=list(action.arg_values),
                        forbidden_values=list(action.forbidden_values),
                    )
                ],
            )
        ],
    )
    return ToolReqRealAgentTask(
        work_id=task.work_id,
        goal_step=goal_step,
        oracle_memory=oracle_memory,
        current_opaque_values=task.current_opaque_values,
        variant=VARIANT_PARTIAL,
    )


def unnecessary_by_absence_twin(task: ToolReqRealAgentTask) -> ToolReqRealAgentTask:
    """The UNNECESSARY-BY-ABSENCE sibling: a self-contained goal with no state block at all.

    The other three classes all phrase the goal as a LOOKUP — "apply the current value of
    <subject>" — and then either withhold the values, supply them, or supply all but one. This
    one drops the lookup framing entirely and names the values as literals in the instruction
    itself. Nothing is referred to that the prompt does not carry, so no prior session could
    hold anything this task needs, and there is no ``Current state:`` heading anywhere.

    It is the mirror of ``partial`` against the lexical tell: a regex on the heading finds none
    and calls this task memory-necessary, and is wrong. With both classes present the heading
    match scores at chance over the four-class corpus, which is the condition under which a
    model result means anything.

    Scoring is unchanged — same tool, same ``arg_values``, same ``forbidden_values`` — so a
    task of this class passes only by writing the current value and never a stale one."""
    if task.variant != VARIANT_NECESSARY:
        raise ValueError(
            f"{task.work_id}: can only derive an absence sibling from a {VARIANT_NECESSARY!r} task"
        )
    action = _goal_action(task.goal_step)
    values = context_values(task)
    # Canonical order, same sort as the context block, so the only difference from the other
    # classes is the framing and never the order the values arrive in.
    listed = ", ".join(values)
    request = (
        f"Using the {REAL_TOOL} tool, write these values to a file named {CONFIG_FILE} in the "
        f"current directory: {listed}. Write only the value(s), nothing else."
    )
    if CONTEXT_HEADING in request:
        raise ValueError(
            f"{task.work_id}: absence sibling carries the {CONTEXT_HEADING!r} heading, which is "
            "the one thing that distinguishes it from the unnecessary twin"
        )
    for value in values:
        if not states_value(request, value):
            raise ValueError(
                f"{task.work_id}: absence sibling does not state required value {value!r}"
            )
    for value in action.forbidden_values:
        if states_value(request, value):
            raise ValueError(
                f"{task.work_id}: absence sibling states superseded value {value!r} — it would "
                "reward a stale write"
            )
    step_id = f"{task.goal_step.step_id}-{VARIANT_UNNECESSARY_BY_ABSENCE}"
    goal_step = SequenceStep(
        step_id=step_id,
        user_request=request,
        available_tools=list(task.goal_step.available_tools),
        expected_memory_reads=[],
        memory_necessary=False,
        outcome_checks=[
            OutcomeCheck(
                check_id=step_id,
                description=(
                    "absence sibling: a self-contained instruction naming the values as "
                    "literals, with no lookup framing and no state block"
                ),
                requires_memory=[],
                requires_action=[
                    ExpectedAction(
                        tool=action.tool,
                        arg_values=list(action.arg_values),
                        forbidden_values=list(action.forbidden_values),
                    )
                ],
            )
        ],
    )
    return ToolReqRealAgentTask(
        work_id=task.work_id,
        goal_step=goal_step,
        oracle_memory={},
        current_opaque_values=task.current_opaque_values,
        variant=VARIANT_UNNECESSARY_BY_ABSENCE,
    )


def four_class_tasks(tasks: Sequence[ToolReqRealAgentTask]) -> list[ToolReqRealAgentTask]:
    """Every task followed by its three siblings, in ``VARIANTS`` order.

    Kept separate from ``twin_tasks`` rather than replacing it: both paid E1 grids walk the
    two-class corpus and pair on adjacency, and quietly doubling what they iterate would
    change what those grids measure. mem-xh9vb's X1 is the only caller of this one."""
    out: list[ToolReqRealAgentTask] = []
    for task in tasks:
        out.append(task)
        out.append(unnecessary_twin(task))
        out.append(unnecessary_by_absence_twin(task))
        out.append(partial_twin(task))
    return out


def twin_tasks(tasks: Sequence[ToolReqRealAgentTask]) -> list[ToolReqRealAgentTask]:
    """Every task followed by its twin, so the walk order pairs adjacently and the two halves
    are always the same size."""
    twinned: list[ToolReqRealAgentTask] = []
    for task in tasks:
        twinned.append(task)
        twinned.append(unnecessary_twin(task))
    return twinned


def load_twin_corpus(
    corpus_dir: Path = DEFAULT_CORPUS,
) -> tuple[list[BenchmarkSequence], list[ToolReqRealAgentTask]]:
    """Load the frozen tool-requiring corpus and return it twinned. The sequences come back
    UNDOUBLED (one per world sequence): they are the establish-leg substrate, which both
    variants of a pair share."""
    sequences, tasks = load_corpus_with_sequences(corpus_dir)
    return sequences, twin_tasks(tasks)


def variant_split(
    tasks: Sequence[ToolReqRealAgentTask],
) -> dict[str, list[ToolReqRealAgentTask]]:
    """The corpus grouped by variant — the two halves the E1 discrimination margin reads."""
    split: dict[str, list[ToolReqRealAgentTask]] = {
        VARIANT_NECESSARY: [],
        VARIANT_UNNECESSARY: [],
    }
    for task in tasks:
        split.setdefault(task.variant, []).append(task)
    return split


def task_payload(task: ToolReqRealAgentTask) -> dict[str, Any]:
    """One task as JSON — the shape the acceptance check reads
    (``.tasks[].goal_step.memory_necessary``)."""
    return {
        "work_id": task.work_id,
        "pair_key": task.pair_key,
        "result_id": task.result_id,
        "variant": task.variant,
        "oracle_memory": task.oracle_memory,
        "current_opaque_values": list(task.current_opaque_values),
        "goal_step": task.goal_step.model_dump(mode="json"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--json", action="store_true", help="emit the twinned corpus as JSON")
    args = ap.parse_args(list(argv) if argv is not None else None)

    _, tasks = load_twin_corpus(args.corpus_dir)
    if not tasks:
        print(f"no tool-requiring tasks under {args.corpus_dir}", file=sys.stderr)
        return 1
    split = variant_split(tasks)
    if args.json:
        print(
            json.dumps(
                {
                    "corpus_dir": str(args.corpus_dir),
                    "n_pairs": len(split[VARIANT_NECESSARY]),
                    "counts": {name: len(half) for name, half in split.items()},
                    "tasks": [task_payload(task) for task in tasks],
                },
                indent=2,
            )
        )
    else:
        for name, half in split.items():
            print(f"{name}: {len(half)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
