"""§12.6 action-impact scorer: mechanical diff (Path 1), judge seam (Path 3),
pre-filter, and cross-check. Hermetic — the only judge is `StubComparativeJudge`,
no model or network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from membench.bbon.comparative_judge import StubComparativeJudge
from membench.bbon.models import AttemptStep, deterministic_id
from membench.metrics import (
    ActionImpactInputs,
    ActionImpactJudgeError,
    diff_trajectories,
    parse_action_impact_verdict,
    score_action_impact,
)

_ATTEMPT_ID = deterministic_id({"attempt": "fixture"})


def _step(
    index: int,
    kind: str,
    *,
    inp: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
) -> AttemptStep:
    body = {"i": index, "kind": kind, "input": inp or {}, "output": output or {}}
    return AttemptStep(
        id=deterministic_id(body),
        attempt_id=_ATTEMPT_ID,
        step_index=index,
        kind=kind,
        input=inp or {},
        output=output or {},
    )


def _verdict_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "memory_changed_tool_choice": False,
        "memory_changed_plan": False,
        "memory_changed_output": False,
        "memory_prevented_known_failure": False,
        "memory_improved_verification": False,
        "rationale": "stub action-impact verdict",
    }
    payload.update(overrides)
    return json.dumps(payload)


# --------------------------------------------------------------------------- #
# Path 1 — mechanical diff
# --------------------------------------------------------------------------- #
def test_diff_identical_streams_no_diff() -> None:
    steps = [_step(0, "Read", inp={"path": "a.py"}), _step(1, "Edit", inp={"path": "a.py"})]
    diff = diff_trajectories(steps, list(steps))
    assert not diff.tool_choice_differs
    assert not diff.plan_differs
    assert not diff.output_differs
    assert not diff.any_behavioral_diff


def test_diff_different_tool_choice() -> None:
    on = [_step(0, "Grep", inp={"q": "x"})]
    off = [_step(0, "Read", inp={"q": "x"})]
    diff = diff_trajectories(on, off)
    assert diff.tool_choice_differs
    assert diff.plan_differs  # kind change implies plan change
    assert diff.any_behavioral_diff


def test_diff_same_tool_different_args_is_plan_only() -> None:
    on = [_step(0, "Edit", inp={"path": "a.py", "new": "x"})]
    off = [_step(0, "Edit", inp={"path": "a.py", "new": "y"})]
    diff = diff_trajectories(on, off)
    assert not diff.tool_choice_differs
    assert diff.plan_differs


def test_diff_output_observable_only_when_data_present() -> None:
    bare_on = [_step(0, "Bash", inp={"cmd": "ls"})]
    bare_off = [_step(0, "Bash", inp={"cmd": "ls"})]
    assert not diff_trajectories(bare_on, bare_off).output_observable

    rich_on = [_step(0, "Bash", inp={"cmd": "ls"}, output={"stdout": "a"})]
    rich_off = [_step(0, "Bash", inp={"cmd": "ls"}, output={"stdout": "b"})]
    rich = diff_trajectories(rich_on, rich_off)
    assert rich.output_observable
    assert rich.output_differs


# --------------------------------------------------------------------------- #
# Path 3 — verdict parsing
# --------------------------------------------------------------------------- #
def test_parse_valid_verdict() -> None:
    v = parse_action_impact_verdict(_verdict_json(memory_changed_plan=True))
    assert v.memory_changed_plan is True
    assert v.memory_changed_tool_choice is False
    assert v.rationale


@pytest.mark.parametrize(
    "reply",
    [
        "no json here",
        "{not valid json",
        json.dumps(["a", "list"]),
    ],
)
def test_parse_rejects_unusable_reply(reply: str) -> None:
    with pytest.raises(ActionImpactJudgeError):
        parse_action_impact_verdict(reply)


def test_parse_rejects_non_boolean_axis() -> None:
    bad = _verdict_json()
    payload = json.loads(bad)
    payload["memory_changed_plan"] = 1  # int, not bool
    with pytest.raises(ActionImpactJudgeError, match="memory_changed_plan"):
        parse_action_impact_verdict(json.dumps(payload))


def test_parse_rejects_empty_rationale() -> None:
    with pytest.raises(ActionImpactJudgeError, match="rationale"):
        parse_action_impact_verdict(_verdict_json(rationale="  "))


# --------------------------------------------------------------------------- #
# Orchestration — pre-filter (no judge)
# --------------------------------------------------------------------------- #
def test_no_judge_identical_streams_are_sound_false() -> None:
    steps = (_step(0, "Read", inp={"path": "a.py"}),)
    m = score_action_impact(ActionImpactInputs(on_steps=steps, off_steps=steps))
    # Behavioral axes the streams prove identical => deterministic False.
    assert m.memory_changed_tool_choice is False
    assert m.memory_changed_plan is False
    # Output is unobservable here (no output data) => stays a seam.
    assert m.memory_changed_output is None
    # Outcome axes are pure judge seams.
    assert m.memory_prevented_known_failure is None
    assert m.memory_improved_verification is None


def test_no_judge_differing_streams_stay_seams() -> None:
    on = (_step(0, "Grep", inp={"q": "x"}),)
    off = (_step(0, "Read", inp={"q": "x"}),)
    m = score_action_impact(ActionImpactInputs(on_steps=on, off_steps=off))
    # Streams differ; with no judge the cause is unknown => None, never guessed True.
    assert m.memory_changed_tool_choice is None
    assert m.memory_changed_plan is None


def test_no_judge_observable_identical_output_is_sound_false() -> None:
    steps = (_step(0, "Bash", inp={"cmd": "ls"}, output={"stdout": "a"}),)
    m = score_action_impact(ActionImpactInputs(on_steps=steps, off_steps=steps))
    # Output is observed and identical => the pre-filter sets it False without a judge.
    assert m.memory_changed_output is False


# --------------------------------------------------------------------------- #
# Orchestration — judge present
# --------------------------------------------------------------------------- #
def test_judge_skipped_when_identical_observed_and_statuses_equal() -> None:
    # Output is OBSERVED and identical, so zero impact is provable => no judge call.
    steps = (_step(0, "Bash", inp={"cmd": "ls"}, output={"stdout": "ok"}),)

    def explode(_prompt: str) -> str:
        raise AssertionError("judge must not be called for a provably zero-impact pair")

    judge = StubComparativeJudge(fn=explode)
    m = score_action_impact(
        ActionImpactInputs(
            on_steps=steps, off_steps=steps, on_status="completed", off_status="completed"
        ),
        judge,
    )
    assert m.memory_changed_tool_choice is False
    assert m.memory_prevented_known_failure is False
    assert m.memory_improved_verification is False


def test_judge_consulted_when_output_unobservable_even_if_streams_identical() -> None:
    # Identical tool calls but NO recorded output: memory could still have changed the
    # final artifact off-stream, so the judge must be consulted (skip must NOT fire).
    steps = (_step(0, "Read", inp={"path": "a.py"}),)
    judge = StubComparativeJudge(
        fn=lambda _p: _verdict_json(memory_changed_output=True, memory_improved_verification=True)
    )
    m = score_action_impact(
        ActionImpactInputs(
            on_steps=steps, off_steps=steps, on_status="completed", off_status="completed"
        ),
        judge,
    )
    # Behavioral axes the streams prove identical are still cross-checked to False...
    assert m.memory_changed_tool_choice is False
    assert m.memory_changed_plan is False
    # ...but unobservable output is the judge's call (not overridden).
    assert m.memory_changed_output is True
    assert m.memory_improved_verification is True


def test_judge_decides_outcome_axes_on_real_diff() -> None:
    on = (_step(0, "Read", inp={"path": "a.py"}), _step(1, "Edit", inp={"path": "a.py"}))
    off = (_step(0, "Bash", inp={"cmd": "pytest"}),)
    judge = StubComparativeJudge(
        fn=lambda _p: _verdict_json(
            memory_changed_tool_choice=True,
            memory_changed_plan=True,
            memory_prevented_known_failure=True,
            memory_improved_verification=True,
        )
    )
    m = score_action_impact(
        ActionImpactInputs(on_steps=on, off_steps=off, on_status="completed", off_status="failed"),
        judge,
    )
    assert m.memory_changed_tool_choice is True
    assert m.memory_changed_plan is True
    assert m.memory_prevented_known_failure is True
    assert m.memory_improved_verification is True


def test_cross_check_overrides_judge_on_identical_axis() -> None:
    # Same tool & args (plan/tool_choice identical) but a different OUTPUT, so the
    # judge is consulted — yet it must NOT be allowed to claim tool_choice/plan changed.
    on = (_step(0, "Bash", inp={"cmd": "ls"}, output={"stdout": "a"}),)
    off = (_step(0, "Bash", inp={"cmd": "ls"}, output={"stdout": "b"}),)
    rogue = StubComparativeJudge(
        fn=lambda _p: _verdict_json(
            memory_changed_tool_choice=True,  # contradicts identical streams
            memory_changed_plan=True,  # contradicts identical streams
            memory_changed_output=True,  # legitimately differs
        )
    )
    m = score_action_impact(ActionImpactInputs(on_steps=on, off_steps=off), rogue)
    assert m.memory_changed_tool_choice is False  # overridden
    assert m.memory_changed_plan is False  # overridden
    assert m.memory_changed_output is True  # honored — output genuinely differs


def test_malformed_judge_reply_raises() -> None:
    on = (_step(0, "Grep", inp={"q": "x"}),)
    off = (_step(0, "Read", inp={"q": "x"}),)
    judge = StubComparativeJudge(fn=lambda _p: "the model rambled with no json")
    with pytest.raises(ActionImpactJudgeError):
        score_action_impact(ActionImpactInputs(on_steps=on, off_steps=off), judge)
