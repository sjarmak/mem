"""§11 enterprise-workflow materialiser — memory-dependent worlds with Confusion+Staleness.

Mirrors ``test_synthetic_task`` but over a world: each materialised sequence must be
memory-dependent (clears ``memory_necessity_gate``), carry populated
``distractor_memories`` (Confusion) and ``superseded_memory_ids`` (Staleness), and be
byte-reproducible from its seed. The oracle pool must stay conflict-free (supersession
uses distinct v1/v2 ids).
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

import pytest

from membench.generators import enterprise_workflow
from membench.generators.enterprise_workflow import (
    _RECORD_REQUEST,
    _SHARED_CANDIDATES,
    _SUBJECTS,
    GRADED_SUBJECTS_PER_GOAL,
    MIN_VALUES_PER_SUBJECT,
    PROJECT_QUESTION_TYPE,
    PUBLISHED_GROUP_SIZES,
    SESSION_QUESTION_TYPE,
    SHARED_DECISION_COUNTS,
    SUPERSESSION_DEPTH,
    TOOL_REQUIRING_DISTRACTORS_PER_SUBJECT,
    _fact,
    _Subject,
    draw_group_size,
    draw_shared_decisions,
    fact_subject,
    fact_value,
    materialize_project_tier,
    materialize_session_tier,
    materialize_world,
    required_shared_per_task,
)
from membench.generators.memory_necessity_gate import memory_necessity_gate, project_necessity_gate
from membench.generators.shape_wellformedness_gate import shape_wellformedness_gate
from membench.memory_systems.lexical_system import DEFAULT_TOP_K
from membench.report.comparison import EPSILON
from membench.runner.agent import ScriptedAgent
from membench.runner.conditions import _assert_superseded_written, _oracle_pool
from membench.runtime import IdClock, StepContext
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team


def _world(seed: int = 5) -> EnterpriseWorld:
    return EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(
                persona_id="p2", name="Grace Hopper", role="site-reliability-engineer", team_id="t1"
            ),
        ],
        channels=[Channel(channel_id="c1", name="kernels", kind="chat")],
        seed=seed,
    )


def _project(seed: int = 5) -> Project:
    return Project(
        project_id=f"world-seed{seed}-project",
        world_id=f"world-seed{seed}",
        name="Acme initiative",
        goal="Reconcile the launch config.",
    )


def test_materializes_requested_number_of_tasks() -> None:
    seqs = materialize_world(_world(), _project(), n_tasks=3, facts_per_task=3)
    assert len(seqs) == 3
    assert all(s.sequence_id.startswith("world-seed5-task") for s in seqs)


def test_every_task_is_memory_dependent() -> None:
    # The construct-validity bar: each generated sequence must clear the gate
    # (oracle beats no-memory) — otherwise the materialiser produced a task that
    # does not require memory.
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        verdict = memory_necessity_gate(seq).verdict
        assert verdict.accepted, f"{seq.sequence_id}: {verdict.reason}"
        assert verdict.delta > EPSILON


def test_confusion_and_staleness_fields_are_populated() -> None:
    seq = materialize_world(_world(), _project(), n_tasks=1, facts_per_task=3)[0]
    goal = seq.steps[-1]
    # Confusion: wrong values for every graded subject, none colliding with a real id.
    # The count per subject is DRAWN (``PUBLISHED_GROUP_SIZES`` minus that subject's
    # authored values), so what is pinned here is the range, not a constant: a constant
    # was the group-cardinality leak (mem-r6yzk R3b).
    widest = max(PUBLISHED_GROUP_SIZES)
    narrowest = min(PUBLISHED_GROUP_SIZES)
    assert 3 * (narrowest - 1) - (SUPERSESSION_DEPTH - 1) <= len(goal.distractor_memories)
    assert len(goal.distractor_memories) <= 3 * (widest - 1) - (SUPERSESSION_DEPTH - 1)
    written = {mid for step in seq.steps for mid in step.expected_memory_writes}
    assert set(goal.distractor_memories).isdisjoint(written)
    # Staleness: every superseding step annotates its predecessor, and each stale id
    # IS a real earlier write (modeled as distinct version ids).
    superseding = [s for s in seq.steps if s.superseded_memory_ids]
    assert superseding, "expected a superseding step"
    for stale_id in goal.superseded_memory_ids:
        assert stale_id in written
        assert stale_id not in goal.expected_memory_reads  # goal depends on the final version


def test_supersession_chain_has_depth_and_satisfies_runner_contract() -> None:
    # mem-z3gi: the chain is v1..vD with D >= 3 — each superseding step marks its
    # predecessor, the goal marks every earlier version stale, and the runner's
    # prior-write assertion accepts the whole chain.
    assert SUPERSESSION_DEPTH >= 3
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        goal = seq.steps[-1]
        assert len(goal.superseded_memory_ids) == SUPERSESSION_DEPTH - 1
        chain_steps = [s for s in seq.steps[:-1] if s.superseded_memory_ids]
        assert len(chain_steps) == SUPERSESSION_DEPTH - 1
        _assert_superseded_written(seq)


def test_superseded_subject_position_varies_by_seed() -> None:
    # A fixed chain position (the old i==0) would let position stand in for the
    # staleness label; across seeds the superseding steps must not all sit at one
    # index. Step ids are harness-side, so reading the position off them is safe.
    def chain_start_index(seed: int) -> int:
        seq = materialize_world(_world(seed), _project(seed), n_tasks=1)[0]
        return next(i for i, s in enumerate(seq.steps) if s.superseded_memory_ids) - 1

    assert len({chain_start_index(seed) for seed in range(12)}) > 1


def test_goal_forbids_the_superseded_values_and_staleness_is_reward_bearing() -> None:
    seq = materialize_world(_world(), _project(), n_tasks=1, facts_per_task=3)[0]
    goal = seq.steps[-1]
    check = goal.outcome_checks[0]
    # The authored stale values ride the check (mem-z3gi item 4).
    assert len(check.forbidden_values) == SUPERSESSION_DEPTH - 1
    pool = _oracle_pool(seq)
    required = {mid: pool[mid] for mid in check.requires_memory}
    stale = {mid: pool[mid] for mid in goal.superseded_memory_ids}
    ctx = StepContext(trial_id="t", session_id="s", step_id=goal.step_id, clock=IdClock())
    # Exact recall (the oracle surface) passes.
    clean = ScriptedAgent().run_step(goal, required, ctx)
    assert clean.check_results[check.check_id] is True
    # Surfacing a stale version FAILS the goal — reward-bearing, not just diagnostic.
    confused = ScriptedAgent().run_step(goal, {**required, **stale}, ctx)
    assert confused.check_results[check.check_id] is False


def test_oracle_pool_has_no_conflict() -> None:
    # Supersession must use distinct ids; _oracle_pool raises on same-id/diff-content.
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        pool = _oracle_pool(seq)
        assert pool  # facts were established


def test_is_byte_reproducible() -> None:
    a = materialize_world(_world(7), _project(7), n_tasks=2, seed=7)
    b = materialize_world(_world(7), _project(7), n_tasks=2, seed=7)
    assert [s.model_dump_json() for s in a] == [s.model_dump_json() for s in b]
    # A different seed yields different content (not a constant).
    c = materialize_world(_world(7), _project(7), n_tasks=2, seed=8)
    assert [s.model_dump_json() for s in c] != [s.model_dump_json() for s in a]


def test_distinct_tasks_do_not_share_a_memory_scope() -> None:
    seqs = materialize_world(_world(), _project(), n_tasks=2)
    ids0 = {mid for step in seqs[0].steps for mid in step.expected_memory_writes}
    ids1 = {mid for step in seqs[1].steps for mid in step.expected_memory_writes}
    assert ids0 and ids1 and ids0.isdisjoint(ids1)


def test_rejects_world_without_personas() -> None:
    empty = _world()
    empty = empty.model_copy(update={"personas": []})
    with pytest.raises(ValueError, match="no personas"):
        materialize_world(empty, _project())


def test_tool_requiring_variant_moves_staleness_onto_the_tool_action() -> None:
    # mem-31vl: the tool-requiring goal demands a tool call carrying the CURRENT
    # value; staleness moves off the text answer (forbidden_values cleared) onto the
    # action's OWN forbidden_values, so the tool is the sole reward-bearing channel.
    seq = materialize_world(_world(), _project(), n_tasks=1, facts_per_task=3, tool_requiring=True)[
        0
    ]
    goal = seq.steps[-1]
    assert goal.available_tools == ["apply_config"]
    check = goal.outcome_checks[0]
    assert check.forbidden_values == []  # text clause cleared
    assert len(check.requires_action) == 1
    action = check.requires_action[0]
    assert action.tool == "apply_config"
    assert action.arg_values and all(action.arg_values)  # a real current value
    assert action.forbidden_values  # stale values live on the action, not the prose
    # the current value is never itself a forbidden (stale) value
    assert not set(action.arg_values) & set(action.forbidden_values)


def test_tool_requiring_variant_is_still_memory_dependent() -> None:
    # Necessity survives the shape change: oracle (surfaces current-only -> valid tool
    # arg) beats no-memory (empty arg -> current value absent).
    for seq in materialize_world(_world(), _project(), n_tasks=3, tool_requiring=True):
        verdict = memory_necessity_gate(seq).verdict
        assert verdict.accepted, f"{seq.sequence_id}: {verdict.reason}"
        assert verdict.delta > EPSILON


def test_default_variant_stays_text_answer() -> None:
    # Backward compat: default (tool_requiring=False) keeps the text-answer goal.
    goal = materialize_world(_world(), _project(), n_tasks=1, facts_per_task=3)[0].steps[-1]
    assert goal.available_tools == []
    assert goal.outcome_checks[0].requires_action == []
    assert goal.outcome_checks[0].forbidden_values  # text staleness still enforced


# --------------------------------------------------------------------------- #
# mem-zfm0m item 1: the fact template has a format-anchored inverse
# --------------------------------------------------------------------------- #


def test_fact_value_inverts_the_fact_template_for_every_authored_subject() -> None:
    """``fact_value`` is the inverse of ``_fact`` and nothing looser: for every authored subject,
    value, and attribution shape (with and without a role, with and without a channel) the value
    round-trips exactly. The twin corpus relies on this to inline each required fact's value."""
    personas = [
        Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
        Persona(persona_id="p2", name="B. Cee", role="", team_id="t1"),
    ]
    for subject in _SUBJECTS:
        for value in subject.values:
            for persona in personas:
                for channel in (None, "kernels"):
                    assert fact_value(_fact(subject.prompt, value, persona, channel)) == value
    persona = personas[0]
    assert fact_value(_fact("the project charter decision", "freeze scope", persona, None)) == (
        "freeze scope"
    )


