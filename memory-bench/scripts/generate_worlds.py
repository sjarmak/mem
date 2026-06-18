#!/usr/bin/env python3
"""Offline operator entrypoint: generate a synthetic enterprise world and freeze it.

Ties the Phase-1 + Phase-2 pipeline into one command (runs against a LOCAL NIM, no
paid API):

    NeMo Data Designer  -> records_to_world -> write_world (freeze)
                                            -> materialize_world -> memory_necessity_gate

This is operator tooling, NOT run in CI — it calls a model. Start a local NIM first
(see the mem-3453 bead), then run from the ``memory-bench`` dir (the package is not
pip-installed; tests use a conftest, scripts use ``PYTHONPATH=.``):

    PYTHONPATH=. python3 scripts/generate_worlds.py --seed 0 --personas 4 --tasks 2 \
        --nim-endpoint http://localhost:8001/v1 --nim-model meta/llama-3.1-8b-instruct

It writes ``world.json`` + ``project.json`` (and the materialised ``sequences.json``)
under ``<out>/<seed>/`` and prints the admission summary for the generated tasks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from membench.generators import materialize_world, memory_necessity_gate
from membench.generators.nemo import records_to_world, write_world
from membench.generators.nemo.model_provider import DEFAULT_NIM_ENDPOINT, DEFAULT_NIM_MODEL
from membench.generators.nemo.world_builder import generate_world_records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--personas", type=int, default=4, help="NeMo records (one per persona)")
    ap.add_argument("--tasks", type=int, default=2, help="sequences materialised per world")
    ap.add_argument("--facts", type=int, default=3, help="facts (subjects) per task")
    ap.add_argument("--nim-endpoint", default=DEFAULT_NIM_ENDPOINT)
    ap.add_argument("--nim-model", default=DEFAULT_NIM_MODEL)
    ap.add_argument("--out", default="fixtures/worlds")
    args = ap.parse_args()

    print(f"generating {args.personas} persona rows via NeMo @ {args.nim_endpoint} ...")
    records = generate_world_records(
        num_records=args.personas,
        seed=args.seed,
        nim_endpoint=args.nim_endpoint,
        nim_model=args.nim_model,
    )
    world, project = records_to_world(records, seed=args.seed)
    out_dir = write_world(world, project, base_dir=args.out)
    print(f"froze world '{world.org_name}' ({world.domain}) -> {out_dir}")

    sequences = materialize_world(world, project, n_tasks=args.tasks, facts_per_task=args.facts)
    admitted = 0
    rows: list[dict[str, object]] = []
    for seq in sequences:
        v = memory_necessity_gate(seq).verdict
        admitted += v.accepted
        rows.append({"sequence_id": seq.sequence_id, "accepted": v.accepted, "delta": v.delta})
        print(f"  {seq.sequence_id}: oracle {v.oracle_reward:.3f} none {v.no_memory_reward:.3f} "
              f"delta {v.delta:.3f} -> {'ADMIT' if v.accepted else 'REJECT'}")

    seq_path = Path(out_dir) / "sequences.json"
    seq_path.write_text(
        json.dumps([seq.model_dump() for seq in sequences], indent=2), encoding="utf-8"
    )
    print(f"wrote {len(sequences)} sequences -> {seq_path}")
    print(f"admitted {admitted}/{len(sequences)} memory-required tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
