"""§11 interruption generator — Handoff-Debt frozen-checkpoint matched pairs.

Adopts the procedural design of "Handoff Debt" (arXiv 2606.02875) into the
mem-lvp §11 generator (mem-dsu, memo .gc/docs/mem-sxe.1-handoff-debt-investigation.md):
at each of 3 deterministic agent-behavioral interruption points the repo
checkpoint + task are held FIXED and only the injected predecessor-memory VIEW
varies across 4 arms (repo-only / raw-trace / summary-notes / structured-notes).
The oracle is authored in pure Python (Tier 0): deterministic, seed-reproducible,
no LLM in CI — mirroring the synthetic-task and retention-schedule generators.
"""

from __future__ import annotations

from membench.generators.interruption import (
    GENERATOR_VERSION,
    PredecessorTrajectory,
    detect_interruption_points,
    generate_handoff_tasks,
    structured_notes_at,
    trajectory_for_seed,
)
from membench.schemas.handoff import (
    FIRST_POST_FAILURE_EDIT,
    FIRST_SOURCE_EDIT,
    FIRST_VALIDATION_RESULT,
    INTERRUPTION_POINTS,
    VIEW_TO_ARM,
    VIEWS,
    PredecessorEvent,
)


def test_generator_version_is_recorded() -> None:
    assert GENERATOR_VERSION.startswith("interruption")


def test_three_interruption_points_and_four_views() -> None:
    # The protocol's two fixed axes: exactly 3 agent-behavioral points, 4 view arms.
    assert INTERRUPTION_POINTS == (
        FIRST_SOURCE_EDIT,
        FIRST_VALIDATION_RESULT,
        FIRST_POST_FAILURE_EDIT,
    )
    assert set(VIEWS) == {"repo-only", "raw-trace", "summary-notes", "structured-notes"}


def test_views_map_to_our_condition_arms() -> None:
    # repo-only = none control; raw-trace = raw-trajectory control (mem-l23);
    # structured-notes = 'ours'; summary-notes = the added compression-only 4th arm.
    assert VIEW_TO_ARM == {
        "repo-only": "none",
        "raw-trace": "raw-trajectory",
        "summary-notes": "summary",
        "structured-notes": "ours",
    }


def test_detector_finds_the_three_points_in_order() -> None:
    events = (
        PredecessorEvent(kind="source_edit", target="a.py"),  # 0 first source edit
        PredecessorEvent(kind="validation", target="pytest", outcome="fail"),  # 1 first validation
        PredecessorEvent(kind="source_edit", target="a.py"),  # 2 first post-failure edit
        PredecessorEvent(kind="validation", target="pytest", outcome="pass"),  # 3
    )
    points = detect_interruption_points(events)
    assert points == {
        FIRST_SOURCE_EDIT: 0,
        FIRST_VALIDATION_RESULT: 1,
        FIRST_POST_FAILURE_EDIT: 2,
    }


def test_detector_omits_absent_post_failure_edit() -> None:
    # The paper: only 31/75 tasks have a post-failure edit. A point that never
    # occurs is OMITTED, never imputed.
    events = (
        PredecessorEvent(kind="source_edit", target="a.py"),
        PredecessorEvent(kind="validation", target="pytest", outcome="pass"),
    )
    points = detect_interruption_points(events)
    assert FIRST_POST_FAILURE_EDIT not in points
    assert set(points) == {FIRST_SOURCE_EDIT, FIRST_VALIDATION_RESULT}


def test_validation_before_any_edit_is_not_the_first_validation_result() -> None:
    # "first validation RESULT" is the first validation AFTER a source edit
    # (post-edit test/build/lint), not a pre-edit baseline run.
    events = (
        PredecessorEvent(kind="validation", target="pytest", outcome="pass"),  # pre-edit baseline
        PredecessorEvent(kind="source_edit", target="a.py"),
        PredecessorEvent(kind="validation", target="pytest", outcome="fail"),  # the real result
    )
    points = detect_interruption_points(events)
    assert points[FIRST_VALIDATION_RESULT] == 2


def test_generate_emits_three_points_times_four_views() -> None:
    tasks = generate_handoff_tasks(seed=0)
    # Every authored trajectory carries all 3 points, so 3 x 4 = 12 tasks.
    assert len(tasks) == 12
    assert {t.point for t in tasks} == set(INTERRUPTION_POINTS)
    assert {t.view for t in tasks} == set(VIEWS)
    assert {t.arm for t in tasks} == set(VIEW_TO_ARM.values())


def test_frozen_checkpoint_matched_pair_invariant() -> None:
    # Within a matched set (same point + checkpoint) the 4 views share an
    # IDENTICAL task prompt and checkpoint; ONLY the view/arm/injected context vary.
    tasks = generate_handoff_tasks(seed=1)
    by_key: dict[str, list] = {}
    for t in tasks:
        by_key.setdefault(t.matched_key, []).append(t)
    assert len(by_key) == 3  # one matched set per interruption point
    for group in by_key.values():
        assert len(group) == 4
        assert len({t.task_prompt for t in group}) == 1
        assert len({t.checkpoint_id for t in group}) == 1
        assert len({t.point for t in group}) == 1
        assert {t.view for t in group} == set(VIEWS)