def test_fact_value_refuses_content_that_is_not_fact_shaped() -> None:
    with pytest.raises(ValueError, match="not a fact-shaped"):
        fact_value("the deploy timeout was changed by Ada")
    with pytest.raises(ValueError, match="not a fact-shaped"):
        fact_value("")


# --------------------------------------------------------------------------- #
# mem-r6yzk.3: session-tier and project-tier question types
# --------------------------------------------------------------------------- #


def test_session_tier_labels_the_sequences_and_keeps_the_world_shape() -> None:
    plain = materialize_world(_world(), _project(), n_tasks=3, facts_per_task=3)
    tiered = materialize_session_tier(_world(), _project(), n_tasks=3, facts_per_task=3)
    assert [s.tier for s in tiered] == ["session"] * 3
    assert [s.question_type for s in tiered] == [SESSION_QUESTION_TYPE] * 3
    # Thin wrapper: nothing but the two labels differs from materialize_world, so the
    # tiers cannot drift apart in fact structure.
    assert [s.model_dump(exclude={"tier", "question_type"}) for s in tiered] == [
        s.model_dump(exclude={"tier", "question_type"}) for s in plain
    ]


def test_project_tier_labels_and_spans_a_scope_the_session_tier_does_not() -> None:
    # Both tiers at the goal's whole graded budget: the project tier cannot be built
    # below it, and the comparison is only about scope if the widths match.
    facts = GRADED_SUBJECTS_PER_GOAL
    session = materialize_session_tier(_world(), _project(), n_tasks=3, facts_per_task=facts)
    project = materialize_project_tier(_world(), _project(), n_tasks=3, facts_per_task=facts)
    assert [s.tier for s in project] == ["project"] * 3
    assert [s.question_type for s in project] == [PROJECT_QUESTION_TYPE] * 3

    # Session tier: every id the goal requires was written inside its OWN sequence.
    for seq in session:
        written = {mid for step in seq.steps for mid in step.expected_memory_writes}
        assert set(seq.steps[-1].expected_memory_reads) <= written

    # Project tier: the continuation tasks require shared decisions an EARLIER task
    # established and their own scope never writes — the cross-sequence gap that defines
    # the tier.
    written_by = {
        memory_id: index
        for index, seq in enumerate(project)
        for step in seq.steps
        for memory_id in step.expected_memory_writes
    }
    for index, seq in enumerate(project[1:], start=1):
        own = {mid for step in seq.steps for mid in step.expected_memory_writes}
        foreign = [mid for mid in seq.steps[-1].expected_memory_reads if mid not in own]
        assert foreign, f"{seq.sequence_id} reads nothing it did not write"
        for memory_id in foreign:
            assert written_by[memory_id] < index


