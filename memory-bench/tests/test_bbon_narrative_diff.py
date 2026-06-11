"""Tests for `membench.bbon.narrative_diff`: step alignment, attempt-level deltas,
pros/cons, and the summary. Pure mechanism — deterministic, no model."""

from membench.bbon.models import Attempt, AttemptStep, deterministic_id
from membench.bbon.narrative_diff import generate_narrative_diff

_HEX64 = "b" * 64


def _attempt(arm: str, status: str, **result: object) -> Attempt:
    return Attempt(
        id=deterministic_id({"arm": arm}),
        work_id=f"w-{arm}",
        arm=arm,
        status=status,  # type: ignore[arg-type]
        result=result,
    )


def _step(attempt_id: str, index: int, kind: str, **inp: object) -> AttemptStep:
    return AttemptStep(
        id=deterministic_id({"a": attempt_id, "i": index, "k": kind}),
        attempt_id=attempt_id,
        step_index=index,
        kind=kind,
        input=inp,
    )


def test_align_flags_kind_difference_and_overrun() -> None:
    left = _attempt("cold", "completed")
    right = _attempt("warm", "completed")
    left_steps = [_step(left.id, 0, "Read"), _step(left.id, 1, "Edit")]
    right_steps = [_step(right.id, 0, "Grep")]
    diff = generate_narrative_diff(left, right, left_steps, right_steps)

    assert len(diff.aligned_steps) == 2
    assert "kind differs (Read vs Grep)" in (diff.aligned_steps[0].delta or "")
    assert "only in left attempt (Edit)" in (diff.aligned_steps[1].delta or "")


def test_no_delta_when_steps_identical() -> None:
    left = _attempt("cold", "completed")
    right = _attempt("warm", "completed")
    steps_l = [_step(left.id, 0, "Read")]
    steps_r = [_step(right.id, 0, "Read")]
    diff = generate_narrative_diff(left, right, steps_l, steps_r)
    assert diff.aligned_steps[0].delta is None
    assert diff.deltas == []
    assert "structurally similar" in diff.summary


def test_status_and_metric_deltas() -> None:
    left = _attempt("cold", "failed", iterations_to_green=2, total_tokens=900)
    right = _attempt("warm", "completed", iterations_to_green=0, total_tokens=400)
    diff = generate_narrative_diff(left, right, [], [])
    paths = {d.path for d in diff.deltas}
    assert paths == {"status", "iterations_to_green", "total_tokens"}


def test_pros_cons_reward_completion_and_efficiency() -> None:
    left = _attempt("cold", "failed", iterations_to_green=2, total_tokens=900)
    right = _attempt("warm", "completed", iterations_to_green=0, total_tokens=400)
    left_steps = [_step(left.id, 0, "Read"), _step(left.id, 1, "Edit"), _step(left.id, 2, "Bash")]
    right_steps = [_step(right.id, 0, "Edit")]
    diff = generate_narrative_diff(left, right, left_steps, right_steps)

    assert "Completed successfully" in diff.pros_cons.right_pros
    assert any("Did not complete" in c for c in diff.pros_cons.left_cons)
    assert any("Fewer tokens" in p for p in diff.pros_cons.right_pros)
    assert any("Fewer iterations to green" in p for p in diff.pros_cons.right_pros)
    assert any("More concise" in p for p in diff.pros_cons.right_pros)
    # the right (warm) arm is mechanically stronger -> the summary names it.
    assert "Right (warm) appears stronger" in diff.summary


def test_missing_metric_omits_its_delta() -> None:
    # cold carries no tokens value -> the token delta is omitted, never imputed.
    left = _attempt("cold", "completed", iterations_to_green=1)
    right = _attempt("warm", "completed", iterations_to_green=1, total_tokens=300)
    diff = generate_narrative_diff(left, right, [], [])
    assert diff.deltas == []


def test_equal_metric_emits_no_delta() -> None:
    left = _attempt("cold", "completed", total_tokens=500)
    right = _attempt("warm", "completed", total_tokens=500)
    diff = generate_narrative_diff(left, right, [], [])
    assert diff.deltas == []
