#!/usr/bin/env python3
"""Gate the public corpus on memory necessity and write the rejection rate as an artifact.

Runs ``necessity_gate`` over every sequence in a frozen corpus (default
``fixtures/worlds-public-v1``), writes ``<corpus>/necessity.json``, and exits
non-zero when fewer than ``--min-accepted`` sequences are admitted — so a corpus
that stops discriminating memory benefit fails loudly instead of shipping.

Each sequence is piloted at its own scope: a project-tier sequence runs under a store
shared with the earlier sequences of its world, because its answer was written by one
of them (mem-r6yzk B1). Arm means are taken over the GRADED steps, so a long sequence
is not rejected for its establishing steps scoring zero in both arms.

    PYTHONPATH=. python3 scripts/gate_public_corpus.py

The default reference agent is the deterministic ``ScriptedAgent``: the oracle arm
is handed the required memory ids and the no-memory arm is not, so a well-formed
sequence separates by construction. The rejection rate under it reports
CONSTRUCTION integrity, not whether a real agent needs memory. The artifact records
``reference_agent`` for exactly that reason.
"""

from __future__ import annotations

import argparse

from membench.generators.necessity_sweep import (
    describe_rate,
    gate_corpus,
    write_necessity_artifact,
)

DEFAULT_CORPUS = "fixtures/worlds-public-v1"
# The floor the public corpus was sized against: below this the benchmark no longer
# has enough admitted tasks to report on.
DEFAULT_MIN_ACCEPTED = 100


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("corpus", nargs="?", default=DEFAULT_CORPUS)
    ap.add_argument("--out", default=None, help="artifact path (default: <corpus>/necessity.json)")
    ap.add_argument(
        "--min-accepted",
        type=int,
        default=DEFAULT_MIN_ACCEPTED,
        help="exit non-zero below this many admitted sequences",
    )
    args = ap.parse_args(argv)

    report = gate_corpus(args.corpus)
    path = write_necessity_artifact(report, path=args.out)
    sweep = report.sweep

    print(f"corpus            {report.corpus_dir}")
    print(f"gate_version      {sweep.gate_version}")
    print(f"reference_agent   {sweep.reference_agent}")
    print(f"epsilon           {sweep.epsilon}")
    print(f"candidates        {sweep.n_candidates}")
    print(f"accepted          {sweep.n_accepted}")
    print(f"rejected          {sweep.n_rejected}")
    print(f"rejection_rate    {sweep.rejection_rate:.4f}")
    for line in describe_rate(sweep):
        print(f"  {line}")
    for result in sweep.rejected:
        print(f"  REJECT {result.sequence_id}: {result.verdict.reason}")
    print(f"wrote             {path}")

    if sweep.n_accepted < args.min_accepted:
        print(
            f"FAIL: {sweep.n_accepted} admitted < required {args.min_accepted}",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