def test_project_tier_exercises_supersession() -> None:
    # "What did we decide about X across sessions" is only a memory question if the
    # earlier decision was overturned: every project-tier goal must carry the stale
    # versions of its chain.
    for seq in materialize_project_tier(
        _world(), _project(), n_tasks=3, facts_per_task=GRADED_SUBJECTS_PER_GOAL
    ):
        goal = seq.steps[-1]
        assert len(goal.superseded_memory_ids) == SUPERSESSION_DEPTH - 1
        written = {mid for step in seq.steps for mid in step.expected_memory_writes}
        for stale_id in goal.superseded_memory_ids:
            assert stale_id in written
            assert stale_id not in goal.expected_memory_reads


def test_project_tier_drop_charter_leaves_the_shared_decisions_unwritten() -> None:
    # The Recovery variant: required by a later goal, written by nobody.
    seqs = materialize_project_tier(_world(), _project(), n_tasks=2, drop_charter=True)
    shared_ids = {
        decision.memory_id
        for decision in draw_shared_decisions(
            _world(), random.Random(f"{_world().world_id}|5|shared-decisions"), n_tasks=2
        )
    }
    written = {mid for seq in seqs for step in seq.steps for mid in step.expected_memory_writes}
    assert shared_ids.isdisjoint(written)
    required = {mid for seq in seqs for mid in seq.steps[-1].expected_memory_reads}
    assert required & shared_ids, "the recovery variant requires no shared decision at all"