def test_repo_only_injects_no_predecessor_context() -> None:
    tasks = generate_handoff_tasks(seed=0)
    repo_only = [t for t in tasks if t.view == "repo-only"]
    assert repo_only
    assert all(t.injected_context == "" for t in repo_only)


def test_raw_trace_is_the_largest_context_views_compress() -> None:
    # The paper's key asymmetry: raw-trace minimizes steady-state effort but
    # maximizes the initial prompt (~12x). At a fixed checkpoint, raw-trace must
    # carry strictly more context than either notes view (which compress it).
    tasks = generate_handoff_tasks(seed=0)
    one_set = [t for t in tasks if t.point == "first_post_failure_edit"]
    ctx = {t.view: t.injected_context for t in one_set}
    assert len(ctx["raw-trace"]) > len(ctx["summary-notes"])
    assert len(ctx["raw-trace"]) > len(ctx["structured-notes"])
    assert ctx["repo-only"] == ""


def test_summary_and_structured_carry_the_same_information_different_form() -> None:
    # The 4th arm isolates STRUCTURE from COMPRESSION: summary-notes and
    # structured-notes encode the same predecessor evidence, so both must name the
    # changed file and the validation command — they differ only in form.
    tasks = generate_handoff_tasks(seed=2)
    one_set = [t for t in tasks if t.point == "first_post_failure_edit"]
    ctx = {t.view: t.injected_context for t in one_set}
    notes = structured_notes_at(trajectory_for_seed(2), point="first_post_failure_edit")
    changed = notes.changed_files[0]
    assert changed in ctx["summary-notes"]
    assert changed in ctx["structured-notes"]
    assert notes.validation_cmd is not None
    assert notes.validation_cmd in ctx["summary-notes"]
    assert notes.validation_cmd in ctx["structured-notes"]


def test_structured_notes_field_schema_is_grounded_in_the_prefix() -> None:
    # The structured-notes schema (changed files / validation cmd / handoff state +
    # problem-understanding / work-done / evidence / uncertainty / next-steps),
    # with the deterministic fields extracted from the frozen checkpoint prefix.
    traj = trajectory_for_seed(0)
    notes = structured_notes_at(traj, point="first_source_edit")
    # At the first source edit only one file has changed and no validation has run.
    assert len(notes.changed_files) == 1
    assert notes.validation_cmd is None
    assert "no validation" in notes.handoff_state.lower()
    # After a failed validation the handoff state reflects the failure.
    failed = structured_notes_at(traj, point="first_post_failure_edit")
    assert failed.validation_cmd is not None
    assert "fail" in failed.handoff_state.lower()
    # The model-filled fields are authored ground truth (non-empty), not stubs.
    for field in (
        failed.problem_understanding,
        failed.work_done,
        failed.evidence,
        failed.uncertainty,
        failed.next_steps,
    ):
        assert field.strip()


def test_deterministic_seed_is_byte_reproducible() -> None:
    a = generate_handoff_tasks(seed=5)
    b = generate_handoff_tasks(seed=5)
    assert [t.model_dump_json() for t in a] == [t.model_dump_json() for t in b]
    c = generate_handoff_tasks(seed=6)
    assert [t.model_dump_json() for t in c] != [t.model_dump_json() for t in a]


def test_distinct_seeds_do_not_share_task_ids() -> None:
    a_ids = {t.task_id for t in generate_handoff_tasks(seed=0)}
    b_ids = {t.task_id for t in generate_handoff_tasks(seed=1)}
    assert a_ids.isdisjoint(b_ids)


def test_test_edits_change_files_but_do_not_trigger_the_source_edit_point() -> None:
    # The interruption points key on a NON-test code change, so a test edit is a
    # changed file (it shows up in changed_files) but never the "first source edit".
    from membench.generators.interruption import _structured_notes_from_prefix
    from membench.schemas.handoff import TEST_EDIT

    events = (
        PredecessorEvent(kind=TEST_EDIT, target="tests/test_a.py", detail="add a case"),
        PredecessorEvent(kind="source_edit", target="a.py", detail="fix it"),
    )
    points = detect_interruption_points(events)
    assert points[FIRST_SOURCE_EDIT] == 1  # the test edit at index 0 is skipped
    notes = _structured_notes_from_prefix(
        PredecessorTrajectory(
            task_id="t",
            title="t",
            task_prompt="t",
            problem_understanding="t",
            uncertainty="t",
            next_steps="t",
            events=events,
        ),
        events,
    )
    assert set(notes.changed_files) == {"tests/test_a.py", "a.py"}


def test_trajectory_bank_is_well_formed() -> None:
    # Every authored trajectory must contain all 3 interruption points so the
    # matched-pair design is complete for every seed.
    for seed in range(6):
        traj = trajectory_for_seed(seed)
        assert isinstance(traj, PredecessorTrajectory)
        points = detect_interruption_points(traj.events)
        assert set(points) == set(INTERRUPTION_POINTS), traj.task_id
