"""Run memory arms over synthetic worlds and report lift — the mem-val5 payoff.

The synthetic substrate makes memory-dependency true by construction, so unlike the
real-trace track (mem-1fl8: no measurable lift, N=8/407) an arm's lift here is
measurable. For each arm we run the generated sequences under all three conditions
and report:

* ``lift``        = arm_reward - none_reward   (did memory help at all?)
* ``oracle_gap``  = oracle_reward - arm_reward  (how far from perfect recall?)

Two harnesses: ``eval_arms_over_sequences`` runs each sequence INDEPENDENTLY
(run_sequence); ``eval_arms_over_project`` runs them under a SHARED store
(run_project), so the gap between them on a cross-task project IS the continuity
signal — a persisting arm reaches oracle under run_project but not when isolated.

CEILING (updated, mem-zt1c): the reference ScriptedAgent retrieves by exact id, so the
id-exact arms (``oracle``, ``filesystem``) reach oracle-level reward by construction and
report 0 Confusion/Staleness. The ``lexical`` query/top-k arm DOES surface seeded
distractors and the superseded v1 — so ``confusion`` / ``staleness`` are non-zero for it
while the exact arms stay 0 (a real arm no longer == oracle on these axes). What this path
does NOT yet claim is reward-level differentiation: at the default top-k the lexical arm
still recalls every required id (recall=1), so its reward matches oracle — token overlap
cannot rank the truth above a distractor (the distinguishing value is absent from the
query, by design). DRIVING the Confusion/Staleness rate back down needs a supersession-
aware / semantic arm or a real agent (Harbor); that is the next lever, not this baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from membench.runner.conditions import StepTrial, run_sequence
from membench.runner.project import run_project
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.sequence import BenchmarkSequence

_CONDITIONS = [Condition.NO_MEMORY, Condition.ORACLE_MEMORY, Condition.MEMORY_ENABLED]


@dataclass(frozen=True)
class ArmResult:
    """One arm's mean reward across the eval, per condition, plus its Confusion/Staleness.

    ``arm_confusion`` / ``arm_staleness`` are the mean ``distractor_retrieval_rate`` /
    ``stale_memory_retrieval_rate`` over the arm's read-attempted MEMORY_ENABLED trials —
    the only trials where retrieval ran, so a non-retrieving step cannot dilute the rate
    toward 0. ``rate_n`` is how many trials contributed, surfaced so a small denominator
    (only goal steps retrieve) is not dressed up as a many-trial mean.
    """

    arm: str
    none_reward: float
    oracle_reward: float
    arm_reward: float
    arm_confusion: float = 0.0
    arm_staleness: float = 0.0
    rate_n: int = 0

    @property
    def lift(self) -> float:
        """How much the arm beat the no-memory baseline."""
        return self.arm_reward - self.none_reward

    @property
    def oracle_gap(self) -> float:
        """How far the arm fell short of perfect (oracle) recall."""
        return self.oracle_reward - self.arm_reward


def _confusion_staleness(trials: list[StepTrial]) -> tuple[list[float], list[float]]:
    """Distractor / stale retrieval rates over the MEMORY_ENABLED trials that actually
    retrieved (``retrieval.read_attempted``). Establishing steps don't retrieve, so they
    carry no Confusion/Staleness signal and are excluded rather than averaged in as 0."""
    confusion: list[float] = []
    staleness: list[float] = []
    for t in trials:
        if t.condition is Condition.MEMORY_ENABLED and t.metrics.retrieval.read_attempted:
            confusion.append(t.metrics.retrieval.distractor_retrieval_rate)
            staleness.append(t.metrics.retrieval.stale_memory_retrieval_rate)
    return confusion, staleness


def _experiment(arm: str) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id=f"arms-eval-{arm}",
        agent=AgentConfig(agent_config_id="scripted-ref"),
        memory=MemoryConfig(memory_config_id=arm, system=arm),
        dataset_id="synthetic",
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _arm_result(
    arm: str,
    rewards: dict[Condition, list[float]],
    confusion: list[float],
    staleness: list[float],
) -> ArmResult:
    return ArmResult(
        arm=arm,
        none_reward=_mean(rewards[Condition.NO_MEMORY]),
        oracle_reward=_mean(rewards[Condition.ORACLE_MEMORY]),
        arm_reward=_mean(rewards[Condition.MEMORY_ENABLED]),
        arm_confusion=_mean(confusion),
        arm_staleness=_mean(staleness),
        rate_n=len(confusion),
    )


def eval_arms_over_sequences(
    sequences: list[BenchmarkSequence], arms: list[str], *, fs_base_dir: str | Path | None = None
) -> list[ArmResult]:
    """Run each sequence INDEPENDENTLY (run_sequence) for every arm; aggregate mean
    reward per condition across all sequences."""
    results: list[ArmResult] = []
    for arm in arms:
        rewards: dict[Condition, list[float]] = {c: [] for c in _CONDITIONS}
        confusion: list[float] = []
        staleness: list[float] = []
        for seq in sequences:
            run = run_sequence(
                seq, _experiment(arm), conditions=_CONDITIONS, fs_base_dir=fs_base_dir
            )
            for trial in run.trials:
                rewards[trial.condition].append(trial.reward)
            seq_conf, seq_stale = _confusion_staleness(run.trials)
            confusion.extend(seq_conf)
            staleness.extend(seq_stale)
        results.append(_arm_result(arm, rewards, confusion, staleness))
    return results


def eval_arms_over_project(
    sequences: list[BenchmarkSequence],
    arms: list[str],
    *,
    project_id: str = "arms-project",
    fs_base_dir: str | Path | None = None,
) -> list[ArmResult]:
    """Run the sequences under a SHARED store (run_project) for every arm; aggregate
    mean reward per condition. The lift here vs. the independent run is continuity.

    Caveat on Confusion/Staleness under the shared store: an earlier task's seeded
    distractors persist and a top-k arm may surface them at a LATER task's goal, where they
    are not in that step's authored distractor set. They inflate the retrieved denominator
    without inflating the numerator, so ``arm_confusion`` here is a LOWER BOUND for later
    tasks — directionally correct (still 0 for exact arms, >0 for lexical) but not a clean
    per-task rate. Use ``eval_arms_over_sequences`` for the isolated, un-diluted rate."""
    results: list[ArmResult] = []
    for arm in arms:
        run = run_project(
            sequences,
            _experiment(arm),
            conditions=_CONDITIONS,
            project_id=project_id,
            fs_base_dir=fs_base_dir,
        )
        rewards: dict[Condition, list[float]] = {c: [] for c in _CONDITIONS}
        for trial in run.trials:
            rewards[trial.condition].append(trial.reward)
        confusion, staleness = _confusion_staleness(run.trials)
        results.append(_arm_result(arm, rewards, confusion, staleness))
    return results


def format_report(title: str, results: list[ArmResult]) -> str:
    """A compact text table of the per-arm rewards + lift."""
    lines = [
        f"# {title}",
        f"{'arm':<14}{'none':>7}{'oracle':>8}{'arm':>7}{'lift':>7}{'oracle_gap':>12}"
        f"{'confusion':>11}{'staleness':>11}",
        "-" * 77,
    ]
    for r in results:
        lines.append(
            f"{r.arm:<14}{r.none_reward:>7.3f}{r.oracle_reward:>8.3f}"
            f"{r.arm_reward:>7.3f}{r.lift:>7.3f}{r.oracle_gap:>12.3f}"
            f"{r.arm_confusion:>11.3f}{r.arm_staleness:>11.3f}"
        )
    return "\n".join(lines)
