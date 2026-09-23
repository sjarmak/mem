#!/usr/bin/env python3
"""Verify frozen worlds reproduce their task instances — NO model call (CI-safe).

For each ``<base>/<seed>/`` world dir, re-hashes the frozen world/project and the
frozen ``sequences.json``, and re-materialises the sequences from the manifest,
comparing all of it to the recorded hashes.
This is the determinism guarantee (mem-ge51): a frozen fixture + its manifest
reproduces the exact task instances without re-running NeMo.

A world frozen under an older generator version keeps its hash checks but cannot be
re-materialised by this tree, and the summary counts those separately rather than
folding them into the worlds that did reproduce.

    PYTHONPATH=. python3 scripts/verify_worlds.py fixtures/worlds

EXIT CODES, and why a missing base is not a pass (mem-r6yzk N4). This script used to
print "no manifested worlds under <base>" and return 0, so a typo'd or moved path read
as a GREEN freeze gate: the one failure the gate exists to catch is the one it reported
success on. A base that does not exist, is not a directory, or holds no manifested world
is now a FAULT:

* ``0`` — every world was re-materialised from its manifest and matched.
* ``1`` — a VERDICT: at least one world did not reproduce.
* ``2`` — a FAULT: the sweep could not run, so there is no verdict to read.
* ``3`` — a WEAKER RESULT: nothing mismatched, but at least one world was verified by
  its frozen hashes alone, because it was frozen under a generator version this tree no
  longer holds. Nothing about it was re-derived.

3 exists because 0 and 3 are different claims and a caller reads the code, not the
prose above it (mem-r6yzk R4). Over ``fixtures/worlds-tool-jev32`` — 32 worlds frozen
under ``enterprise-workflow.v3``, which is not the generator this tree holds — this
script printed "0/32
worlds reproduce deterministically", "32/32 verified by frozen hash only, not
re-materialised", and exited 0. A CI step reading the exit code concluded that the
legacy worlds reproduce. They were never re-derived at all. A caller that treats any
non-zero as failure gets the strict reading for free; one that wants to accept a
hash-only corpus has to say so by admitting 3.

There is deliberately no ``--allow-empty``. The claim this script makes is "these frozen
worlds reproduce"; over zero worlds that claim is vacuously true, which is precisely the
false green above, and no caller wants it — the corpus call sites
(``tests/test_public_corpus_contract.py``, the ``worlds-tool`` sweep documented in
``membench/runner/toolreq_realagent.py``) each name a corpus that must already be frozen.
A flag would exist only to be reached for when the gate fires, which is the moment it is
telling the truth.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from membench.generators.world_manifest import MANIFEST_FILE, verify_world

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_FAULT = 2
EXIT_HASH_ONLY = 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base", nargs="?", default="fixtures/worlds")
    args = ap.parse_args(argv)

    base = Path(args.base)
    if not base.exists():
        print(
            f"FAULT: no such corpus directory: {base} (resolved {base.resolve()}) — "
            "a freeze gate over a path that is not there is not a pass",
            file=sys.stderr,
        )
        return EXIT_FAULT
    if not base.is_dir():
        print(f"FAULT: corpus base is not a directory: {base}", file=sys.stderr)
        return EXIT_FAULT

    world_dirs = sorted(d for d in base.glob("*") if (d / MANIFEST_FILE).exists())
    if not world_dirs:
        print(
            f"FAULT: no manifested worlds under {base} (resolved {base.resolve()}) — "
            f"no subdirectory holds a {MANIFEST_FILE}, so nothing was verified",
            file=sys.stderr,
        )
        return EXIT_FAULT

    failed = 0
    hash_only = 0
    for d in world_dirs:
        result = verify_world(d)
        if result.ok:
            hash_only += not result.rematerialised
            print(f"OK    {d}")
        else:
            failed += 1
            print(f"FAIL  {d}")
            for m in result.mismatches:
                print(f"        - {m}")
        for note in result.notes:
            print(f"        ~ {note}")
    reproduced = len(world_dirs) - failed - hash_only
    print(f"\n{reproduced}/{len(world_dirs)} worlds reproduce deterministically")
    if failed:
        return EXIT_MISMATCH
    if hash_only:
        # Counted off VerifyResult.rematerialised, the fact itself, so this never rides
        # on whether a world happened to carry a note.
        print(f"{hash_only}/{len(world_dirs)} verified by frozen hash only, not re-materialised")
        print(
            f"EXIT {EXIT_HASH_ONLY}: nothing mismatched, but {hash_only} of {len(world_dirs)} "
            "world(s) were never re-derived — this is not the determinism claim"
        )
        return EXIT_HASH_ONLY
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