def test_tier_materializers_are_byte_reproducible_from_seed() -> None:
    for materialize in (materialize_session_tier, materialize_project_tier):
        a = materialize(_world(7), _project(7), n_tasks=2, seed=7)
        b = materialize(_world(7), _project(7), n_tasks=2, seed=7)
        assert [s.model_dump_json() for s in a] == [s.model_dump_json() for s in b]
        c = materialize(_world(7), _project(7), n_tasks=2, seed=8)
        assert [s.model_dump_json() for s in c] != [s.model_dump_json() for s in a]


def test_tier_shapes_differ_as_frozen_objects() -> None:
    # If the two materialisers produced identical objects the tier label would be
    # inert and every dispatch test below could pass trivially.
    session = materialize_session_tier(_world(), _project(), n_tasks=2, seed=5)
    project = materialize_project_tier(_world(), _project(), n_tasks=2, seed=5)
    assert [s.model_dump_json() for s in session] != [s.model_dump_json() for s in project]


def _asked_prompts(seq: BenchmarkSequence) -> set[str]:
    """The subjects the goal question NAMES, parsed off the wording both variants share
    ("... the current value of: <prompt>, <prompt>.")."""
    request = seq.steps[-1].user_request
    head, marker, tail = request.rpartition("value of: ")
    assert marker, f"goal question does not name its subjects: {request!r}"
    assert head
    return {prompt.strip() for prompt in tail.rstrip(".").split(", ")}


def _graded_prompts(seq: BenchmarkSequence, texts: dict[str, str]) -> set[str]:
    """The subjects the goal's outcome check actually grades, read back off the content
    of each id it requires. ``texts`` spans the whole tier because a project-tier task
    requires a charter a SIBLING sequence wrote."""
    (check,) = seq.steps[-1].outcome_checks
    return {fact_subject(texts[memory_id]) for memory_id in check.requires_memory}


def _tier_texts(sequences: list[BenchmarkSequence]) -> dict[str, str]:
    return {
        memory_id: content
        for seq in sequences
        for step in seq.steps
        for memory_id, content in step.expected_memory_writes.items()
    }


def test_the_question_asks_for_every_subject_the_outcome_check_grades() -> None:
    """mem-r6yzk B5 — the question and the grader must name the SAME subjects.

    The project-tier goal grades the charter (it is in ``requires_memory``) but used to
    list only the per-task subjects in its wording, so the task was scored on a value it
    never asked for. Any agent, memory or not, loses that clause; the tier's headline
    number measured an unasked question.
    """
    for sequences in (
        materialize_session_tier(_world(), _project(), n_tasks=3, seed=5),
        materialize_project_tier(_world(), _project(), n_tasks=3, seed=5),
        materialize_project_tier(_world(), _project(), n_tasks=3, seed=5, tool_requiring=True),
    ):
        texts = _tier_texts(sequences)
        for seq in sequences:
            assert _asked_prompts(seq) == _graded_prompts(seq, texts), seq.sequence_id


