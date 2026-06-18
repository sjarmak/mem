#!/usr/bin/env python3
"""Verify frozen worlds reproduce their task instances — NO model call (CI-safe).

For each ``<base>/<seed>/`` world dir, re-hashes the frozen world/project and
re-materialises the sequences from the manifest, comparing to the recorded hashes.
This is the determinism guarantee (mem-ge51): a frozen fixture + its manifest
reproduces the exact task instances without re-running NeMo.

    PYTHONPATH=. python3 scripts/verify_worlds.py fixtures/worlds
"""

from __future__ import annotations

import argparse
from pathlib import Path

from membench.generators.world_manifest import MANIFEST_FILE, verify_world


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base", nargs="?", default="fixtures/worlds")
    args = ap.parse_args()

    base = Path(args.base)
    world_dirs = sorted(d for d in base.glob("*") if (d / MANIFEST_FILE).exists())
    if not world_dirs:
        print(f"no manifested worlds under {base}")
        return 0

    failed = 0
    for d in world_dirs:
        result = verify_world(d)
        if result.ok:
            print(f"OK    {d}")
        else:
            failed += 1
            print(f"FAIL  {d}")
            for m in result.mismatches:
                print(f"        - {m}")
    print(f"\n{len(world_dirs) - failed}/{len(world_dirs)} worlds reproduce deterministically")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
