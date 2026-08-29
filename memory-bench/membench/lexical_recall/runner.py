"""Measure candidate-generation recall under the preregistered budgets (mem-lbuvd).

The gate here is a SUBSET assertion, and it lives in this package rather than in
`beads_ordering`. `beads_ordering.client.candidate_parity` is an arm-versus-arm
gate that raises when two arms return different candidate sets, and it runs at
`beads_ordering/runner.py:258` before the label check executes. An experiment that
varies candidate generation makes differing candidate sets the expected outcome.
Relaxing that shared symbol would weaken it for `beads_ordering.runner` and
`density_linkage_evidence`, which both call it and both need it strict.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from membench.beads_ordering.models import FrozenCorpus, OrderingTask
from membench.lexical_recall.generators import CandidateGenerator
from membench.lexical_recall.models import (
    FrozenMissCorpus,
    Generator,
    MissKind,
    TaskClass,
    TaskRecall,
)

RECALL_AT_10 = 10

G1_RECOVERS = 0.50
G1_DOES_NOT_RECOVER = 0.20
G2_PRESERVES = 0.95
G2_REGRESSES = 0.80


class LexicalRecallError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskSpec:
    """One task flattened to what recall needs, so both classes share a code path."""

    task_id: str
    task_class: TaskClass
    miss_kind: MissKind | None
    query: str
    primary_relevant: str
    useful_ids: frozenset[str]
    labelled: frozenset[str]
    corpus_size: int


def miss_specs(corpus: FrozenMissCorpus) -> tuple[TaskSpec, ...]:
    size = len(corpus.memories)
    return tuple(
        TaskSpec(
            task_id=task.task_id,
            task_class=TaskClass.LEXICAL_MISS,
            miss_kind=task.miss_kind,
            query=task.query,
            primary_relevant=task.primary_relevant,
            useful_ids=frozenset({task.primary_relevant, *task.acceptable_entry_points}),
            labelled=frozenset(
                {task.primary_relevant, *task.acceptable_entry_points, *task.distractors}
            ),
            corpus_size=size,
        )
        for task in corpus.tasks
    )


def control_specs(tasks: Sequence[OrderingTask]) -> tuple[TaskSpec, ...]:
    return tuple(
        TaskSpec(
            task_id=task.task_id,
            task_class=TaskClass.LEXICAL_HIT_CONTROL,
            miss_kind=None,
            query=task.query,
            primary_relevant=task.primary_relevant,
            useful_ids=frozenset({task.primary_relevant, *task.acceptable_entry_points}),
            labelled=frozenset(
                {task.primary_relevant, *task.acceptable_entry_points, *task.distractors}
            ),
            corpus_size=task.corpus_size,
        )
        for task in tasks
    )


def validate_lexical_miss_truth(spec: TaskSpec, literal_ids: Sequence[str]) -> None:
    """The subset gate for the lexical-miss class.

    Equality is the wrong assertion here: the whole point of the class is that the
    literal matcher returns strictly less than the labelled set. What must hold is
    that it returns nothing OUTSIDE the labels, and that it does not return the
    primary.
    """

    if spec.task_class is not TaskClass.LEXICAL_MISS:
        raise LexicalRecallError(f"{spec.task_id} is not a lexical-miss task")
    returned = set(literal_ids)
    stray = returned - spec.labelled
    if stray:
        raise LexicalRecallError(
            f"{spec.task_id}: literal candidate set is not a subset of the labels "
            f"({len(stray)} unlabelled ids)"
        )
    if spec.primary_relevant in returned:
        raise LexicalRecallError(
            f"{spec.task_id}: the literal matcher returned the primary Memory, "
            "so this task is not a lexical miss at run time"
        )
    if not returned:
        raise LexicalRecallError(
            f"{spec.task_id}: the literal matcher returned nothing, so recall would "
            "be 0 for a degenerate reason rather than a missed candidate"
        )


def validate_control_truth(spec: TaskSpec, literal_ids: Sequence[str]) -> None:
    """The control class keeps the ordering experiment's equality property.

    It is re-measured rather than assumed: 'recall is 1.0 by construction' is the
    claim this whole bead exists to make explicit, so it gets evidence.
    """

    if set(literal_ids) != spec.labelled:
        raise LexicalRecallError(
            f"{spec.task_id}: literal candidate set does not equal the labelled set"
        )


def _rank_of(ranked: Sequence[str], wanted: frozenset[str]) -> int | None:
    for position, memory_id in enumerate(ranked, start=1):
        if memory_id in wanted:
            return position
    return None


def measure(
    spec: TaskSpec,
    generator: Generator,
    ranked: Sequence[str],
    matched_k: int,
) -> TaskRecall:
    primary_rank = _rank_of(ranked, frozenset({spec.primary_relevant}))
    useful_rank = _rank_of(ranked, spec.useful_ids)
    return TaskRecall(
        task_id=spec.task_id,
        task_class=spec.task_class,
        miss_kind=spec.miss_kind,
        generator=generator,
        matched_k=matched_k,
        candidate_set_size=len(ranked),
        primary_rank=primary_rank,
        useful_rank=useful_rank,
        primary_at_matched_k=primary_rank is not None and primary_rank <= matched_k,
        primary_at_10=primary_rank is not None and primary_rank <= RECALL_AT_10,
        primary_unbounded=primary_rank is not None,
        useful_at_matched_k=useful_rank is not None and useful_rank <= matched_k,
    )


def run_class(
    specs: Sequence[TaskSpec],
    generators_for: Callable[[int], dict[Generator, CandidateGenerator]],
) -> tuple[TaskRecall, ...]:
    """Measure every generator on every task, with matched-k set by the literal arm.

    Generators are looked up by corpus size rather than passed in directly: the
    ordering corpus is nested (a 50-task sees the first 50 Memories, a 500-task all
    500), so each size needs its own index and its own seeded workspace.
    """

    rows: list[TaskRecall] = []
    for spec in specs:
        generators = generators_for(spec.corpus_size)
        if Generator.LITERAL not in generators:
            raise LexicalRecallError("the literal arm sets matched-k and cannot be omitted")
        literal_ranked = generators[Generator.LITERAL].rank(spec.query)
        if spec.task_class is TaskClass.LEXICAL_MISS:
            validate_lexical_miss_truth(spec, literal_ranked)
        else:
            validate_control_truth(spec, literal_ranked)
        matched_k = len(literal_ranked)
        for name, generator in generators.items():
            ranked = literal_ranked if name is Generator.LITERAL else generator.rank(spec.query)
            rows.append(measure(spec, name, ranked, matched_k))
    return tuple(rows)


def _mean(values: Sequence[bool]) -> float:
    if not values:
        raise LexicalRecallError("cannot average an empty set of tasks")
    return sum(1 for value in values if value) / len(values)


def recall_summary(rows: Sequence[TaskRecall]) -> dict[str, object]:
    """Per class, per generator, with the per-kind strata and their n."""

    summary: dict[str, object] = {}
    for task_class in TaskClass:
        per_generator: dict[str, object] = {}
        for generator in Generator:
            selected = [
                row for row in rows if row.task_class is task_class and row.generator is generator
            ]
            if not selected:
                continue
            entry: dict[str, object] = {
                "n_tasks": len(selected),
                "primary_recall_at_matched_k": _mean([r.primary_at_matched_k for r in selected]),
                "primary_recall_at_10": _mean([r.primary_at_10 for r in selected]),
                "primary_recall_unbounded": _mean([r.primary_unbounded for r in selected]),
                "useful_recall_at_matched_k": _mean([r.useful_at_matched_k for r in selected]),
                "median_candidate_set_size": statistics.median(
                    [r.candidate_set_size for r in selected]
                ),
                "median_matched_k": statistics.median([r.matched_k for r in selected]),
            }
            if task_class is TaskClass.LEXICAL_MISS:
                by_kind: dict[str, object] = {}
                for kind in MissKind:
                    stratum = [r for r in selected if r.miss_kind is kind]
                    if not stratum:
                        continue
                    by_kind[kind.value] = {
                        "n_tasks": len(stratum),
                        "primary_recall_at_matched_k": _mean(
                            [r.primary_at_matched_k for r in stratum]
                        ),
                        "primary_recall_at_10": _mean([r.primary_at_10 for r in stratum]),
                    }
                entry["by_miss_kind"] = by_kind
            per_generator[generator.value] = entry
        if per_generator:
            summary[task_class.value] = per_generator
    return summary


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise LexicalRecallError("cannot take a percentile of nothing")
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


def g1_verdict(value: float) -> str:
    if value >= G1_RECOVERS:
        return "recovers"
    if value <= G1_DOES_NOT_RECOVER:
        return "does_not_recover"
    return "inconclusive"


def g2_verdict(value: float) -> str:
    if value >= G2_PRESERVES:
        return "preserves_literal_recall"
    if value <= G2_REGRESSES:
        return "regresses"
    return "inconclusive"


def gate_verdicts(rows: Sequence[TaskRecall]) -> dict[str, object]:
    """G1, G2 and G3 exactly as the preregistration fixes them."""

    verdicts: dict[str, object] = {}
    meets_both: list[str] = []
    for generator in (Generator.FTS, Generator.EMBEDDING):
        miss = [
            r for r in rows if r.task_class is TaskClass.LEXICAL_MISS and r.generator is generator
        ]
        control = [
            r
            for r in rows
            if r.task_class is TaskClass.LEXICAL_HIT_CONTROL and r.generator is generator
        ]
        literal_control = [
            r
            for r in rows
            if r.task_class is TaskClass.LEXICAL_HIT_CONTROL and r.generator is Generator.LITERAL
        ]
        g1_value = _mean([r.primary_at_matched_k for r in miss])
        g2_value = _mean([r.primary_at_matched_k for r in control])
        by_task = {r.task_id: r.candidate_set_size for r in literal_control}
        ratios = [
            r.candidate_set_size / by_task[r.task_id]
            for r in control
            if by_task.get(r.task_id, 0) > 0
        ]
        g1 = g1_verdict(g1_value)
        g2 = g2_verdict(g2_value)
        if g1 == "recovers" and g2 == "preserves_literal_recall":
            meets_both.append(generator.value)
        verdicts[generator.value] = {
            "G1_recovery": {"statistic": g1_value, "verdict": g1},
            "G2_no_regression": {"statistic": g2_value, "verdict": g2},
            "G3_candidate_cost": {
                "median_ratio_vs_literal": statistics.median(ratios) if ratios else None,
                "p90_ratio_vs_literal": _percentile(ratios, 0.9) if ratios else None,
                "threshold": "none, descriptive",
            },
        }
    verdicts["combined_recommendation"] = {
        "candidate_generation_earns_its_own_arm": bool(meets_both),
        "generators_meeting_both_gates": meets_both,
    }
    return verdicts


def control_tasks_from(corpus: FrozenCorpus) -> tuple[OrderingTask, ...]:
    return tuple(corpus.tasks)
