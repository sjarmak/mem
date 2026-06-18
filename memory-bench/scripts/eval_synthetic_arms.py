#!/usr/bin/env python3
"""Run memory arms over a frozen synthetic world — first lift numbers (CI-safe, no NeMo).

Reads a frozen world (from scripts/generate_worlds.py), materializes its tasks both
independently and as a cross-task project, runs the arms under all conditions, and
prints the per-arm reward + lift. The independent-vs-shared-store gap on the project
is the continuity signal.

    PYTHONPATH=. python3 scripts/eval_synthetic_arms.py fixtures/worlds/0
"""

from __future__ import annotations

import argparse
import tempfile

from membench.generators import materialize_project, materialize_world
from membench.generators.nemo import read_world
from membench.report.synthetic_arms import (
    eval_arms_over_project,
    eval_arms_over_sequences,
    format_report,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("world_dir", help="a frozen world dir, e.g. fixtures/worlds/0")
    ap.add_argument("--arms", nargs="+", default=["none", "oracle", "filesystem"])
    ap.add_argument("--tasks", type=int, default=3)
    ap.add_argument("--facts", type=int, default=3)
    args = ap.parse_args()

    world, project = read_world(args.world_dir)
    independent = materialize_world(world, project, n_tasks=args.tasks, facts_per_task=args.facts)
    cross_task = materialize_project(world, project, n_tasks=args.tasks, facts_per_task=args.facts)

    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2, (
        tempfile.TemporaryDirectory()
    ) as d3:
        print(
            format_report(
                "Independent tasks (run_sequence)",
                eval_arms_over_sequences(independent, args.arms, fs_base_dir=d1),
            )
        )
        print()
        print(
            format_report(
                "Cross-task project, ISOLATED (run_sequence)",
                eval_arms_over_sequences(cross_task, args.arms, fs_base_dir=d2),
            )
        )
        print()
        print(
            format_report(
                "Cross-task project, SHARED store (run_project)",
                eval_arms_over_project(cross_task, args.arms, fs_base_dir=d3),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
