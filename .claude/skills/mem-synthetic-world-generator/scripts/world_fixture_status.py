#!/usr/bin/env python3
"""Read-only diagnostic: frozen-world inventory + version-drift + determinism status.

For every ``fixtures/worlds/<seed>/`` with a ``manifest.json``, reports the
manifest's pinned generator versions against the CURRENT code versions and runs
``verify_world`` — so a ``sequences_sha256`` failure is immediately attributable
(version drift vs real non-determinism vs edited fixture). Writes nothing.

Run from the memory-bench dir:

    PYTHONPATH=. python3 ../.claude/skills/mem-synthetic-world-generator/scripts/world_fixture_status.py [base_dir]

Exit 0 always (this is a status report, not a gate; use scripts/verify_worlds.py
as the pass/fail gate).
"""

from __future__ import annotations

import sys
from pathlib import Path

from membench.generators.enterprise_workflow import (
    GENERATOR_VERSION as CURRENT_WORKFLOW_VERSION,
)
from membench.generators.world_manifest import (
    MANIFEST_FILE,
    read_manifest,
    verify_world,
)
from membench.schemas.world import WORLD_SCHEMA_VERSION as CURRENT_WORLD_SCHEMA


def main() -> int:
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixtures/worlds")
    world_dirs = sorted(d for d in base.glob("*") if (d / MANIFEST_FILE).exists())
    unmanifested = sorted(
        d for d in base.glob("*") if d.is_dir() and not (d / MANIFEST_FILE).exists()
    )
    if not world_dirs and not unmanifested:
        print(f"no world dirs under {base}")
        return 0

    print(
        f"current code: workflow={CURRENT_WORKFLOW_VERSION} world_schema={CURRENT_WORLD_SCHEMA}"
    )
    print()
    for d in world_dirs:
        m = read_manifest(d)
        result = verify_world(d)
        drift = []
        if m.workflow_generator_version != CURRENT_WORKFLOW_VERSION:
            drift.append(
                f"workflow {m.workflow_generator_version} -> {CURRENT_WORKFLOW_VERSION}"
            )
        if m.world_schema_version != CURRENT_WORLD_SCHEMA:
            drift.append(
                f"world_schema {m.world_schema_version} -> {CURRENT_WORLD_SCHEMA}"
            )
        status = "OK" if result.ok else "FAIL"
        print(
            f"{status:4s} {d}  seed={m.seed} nim_model={m.nim_model} "
            f"n_tasks={m.n_tasks} facts={m.facts_per_task}"
        )
        for line in drift:
            print(f"       version drift: {line}")
        for mm in result.mismatches:
            print(f"       {mm}")
        if (
            not result.ok
            and drift
            and any("sequences_sha256" in mm for mm in result.mismatches)
        ):
            print(
                "       diagnosis: sequences mismatch WITH version drift — expected "
                "(materializer moved); re-freeze under change control"
            )
        elif not result.ok and not drift:
            print(
                "       diagnosis: mismatch WITHOUT version drift — real "
                "non-determinism or an edited fixture; stop and bisect"
            )
    for d in unmanifested:
        print(f"---- {d}  (no manifest.json: predates Phase 4 or incomplete freeze)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
