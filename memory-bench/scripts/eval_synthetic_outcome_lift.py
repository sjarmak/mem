#!/usr/bin/env python3
"""§4.4 large-N OUTCOME-LIFT over the synthetic generator (mem-lvp.27).

The headline memory-vs-no-memory outcome-lift on the substrate where memory-
dependency is true BY CONSTRUCTION — unlike the real-trace track (mem-1fl8: no
measurable lift, N=8/407) and unlike the lvp.8 fixtures (no executable oracle). Each
materialised task carries a pass/fail outcome oracle: the ``memory_necessity_gate``
confirms the oracle arm beats no-memory, and ``run_sequence``'s per-step reward IS the
task's success signal (the goal step recalls the established facts or it does not).

This driver composes the pieces already on main — it adds no new eval logic:

  * ``enterprise_workflow.materialize_world`` — an AUTHORED world (no NeMo, no model,
    CI-safe, FREE), seeded for byte-reproducible large-N task variety.
  * ``memory_necessity_gate`` — the construct-validity admission check: every task
    must be memory-dependent before it counts toward the lift.
  * ``report.synthetic_arms.eval_arms_over_sequences`` / ``eval_arms_over_project`` —
    the per-arm mean reward (= pass-rate) + lift, isolated and under a shared store.

ARM ROLES (the none/ours/builtin framing over the synthetic systems):
  * ``none``       — NO_MEMORY control (the baseline every lift is measured against).
  * ``oracle``     — the task-validity ceiling (exact ground-truth injection).
  * ``filesystem`` — ROLE ``ours``: an id-exact store standing in for our system.
  * ``lexical``    — ROLE ``builtin``: a generic token-overlap top-k retriever, the
    off-the-shelf baseline-to-beat that also surfaces seeded distractors / stale v1
    (so its Confusion/Staleness are non-zero where the exact arms read 0).

RESULTS ARE HELD (publication freeze): the lift NUMBERS are written to a gitignored
``.mem/`` path and surfaced to the mayor per-action, framed SYNTHETIC. This script's
CODE lands on main; its OUTPUT does not.

VALIDITY (carry, do not bury): a synthetic outcome-lift is a SYNTHETIC result. Its
construct validity vs real work is unproven — the realism metric (mem-ovi) is the
companion that makes it defensible, and the §12.6 action-impact axes over REAL agent
trajectories are a separate provision+build+run (decomposed from this bead). The
ScriptedAgent reward measured here is the STRUCTURAL memory-dependency, not a
real-agent task-completion rate.

    PYTHONPATH=. python3 scripts/eval_synthetic_outcome_lift.py --tasks 40 --facts 3
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from membench.generators import materialize_project, materialize_world
from membench.generators.memory_necessity_gate import memory_necessity_gate
from membench.report.comparison import EPSILON
from membench.report.synthetic_arms import (
    ArmResult,
    eval_arms_over_project,
    eval_arms_over_sequences,
    format_report,
)
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team

# The none/ours/builtin role each memory system plays in the lift framing. Surfaced in
# the report so the mapping is explicit, never a silent relabel of the system name.
ARM_ROLES = {
    "none": "none (NO_MEMORY control)",
    "oracle": "oracle (validity ceiling)",
    "filesystem": "ours (id-exact store)",
    "lexical": "builtin (generic top-k)",
}
DEFAULT_ARMS = ["none", "oracle", "filesystem", "lexical"]


def authored_world(seed: int) -> tuple[EnterpriseWorld, Project]:
    """A deterministic authored enterprise world — no NeMo, no model. The personas /
    channels attribute the materialised facts; ``materialize_world`` seeds the per-task
    subject/value/distractor choices from ``seed`` so a larger ``n_tasks`` yields varied
    (not duplicated) memory-dependent tasks from the 6 authored decision subjects."""
    world = EnterpriseWorld(
        world_id=f"lvp27-world-seed{seed}",
        domain="platform-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Platform"), Team(team_id="t2", name="SRE")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Grace Hopper", role="sre", team_id="t2"),
            Persona(persona_id="p3", name="Alan Turing", role="platform-lead", team_id="t1"),
        ],
        channels=[
            Channel(channel_id="c1", name="platform", kind="chat"),
            Channel(channel_id="c2", name="incidents", kind="chat"),
        ],
        seed=seed,
    )
    project = Project(
        project_id=f"lvp27-world-seed{seed}-project",
        world_id=world.world_id,
        name="Acme platform initiative",
        goal="Reconcile the launch configuration across services.",
    )
    return world, project


@dataclass(frozen=True)
class Admission:
    """The construct-validity readout over a materialised batch: how many tasks the
    necessity gate admitted (oracle beats no-memory by > epsilon). A task the gate
    rejects measures nothing about memory and must not count toward the lift."""

    total: int
    admitted: int
    rejected_ids: tuple[str, ...]

    @property
    def rate(self) -> float:
        return self.admitted / self.total if self.total else 0.0


def gate_admission(sequences: list[BenchmarkSequence]) -> Admission:
    """Run the necessity gate over every task and report admission. Pure construct-
    validity accounting — the eval still runs over the full batch, but a low admission
    rate is the signal that the substrate (not the arm) is the limiting factor."""
    rejected = tuple(
        s.sequence_id for s in sequences if not memory_necessity_gate(s).verdict.accepted
    )
    return Admission(
        total=len(sequences), admitted=len(sequences) - len(rejected), rejected_ids=rejected
    )


def _results_payload(title: str, results: list[ArmResult]) -> dict:
    return {
        "title": title,
        "arms": [
            {
                "arm": r.arm,
                "role": ARM_ROLES.get(r.arm, r.arm),
                "none_reward": r.none_reward,
                "oracle_reward": r.oracle_reward,
                "arm_reward": r.arm_reward,
                "lift": r.lift,
                "oracle_gap": r.oracle_gap,
                "confusion": r.arm_confusion,
                "staleness": r.arm_staleness,
                "rate_n": r.rate_n,
            }
            for r in results
        ],
    }


def _role_legend() -> str:
    return "  ".join(f"{arm}={role}" for arm, role in ARM_ROLES.items())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tasks", type=int, default=40, help="materialised tasks (the N in large-N)")
    ap.add_argument("--facts", type=int, default=3, help="established facts per task")
    ap.add_argument("--arms", nargs="+", default=DEFAULT_ARMS)
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the structured results JSON here (HELD — keep under a gitignored .mem/ path)",
    )
    args = ap.parse_args()

    world, project = authored_world(args.seed)
    independent = materialize_world(world, project, n_tasks=args.tasks, facts_per_task=args.facts)
    cross_task = materialize_project(world, project, n_tasks=args.tasks, facts_per_task=args.facts)

    # The per-sequence necessity gate is the construct-validity oracle for the INDEPENDENT
    # batch only: it runs each sequence in isolation, so it can only see within-sequence
    # memory-dependency. The cross-task project's dependency is PROJECT-level by
    # construction (a task's required facts are established in an EARLIER task, absent from
    # its own isolated sequence), so the isolated gate legitimately rejects most of it —
    # that is not a substrate defect, and the cross-task dependency is instead shown by the
    # ISOLATED→SHARED lift gap below (none reaches oracle only under the shared store).
    adm = gate_admission(independent)
    print(f"# Synthetic outcome-lift (mem-lvp.27) — N={args.tasks} tasks, {args.facts} facts/task")
    print(f"# arm roles: {_role_legend()}")
    print(
        f"# necessity gate (independent, epsilon={EPSILON}): {adm.admitted}/{adm.total} admitted "
        f"(rate {adm.rate:.3f}) — within-sequence construct-validity precondition"
    )
    if adm.rejected_ids:
        print(f"#   rejected: {', '.join(adm.rejected_ids)}")
    if adm.rate < 1.0:
        print(
            f"#   WARNING: {adm.total - adm.admitted} independent task(s) are NOT "
            "memory-dependent; their no-lift steps depress the lift — read it over the "
            "admitted subset"
        )
    print(
        "# cross-task batch: dependency is PROJECT-level (shared store), not within-sequence "
        "— the isolated gate does not apply; the ISOLATED→SHARED gap is its validity signal"
    )
    print()

    sections: list[dict] = []
    with (
        tempfile.TemporaryDirectory() as d1,
        tempfile.TemporaryDirectory() as d2,
        tempfile.TemporaryDirectory() as d3,
    ):
        indep = eval_arms_over_sequences(independent, args.arms, fs_base_dir=d1)
        iso = eval_arms_over_sequences(cross_task, args.arms, fs_base_dir=d2)
        shared = eval_arms_over_project(cross_task, args.arms, fs_base_dir=d3)

    for title, results in (
        ("Independent tasks (run_sequence)", indep),
        ("Cross-task project, ISOLATED (run_sequence)", iso),
        ("Cross-task project, SHARED store (run_project)", shared),
    ):
        print(format_report(title, results))
        print()
        sections.append(_results_payload(title, results))

    if args.out is not None:
        payload = {
            "bead": "mem-lvp.27",
            "substrate": "synthetic (authored enterprise world, ScriptedAgent oracle)",
            "held": True,
            "seed": args.seed,
            "n_tasks": args.tasks,
            "facts_per_task": args.facts,
            "epsilon": EPSILON,
            "admission": {
                "independent": {
                    "total": adm.total,
                    "admitted": adm.admitted,
                    "rate": adm.rate,
                    "rejected_ids": list(adm.rejected_ids),
                },
                "cross_task_note": (
                    "project-level dependency (shared store); the per-sequence isolated gate "
                    "does not apply — validity is the ISOLATED→SHARED lift gap"
                ),
            },
            "sections": sections,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"# results written (HELD): {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
