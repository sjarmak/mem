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

from collections.abc import Collection

from membench.metrics.scorers import outcome_check_passes
from membench.schemas.sequence import ExpectedAction, OutcomeCheck, SequenceStep
from membench.schemas.trace import ToolCall

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
