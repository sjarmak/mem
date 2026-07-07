"""ScriptedAgent memory-gated tool action (mem-31vl).

The reference agent invokes a required tool with its recalled content as the
argument, so a superseded value that rode into recall fails the action — the tool
path, not the text answer, is what a naive arm fails on. No model, no Docker.
"""

from membench.runner.agent import ScriptedAgent
from membench.runtime import IdClock, StepContext
from membench.schemas.sequence import ExpectedAction, OutcomeCheck, SequenceStep


def _ctx() -> StepContext:
    return StepContext(trial_id="t", session_id="s", step_id="goal", clock=IdClock())


def _goal_step() -> SequenceStep:
    # Goal requires calling `apply_config` with the CURRENT value as its argument;
    # the superseded value "30s" is forbidden INSIDE the tool argument. The text
    # forbidden clause is left empty, so the action is the sole reward-bearing channel.
    return SequenceStep(
        step_id="goal",
        user_request="apply the current timeout",
        available_tools=["apply_config"],
        expected_memory_reads=["cfg-current"],
        outcome_checks=[
            OutcomeCheck(
                check_id="goal",
                requires_memory=["cfg-current"],
                requires_action=[
                    ExpectedAction(
                        tool="apply_config", arg_values=["45s"], forbidden_values=["30s"]
                    )
                ],
            )
        ],
    )


def test_scripted_agent_passes_when_recall_is_clean_current() -> None:
    res = ScriptedAgent().run_step(
        _goal_step(), {"cfg-current": "the timeout is 45s — by Ada"}, _ctx()
    )
    assert res.check_results["goal"] is True
    # the recalled value actually rode into the tool argument
    assert any(
        call.name == "apply_config" and "45s" in " ".join(map(str, call.arguments.values()))
        for call in res.tool_calls
    )


def test_scripted_agent_fails_when_stale_version_contaminates_recall() -> None:
    # A naive arm surfaces BOTH the current and the superseded version; the stale
    # value lands in the tool argument and fails the action.
    memory = {
        "cfg-current": "the timeout is 45s — by Ada",
        "cfg-v1": "the timeout is 30s — by Ada",
    }
    res = ScriptedAgent().run_step(_goal_step(), memory, _ctx())
    assert res.check_results["goal"] is False


def test_scripted_agent_fails_with_no_memory() -> None:
    res = ScriptedAgent().run_step(_goal_step(), {}, _ctx())
    assert res.check_results["goal"] is False
