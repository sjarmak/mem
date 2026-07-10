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
from dataclasses import dataclass
from pathlib import Path

from membench.generators import materialize_world, memory_necessity_gate
from membench.generators.nemo import records_to_world, write_world
from membench.generators.nemo.model_provider import DEFAULT_NIM_ENDPOINT, DEFAULT_NIM_MODEL
from membench.generators.nemo.world_builder import generate_world_records
from membench.generators.world_manifest import build_manifest, write_manifest


@dataclass(frozen=True)
class SequenceAdmission:
    """One materialised sequence and the memory-necessity gate's verdict on it."""

    sequence_id: str
    accepted: bool
    delta: float
    oracle_reward: float
    no_memory_reward: float


@dataclass(frozen=True)
class WorldResult:
    """The frozen-world artifact locations plus per-sequence admission for one seed."""

    seed: int
    org_name: str
    domain: str
    out_dir: Path
    admissions: tuple[SequenceAdmission, ...]

    @property
    def total(self) -> int:
        return len(self.admissions)

    @property
    def admitted(self) -> int:
        return sum(a.accepted for a in self.admissions)


def generate_and_freeze(
    *,
    seed: int,
    personas: int,
    tasks: int,
    facts: int,
    nim_endpoint: str,
    nim_model: str,
    out: str,
    verbose: bool = True,
) -> WorldResult:
    """Generate one synthetic world, freeze it under ``<out>/<seed>/``, and gate its tasks.

    Runs the full Phase-1 + Phase-2 pipeline (NeMo -> world -> freeze -> materialise ->
    memory-necessity gate) against a LOCAL NIM. Returns the artifact locations and the
    per-sequence admission verdicts so callers can aggregate across seeds.
    """
    if verbose:
        print(f"[seed {seed}] generating {personas} persona rows via NeMo @ {nim_endpoint} ...")
    records = generate_world_records(
        num_records=personas,
        seed=seed,
        nim_endpoint=nim_endpoint,
        nim_model=nim_model,
    )
    world, project = records_to_world(records, seed=seed)
    out_dir = Path(write_world(world, project, base_dir=out))
    if verbose:
        print(f"[seed {seed}] froze world '{world.org_name}' ({world.domain}) -> {out_dir}")

    sequences = materialize_world(world, project, n_tasks=tasks, facts_per_task=facts)
    admissions: list[SequenceAdmission] = []
    for seq in sequences:
        v = memory_necessity_gate(seq).verdict
        admissions.append(
            SequenceAdmission(
                sequence_id=seq.sequence_id,
                accepted=v.accepted,
                delta=v.delta,
                oracle_reward=v.oracle_reward,
                no_memory_reward=v.no_memory_reward,
            )
        )
        if verbose:
            print(
                f"  {seq.sequence_id}: oracle {v.oracle_reward:.3f} "
                f"none {v.no_memory_reward:.3f} delta {v.delta:.3f} "
                f"-> {'ADMIT' if v.accepted else 'REJECT'}"
            )

    seq_path = out_dir / "sequences.json"
    seq_path.write_text(
        json.dumps([seq.model_dump() for seq in sequences], indent=2), encoding="utf-8"
    )
    if verbose:
        print(f"[seed {seed}] wrote {len(sequences)} sequences -> {seq_path}")

    manifest = build_manifest(
        world,
        project,
        sequences,
        nim_model=nim_model,
        n_tasks=tasks,
        facts_per_task=facts,
        seed=seed,
    )
    mpath = write_manifest(manifest, world_dir=str(out_dir))
    result = WorldResult(
        seed=seed,
        org_name=world.org_name,
        domain=world.domain,
        out_dir=out_dir,
        admissions=tuple(admissions),
    )
    if verbose:
        sha = manifest.sequences_sha256[:12]
        print(f"[seed {seed}] wrote manifest (sequences_sha256={sha}) -> {mpath}")
        print(f"[seed {seed}] admitted {result.admitted}/{result.total} memory-required tasks")
    return result


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

    generate_and_freeze(
        seed=args.seed,
        personas=args.personas,
        tasks=args.tasks,
        facts=args.facts,
        nim_endpoint=args.nim_endpoint,
        nim_model=args.nim_model,
        out=args.out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
