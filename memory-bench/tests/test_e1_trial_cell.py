"""mem-605zn — the four-leg trial: establish, revise, goal, stale_writer on ONE store.

The two-leg pair measures whether a fact written in one session reaches the next. The Beads
version-history thread asks four more things of an agent, each of which needs a session of its
own: does it capture a fact, does it revise the fact when told the fact changed, does the CURRENT
version reach a fresh session that must act on it, and what does a session still primed on the
OLD version do when it tries to write. Every test here pins a property that makes those four legs
one trial rather than four unrelated calls: one store and one cwd across all four, the cwd wiped
between every pair of neighbours, the previous version derived POSITIONALLY off the goal action's
own supersession chain (never typed into a fixture), the goal leg byte-identical to the pair's,
and the three non-goal legs byte-identical across a twin pair.

Nothing here spends anything: every cell runs against an injected runner.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from membench.runner import e1_grid
from membench.runner.e1_grid import LEG_ROLES, LegRecord, RungCell
from membench.runner.headless_agent import result_event, serialize_stream
from membench.runner.leg_plans import LEG_PLANS, PAIR_ROLES, TRIAL_ROLES
from membench.runner.toolreq_corpus import (
    CONTEXT_HEADING,
    PREVIOUS_HEADING,
    established_context,
    prior_context,
    prior_values,
    unnecessary_twin,
)
from membench.runner.toolreq_realagent import ToolReqRealAgentTask
from membench.schemas.sequence import ExpectedAction
from tests.toolreq_helpers import corpus_one, multi_value_corpus

MODEL = "claude-test-model-1"


def _prompt(argv: Any) -> str:
    """The prompt a leg was spawned with: ``claude -p <prompt> ...``."""
    args = [str(arg) for arg in argv]
    return args[args.index("-p") + 1]


def _prompt_runner(seen: list[dict[str, str]]) -> Any:
    """Records the prompt, the cwd and the pinned config dir of every leg; makes no tool call."""

    def runner(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env") or {}
        seen.append(
            {
                "prompt": _prompt(argv),
                "cwd": str(kwargs.get("cwd", "")),
                "config": str(env.get("CLAUDE_CONFIG_DIR", "")),
            }
        )
        return subprocess.CompletedProcess(list(argv), 0, serialize_stream([result_event()]), "")

    return runner


def _action(task: ToolReqRealAgentTask) -> ExpectedAction:
    return task.goal_step.outcome_checks[0].requires_action[0]


def _with_forbidden(task: ToolReqRealAgentTask, forbidden: list[str]) -> ToolReqRealAgentTask:
    """The same task with a different supersession chain under its goal action."""
    check = task.goal_step.outcome_checks[0]
    action = check.requires_action[0].model_copy(update={"forbidden_values": forbidden})
    step = task.goal_step.model_copy(
        update={"outcome_checks": [check.model_copy(update={"requires_action": [action]})]}
    )
    return replace(task, goal_step=step)


def _cell(
    task: ToolReqRealAgentTask, runner: Any, *, plan: tuple[str, ...], repeats: int = 1
) -> tuple[RungCell, list]:
    legs: list[LegRecord] = []
    cell = e1_grid.run_rung_cell(
        task,
        rung="R4",
        repeats=repeats,
        model=MODEL,
        dry_run=False,
        runner=runner,
        on_leg=legs.append,
        leg_plan=plan,
    )
    return cell, legs


def _trial(task: ToolReqRealAgentTask, runner: Any, *, repeats: int = 1) -> tuple[RungCell, list]:
    return _cell(task, runner, plan=TRIAL_ROLES, repeats=repeats)


def test_the_plans_are_named_and_the_pair_is_the_one_the_ladder_already_runs() -> None:
    """Adding a plan must not move the ladder: ``LEG_ROLES`` IS the pair plan, by identity."""
    assert LEG_PLANS == {"pair": PAIR_ROLES, "trial": TRIAL_ROLES}
    assert PAIR_ROLES == ("establish", "goal") == LEG_ROLES
    assert TRIAL_ROLES == ("establish", "revise", "goal", "stale_writer")


def test_prior_values_pairs_each_current_value_with_its_immediate_predecessor(
    tmp_path: Path,
) -> None:
    """Read off the goal action, never a fixture: ``forbidden_values`` is the chain minus its
    head, laid out per current value, so the LAST forbidden value under each current one is the
    version the current one replaced."""
    _seqs, tasks = corpus_one(tmp_path)
    action = _action(tasks[0])
    prior = prior_values(tasks[0])
    assert prior == {action.arg_values[0]: action.forbidden_values[-1]}
    assert set(prior) == set(tasks[0].current_opaque_values)
    assert not set(prior.values()) & set(prior)

    _seqs, multi = multi_value_corpus(tmp_path / "multi")
    action = _action(multi[0])
    assert prior_values(multi[0]) == {
        action.arg_values[0]: action.forbidden_values[0],
        action.arg_values[1]: action.forbidden_values[1],
    }


def test_prior_values_reads_a_deeper_chain_positionally(tmp_path: Path) -> None:
    """The generated corpus supersedes each fact three deep (``SUPERSESSION_DEPTH``): two
    forbidden values per current one, oldest first. The predecessor is the LAST, not the first."""
    _seqs, tasks = corpus_one(tmp_path)
    task = _with_forbidden(tasks[0], ["toolreq-v1", "toolreq-v2"])
    assert prior_values(task) == {_action(task).arg_values[0]: "toolreq-v2"}


def test_prior_values_refuses_a_task_with_no_predecessor_or_a_ragged_chain(
    tmp_path: Path,
) -> None:
    """No superseded value means no version to be stale on: the trial would degrade into a pair
    with two extra legs and report a stale write that could not have happened. A chain that does
    not divide evenly over the current values has no positional predecessor either."""
    _seqs, tasks = corpus_one(tmp_path)
    with pytest.raises(ValueError, match="superseded"):
        prior_values(_with_forbidden(tasks[0], []))
    _seqs, multi = multi_value_corpus(tmp_path / "multi")
    with pytest.raises(ValueError, match="ragged"):
        prior_values(_with_forbidden(multi[0], ["a", "b", "c"]))


def test_prior_context_restates_the_block_with_the_previous_values(tmp_path: Path) -> None:
    """Same lines, same order, same labels; only the scored value moves back one version. Both
    halves of the twin render it identically because both render it off the same block."""
    _seqs, tasks = corpus_one(tmp_path)
    necessary = tasks[0]
    twin = unnecessary_twin(necessary)
    prior = prior_context(necessary)
    assert prior == prior_context(twin)
    assert prior.splitlines()[0] == CONTEXT_HEADING
    assert len(prior.splitlines()) == len(established_context(necessary).splitlines())
    (current, previous), *_rest = prior_values(necessary).items()
    assert previous in prior and current not in prior
    assert current in established_context(necessary) and previous not in established_context(
        necessary
    )
    relabelled = prior_context(necessary, heading=PREVIOUS_HEADING)
    assert relabelled.splitlines()[0] == PREVIOUS_HEADING
    assert relabelled.splitlines()[1:] == prior.splitlines()[1:]


def test_a_trial_spends_four_legs_on_one_store_and_one_cwd(tmp_path: Path) -> None:
    """The property that makes a revision and a stale write OBSERVABLE: every leg opens the store
    the previous legs wrote to. A repeat still gets its own store and sandbox."""
    _seqs, tasks = corpus_one(tmp_path)
    seen: list[dict[str, str]] = []
    cell, _legs = _trial(tasks[0], _prompt_runner(seen), repeats=2)
    assert len(seen) == 2 * len(TRIAL_ROLES)
    assert cell.runs == 2 * len(TRIAL_ROLES)
    assert cell.leg_plan == TRIAL_ROLES
    first, second = seen[: len(TRIAL_ROLES)], seen[len(TRIAL_ROLES) :]
    assert len({(leg["cwd"], leg["config"]) for leg in first}) == 1
    assert len({(leg["cwd"], leg["config"]) for leg in second}) == 1
    assert first[0]["cwd"] != second[0]["cwd"]
    assert first[0]["config"] != second[0]["config"]


def test_every_trial_leg_is_recorded_under_its_role_and_position(tmp_path: Path) -> None:
    _seqs, tasks = corpus_one(tmp_path)
    _cell, legs = _trial(tasks[0], _prompt_runner([]), repeats=2)
    assert [leg.role for leg in legs] == list(TRIAL_ROLES) * 2
    assert [leg.leg for leg in legs] == list(range(8))
    assert len({leg.filename for leg in legs}) == 8
    assert all(leg.bd_evidence is not None and leg.bd_evidence.role == leg.role for leg in legs)


def test_the_trial_prompts_state_the_version_each_session_should_know(tmp_path: Path) -> None:
    """Establish knows v1 as the current state. Revise is told v1 gave way to v2. Goal gets the
    prompt a PAIR's goal leg is spawned with, byte for byte, so its result is comparable to the
    pair's. Stale writer is a session that only ever knew v1."""
    _seqs, tasks = corpus_one(tmp_path)
    task = tasks[0]
    (current, previous), *_rest = prior_values(task).items()
    seen: list[dict[str, str]] = []
    _trial(task, _prompt_runner(seen))
    establish, revise, goal, stale = (leg["prompt"] for leg in seen)
    seen_pair: list[dict[str, str]] = []
    _cell(task, _prompt_runner(seen_pair), plan=PAIR_ROLES)
    pair_goal = seen_pair[PAIR_ROLES.index("goal")]["prompt"]

    assert e1_grid.ESTABLISH_INSTRUCTION in establish
    assert previous in establish and current not in establish

    assert e1_grid.REVISE_INSTRUCTION in revise
    assert previous in revise and current in revise
    assert revise.index(PREVIOUS_HEADING) < revise.index(CONTEXT_HEADING)
    assert revise.index(previous) < revise.index(current)

    assert goal == pair_goal
    assert e1_grid.rung_step(task, "R4").user_request in goal
    assert current not in goal and previous not in goal

    assert e1_grid.STALE_WRITER_INSTRUCTION in stale
    assert previous in stale and current not in stale
    assert stale != establish


