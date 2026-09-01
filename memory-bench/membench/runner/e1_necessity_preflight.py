"""mem-9q8dg — OUTCOME-SIDE necessity verification for the E1 twin corpus.

``SequenceStep.memory_necessary`` is a LABEL. A label asserting necessity is not evidence
of it, and the E1 discrimination endpoint is worthless if the two halves are not what they
say they are. This preflight measures the claim instead of trusting it: it runs the goal
leg ALONE (no establish leg, no store, no surfaced memory — the ``none`` arm) over both
halves and reads the two pass rates.

What a healthy corpus looks like:

* the UNNECESSARY half PASSES with an empty store — its goal states the value in context,
  so a memory-free agent has everything it needs;
* the NECESSARY half FAILS with an empty store — its value is an opaque token that exists
  nowhere in the prompt, so passing would mean a prompt leak (which the adapter's firewall
  forbids) or a hallucination that guessed a 12-hex-digit token.

The thresholds mirror the bead's acceptance criterion: ``unnecessary_pass_rate > 0.8`` and
``necessary_pass_rate < 0.2``.

**A dry run is not the verification.** Without ``--paid`` (the default) the runner is the
stand-in ``realagent_probe.simulated_runner`` rather than a real ``claude -p`` — an honest
memory-copying agent that writes the current values iff they appear in the prompt. It
reproduces the
predicted structure BY CONSTRUCTION and proves only that the corpus geometry and the wiring
are right — never that a real agent behaves this way. Only ``--paid`` produces evidence, and
the emitted JSON says which was run: ``mode`` and ``verified`` are always present, and
``verified`` is false on the dry path no matter how clean the numbers look.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from membench.runner.headless_agent import CellRecorder, MemoryChannel
from membench.runner.realagent_probe import run_arm
from membench.runner.toolreq_corpus import load_twin_corpus
from membench.runner.toolreq_realagent import (
    DEFAULT_CORPUS,
    VARIANT_NECESSARY,
    VARIANT_UNNECESSARY,
    ToolReqRealAgentTask,
)

# The arm this preflight runs: empty surfaced memory, i.e. the empty-store floor. Necessity is
# a property measured against NO memory; any other arm answers a different question.
ARM = "none"

UNNECESSARY_PASS_FLOOR = 0.8
NECESSARY_PASS_CEILING = 0.2


@dataclass(frozen=True)
class NecessityPreflight:
    """Both halves' empty-store pass rates and what they imply about the corpus.

    ``verified`` is the load-bearing field: it is true only when real agent turns were spent.
    ``accepted`` says whether the rates clear the thresholds — a dry run can be ``accepted``
    and NOT ``verified``, and that combination means "wired correctly, unmeasured"."""

    mode: str
    verified: bool
    corpus_dir: str
    repeats: int
    n_necessary: int
    n_unnecessary: int
    necessary_pass_rate: float
    unnecessary_pass_rate: float
    discrimination_margin: float
    accepted: bool
    note: str


def _half_pass_rate(
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    repeats: int,
    model: str,
    dry_run: bool,
    channel: MemoryChannel,
) -> float:
    """The fraction of runs across ``tasks`` whose goal action passed with NO surfaced memory.

    Empty ``tasks`` is a caller error rather than a 0.0 rate: a rate over nothing would read as
    "the half failed" and could satisfy the necessary-half threshold with an empty corpus."""
    if not tasks:
        raise ValueError("necessity preflight: no tasks in this half")
    passes = 0
    runs = 0
    for task in tasks:
        outcome = run_arm(
            arm=ARM,
            step=task.goal_step,
            memory={},
            channel=channel,
            repeats=repeats,
            model=model,
            dry_run=dry_run,
            current_values=task.current_opaque_values,
            recorder=CellRecorder(),
        )
        passes += outcome.passes
        runs += outcome.runs
    return passes / runs


def necessity_preflight(
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    corpus_dir: Path,
    repeats: int = 1,
    model: str = "",
    dry_run: bool = True,
    channel: MemoryChannel = MemoryChannel.RECALLED,
) -> NecessityPreflight:
    """Run the empty-store leg over both halves and summarize. The validity verdict rides
    THIS summary — the object the driver emits — never a per-cell metric vector, where it
    would flatten into a paired mean."""
    halves = {
        VARIANT_NECESSARY: [t for t in tasks if t.variant == VARIANT_NECESSARY],
        VARIANT_UNNECESSARY: [t for t in tasks if t.variant == VARIANT_UNNECESSARY],
    }
    rates = {
        name: _half_pass_rate(half, repeats=repeats, model=model, dry_run=dry_run, channel=channel)
        for name, half in halves.items()
    }
    necessary = rates[VARIANT_NECESSARY]
    unnecessary = rates[VARIANT_UNNECESSARY]
    accepted = unnecessary > UNNECESSARY_PASS_FLOOR and necessary < NECESSARY_PASS_CEILING
    note = (
        "PAID: real claude -p turns; these rates are outcome-side evidence."
        if not dry_run
        else (
            "DRY RUN — NOT VERIFICATION. simulated_runner writes the current values iff they "
            "appear in the prompt, so this structure is reproduced by construction and says "
            "nothing about a real agent. Re-run with --paid for evidence."
        )
    )
    return NecessityPreflight(
        mode="paid" if not dry_run else "dry_run",
        verified=not dry_run,
        corpus_dir=str(corpus_dir),
        repeats=repeats,
        n_necessary=len(halves[VARIANT_NECESSARY]),
        n_unnecessary=len(halves[VARIANT_UNNECESSARY]),
        necessary_pass_rate=necessary,
        unnecessary_pass_rate=unnecessary,
        discrimination_margin=unnecessary - necessary,
        accepted=accepted,
        note=note,
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--model", default="")
    ap.add_argument(
        "--paid",
        action="store_true",
        help="spend real claude -p turns; without it the run is a wiring dry run, not evidence",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    _, tasks = load_twin_corpus(args.corpus_dir)
    result = necessity_preflight(
        tasks,
        corpus_dir=args.corpus_dir,
        repeats=args.repeats,
        model=args.model,
        dry_run=not args.paid,
    )
    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(
            f"[{result.mode}] necessary {result.necessary_pass_rate:.3f} "
            f"unnecessary {result.unnecessary_pass_rate:.3f} "
            f"margin {result.discrimination_margin:.3f} "
            f"-> {'ACCEPT' if result.accepted else 'REJECT'} (verified={result.verified})"
        )
        print(result.note)
    return 0 if result.accepted else 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
