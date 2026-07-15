#!/usr/bin/env python3
"""mem-rk41.5 — FREE offline driver: feed a frozen tool-requiring world through the
grid's mem-xe2p mechanism-fires gate, entirely offline.

Operationalizes Stephanie's mem-lq2w unblock: the paid Harbor ours-vs-builtin run is
resource-free, so the only remaining gate is design-readiness — and mem-rk41.3 proved
the tool-requiring synthetic corpus could not reach the grid. This driver closes that:
it projects a frozen tool-requiring world (``materialize_world(tool_requiring=True)``,
mem-rk41.1) into the exact shapes ``run_grid_3arm`` consumes and PROVES, offline and
free, that ``tier1_exact_signature_retrieval`` fires on the synthetic corpus — the
mechanism the mem-lvp.24 real-rig null never engaged (mem-xe2p).

Pipeline (all FREE — git/Docker/agent none of it; only the built ``mem`` CLI + SQLite):

    read_world + re-materialize (tool_requiring)      # the frozen determinism contract
      -> toolreq_bundle_adapter.sequence_records      # WorkRecords, shared value-free sig
      -> mem import-records                            # ingest -> mem computes the signature
      -> resolve_held_signatures (mem retrieve)        # the CANONICAL full+relaxed sigs
      -> toolreq_bundle_adapter.sequence_lessons       # facts embed those sigs verbatim
      -> mem import-lessons                            # attach lessons by work_id
      -> REUSE run_grid_3arm.{resolve_held_signatures, tier1_mechanism_gate}
              + run_grid.load_admitted_bundles
              + bundle_grid.resolve_payloads   # the harness-side resolver (mem-rsmq7)
      -> assert the SAME gate fired

HALT DISCIPLINE (this bead): this driver builds + verifies the OFFLINE gate only. It
NEVER fires the paid ours-vs-builtin grid — there is no code path to it here; that
stays Stephanie's per-action go once staged-ready. ``--dry-run`` runs the identical
free gate in an ephemeral workspace (nothing persisted under ``.mem/``).

    uv run python scripts/gate_toolreq_offline.py --world-dir fixtures/worlds/<seed>
    uv run python scripts/gate_toolreq_offline.py --world-dir fixtures/worlds/<seed> --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Sibling-script reuse (the test_run_grid_3arm idiom): these two live in run_grid_3arm,
# load_admitted_bundles in run_grid. Importing here runs the SAME gate the paid driver
# runs — no reimplementation, no drift.
from run_grid import load_admitted_bundles
from run_grid_3arm import resolve_held_signatures, tier1_mechanism_gate

from membench.generators.enterprise_workflow import materialize_world
from membench.generators.nemo.world_builder import read_world
from membench.generators.toolreq_bundle_adapter import (
    sequence_bundles,
    sequence_lessons,
    sequence_records,
)
from membench.generators.world_manifest import read_manifest
from membench.grading.mechanism_gate import MechanismFiresGate
from membench.harbor.bundle_grid import (
    RETRIEVAL_SCOPE,
    resolve_payloads,
    signature_overlap_observations,
)
from membench.mem_cli import run_mem_json, write_ndjson
from membench.memory_systems.ours_system import _default_runner
from membench.schemas.bundle import TaskBundle
from membench.schemas.sequence import BenchmarkSequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEM_BIN = str(PROJECT_ROOT / "bin/mem")
DEFAULT_OUT = PROJECT_ROOT / ".mem/toolreq-gate"


@dataclass(frozen=True)
class GateOutcome:
    """The offline gate's result — the gate block, the per-anchor covariate, and the
    coverage the caller (script main or an integration test) asserts against."""

    gate: MechanismFiresGate
    observations: dict[str, float]
    payloads: dict[str, dict[str, str]]
    held_signatures: dict[str, tuple[str, ...]]
    bundles: tuple[TaskBundle, ...]
    held_anchor: str


def load_toolreq_sequences(world_dir: Path) -> list[BenchmarkSequence]:
    """Re-materialize the frozen world's tool-requiring sequences from its manifest —
    the determinism contract (``verify_world`` uses the same path). Refuses a world whose
    manifest was frozen in the text-answer shape: the apply_config staleness signature
    only exists in the tool-requiring variant."""
    world, project = read_world(world_dir)
    manifest = read_manifest(world_dir)
    if not manifest.tool_requiring:
        raise ValueError(
            f"{world_dir} is not a tool-requiring world (manifest.tool_requiring=False); "
            "this offline gate needs materialize_world(tool_requiring=True) — its "
            "apply_config staleness signature is the mechanism under test"
        )
    return materialize_world(
        world,
        project,
        n_tasks=manifest.n_tasks,
        facts_per_task=manifest.facts_per_task,
        seed=manifest.seed,
        tool_requiring=True,
    )


def _write_bundles(bundles_dir: Path, bundles: Sequence[TaskBundle]) -> Path:
    """Write one ``<work_id>.json`` per bundle + the two-stage admission manifest
    (``grid-ready-pool.json``) ``load_admitted_bundles`` reads. Every synthetic bundle is
    admitted by construction — the real-corpus scope/oracle gates do not apply offline."""
    bundles_dir.mkdir(parents=True, exist_ok=True)
    for bundle in bundles:
        (bundles_dir / f"{bundle.work_id}.json").write_text(
            bundle.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    manifest_path = bundles_dir / "grid-ready-pool.json"
    manifest_path.write_text(
        json.dumps(
            {
                "admitted": [bundle.work_id for bundle in bundles],
                "provenance": {
                    "source": "mem-rk41.5 toolreq_bundle_adapter",
                    "note": "synthetic tool-requiring world; offline mechanism-fires gate only",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def run_offline_gate(
    sequences: Sequence[BenchmarkSequence],
    *,
    store_path: Path,
    out_dir: Path,
    mem_bin: str = DEFAULT_MEM_BIN,
    override_reason: str | None = None,
) -> GateOutcome:
    """Build the substrate + run the mem-xe2p gate offline. Raises
    ``MechanismNeverFiredError`` if tier-1 exact-signature retrieval never engages (unless
    ``override_reason`` waives it), and ``RuntimeError`` if the held anchor shows no
    signature overlap or a payload leaks a LOO-excluded id."""
    out_dir.mkdir(parents=True, exist_ok=True)
    bundles_dir = out_dir / "bundles"

    records = sequence_records(sequences)
    bundles = sequence_bundles(sequences)
    records_path = out_dir / "records.ndjson"
    write_ndjson(records_path, records)
    manifest_path = _write_bundles(bundles_dir, bundles)

    run_mem_json(
        [mem_bin, "import-records", "--file", str(records_path), "--store", str(store_path)]
    )

    runner = _default_runner(mem_bin)
    # Canonical full+relaxed signatures from the retrieval envelope (never recomputed in
    # Python) — the strings the lessons embed. Resolved BEFORE lessons exist: they derive
    # from each held record's OWN trace errors, independent of any attached lesson.
    held_signatures = resolve_held_signatures(bundles, store_path=store_path, runner=runner)

    lessons = sequence_lessons(sequences, held_signatures)
    lessons_path = out_dir / "lessons.ndjson"
    write_ndjson(lessons_path, lessons)
    run_mem_json(
        [mem_bin, "import-lessons", "--file", str(lessons_path), "--store", str(store_path)]
    )

    # Reload through disk so the grid's own loader validates the bundles, then run the
    # verbatim grid functions over the now-lesson-bearing store.
    loaded = load_admitted_bundles(bundles_dir, manifest_path)
    payloads = resolve_payloads(loaded, store_path=store_path, runner=runner)
    gate_signatures = resolve_held_signatures(loaded, store_path=store_path, runner=runner)
    gate = tier1_mechanism_gate(payloads, gate_signatures, override_reason=override_reason)

    observations = signature_overlap_observations(payloads, gate_signatures)
    # The held anchor is the last (latest-closed) task: the one with earlier retrievable
    # neighbours. It is where the mechanism must demonstrably fire.
    held_anchor = loaded[-1].work_id
    # The anchor-specific check is stronger than the grid gate (it demands the mechanism
    # fire on THE intended anchor, not merely somewhere). Honour ``override_reason`` here
    # too, so ``--override-mechanism-gate`` means what its help says — "continue even if
    # the gate never fired" — rather than being silently waivable only for the grid gate.
    if observations.get(held_anchor, 0.0) <= 0.0 and override_reason is None:
        raise RuntimeError(
            f"held anchor {held_anchor} shows signature_overlap=0 — the tier-1 mechanism "
            "did not fire on the intended anchor even if it fired elsewhere"
        )
    # resolve_payloads already raises on a LOO leak; re-assert per bundle so the driver's
    # contract is explicit rather than inherited.
    for bundle in loaded:
        leaked = set(payloads.get(bundle.work_id, {})) & set(bundle.loo_excluded_work_ids)
        if leaked:
            raise RuntimeError(
                f"{bundle.work_id}: payload leaks LOO-excluded id(s) {sorted(leaked)}"
            )

    return GateOutcome(
        gate=gate,
        observations=observations,
        payloads=payloads,
        held_signatures=gate_signatures,
        bundles=tuple(loaded),
        held_anchor=held_anchor,
    )


def write_summary(outcome: GateOutcome, *, out_dir: Path, store_path: Path) -> Path:
    with_payload = [wid for wid, sources in outcome.payloads.items() if sources]
    summary = {
        "bead": "mem-rk41.5",
        "scope": RETRIEVAL_SCOPE,
        "store": str(store_path),
        "n_bundles": len(outcome.bundles),
        "held_anchor": outcome.held_anchor,
        "mechanism_fires": outcome.gate.model_dump(mode="json"),
        "signature_overlap_observations": outcome.observations,
        "retrieval_coverage": {
            "n_with_payload": len(with_payload),
            "with_payload": sorted(with_payload),
        },
        "held_signatures": {wid: list(sigs) for wid, sigs in outcome.held_signatures.items()},
        "halt": "offline mechanism-fires gate only; paid ours-vs-builtin grid NOT fired",
    }
    out = out_dir / "summary-toolreq-offline.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--world-dir",
        type=Path,
        required=True,
        help="a frozen tool-requiring world dir (world.json + manifest.json)",
    )
    parser.add_argument("--mem-bin", default=DEFAULT_MEM_BIN)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="workspace for records/bundles/lessons/summary (ignored under --dry-run)",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        help="store path (default <out>/store.db; ignored under --dry-run)",
    )
    parser.add_argument(
        "--override-mechanism-gate",
        metavar="REASON",
        default=None,
        help="record a reason and continue even if the gate never fired (mem-xe2p)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the identical FREE gate in an ephemeral workspace; persist nothing",
    )
    args = parser.parse_args(argv)

    sequences = load_toolreq_sequences(args.world_dir)

    ephemeral = Path(tempfile.mkdtemp(prefix="toolreq-gate-")) if args.dry_run else None
    out_dir = ephemeral if ephemeral is not None else args.out
    store_path = (
        (ephemeral / "store.db")
        if ephemeral is not None
        else (args.store if args.store is not None else out_dir / "store.db")
    )

    try:
        outcome = run_offline_gate(
            sequences,
            store_path=store_path,
            out_dir=out_dir,
            mem_bin=args.mem_bin,
            override_reason=args.override_mechanism_gate,
        )
        with_payload = sum(1 for sources in outcome.payloads.values() if sources)
        print(
            f"mechanism-fires gate: {outcome.gate.reason}\n"
            f"retrieval coverage: {with_payload}/{len(outcome.bundles)} bundle(s) with a "
            f"non-empty ours payload; held anchor {outcome.held_anchor} "
            f"signature_overlap={outcome.observations[outcome.held_anchor]:g}"
        )
        if ephemeral is None:
            summary_path = write_summary(outcome, out_dir=out_dir, store_path=store_path)
            print(f"summary -> {summary_path}")
        else:
            print("DRY RUN: ephemeral workspace, nothing persisted; paid grid NOT fired.")
    except RuntimeError as exc:
        # The gate's failure paths (MechanismNeverFiredError, zero anchor overlap, a LOO
        # leak) all raise RuntimeError. Surface the one actionable line rather than a raw
        # traceback, consistent with this driver's print-based UX; exit non-zero.
        print(f"gate FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if ephemeral is not None:
            shutil.rmtree(ephemeral, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
