"""§11 memory-necessity gate — run the pilot, then judge it.

``pilot_filter`` decides admission from two rewards but, by design, leaves
"running the pilot is the caller's job". This module is that caller: it runs a
generated ``BenchmarkSequence`` under the two conditions the task-validity gate
compares — NO_MEMORY and ORACLE_MEMORY — aggregates each arm's mean reward with
the same ``summarize_trials`` summary reporting uses, and feeds the pair to
``pilot_filter``. The result is one reusable admission call any generator (the
authored blueprints today, NeMo-materialised worlds later) can gate on.

Two scopes, one decision (mem-r6yzk B1):

* ``memory_necessity_gate`` pilots ONE sequence under ``run_sequence``, an isolated
  store. That is the right scope for a session-tier task, whose whole answer was
  written inside its own steps.
* ``project_necessity_gate`` pilots a sequence under ``run_project`` ALONGSIDE the
  earlier sequences of its world, so the oracle arm can be handed memory an earlier
  SESSION wrote. Without it a cross-session task is rejected for being exactly what
  it is: the oracle arm never sees the sibling's shared decision either, so oracle ties
  no_memory and the gate reports "does not discriminate" about a task that
  discriminates perfectly once the store spans the world.

``necessity_gate`` dispatches between the two on a ``GateCandidate``'s context.

Both score the GRADED steps only. An establishing step carries no outcome check and
scores 0.0 in every arm, so averaging over the whole sequence divides the real delta
by the step count: a 21-step sequence was rejected for LENGTH under a reason string
that blamed non-discrimination (mem-r6yzk B1 item 4). The graded steps are the only
place a memory difference can be observed, so they are the only place it is measured,
and ``PilotVerdict.scope`` records which set the means came from.

MEMORY_ENABLED is deliberately not run: necessity is a property of the task
(does ground-truth memory beat no memory?), independent of any arm under test.
The decision reuses ``EPSILON`` so generation and reporting agree on "beats".
No model is called — the reference ``ScriptedAgent`` (the runner's default)
makes the pilot deterministic and CI-safe.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from membench.generators.pilot_filter import PilotVerdict, pilot_filter
from membench.report.comparison import EPSILON, summarize_trials
from membench.runner.agent import Agent
from membench.runner.conditions import StepTrial, run_sequence
from membench.runner.project import run_project
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.sequence import BenchmarkSequence

# The two conditions the necessity decision compares (DIV-3). MEMORY_ENABLED is
# the arm under test, not part of whether the task *requires* memory.
_NECESSITY_CONDITIONS = (Condition.NO_MEMORY, Condition.ORACLE_MEMORY)


@dataclass(frozen=True)
class NecessityResult:
    """One sequence's admission decision. ``verdict`` carries the rewards, delta,
    epsilon, scope and reason from ``pilot_filter``; ``sequence_id`` names the task so a
    batch run is traceable."""

    sequence_id: str
    verdict: PilotVerdict


@dataclass(frozen=True)
class GateCandidate:
    """A sequence to gate, plus the earlier sequences of its world that a pilot must
    replay first for the sequence's answer to be reachable at all.

    An empty ``context`` means "answerable inside its own scope" and selects the
    isolated pilot; a non-empty one selects the shared-store project pilot. The context
    is supplied by the caller that knows the corpus layout — ``necessity_sweep`` derives
    it — never guessed here from a tier string."""

    sequence: BenchmarkSequence
    context: tuple[BenchmarkSequence, ...] = ()


@dataclass(frozen=True)
class _ArmRewards:
    """The two arm means a decision is made on, and the phrase naming what they cover."""

    oracle: float
    no_memory: float
    scope: str


def _necessity_experiment(seq: BenchmarkSequence) -> ExperimentConfig:
    """A minimal two-condition experiment for the pilot. The memory system is left
    at the ``none`` default — ORACLE injects ground truth and NO_MEMORY withholds
    it, so neither arm needs the system under test."""
    return ExperimentConfig(
        experiment_id=f"necessity-{seq.sequence_id}",
        agent=AgentConfig(agent_config_id="scripted-ref"),
        memory=MemoryConfig(memory_config_id="necessity-none"),
        dataset_id="synthetic",
        conditions=list(_NECESSITY_CONDITIONS),
    )


def _graded_step_ids(seq: BenchmarkSequence) -> frozenset[str]:
    """The steps a memory difference can show up in: the ones carrying an outcome check.

    Raises when a sequence has none. A task with nothing graded cannot discriminate
    anything, and reporting a 0.000 delta for it would dress a malformed candidate up as
    a measured non-discrimination verdict."""
    graded = frozenset(step.step_id for step in seq.steps if step.outcome_checks)
    if not graded:
        raise ValueError(
            f"sequence {seq.sequence_id!r} carries no outcome check on any step; "
            "there is nothing for the necessity pilot to grade"
        )
    return graded


def _arm_rewards(trials: Sequence[StepTrial], *, scope: str) -> _ArmRewards:
    """Mean reward per arm over ``trials``, with the scope phrase the verdict quotes.

    Raises when either arm is missing rather than defaulting it to 0.0: an absent arm is
    a broken pilot, and a fabricated zero would read as a real measurement."""
    if not trials:
        raise ValueError(f"the necessity pilot produced no trials over {scope}")
    summaries = summarize_trials(trials)
    missing = [c.value for c in _NECESSITY_CONDITIONS if c.value not in summaries]
    if missing:
        raise ValueError(f"the necessity pilot produced no {missing} trials over {scope}")
    return _ArmRewards(
        oracle=summaries[Condition.ORACLE_MEMORY.value].mean_reward,
        no_memory=summaries[Condition.NO_MEMORY.value].mean_reward,
        scope=scope,
    )


def _decide(sequence_id: str, rewards: _ArmRewards, epsilon: float) -> NecessityResult:
    """Hand one pilot's arm means to ``pilot_filter`` and label the verdict."""
    verdict = pilot_filter(
        oracle_reward=rewards.oracle,
        no_memory_reward=rewards.no_memory,
        epsilon=epsilon,
        scope=rewards.scope,
    )
    return NecessityResult(sequence_id=sequence_id, verdict=verdict)


