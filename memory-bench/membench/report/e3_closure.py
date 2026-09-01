"""E3a headline — write→later-read CLOSURE, and the first headline consumer of
``score_retrieval`` / ``score_retention``.

One seed = one three-step world (``generators.e3_closure_world``) driven through
``runner.conditions.run_sequence``. The endpoint is the B step's reward: closure
holds for a seed iff the apply step deployed with the pin an EARLIER step wrote.
``closure_rate`` is the Bernoulli mean over seeds.

The lattice this module exists to establish, before any arm is compared:

- **CEILING** — ``ScriptedAgent`` on an id-exact arm must reach ``closure_rate ==
  1.0``. Anything less means the fixture, not the arm, is the finding; the run
  reports ``halt: true`` and the CLI exits non-zero rather than printing an
  uninterpretable number.
- **FLOOR** — ``NeverWritesAgent`` on the same write-bearing arm must reach ``0.0``.
  That is what proves closure measures the WRITE and not the prompt.

Honest absences, carried in the summary rather than silently reported as zeros:

- ``supersession_correct`` / ``stale_memory_removed`` are UNMEASURABLE here. The
  ``MemorySystem`` ABC has no removal op, so ``removed_ids`` is always empty and
  passing ``superseded_expected_ids`` would flip both fields from constant-True to
  constant-FALSE for every arm — a fabricated penalty, not a measurement. The
  channels that do move — ``stale_memory_retrieval_rate`` and the forbidden-value /
  forbidden-write rates — are reported instead.
- ``correct_scope_rate`` is a pass-through default in the deterministic path (no
  arm here distinguishes scope), so it is reported as unmeasured, not as a 1.0 win.

Validity gates ride THIS summary, never a per-cell metric vector: a gate folded into
a per-seed vector flattens into the paired mean and stops being a gate.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from statistics import mean

from membench.generators.e3_closure_world import (
    GENERATOR_VERSION,
    ClosureWorld,
    assert_unguessable,
    generate_closure_world,
)
from membench.runner.agent import Agent, NeverWritesAgent, ScriptedAgent
from membench.runner.conditions import SequenceRun, run_sequence
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig

# A two-step Bernoulli endpoint read at 5 seeds cannot separate 0.8 from 1.0; the
# driver refuses rather than emitting a number nobody should quote.
MIN_SEEDS = 20

# arm name → the condition it is driven under. `oracle` and `none` are the fixed
# controls (the runner picks their system itself); every other name is the
# memory_enabled system under test.
_CONTROL_CONDITIONS = {
    "oracle": Condition.ORACLE_MEMORY,
    "none": Condition.NO_MEMORY,
}

# Arms whose retrieval is exact-by-id, so a ScriptedAgent ceiling of 1.0 is the
# expected reading. A non-id-exact arm may legitimately come in below 1.0 and is
# NOT a fixture failure — the ceiling gate only applies to these.
ID_EXACT_ARMS = frozenset({"oracle", "filesystem"})

_AGENTS: dict[str, type[Agent]] = {
    "scripted": ScriptedAgent,
    "never-writes": NeverWritesAgent,
}

# The store-level temporal LOO of the real corpus does not apply to a generated
# world; these are the three STRUCTURAL substitutes that actually hold here. Named
# in the summary so no reader mistakes this for `closedBefore`-bounded retrieval.
LOO_SUBSTITUTES = (
    "_oracle_pool raises on a same-id/different-content rewrite, so the oracle "
    "cannot hand an early step a later step's content (within-sequence future leak)",
    "_assert_superseded_written raises unless every superseded id was written by a "
    "STRICTLY EARLIER step, so the stale signal is retrievable rather than silently 0",
    "run_sequence resets the memory system once per condition on a per-condition "
    "scope root, so no condition inherits another condition's store",
)


@dataclass(frozen=True)
class ClosureCell:
    """One seed's readout. Per-cell metrics ONLY — the validity gates live on the
    summary, deliberately not in here."""

    seed: int
    sequence_id: str
    closure: bool
    apply_reward: float
    # From MetricsBundle.retrieval on the apply step.
    relevant_memory_retrieved: bool
    distractor_retrieval_rate: float
    stale_memory_retrieval_rate: float
    missed_required_memory_count: int
    # From MetricsBundle.retention, summed over the sequence's write-bearing steps.
    write_hit_rate: float
    forbidden_write_rate: float


def _experiment(arm: str, agent_config_id: str) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id=f"e3-closure-{arm}-{agent_config_id}",
        agent=AgentConfig(agent_config_id=agent_config_id),
        memory=MemoryConfig(memory_config_id=arm, system=arm),
        dataset_id="e3-closure-world",
        conditions=[_CONTROL_CONDITIONS.get(arm, Condition.MEMORY_ENABLED)],
    )


def score_closure_run(world: ClosureWorld, run: SequenceRun) -> ClosureCell:
    """Read one seed's closure cell off the run's trials.

    The apply (B) step's trial carries the endpoint; retention is averaged over the
    trials whose step actually expected a write, so establishing steps are not
    diluted by the read-only step's vacuous 0.0."""
    trials = {t.step_id: t for t in run.trials}
    apply_trial = trials[world.apply_step_id]
    metrics = apply_trial.metrics
    retrieval = metrics.retrieval

    write_steps = [s.step_id for s in world.sequence.steps if s.expected_memory_writes]
    write_hits = [trials[sid].metrics.retention.write_hit_rate for sid in write_steps]
    forbidden = [t.metrics.retention.forbidden_write_rate for t in run.trials]

    return ClosureCell(
        seed=world.seed,
        sequence_id=world.sequence.sequence_id,
        closure=metrics.task.reward == 1.0,
        apply_reward=metrics.task.reward,
        relevant_memory_retrieved=retrieval.relevant_memory_retrieved,
        distractor_retrieval_rate=retrieval.distractor_retrieval_rate,
        stale_memory_retrieval_rate=retrieval.stale_memory_retrieval_rate,
        missed_required_memory_count=retrieval.missed_required_memory_count,
        write_hit_rate=mean(write_hits) if write_hits else 0.0,
        forbidden_write_rate=mean(forbidden) if forbidden else 0.0,
    )


