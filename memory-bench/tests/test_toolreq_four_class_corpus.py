"""The four-class corpus (mem-xh9vb) must defeat the heading regex in BOTH directions.

The E1 twin pair is a lexical tell: the memory-unnecessary half is the necessary request
plus a ``Current state:`` block, so a regex on the heading labels the whole two-class
corpus correctly. A model that beats chance on it has shown nothing about need
classification. These tests pin the property that makes the four-class corpus worth
running: the heading match scores at chance, and each new class is wrong in a different
direction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from membench.metrics.scorers import states_value
from membench.runner.toolreq_corpus import (
    CONTEXT_HEADING,
    context_values,
    four_class_tasks,
    partial_twin,
    scored_value,
    twin_tasks,
    unnecessary_by_absence_twin,
)
from membench.runner.toolreq_realagent import (
    VARIANT_NECESSARY,
    VARIANT_NEEDS_MEMORY,
    VARIANT_PARTIAL,
    VARIANT_UNNECESSARY,
    VARIANT_UNNECESSARY_BY_ABSENCE,
    VARIANTS,
    ToolReqRealAgentTask,
    load_corpus_with_sequences,
)

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "worlds-tool-jev32"

pytestmark = pytest.mark.skipif(
    not CORPUS.is_dir(),
    reason=f"{CORPUS} is gitignored generated substrate; run scripts/generate_worlds.py first",
)


@pytest.fixture(scope="module")
def necessary() -> list[ToolReqRealAgentTask]:
    _, tasks = load_corpus_with_sequences(CORPUS)
    return tasks


@pytest.fixture(scope="module")
def four(necessary: list[ToolReqRealAgentTask]) -> list[ToolReqRealAgentTask]:
    return four_class_tasks(necessary)


def heading_regex_predicts_needs_memory(task: ToolReqRealAgentTask) -> bool:
    """The mechanical comparator: no state block means the prompt withheld something."""
    return CONTEXT_HEADING not in task.goal_step.user_request


def test_every_seed_yields_all_four_classes(four: list[ToolReqRealAgentTask]) -> None:
    counts = {variant: sum(t.variant == variant for t in four) for variant in VARIANTS}
    assert len(set(counts.values())) == 1, counts
    assert len(four) == 4 * counts[VARIANT_NECESSARY]


def test_the_four_classes_of_a_seed_share_one_pair_key(four: list[ToolReqRealAgentTask]) -> None:
    """paired_delta_ci pairs BY KEY; a class keyed apart contributes an imputed zero."""
    for i in range(0, len(four), 4):
        group = four[i : i + 4]
        assert len({t.pair_key for t in group}) == 1
        assert [t.variant for t in group] == list(VARIANTS)


def test_result_ids_stay_distinct_across_classes(four: list[ToolReqRealAgentTask]) -> None:
    """Twins share a pair key but must never share a result FILE."""
    assert len({t.result_id for t in four}) == len(four)


def test_the_heading_regex_scores_at_chance_over_the_four_classes(
    four: list[ToolReqRealAgentTask],
) -> None:
    """The reason this corpus exists. On the two-class corpus the same regex is perfect."""
    correct = sum(
        heading_regex_predicts_needs_memory(t) == VARIANT_NEEDS_MEMORY[t.variant] for t in four
    )
    assert correct / len(four) == pytest.approx(0.5)


def test_the_heading_regex_is_perfect_on_the_two_class_corpus(
    necessary: list[ToolReqRealAgentTask],
) -> None:
    """Pins the tell the four-class corpus exists to break, so a change that quietly
    reintroduces it reds here rather than passing as an improvement."""
    two = twin_tasks(necessary)
    correct = sum(
        heading_regex_predicts_needs_memory(t) == VARIANT_NEEDS_MEMORY[t.variant] for t in two
    )
    assert correct == len(two)


def test_partial_carries_the_heading_and_still_needs_memory(
    four: list[ToolReqRealAgentTask],
) -> None:
    for task in (t for t in four if t.variant == VARIANT_PARTIAL):
        assert CONTEXT_HEADING in task.goal_step.user_request
        assert task.goal_step.memory_necessary is True
        assert heading_regex_predicts_needs_memory(task) is False  # the regex is wrong here


def test_absence_carries_no_heading_and_needs_no_memory(
    four: list[ToolReqRealAgentTask],
) -> None:
    for task in (t for t in four if t.variant == VARIANT_UNNECESSARY_BY_ABSENCE):
        assert CONTEXT_HEADING not in task.goal_step.user_request
        assert task.goal_step.memory_necessary is False
        assert heading_regex_predicts_needs_memory(task) is True  # the regex is wrong here too


def test_partial_withholds_the_scored_value_and_states_every_other(
    necessary: list[ToolReqRealAgentTask],
) -> None:
    for task in necessary:
        sibling = partial_twin(task)
        withheld = scored_value(task)
        request = sibling.goal_step.user_request
        assert not states_value(request, withheld)
        for value in context_values(task):
            if value != withheld:
                assert states_value(request, value)


def test_partial_keeps_only_the_facts_carrying_the_withheld_value(
    necessary: list[ToolReqRealAgentTask],
) -> None:
    """The oracle ceiling for the partial class is the withheld value alone — surfacing the
    already-stated facts would make the ceiling reward information the prompt carries."""
    for task in necessary:
        sibling = partial_twin(task)
        assert sibling.oracle_memory
        assert set(sibling.oracle_memory) <= set(task.oracle_memory)
        assert sibling.oracle_memory != task.oracle_memory


def test_absence_states_every_required_value(necessary: list[ToolReqRealAgentTask]) -> None:
    for task in necessary:
        sibling = unnecessary_by_absence_twin(task)
        for value in context_values(task):
            assert states_value(sibling.goal_step.user_request, value)


def test_absence_drops_the_lookup_framing(necessary: list[ToolReqRealAgentTask]) -> None:
    """A task that still says "the current value of X" refers to something the prompt does
    not carry, which is the definition of needing memory — whatever it then inlines."""
    for task in necessary:
        assert "the current value of" in task.goal_step.user_request
        absent = unnecessary_by_absence_twin(task)
        assert "the current value of" not in absent.goal_step.user_request


def test_no_class_states_a_superseded_value(
    four: list[ToolReqRealAgentTask], necessary: list[ToolReqRealAgentTask]
) -> None:
    """A stale value in the prompt would let a wrong write score."""
    forbidden_by_work = {
        t.work_id: [
            value
            for check in t.goal_step.outcome_checks
            for action in check.requires_action
            for value in action.forbidden_values
        ]
        for t in necessary
    }
    for task in four:
        for value in forbidden_by_work[task.work_id]:
            assert not states_value(task.goal_step.user_request, value)


def test_every_class_scores_the_same_action(
    four: list[ToolReqRealAgentTask], necessary: list[ToolReqRealAgentTask]
) -> None:
    """Same tool, same arg_values, same forbidden_values across all four: the moved
    variable is the prompt, never the scorer."""

    def action(task: ToolReqRealAgentTask) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        a = task.goal_step.outcome_checks[0].requires_action[0]
        return a.tool, tuple(a.arg_values), tuple(a.forbidden_values)

    by_work = {t.work_id: action(t) for t in necessary}
    for task in four:
        assert action(task) == by_work[task.work_id]


def test_only_a_necessary_task_can_be_derived_from(necessary: list[ToolReqRealAgentTask]) -> None:
    sibling = partial_twin(necessary[0])
    assert sibling.variant != VARIANT_NECESSARY
    with pytest.raises(ValueError, match=VARIANT_NECESSARY):
        partial_twin(sibling)
    with pytest.raises(ValueError, match=VARIANT_NECESSARY):
        unnecessary_by_absence_twin(sibling)


def test_the_needs_memory_table_covers_every_variant() -> None:
    assert set(VARIANT_NEEDS_MEMORY) == set(VARIANTS)
    assert VARIANT_NEEDS_MEMORY[VARIANT_UNNECESSARY] is False
    assert sum(VARIANT_NEEDS_MEMORY.values()) * 2 == len(VARIANTS)  # balanced label
