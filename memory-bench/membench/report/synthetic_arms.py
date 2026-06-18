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

HONEST CEILING: the reference ScriptedAgent retrieves by exact id, so persisting
arms reach oracle-level by construction. That makes memory-NECESSITY and CONTINUITY
measurable, but NOT Confusion (distractors) or Staleness (v1) — those need semantic
/ top-k retrieval or a real agent (Harbor). This harness does not pretend otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from membench.runner.conditions import run_sequence
from membench.runner.project import run_project
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.sequence import BenchmarkSequence

_CONDITIONS = [Condition.NO_MEMORY, Condition.ORACLE_MEMORY, Condition.MEMORY_ENABLED]


@dataclass(frozen=True)
class ArmResult:
    """One arm's mean reward across the eval, per condition."""

    arm: str
    none_reward: float
    oracle_reward: float
    arm_reward: float

    @property
    def lift(self) -> float:
        """How much the arm beat the no-memory baseline."""
        return self.arm_reward - self.none_reward

    @property
    def oracle_gap(self) -> float:
        """How far the arm fell short of perfect (oracle) recall."""
        return self.oracle_reward - self.arm_reward


def _experiment(arm: str) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id=f"arms-eval-{arm}",
        agent=AgentConfig(agent_config_id="scripted-ref"),
        memory=MemoryConfig(memory_config_id=arm, system=arm),
        dataset_id="synthetic",
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _arm_result(arm: str, rewards: dict[Condition, list[float]]) -> ArmResult:
    return ArmResult(
        arm=arm,
        none_reward=_mean(rewards[Condition.NO_MEMORY]),
        oracle_reward=_mean(rewards[Condition.ORACLE_MEMORY]),
        arm_reward=_mean(rewards[Condition.MEMORY_ENABLED]),
    )


def eval_arms_over_sequences(
    sequences: list[BenchmarkSequence], arms: list[str], *, fs_base_dir: str | Path | None = None
) -> list[ArmResult]:
    """Run each sequence INDEPENDENTLY (run_sequence) for every arm; aggregate mean
    reward per condition across all sequences."""
    results: list[ArmResult] = []
    for arm in arms:
        rewards: dict[Condition, list[float]] = {c: [] for c in _CONDITIONS}
        for seq in sequences:
            run = run_sequence(
                seq, _experiment(arm), conditions=_CONDITIONS, fs_base_dir=fs_base_dir
            )
            for trial in run.trials:
                rewards[trial.condition].append(trial.reward)
        results.append(_arm_result(arm, rewards))
    return results


def eval_arms_over_project(
    sequences: list[BenchmarkSequence],
    arms: list[str],
    *,
    project_id: str = "arms-project",
    fs_base_dir: str | Path | None = None,
) -> list[ArmResult]:
    """Run the sequences under a SHARED store (run_project) for every arm; aggregate
    mean reward per condition. The lift here vs. the independent run is continuity."""
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
        results.append(_arm_result(arm, rewards))
    return results


def format_report(title: str, results: list[ArmResult]) -> str:
    """A compact text table of the per-arm rewards + lift."""
    lines = [
        f"# {title}",
        f"{'arm':<14}{'none':>7}{'oracle':>8}{'arm':>7}{'lift':>7}{'oracle_gap':>12}",
        "-" * 55,
    ]
    for r in results:
        lines.append(
            f"{r.arm:<14}{r.none_reward:>7.3f}{r.oracle_reward:>8.3f}"
            f"{r.arm_reward:>7.3f}{r.lift:>7.3f}{r.oracle_gap:>12.3f}"
        )
    return "\n".join(lines)