def run_closure_cells(seeds: list[int], *, arm: str, agent_name: str) -> list[ClosureCell]:
    """Drive every seed's world through ``run_sequence`` and score its cell."""
    if agent_name not in _AGENTS:
        raise ValueError(f"unknown agent {agent_name!r} (known: {', '.join(sorted(_AGENTS))})")
    agent = _AGENTS[agent_name]()
    experiment = _experiment(arm, agent.agent_config_id)
    cells: list[ClosureCell] = []
    for seed in seeds:
        world = generate_closure_world(seed)
        # The leak proof runs on every scored world, not only under --check-unguessable:
        # a closure number from a leaked world would be meaningless.
        assert_unguessable(world)
        run = run_sequence(world.sequence, experiment, agent)
        cells.append(score_closure_run(world, run))
    return cells


def summarize_closure(
    cells: list[ClosureCell], *, arm: str, agent_name: str, n_seeds: int
) -> dict[str, object]:
    """The emitted summary — endpoint, aggregates, validity gates, honest absences.

    ``halt`` is the fixture-validity gate: a ceiling run (the reference agent on an
    id-exact arm) that does not reach ``closure_rate == 1.0`` means the fixture is
    broken and NO closure number from it is interpretable."""
    closure_rate = mean(1.0 if c.closure else 0.0 for c in cells) if cells else 0.0
    is_ceiling_run = agent_name == "scripted" and arm in ID_EXACT_ARMS
    halt = is_ceiling_run and closure_rate != 1.0
    return {
        "generator_version": GENERATOR_VERSION,
        "arm": arm,
        "agent": agent_name,
        "n_seeds": n_seeds,
        "min_seeds": MIN_SEEDS,
        "closure_rate": closure_rate,
        "retrieval": {
            "relevant_memory_retrieved_rate": (
                mean(1.0 if c.relevant_memory_retrieved else 0.0 for c in cells) if cells else 0.0
            ),
            "distractor_retrieval_rate": (
                mean(c.distractor_retrieval_rate for c in cells) if cells else 0.0
            ),
            "stale_memory_retrieval_rate": (
                mean(c.stale_memory_retrieval_rate for c in cells) if cells else 0.0
            ),
            "missed_required_memory_count": (
                mean(float(c.missed_required_memory_count) for c in cells) if cells else 0.0
            ),
        },
        "retention": {
            "write_hit_rate": mean(c.write_hit_rate for c in cells) if cells else 0.0,
            "forbidden_write_rate": (mean(c.forbidden_write_rate for c in cells) if cells else 0.0),
        },
        "validity": {
            "is_ceiling_run": is_ceiling_run,
            "ceiling_ok": (not is_ceiling_run) or closure_rate == 1.0,
            "halt": halt,
            "halt_reason": (
                "ceiling below 1.0 on an id-exact arm: the fixture is broken, so no "
                "closure number from it is interpretable"
                if halt
                else ""
            ),
            "leak_proof": (
                "mechanical substring check: the v2 literal appears in no step's "
                "user_request / available_tools / environment_state "
                "(generators.e3_closure_world.assert_unguessable). The NO_MEMORY leg is "
                "a SMOKE check only — outcome_check_passes short-circuits on the missing "
                "required id, so it would read 0 whether or not the answer leaked."
            ),
            "loo_substitutes": list(LOO_SUBSTITUTES),
        },
        "unmeasurable_endpoints": {
            "supersession_correct": (
                "no removal op on the MemorySystem ABC, so removed_ids is always empty; "
                "reporting it would fabricate a constant FALSE for every arm"
            ),
            "stale_memory_removed": "same absence as supersession_correct",
            "correct_scope_rate": (
                "no arm on this path distinguishes memory scope; the scorer's "
                "pass-through default would read as a 1.0 win, not a measurement"
            ),
        },
        "cells": [asdict(c) for c in cells],
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E3a write→later-read closure")
    parser.add_argument("--seeds", type=int, required=True, help=f"seed count (>= {MIN_SEEDS})")
    parser.add_argument("--arm", default="filesystem")
    parser.add_argument("--agent", default="scripted", choices=sorted(_AGENTS))
    parser.add_argument("--json", action="store_true", help="emit compact JSON")
    args = parser.parse_args(argv)

    if args.seeds < MIN_SEEDS:
        parser.error(
            f"--seeds {args.seeds} is below the seed floor: a two-step Bernoulli "
            f"endpoint needs at least {MIN_SEEDS} seeds to be readable"
        )

    cells = run_closure_cells(list(range(args.seeds)), arm=args.arm, agent_name=args.agent)
    summary = summarize_closure(cells, arm=args.arm, agent_name=args.agent, n_seeds=args.seeds)
    print(json.dumps(summary) if args.json else json.dumps(summary, indent=2))
    return 2 if summary["validity"]["halt"] else 0  # type: ignore[index]


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(_main())
