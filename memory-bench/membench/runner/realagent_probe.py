"""Real-agent tool-requiring probe (mem-rk41.4) — the store-free building blocks.

De-risk question, cheaply, BEFORE building the synthetic-world->TaskBundle adapter:
does a REAL ``claude -p`` agent need surfaced memory to drive a TOOL action carrying
the current (non-superseded) value of a fact it was never shown in-context?

The tool-requiring shape in ``generators.enterprise_workflow`` keys its goal on a
synthetic ``apply_config`` tool — which a real agent never emits (it has Bash/Write/
Edit, not synthetic tools). This module bridges the shape to a REAL tool (``Write`` a
config file) so the same ``metrics.scorers.outcome_check_passes`` grader applies to a
real agent's parsed ``tool_calls``. Two arms differ ONLY by what memory the prompt
carries (see ``scripts/probe_realagent_toolreq.py``):

* ``none``   — empty surfaced memory. The agent is never shown the value, so it can
  only Write it via a prompt leak or a lucky hallucination. Opaque authored values
  make both vanishingly unlikely, so ``none`` acts as a leak detector.
* ``oracle`` — the id-exact CEILING: the current value is surfaced (store-free, from
  the step's own authored memory). Tests whether a real agent can USE perfect memory
  to drive the real tool with the current value and not the stale one.

Decision (a necessity + usability ceiling gate): ``none`` fails (no leak) AND
``oracle`` passes (real agent drives the tool with the current value) => the substrate
can separate memory quality under a real agent => the adapter is worth building. If the
oracle CEILING cannot make a real agent pass, no real substrate will => kill the path.
``builtin`` (native memory) is deliberately out of scope here: it needs a persistent
config-dir / session-continuity environment the stateless headless path does not
provide (that IS the adapter cost this probe gates).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Collection
from dataclasses import dataclass

from membench.metrics.scorers import outcome_check_passes
from membench.runner.headless_agent import (
    CellCalls,
    MemoryChannel,
    RecordingRunner,
    cell_agent,
    one_cycle,
)
from membench.runtime import StepContext
from membench.schemas.sequence import ExpectedAction, OutcomeCheck, SequenceStep
from membench.schemas.trace import ToolCall
from membench.spawn import Runner

# The real Claude Code tool the abstract goal is bridged onto, and the file it writes.
REAL_TOOL = "Write"
CONFIG_FILE = "config.json"

# Opaque authored values: unguessable tokens so a ``none``-arm agent cannot pass by
# hallucinating a plausible value — only a genuine prompt leak would surface these.
DEFAULT_SUBJECT = "production deploy region"
DEFAULT_CURRENT_VALUE = "region-rk41-CURRENT-7f3a9c"
DEFAULT_STALE_VALUE = "region-rk41-STALE-1b9e42"
DEFAULT_CURRENT_ID = "probe-region-v2-current"


def build_probe_step(
    *,
    subject: str = DEFAULT_SUBJECT,
    current_value: str = DEFAULT_CURRENT_VALUE,
    stale_value: str = DEFAULT_STALE_VALUE,
    current_id: str = DEFAULT_CURRENT_ID,
    step_id: str = "probe-goal",
) -> SequenceStep:
    """A goal step whose success REQUIRES a real ``Write`` call carrying ``current_value``
    and never ``stale_value`` — the tool-requiring shape bridged onto a real tool."""
    return SequenceStep(
        step_id=step_id,
        user_request=(
            f"Using the {REAL_TOOL} tool, write the current {subject} to a file named "
            f"{CONFIG_FILE} in the current directory. Write only the value, nothing else."
        ),
        available_tools=[REAL_TOOL],
        expected_memory_reads=[current_id],
        outcome_checks=[
            OutcomeCheck(
                check_id=f"{step_id}-goal-check",
                description=(
                    f"goal requires a {REAL_TOOL} call carrying the current {subject} "
                    "and never the superseded value"
                ),
                requires_memory=[current_id],
                requires_action=[
                    ExpectedAction(
                        tool=REAL_TOOL,
                        arg_values=[current_value],
                        forbidden_values=[stale_value],
                    )
                ],
            )
        ],
    )


def oracle_memory(
    *,
    subject: str = DEFAULT_SUBJECT,
    current_value: str = DEFAULT_CURRENT_VALUE,
    current_id: str = DEFAULT_CURRENT_ID,
) -> dict[str, str]:
    """The id-exact CEILING arm's surfaced memory: only the current value (the naive
    arm would also surface the stale one — that is the offline discrimination gate's
    job, not this real-agent ceiling probe)."""
    return {current_id: f"the current {subject} is {current_value}"}


def score_goal_action(
    step: SequenceStep,
    *,
    tool_calls: Collection[ToolCall],
    final_answer: str = "",
) -> bool:
    """Grade the goal purely on the ACTION a real agent took — did some single Write
    call carry the current value and no Write call carry the stale one.

    Reuses ``outcome_check_passes`` but bypasses its memory-availability gate (passes
    the check's own required ids as available) ON PURPOSE: the arm difference lives in
    the PROMPT (what memory the agent was shown), not in the grader. This keeps the
    ``none`` arm a genuine leak detector — it can still "pass" if the value leaked into
    the response, which is exactly the signal the kill branch watches for."""
    if not step.outcome_checks:
        raise ValueError("probe step has no outcome_checks to score")
    check = step.outcome_checks[0]
    return outcome_check_passes(
        check,
        available_ids=set(check.requires_memory),
        stated_text=final_answer,
        tool_calls=tool_calls,
    )


# --- arm execution (shared by the probe and the mem-rk41.3 corpus driver) --------------
#
# Extracted here (from ``scripts/probe_realagent_toolreq.py``) so the single hardcoded
# probe and the whole-corpus none/oracle driver run the SAME arm loop, simulated runner,
# and scorer — one code path, no drift. The one generalization the corpus needs: the
# simulated runner keys on a SET of current values (a multi-subject goal), not one.


@dataclass(frozen=True)
class ArmOutcome:
    """One (arm, channel) cell: how many of ``runs`` scored a passing goal action."""

    arm: str
    channel: str
    passes: int
    runs: int


def simulated_runner(current_values: Collection[str]) -> Runner:
    """A ``Runner`` stand-in that SIMULATES an honest memory-copying agent: it Writes
    the current value(s) iff EVERY one appears in the prompt (i.e. the arm surfaced them),
    else it makes no tool call. This proves the arm wiring + external scorer discriminate
    end to end offline — it is NOT a measurement of real agent behaviour."""
    values = list(current_values)

    def run(argv: Collection[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        argv_list = list(argv)
        # HeadlessClaudeAgent.argv_for builds ["claude", "-p", prompt, ...]; assert the layout
        # so a future reordering fails loudly instead of silently never firing.
        assert argv_list[:2] == ["claude", "-p"], f"unexpected argv layout: {argv_list[:3]}"
        prompt = argv_list[2] if len(argv_list) > 2 else ""
        events: list[dict[str, object]] = []
        if values and all(value in prompt for value in values):
            events.append(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": REAL_TOOL,
                                "input": {"file_path": CONFIG_FILE, "content": " ".join(values)},
                            }
                        ],
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                }
            )
        events.append({"type": "result", "result": "done"})
        stdout = "\n".join(json.dumps(event) for event in events)
        return subprocess.CompletedProcess(argv_list, returncode=0, stdout=stdout, stderr="")

    return run


def run_arm(
    *,
    arm: str,
    step: SequenceStep,
    memory: dict[str, str],
    channel: MemoryChannel,
    repeats: int,
    model: str,
    dry_run: bool,
    current_values: Collection[str],
) -> tuple[ArmOutcome, CellCalls]:
    """Run one (arm, channel) cell ``repeats`` times, score each goal action externally, and return
    the cycle of ``claude -p`` invocations it ACTUALLY made alongside the score.

    ``memory`` is the surfaced memory the arm shows the agent ({} for ``none``, the id-exact
    current value(s) for ``oracle``); the arm difference lives entirely in that dict, never
    in the scorer. ``dry_run`` swaps the real ``claude -p`` for ``simulated_runner`` (no
    token). A fresh neutral sandbox per repeat keeps ``config.json`` from bleeding across
    trials and keeps the agent out of any mem worktree.

    The invocations are RECORDED off the CLI seam (``RecordingRunner``), not reported by the
    agent: the runner sees every call this cell spawns whether or not anyone declared it, which
    is what a ``prompt`` field on the step result could not do (see ``RecordingRunner``). The
    cache checks them against the identity at its write boundary, so a cell cannot be published
    under a fingerprint describing a command line it never sent."""
    recorder = RecordingRunner(simulated_runner(current_values) if dry_run else subprocess.run)
    passes = 0
    for i in range(repeats):
        with tempfile.TemporaryDirectory(prefix=f"toolreq-{arm}-") as sandbox:
            agent = cell_agent(model=model, channel=channel, runner=recorder, cwd=sandbox)
            ctx = StepContext(
                trial_id=f"{arm}-{channel.value}-{i}",
                session_id=f"{arm}-{channel.value}",
                step_id=step.step_id,
            )
            result = agent.run_step(step, dict(memory), ctx)
        if score_goal_action(step, tool_calls=result.tool_calls, final_answer=result.final_answer):
            passes += 1
    outcome = ArmOutcome(arm=arm, channel=channel.value, runs=repeats, passes=passes)
    return outcome, one_cycle(recorder.calls, repeats=repeats, arm=arm, channel=channel)