def test_no_authored_prompt_contains_the_separator_the_question_lists_them_with() -> None:
    # _asked_prompts splits on ", ", and the goal question joins prompts with it. A
    # prompt containing that separator would make the parse silently wrong.
    for prompt in [s.prompt for s in _SUBJECTS]:
        assert ", " not in prompt


def test_every_prompt_the_question_names_is_held_by_more_than_one_candidate() -> None:
    """The constraint that keeps the question from marking its own gold fact.

    The tiers no longer share byte-identical wording — a project question names the
    charter prompt, because it grades the charter. That is safe only while naming a
    prompt does not identify a candidate: every subject the question names must be
    carried by at least TWO candidates in the pool, holding DIFFERENT values, so the
    prompt tokens rank them together and the choice between them is a memory question.
    """
    for sequences in (
        materialize_session_tier(_world(), _project(), n_tasks=3, seed=5),
        materialize_project_tier(_world(), _project(), n_tasks=3, seed=5),
    ):
        texts = _tier_texts(sequences)
        for seq in sequences:
            goal = seq.steps[-1]
            (check,) = goal.outcome_checks
            pool = {
                memory_id: content
                for step in seq.steps
                for memory_id, content in step.expected_memory_writes.items()
            }
            pool.update({mid: texts[mid] for mid in check.requires_memory})
            pool.update(goal.distractor_memories)
            for prompt in _asked_prompts(seq):
                values = [
                    fact_value(content)
                    for content in pool.values()
                    if fact_subject(content) == prompt
                ]
                assert len(values) >= 2, f"{seq.sequence_id}: {prompt!r} has one candidate"
                assert len(set(values)) >= 2, f"{seq.sequence_id}: {prompt!r} candidates agree"


def _shared_draw(seed: int, *, n_tasks: int = 3) -> tuple[Any, ...]:
    rng = random.Random(f"world-seed{seed}|{seed}|shared-decisions")
    return draw_shared_decisions(_world(seed), rng, n_tasks=n_tasks)


def _cross_session_sets(seed: int, n_tasks: int = 3) -> list[tuple[str, ...]]:
    """The shared-decision ids each task of a world requires, in task order."""
    rng = random.Random(f"world-seed{seed}|{seed}|shared-decisions")
    decisions = draw_shared_decisions(_world(seed), rng, n_tasks=n_tasks)
    per_task = required_shared_per_task(decisions, rng, n_tasks=n_tasks)
    return [tuple(sorted(d.memory_id for d in required)) for required in per_task]


def test_which_subject_is_cross_session_varies_across_worlds() -> None:
    """mem-r6yzk R1 — the cross-session property used to be ONE fixed lookup.

    Every project record in the first release had exactly one cross-session gold, and on
    80 of 80 it was the project charter. "Always fetch the charter" cleared the tier.
    The shared subjects are now sampled per world from the whole candidate bank.
    """
    shared: Counter[str] = Counter()
    for seed in range(100, 140):
        for decision in _shared_draw(seed):
            shared[decision.subject.key] += 1
    assert len(shared) > 1
    modal_share = max(shared.values()) / sum(shared.values())
    assert modal_share <= 0.35, f"one subject is shared in {modal_share:.2f} of draws: {shared}"
    assert shared["charter"] < sum(shared.values()), "the charter is still every world's decision"


def test_the_number_of_shared_decisions_a_record_needs_is_not_constant() -> None:
    sizes = {
        len(required)
        for seed in range(100, 140)
        for required in _cross_session_sets(seed)[1:]  # task 0 has nothing before it
    }
    assert len(sizes) > 1, f"every record needs the same number of shared decisions: {sizes}"
    assert min(sizes) >= 1


def test_two_records_of_one_world_are_not_answered_by_the_same_fetch() -> None:
    # Distinct WITHIN a pass over the subset budget, which at the released shape
    # (3 tasks, 2 published records, a budget of at least 3) means distinct outright.
    for seed in range(100, 140):
        published = _cross_session_sets(seed)[1:]
        assert len(set(published)) == len(published), f"world-seed{seed}: {published}"


def test_a_shared_decision_is_established_once_and_only_by_its_own_task() -> None:
    for seed in (5, 6, 7):
        decisions = _shared_draw(seed)
        sequences = materialize_project_tier(_world(seed), _project(seed), n_tasks=3, seed=seed)
        for decision in decisions:
            writers = [
                index
                for index, seq in enumerate(sequences)
                for step in seq.steps
                if decision.memory_id in step.expected_memory_writes
            ]
            assert writers == [decision.establish_at], f"{decision.subject.key}: {writers}"