def memory_necessity_gate(
    seq: BenchmarkSequence,
    *,
    agent: Agent | None = None,
    epsilon: float = EPSILON,
) -> NecessityResult:
    """Run ``seq`` under NO_MEMORY and ORACLE_MEMORY in an ISOLATED store and decide
    whether it discriminates memory benefit.

    Admitted only when the oracle arm's mean reward over the sequence's GRADED steps
    beats the no-memory arm's by more than ``epsilon`` (DIV-3). A task that ties
    measures nothing about memory and is rejected — the construct-validity precondition
    for the whole benchmark.

    Scope note: a sequence whose answer was written by a DIFFERENT sequence (a
    cross-session project task) cannot pass here, because the isolated oracle pool lacks
    that memory too. That is a statement about isolation, not about necessity — gate
    such a task with ``project_necessity_gate``.
    """
    graded_ids = _graded_step_ids(seq)
    run = run_sequence(seq, _necessity_experiment(seq), agent)
    graded = [trial for trial in run.trials if trial.step_id in graded_ids]
    scope = f"the {len(graded_ids)} graded step(s) of {seq.sequence_id}"
    return _decide(seq.sequence_id, _arm_rewards(graded, scope=scope), epsilon)


def project_necessity_gate(
    target: BenchmarkSequence,
    context: Sequence[BenchmarkSequence],
    *,
    agent: Agent | None = None,
    epsilon: float = EPSILON,
) -> NecessityResult:
    """Run ``target`` under a store SHARED with ``context`` — the earlier sequences of
    its world — and decide whether it discriminates memory benefit.

    ``context`` is replayed first, in world order, so the oracle arm holds what those
    sessions wrote, the world's shared decisions above all. Only ``target``'s own graded
    steps are scored; the context's trials are the setup that makes the question
    answerable, not evidence about it.
    """
    if not context:
        raise ValueError(
            f"project_necessity_gate on {target.sequence_id!r} was given no context; a "
            "project pilot with an empty prefix is the isolated pilot "
            "(memory_necessity_gate), not a cross-session one"
        )
    graded_ids = _graded_step_ids(target)
    run = run_project(
        [*context, target],
        _necessity_experiment(target),
        agent,
        project_id=f"necessity-{target.sequence_id}",
    )
    # A missing key is a fault in run_project, not an empty result: .get(..., []) here
    # would turn a runner regression into a fabricated "does not discriminate" verdict.
    trials = run.by_sequence()[target.sequence_id]
    graded = [trial for trial in trials if trial.step_id in graded_ids]
    scope = (
        f"the {len(graded_ids)} graded step(s) of {target.sequence_id} under a store "
        f"shared with {len(context)} earlier sequence(s)"
    )
    return _decide(target.sequence_id, _arm_rewards(graded, scope=scope), epsilon)


def necessity_gate(
    candidate: GateCandidate,
    *,
    agent: Agent | None = None,
    epsilon: float = EPSILON,
) -> NecessityResult:
    """Gate one candidate at the scope its own context declares: empty context takes the
    isolated pilot, a non-empty one the shared-store project pilot. One entry point, so
    a sweep never has to branch on tier."""
    if not candidate.context:
        return memory_necessity_gate(candidate.sequence, agent=agent, epsilon=epsilon)
    return project_necessity_gate(
        candidate.sequence, candidate.context, agent=agent, epsilon=epsilon
    )


__all__ = [
    "GateCandidate",
    "NecessityResult",
    "memory_necessity_gate",
    "necessity_gate",
    "project_necessity_gate",
]