def test_the_non_goal_trial_legs_are_byte_identical_across_a_twin_pair(tmp_path: Path) -> None:
    """The contrast, preserved for the trial: the only thing that differs between the halves is
    whether the GOAL prompt restates the current value."""
    _seqs, tasks = corpus_one(tmp_path)
    necessary = tasks[0]
    twin = unnecessary_twin(necessary)
    seen_necessary: list[dict[str, str]] = []
    seen_twin: list[dict[str, str]] = []
    _trial(necessary, _prompt_runner(seen_necessary))
    _trial(twin, _prompt_runner(seen_twin))
    prompts_necessary = [leg["prompt"] for leg in seen_necessary]
    prompts_twin = [leg["prompt"] for leg in seen_twin]
    goal = TRIAL_ROLES.index("goal")
    for index in range(len(TRIAL_ROLES)):
        if index == goal:
            assert prompts_necessary[index] != prompts_twin[index]
        else:
            assert prompts_necessary[index] == prompts_twin[index], TRIAL_ROLES[index]


def test_a_task_with_no_predecessor_is_refused_before_any_leg_is_spent(tmp_path: Path) -> None:
    _seqs, tasks = corpus_one(tmp_path)
    task = _with_forbidden(tasks[0], [])

    def spend(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        pytest.fail("a leg was spent on a task the trial cannot prime")

    with pytest.raises(ValueError, match="superseded"):
        _trial(task, spend)


def test_an_unknown_plan_is_refused(tmp_path: Path) -> None:
    _seqs, tasks = corpus_one(tmp_path)
    with pytest.raises(ValueError, match="plan"):
        e1_grid.cell_steps(tasks[0], "R4", plan=("establish", "bogus"))
    with pytest.raises(ValueError, match="plan"):
        e1_grid.run_rung_cell(
            tasks[0],
            rung="R4",
            repeats=1,
            model=MODEL,
            dry_run=False,
            runner=_prompt_runner([]),
            leg_plan=("goal",),
        )


SCAVENGED = "the retention window is toolreq-carried-value"


def _cwd_dropping_runner(seen: list[list[str]], read: list[str]) -> Any:
    def runner(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cwd = Path(str(kwargs.get("cwd")))
        seen.append(sorted(p.name for p in cwd.iterdir()))
        loaded = cwd / "CLAUDE.md"
        read.append(loaded.read_text(encoding="utf-8") if loaded.exists() else "")
        loaded.write_text(SCAVENGED)
        return subprocess.CompletedProcess(list(argv), 0, serialize_stream([result_event()]), "")

    return runner


def test_the_cwd_is_closed_between_every_pair_of_trial_legs(tmp_path: Path) -> None:
    """Four legs, three wipes. A leg that dropped the value in ``CLAUDE.md`` must not hand it to
    ANY later leg; the store is the only channel between neighbours."""
    _seqs, tasks = corpus_one(tmp_path)
    seen: list[list[str]] = []
    read: list[str] = []
    _trial(tasks[0], _cwd_dropping_runner(seen, read))
    assert len(seen) == len(TRIAL_ROLES)
    assert all(names == ["AGENTS.md", "CLAUDE.md"] for names in seen)
    assert all(SCAVENGED not in text for text in read[1:])
    assert len(set(read)) == 1


@pytest.mark.parametrize("rung", list(e1_grid.RUNG_IDS))
def test_the_revise_and_stale_writer_legs_carry_no_memory_wording_of_their_own(
    rung: str, tmp_path: Path
) -> None:
    """The ONLY memory wording in any leg is the ladder's clause; a floor rung's revise leg says
    nothing about remembering anything, which is what keeps it a floor."""
    _seqs, tasks = corpus_one(tmp_path)
    guidance = e1_grid.guidance_block(rung).lower()
    for step in (e1_grid.revise_step(tasks[0], rung), e1_grid.stale_writer_step(tasks[0], rung)):
        request = step.user_request
        assert e1_grid.guidance_block(rung) in request or not guidance
        for word in ("memory", "remember", "recall", "record"):
            assert (word in request.lower()) == (word in guidance), (step.step_id, word)


def test_the_cell_row_carries_its_plan_and_a_legacy_row_reads_as_a_pair(tmp_path: Path) -> None:
    """An analyzer reading a trial artifact must know it holds four legs per repeat; a row written
    before plans existed holds pairs, and says so by default rather than by guess."""
    _seqs, tasks = corpus_one(tmp_path)
    cell, _legs = _trial(tasks[0], _prompt_runner([]))
    row = cell.row()
    assert row["leg_plan"] == list(TRIAL_ROLES)
    assert RungCell.from_row(row).leg_plan == TRIAL_ROLES
    legacy = {key: value for key, value in row.items() if key != "leg_plan"}
    assert RungCell.from_row(legacy).leg_plan == PAIR_ROLES
