#!/usr/bin/env python3
"""Read-only status report on the oracle-validity funnel artifacts.

Prints, for the current checkout: git pin, bundle-pool sizes under .mem/,
grid-ready manifest counts, and admit-report sound/broken tallies where the
report JSONs exist. Writes nothing, runs nothing paid, touches no store.

Usage (from the repo root, or pass --repo):

    python3 .claude/skills/mem-oracle-validity-wall-campaign/scripts/funnel_status.py
    python3 .../funnel_status.py --repo /path/to/mem
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return out.stdout.strip() or out.stderr.strip()
    except OSError as exc:  # git missing — report, don't crash
        return f"(git unavailable: {exc})"


def count_json_bundles(d: Path) -> int:
    return sum(1 for p in d.glob("*.json"))


def summarize_manifest(path: Path) -> str:
    """Best-effort count of entries in a grid-ready manifest (list or dict)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return f"unreadable ({exc.__class__.__name__})"
    if isinstance(data, list):
        return f"{len(data)} entries"
    if isinstance(data, dict):
        for key in ("bundles", "admitted", "work_ids", "pool"):
            if isinstance(data.get(key), list):
                return f"{len(data[key])} under '{key}'"
        return f"dict with keys {sorted(data)[:6]}"
    return "unrecognized shape"


def summarize_admit_report(path: Path) -> str:
    """Best-effort sound/broken tally from an admit/validity report JSON."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return f"unreadable ({exc.__class__.__name__})"
    if not isinstance(data, dict):
        return "unrecognized shape"
    parts = []
    for key in ("admitted", "rejected", "sound", "broken", "checked", "pool"):
        val = data.get(key)
        if isinstance(val, list):
            parts.append(f"{key}={len(val)}")
        elif isinstance(val, (int, float)):
            parts.append(f"{key}={val}")
    return " ".join(parts) if parts else f"keys: {sorted(data)[:8]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    args = ap.parse_args()
    repo = args.repo.resolve()
    if not (repo / ".git").exists() and not (repo / "src").exists():
        print(f"warning: {repo} does not look like the mem repo root", file=sys.stderr)

    print(f"repo:   {repo}")
    print(f"branch: {git(repo, 'branch', '--show-current')}")
    print(f"HEAD:   {git(repo, 'rev-parse', '--short', 'HEAD')}")
    print(
        f"ahead of origin/main: {git(repo, 'rev-list', '--count', 'origin/main..HEAD')}"
    )
    print()

    mem_dir = repo / ".mem"
    if not mem_dir.is_dir():
        print(
            ".mem/ absent — gitignored data; pools may live in a sibling worktree's .mem/"
        )
        return 0

    print("bundle pools (.mem/bundles*/, *.json count):")
    for d in sorted(mem_dir.glob("bundles*")):
        if d.is_dir():
            print(f"  {d.name:24s} {count_json_bundles(d)}")
    print()

    print("grid-ready manifests (.mem/grid-ready-pool*.json):")
    manifests = sorted(mem_dir.glob("grid-ready-pool*.json"))
    if not manifests:
        print("  none present")
    for m in manifests:
        print(f"  {m.name:40s} {summarize_manifest(m)}")
    print()

    print("admit/validity reports (.mem/*admit*.json, .mem/*validity*.json):")
    reports = sorted(
        set(mem_dir.glob("*admit*.json")) | set(mem_dir.glob("*validity*.json"))
    )
    if not reports:
        print("  none present at .mem/ top level (check the run's --report-out path)")
    for r in reports:
        print(f"  {r.name:40s} {summarize_admit_report(r)}")
    print()

    stores = sorted(mem_dir.glob("store*.db"))
    print(f"stores present: {len(stores)}")
    for s in stores:
        print(f"  {s.name:28s} {s.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