def test_every_shared_decision_has_distractors_holding_different_values() -> None:
    # Naming a shared decision's prompt in the question is only safe while other
    # candidates hold those tokens. Its distractors are those candidates: same template,
    # same subject, different decisions.
    for seed in (5, 6, 7):
        sequences = materialize_project_tier(_world(seed), _project(seed), n_tasks=3, seed=seed)
        drawn = {d.memory_id: len(d.distractors) for d in _shared_draw(seed)}
        for decision in _shared_draw(seed):
            # A shared decision authors one value, so its group is 1 + its distractors.
            assert len(decision.distractors) + 1 in PUBLISHED_GROUP_SIZES
            wrong = {fact_value(text) for text in decision.distractors.values()}
            assert len(wrong) == len(decision.distractors)
            assert fact_value(decision.content) not in wrong
        for seq in sequences[1:]:
            goal = seq.steps[-1]
            required_shared = [
                d for d in _shared_draw(seed) if d.memory_id in goal.expected_memory_reads
            ]
            assert required_shared
            for decision in required_shared:
                published = [
                    content
                    for content in goal.distractor_memories.values()
                    if fact_subject(content) == decision.subject.prompt
                ]
                assert len(published) == drawn[decision.memory_id], seq.sequence_id


def test_a_shared_subject_is_never_also_drawn_as_a_local_one() -> None:
    # One prompt carrying two gold values cannot be graded: the shared fact and the
    # local fact would each answer the other's clause.
    for seed in range(100, 110):
        shared_prompts = {d.subject.prompt for d in _shared_draw(seed)}
        for seq in materialize_project_tier(_world(seed), _project(seed), n_tasks=3, seed=seed):
            local_prompts = [
                fact_subject(content)
                for step in seq.steps[:-1]
                for content in step.expected_memory_writes.values()
                if fact_subject(content) not in shared_prompts
            ]
            assert local_prompts
            assert shared_prompts.isdisjoint(local_prompts)


def test_the_tool_requiring_shape_keeps_the_narrow_pool() -> None:
    """Pool width is a property of the shape, not a global. A published record hands
    the arm its whole pool, so extra wrong values per subject cost nothing and flatten
    the recency prior. A tool-requiring world is retrieved live at
    ``lexical_system.DEFAULT_TOP_K``; at a published width its pool runs past that
    window, the two stale versions can fall outside it, and the naive arm passes the
    goal by never seeing the trap the reward is built on."""
    rng = random.Random(3)
    for authored in (1, SUPERSESSION_DEPTH):
        assert draw_group_size(rng, authored=authored, tool_requiring=True) == (
            authored + TOOL_REQUIRING_DISTRACTORS_PER_SUBJECT
        )
    # Narrower at BOTH roles than the narrowest published group, which is what keeps
    # the whole live pool inside the retriever window.
    assert min(PUBLISHED_GROUP_SIZES) > 1 + TOOL_REQUIRING_DISTRACTORS_PER_SUBJECT
    assert min(PUBLISHED_GROUP_SIZES) >= SUPERSESSION_DEPTH + TOOL_REQUIRING_DISTRACTORS_PER_SUBJECT
    # The tool-requiring branch consumes no rng, which is what keeps a live shape's
    # draws where they were when the published shape started drawing its widths.
    before = rng.getstate()
    draw_group_size(rng, authored=1, tool_requiring=True)
    assert rng.getstate() == before

    def pool(seq: BenchmarkSequence) -> int:
        ids = {
            mid
            for step in seq.steps
            for mid in (*step.expected_memory_writes, *step.distractor_memories)
        }
        return len(ids)

    published = materialize_session_tier(_world(), _project(), n_tasks=2, facts_per_task=3)
    live = materialize_session_tier(
        _world(), _project(), n_tasks=2, facts_per_task=3, tool_requiring=True
    )
    assert all(pool(s) > DEFAULT_TOP_K for s in published), [pool(s) for s in published]
    assert all(pool(s) <= DEFAULT_TOP_K for s in live), [pool(s) for s in live]


