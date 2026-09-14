"""The four-leg trial's observations: what each bd call did, and what the legs add up to.

Everything here is mechanical. A call's outcome is bd's own acknowledgement (``Remembered``,
``Updated``, ``(recalled``), its exit status, or the absence of any of those; which version a
call states is a token match against the task's current and superseded values. The trial
verdicts are a fixed reading of those per-leg observations, never a judgement about intent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from membench.runner import e1_reliability
from membench.runner.e1_reliability import (
    ACTION_SCORING_SINCE,
    CALL_OBSERVATION_SINCE,
    RELIABILITY_VERSION,
    BdCallObservation,
    BdLegEvidence,
    score_bd_leg,
    trial_outcomes,
)
from membench.runner.leg_plans import TRIAL_ROLES
from membench.runner.toolreq_corpus import superseded_values
from membench.schemas.trace import ToolCall
from tests.toolreq_helpers import corpus_one

# Stand-ins for the opaque tokens a real task carries; the scorer tests below read the real ones
# off the fixture task instead (``_versions``), the verdict tests never need a task.
CURRENT = "toolreq-current-nonce"
PREVIOUS = "toolreq-previous-nonce"


def _bash(
    command: str, result: str | None = None, *, error: bool = False, use: int = 0
) -> ToolCall:
    return ToolCall(
        name="Bash",
        arguments={"command": command},
        result=result,
        is_error=error,
        tool_use_id=f"call-{use}",
        tool_use_index=use,
        tool_result_index=use,
    )


def _versions(tmp_path: Path) -> tuple[Any, str, str]:
    """The fixture task with its current opaque value and the one it superseded."""
    _seqs, tasks = corpus_one(tmp_path)
    task = tasks[0]
    (current,) = task.current_opaque_values
    (previous,) = superseded_values(task)
    return task, current, previous


def _score(task: Any, calls: list[ToolCall], *, role: str, leg: int | None = None) -> BdLegEvidence:
    return score_bd_leg(
        task,
        calls,
        leg=TRIAL_ROLES.index(role) if leg is None else leg,
        role=role,
        status="ok",
        config_dir=None,
    )


def _observed(evidence: BdLegEvidence) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return [
        (call.verb, call.outcome, call.current_values, call.superseded_values)
        for call in evidence.bd_calls
    ]


def test_the_scorer_version_moved_and_action_scores_from_the_previous_version_still_count() -> None:
    """Bumping the version must not turn every paid v3 goal leg into an unknown: action and
    ordering scores have been sound since v3. The call observations moved to v5 because v4
    scored every redirected command (``bd recall k 2>&1``) as compound; v4 observations are
    not read."""
    assert RELIABILITY_VERSION == 5
    assert ACTION_SCORING_SINCE == 3
    assert CALL_OBSERVATION_SINCE == 5
    v3 = BdLegEvidence(
        scoring_version=3,
        leg=1,
        role="goal",
        status="ok",
        goal_action_success=True,
        bd_recall_before_action=True,
    )
    assert e1_reliability._bd_component(v3, "bd_recall_before_action") is True
    v2 = v3.model_copy(update={"scoring_version": 2})
    assert e1_reliability._bd_component(v2, "bd_recall_before_action") is None


def test_each_bd_call_is_observed_with_its_acknowledgement_and_the_version_it_states(
    tmp_path: Path,
) -> None:
    task, current, previous = _versions(tmp_path)
    recalled = f'(recalled "window" -- a bare existing key READS.)\n{current}'
    calls = [
        _bash(f'bd remember --key window "{previous}"', f"Remembered [window]: {previous}", use=0),
        _bash(f'bd remember --key window "{current}"', f"Updated [window]: {current}", use=1),
        _bash("bd recall window", current, use=2),
        _bash('bd memories "window"', 'No memories matching "window"\n\n', use=3),
        _bash("bd remember list", 'Error: "list" looks like a command', error=True, use=4),
        _bash(f'bd remember "{previous}"', None, use=5),
        _bash("bd remember window", recalled, use=6),
    ]
    evidence = _score(task, calls, role="revise")
    assert evidence.scoring_version == RELIABILITY_VERSION
    assert _observed(evidence) == [
        ("remember", "remembered", (), (previous,)),
        ("remember", "updated", (current,), ()),
        ("recall", "returned", (current,), ()),
        ("memories", "empty", (), ()),
        ("remember", "refused", (), ()),
        ("remember", "unacknowledged", (), (previous,)),
        ("remember", "recalled", (current,), ()),
    ]
    assert [call.position for call in evidence.bd_calls] == list(range(7))
    assert [call.key for call in evidence.bd_calls[:3]] == ["window", "window", ""]
    assert evidence.bd_calls[1].tool_use_index == 1
    assert evidence.bd_accepted_writes == 2
    assert evidence.bd_capture_complete is True


def test_a_compound_shell_call_leaves_every_invocation_unattributed(tmp_path: Path) -> None:
    """The frozen scorer credits no acceptance and no payload to a chained command, because one
    tool_result cannot say which command printed what. The observations agree with the counts:
    each write still reports the value it OFFERED, since that is read off its own argv."""
    task, current, previous = _versions(tmp_path)
    compound = _bash(
        f'bd remember --key a "{current}"; bd remember --key b "{previous}"; bd recall a',
        f"Remembered [a]: {current}\nUpdated [b]: {previous}\n{current}",
    )
    evidence = _score(task, [compound], role="revise")
    assert _observed(evidence) == [
        ("remember", "unattributed", (current,), ()),
        ("remember", "unattributed", (), (previous,)),
        ("recall", "unattributed", (), ()),
    ]
    assert [call.tool_use_index for call in evidence.bd_calls] == [0, 0, 0]
    assert evidence.bd_accepted_writes == 0
    assert evidence.bd_unattributed_write_blocks == 1


def test_a_recall_that_returns_a_superseded_value_is_flagged_on_the_leg(tmp_path: Path) -> None:
    task, current, previous = _versions(tmp_path)
    stale = _score(task, [_bash("bd recall window", previous)], role="goal")
    assert stale.bd_recall_states_superseded is True
    assert stale.bd_recall_complete is False
    listing = f'Memories matching "win":\n\n  k\n    {current}\n  j\n    {previous}\n'
    both = _score(task, [_bash("bd memories win", listing)], role="goal")
    assert both.bd_recall_states_superseded is True
    assert both.bd_recall_complete is True
    fresh = _score(task, [_bash("bd recall window", current)], role="goal")
    assert fresh.bd_recall_states_superseded is False
    # A superseded value the agent WROTE is not a stale recall.
    wrote = _score(
        task, [_bash(f'bd remember "{previous}"', f"Remembered [k]: {previous}")], role="goal"
    )
    assert wrote.bd_recall_states_superseded is False


def test_evidence_without_call_observations_still_validates_as_legacy() -> None:
    legacy = BdLegEvidence.model_validate({"leg": 0, "role": "establish", "status": "ok"})
    assert legacy.bd_calls == ()
    assert legacy.bd_recall_states_superseded is False
    assert legacy.scoring_version == 0


def _leg(role: str, calls: list[BdCallObservation], **fields: Any) -> BdLegEvidence:
    return BdLegEvidence(
        scoring_version=RELIABILITY_VERSION,
        leg=TRIAL_ROLES.index(role),
        role=role,  # type: ignore[arg-type]
        status="ok",
        bd_calls=tuple(calls),
        **fields,
    )


def _call(
    position: int, verb: str, outcome: str, *, current: bool = False, superseded: bool = False
) -> BdCallObservation:
    return BdCallObservation(
        position=position,
        tool_use_index=position,
        verb=verb,
        key="window",
        outcome=outcome,  # type: ignore[arg-type]
        current_values=(CURRENT,) if current else (),
        superseded_values=(PREVIOUS,) if superseded else (),
    )


def _by_role(**legs: BdLegEvidence) -> dict[str, BdLegEvidence]:
    return legs


def test_trial_outcomes_read_the_happy_path_off_the_four_legs() -> None:
    outcomes = trial_outcomes(
        _by_role(
            establish=_leg("establish", [_call(0, "remember", "remembered", superseded=True)]),
            revise=_leg(
                "revise",
                [
                    _call(0, "recall", "returned", superseded=True),
                    _call(1, "remember", "updated", current=True),
                ],
                bd_capture_complete=True,
            ),
            goal=_leg(
                "goal",
                [_call(0, "recall", "returned", current=True)],
                bd_recall_complete=True,
                goal_action_success=True,
            ),
            stale_writer=_leg("stale_writer", [_call(0, "remember", "updated", superseded=True)]),
        )
    )
    assert outcomes == {
        "capture_superseded": True,
        "revision": "updated_in_place",
        "revision_captured_current": True,
        "retrieval_current_only": True,
        "retrieval_states_superseded": False,
        "goal_action_success": True,
        "stale_write": "updated_in_place",
        "after_rejection": None,
    }


@pytest.mark.parametrize(
    ("calls", "revision"),
    [
        ([_call(0, "remember", "updated", current=True)], "updated_in_place"),
        ([_call(0, "remember", "remembered", current=True)], "wrote_beside"),
        ([_call(0, "remember", "remembered", superseded=True)], "wrote_without_current"),
        (
            [_call(0, "remember", "refused"), _call(1, "remember", "remembered", current=True)],
            "wrote_beside",
        ),
        ([_call(0, "recall", "returned", superseded=True)], "no_write"),
        ([_call(0, "remember", "refused", current=True)], "no_write"),
        ([], "no_write"),
        ([_call(0, "remember", "unattributed", current=True)], None),
    ],
)
def test_revision_is_classified_by_the_first_accepted_write_that_states_the_current_value(
    calls: list[BdCallObservation], revision: str | None
) -> None:
    outcomes = trial_outcomes(_by_role(revise=_leg("revise", calls)))
    assert outcomes["revision"] == revision


@pytest.mark.parametrize(
    ("calls", "stale_write", "after"),
    [
        ([_call(0, "remember", "updated", superseded=True)], "updated_in_place", None),
        ([_call(0, "remember", "remembered", superseded=True)], "wrote_beside", None),
        ([_call(0, "remember", "remembered", current=True)], "wrote_without_superseded", None),
        ([_call(0, "remember", "unacknowledged", superseded=True)], "unacknowledged", None),
        ([_call(0, "recall", "returned", current=True)], "no_write", None),
        ([_call(0, "remember", "recalled", current=True)], "no_write", None),
        ([], "no_write", None),
        ([_call(0, "remember", "unattributed", superseded=True)], None, None),
        ([_call(0, "remember", "refused", superseded=True)], "rejected", "stopped"),
        (
            [
                _call(0, "remember", "refused", superseded=True),
                _call(1, "recall", "returned", current=True),
            ],
            "rejected",
            "reread",
        ),
        (
            [
                _call(0, "remember", "refused", superseded=True),
                _call(1, "remember", "updated", superseded=True),
            ],
            "rejected",
            "retried",
        ),
        (
            [
                _call(0, "remember", "refused", superseded=True),
                _call(1, "recall", "returned", current=True),
                _call(2, "remember", "updated", current=True),
            ],
            "rejected",
            "reread",
        ),
    ],
)
def test_the_stale_write_is_classified_by_its_first_write_and_what_followed_a_rejection(
    calls: list[BdCallObservation], stale_write: str | None, after: str | None
) -> None:
    """Today's bd rejects nothing, so a baseline shows ``updated_in_place`` or ``wrote_beside``.
    After stale-update protection lands the same trial shows ``rejected``, and the leg's
    remaining calls say whether the agent re-read, retried blindly, or gave up."""
    outcomes = trial_outcomes(_by_role(stale_writer=_leg("stale_writer", calls)))
    assert outcomes["stale_write"] == stale_write
    assert outcomes["after_rejection"] == after


def test_missing_or_unmeasured_legs_leave_their_verdicts_unknown() -> None:
    outcomes = trial_outcomes({})
    assert outcomes == {
        "capture_superseded": None,
        "revision": None,
        "revision_captured_current": None,
        "retrieval_current_only": None,
        "retrieval_states_superseded": None,
        "goal_action_success": None,
        "stale_write": None,
        "after_rejection": None,
    }
    timed_out = _leg("stale_writer", [_call(0, "remember", "updated", superseded=True)]).model_copy(
        update={"status": "timeout"}
    )
    assert trial_outcomes(_by_role(stale_writer=timed_out))["stale_write"] is None
    legacy = _leg("goal", [], bd_recall_complete=True).model_copy(update={"scoring_version": 3})
    partial = trial_outcomes(_by_role(goal=legacy))
    assert partial["retrieval_current_only"] is None
    assert partial["retrieval_states_superseded"] is None


def test_retrieval_current_only_needs_the_current_value_and_no_superseded_one() -> None:
    clean = _leg("goal", [_call(0, "recall", "returned", current=True)], bd_recall_complete=True)
    assert trial_outcomes(_by_role(goal=clean))["retrieval_current_only"] is True
    mixed = _leg(
        "goal",
        [_call(0, "memories", "returned", current=True, superseded=True)],
        bd_recall_complete=True,
        bd_recall_states_superseded=True,
    )
    outcomes = trial_outcomes(_by_role(goal=mixed))
    assert outcomes["retrieval_current_only"] is False
    assert outcomes["retrieval_states_superseded"] is True
    none = _leg("goal", [], bd_recall_complete=False)
    assert trial_outcomes(_by_role(goal=none))["retrieval_current_only"] is False


def test_a_leg_whose_role_disagrees_with_its_slot_is_refused() -> None:
    with pytest.raises(ValueError, match="role"):
        trial_outcomes({"goal": _leg("stale_writer", [])})