def test_a_tool_requiring_shape_still_separates_quality_from_naive() -> None:
    """The reason the width is scoped, as the gate that caught it. Every sequence of a
    tool-requiring world clears ``shape_wellformedness_gate`` at the retrieval width the
    arms actually run at, which is what a regenerated worlds-tool corpus depends on.

    Session shapes only, over several seeds. ``shape_wellformedness_gate`` runs its two
    arms on ONE sequence with no prefix, and a project-tier task answers from what an
    earlier task of its world wrote, so both arms score zero there and the gate reports
    a delta of 0.000 about a task that discriminates perfectly under ``run_project``.
    That is the same scoping the necessity gate takes a ``context_for`` prefix to avoid
    (mem-r6yzk B1), and the gate has no such parameter."""
    for seed in range(6):
        sequences = materialize_session_tier(
            _world(seed), _project(seed), n_tasks=2, facts_per_task=3, tool_requiring=True
        )
        for seq in sequences:
            result = shape_wellformedness_gate(seq, facts_per_task=3, top_k=DEFAULT_TOP_K)
            assert result.wellformed, f"{seq.sequence_id}: {result.reason}"


def test_the_shared_decisions_honour_the_same_width() -> None:
    """A cross-session decision is graded like any local subject, so a wide local pool
    beside a narrow shared one would mark the shared fact by its pool size alone."""
    rng = random.Random(11)
    decisions = draw_shared_decisions(_world(), rng, n_tasks=3, tool_requiring=True)
    assert decisions
    for decision in decisions:
        assert len(decision.distractors) == TOOL_REQUIRING_DISTRACTORS_PER_SUBJECT

    sizes = Counter()
    for seed in range(400):
        for decision in draw_shared_decisions(_world(), random.Random(seed), n_tasks=3):
            sizes[len(decision.distractors) + 1] += 1
    assert set(sizes) == set(PUBLISHED_GROUP_SIZES), sizes


def test_the_group_size_a_subject_publishes_is_blind_to_its_role() -> None:
    """mem-r6yzk R3b. The draw is the leak fix, so it is asserted at the draw and again
    over the released corpus (``tests/test_public_corpus_contract.py``).

    ``authored`` is the role: ``SUPERSESSION_DEPTH`` values for the superseded subject,
    one for every other. Feeding the two roles the SAME rng stream has to produce the
    same sizes in the same order, which is the strongest form of "the size does not
    depend on the role" - not a distribution that matches, an identical draw.
    """
    local = [draw_group_size(random.Random(s), authored=1, tool_requiring=False) for s in range(50)]
    chained = [
        draw_group_size(random.Random(s), authored=SUPERSESSION_DEPTH, tool_requiring=False)
        for s in range(50)
    ]
    assert local == chained
    assert set(local) == set(PUBLISHED_GROUP_SIZES), Counter(local)


def test_a_group_too_small_for_its_authored_values_is_refused() -> None:
    """A size at or below ``authored`` would publish a subject with no distractor, and
    a subject whose only candidates are its own versions marks itself. The draw refuses
    rather than clamping: clamping is how a size becomes role-dependent again."""

    class _Fixed(random.Random):
        def choice(self, seq):  # type: ignore[override]
            return SUPERSESSION_DEPTH

    with pytest.raises(ValueError, match="cannot hold"):
        draw_group_size(_Fixed(0), authored=SUPERSESSION_DEPTH, tool_requiring=False)


def test_a_project_of_one_task_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 2 tasks"):
        materialize_project_tier(_world(), _project(), n_tasks=1)


def test_every_fact_is_attributed_like_every_other_fact() -> None:
    # It used to be the only fact rendered without an "in #channel" clause, which
    # separated it from its own distractor on sight.
    sequences = materialize_project_tier(_world(), _project(), n_tasks=3, seed=5)
    contents = [
        content
        for seq in sequences
        for step in seq.steps
        for content in list(step.expected_memory_writes.values())
        + list(step.distractor_memories.values())
    ]
    assert contents
    for content in contents:
        assert " in #" in content, content


def test_tier_labels_never_reach_agent_visible_text() -> None:
    # question_type is harness-side. If the label appeared in a request, a memory
    # content or a distractor, it would be a lexical tell for the tier. (The bare words
    # "session" / "project" are not checked: "project" is ordinary domain vocabulary in
    # the charter step's wording. The two tiers' questions DO differ — a project
    # question names the charter prompt, because it grades the charter — and what keeps
    # that safe is the multi-candidate property pinned above, not identical wording.)
    labels = (SESSION_QUESTION_TYPE, PROJECT_QUESTION_TYPE)
    seqs = materialize_session_tier(_world(), _project(), n_tasks=2) + materialize_project_tier(
        _world(), _project(), n_tasks=2
    )
    visible: list[str] = []
    for seq in seqs:
        for step in seq.steps:
            visible.append(step.user_request)
            visible.extend(step.expected_memory_writes.values())
            visible.extend(step.distractor_memories.values())
    for text in visible:
        for label in labels:
            assert label not in text.lower(), text


def test_establishing_requests_stay_uniform_across_tiers() -> None:
    # Every establishing step in BOTH tiers renders _RECORD_REQUEST verbatim, shared
    # decisions included. The charter step used to carry its own wording ("Record the
    # project charter decision."), which marked the one cross-session write on sight.
    for seq in materialize_session_tier(_world(), _project(), n_tasks=2) + materialize_project_tier(
        _world(), _project(), n_tasks=2
    ):
        for step in seq.steps[:-1]:
            prompt = fact_subject(next(iter(step.expected_memory_writes.values())))
            assert step.user_request == _RECORD_REQUEST.format(prompt=prompt)


def test_both_tiers_clear_the_memory_necessity_gate_at_their_own_scope() -> None:
    # Session tier: every task is sequence-scoped, so the isolated gate admits all of
    # them. Project tier: task 0 writes the charter and is solvable in isolation; a
    # continuation task is not, and piloting it alone measures isolation rather than
    # necessity — it is gated under a store shared with the tasks before it.
    for seq in materialize_session_tier(_world(), _project(), n_tasks=3):
        verdict = memory_necessity_gate(seq).verdict
        assert verdict.accepted, f"{seq.sequence_id}: {verdict.reason}"
    project = materialize_project_tier(_world(), _project(), n_tasks=3)
    assert memory_necessity_gate(project[0]).verdict.accepted
    for index, seq in enumerate(project[1:], start=1):
        verdict = project_necessity_gate(seq, project[:index]).verdict
        assert verdict.accepted, f"{seq.sequence_id}: {verdict.reason}"


def test_the_shipped_value_banks_are_gradeable_apart() -> None:
    """The guard runs at import, so a green suite already implies this. Calling it
    explicitly is what keeps it a measurement: a guard that quietly stops checking
    anything still imports clean, and the two tests below would still pass against a
    freshly-broken copy of it."""
    enterprise_workflow._assert_value_banks_are_gradeable()


def test_a_charter_value_that_states_a_subject_value_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure this guard exists for. A charter decision phrased around a subject's
    value grades as that subject: ``states_value`` is a word-boundary literal match, so
    the charter clause answers the subject clause and the record scores full marks
    without recalling either fact. Caught at import, not at scoring time, because by
    scoring time the corpus is minted and the number is already wrong."""
    stolen = _SUBJECTS[0].values[0]
    charter = next(s for s in _SUBJECTS if s.key == "charter")
    monkeypatch.setattr(
        enterprise_workflow,
        "_SHARED_CANDIDATES",
        tuple(
            (
                _Subject(
                    subject.key,
                    subject.prompt,
                    (*subject.values[:-1], f"standardise on a {stolen} budget"),
                )
                if subject.key == charter.key
                else subject
            )
            for subject in _SUBJECTS
        ),
    )
    with pytest.raises(ValueError) as caught:
        enterprise_workflow._assert_value_banks_are_gradeable()
    message = str(caught.value)
    assert "gradeable apart" in message
    assert repr(stolen) in message
    assert f"{_SUBJECTS[0].key}:" in message


def test_a_subject_bank_too_shallow_for_a_chain_plus_its_distractors_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subject with fewer than MIN_VALUES_PER_SUBJECT values cannot fund a full chain
    and a full distractor set from distinct values, so a distractor reuses a chain value
    — a distractor that grades as the gold fact, or as the stale one the goal forbids."""
    shallow = _Subject(
        "shallow", "the shallow subject", _SUBJECTS[0].values[: MIN_VALUES_PER_SUBJECT - 1]
    )
    monkeypatch.setattr(enterprise_workflow, "_SHARED_CANDIDATES", (shallow,))
    with pytest.raises(ValueError) as caught:
        enterprise_workflow._assert_value_banks_are_gradeable()
    assert "too few on ['shallow']" in str(caught.value)


def test_every_bank_funds_a_chain_plus_its_distractors() -> None:
    assert max(PUBLISHED_GROUP_SIZES) == MIN_VALUES_PER_SUBJECT
    assert min(PUBLISHED_GROUP_SIZES) > SUPERSESSION_DEPTH
    for subject in _SHARED_CANDIDATES:
        assert len(set(subject.values)) >= MIN_VALUES_PER_SUBJECT, subject.key
    # A project draws its shared decisions off the same bank the local subjects come
    # from, so the widest draw must still leave facts_per_task local subjects.
    assert len(_SUBJECTS) - max(SHARED_DECISION_COUNTS) >= 3
