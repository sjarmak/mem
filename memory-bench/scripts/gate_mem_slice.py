"""Run the CSB fail-to-pass validity gate on the mem slice recovered by the
commit-linkage backfill.

Each recovered link gives the LANDING commit; the gold diff is `<sha>^..<sha>`
with base = parent (exact by construction). We build a minimal TaskBundle from
that and run the project's own LiveReproRunner (worktree @ base, git apply impl,
apply gold tests, npx vitest run) through validity_gate. A bundle is a SOUND
oracle iff the gold diff reproduces (tests pass) AND the empty diff fails.

Usage: python scripts/gate_mem_slice.py [--limit N] [--repo DIR] [--store DB]
(repo/store default to this checkout; override with the flags or MEM_REPO/MEM_STORE).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from membench.bundle.replay import ReplayResult
from membench.grading.dual_verifier import is_test_path
from membench.grading.validity_gate import validity_gate
from membench.harbor.repro_live import LiveReproRunner
from membench.schemas.bundle import BundleEnv, TaskBundle

# Default to the repo this script lives in (memory-bench/scripts/ → two levels up),
# overridable via --repo/--store or the MEM_REPO/MEM_STORE env vars. No hardcoded
# absolute path, so the gate runs on any checkout.
_DEFAULT_REPO = str(Path(__file__).resolve().parents[2])
REPO = os.environ.get("MEM_REPO", _DEFAULT_REPO)
STORE = os.environ.get("MEM_STORE", str(Path(REPO) / ".mem" / "store-v7-linked.db"))


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", REPO, *args], capture_output=True, text=True, check=True
    ).stdout


def eligible_bundles() -> list[tuple[str, str, str, str | None]]:
    db = sqlite3.connect(STORE)
    rows = db.execute(
        "SELECT work_id, commit_sha, title, trace_path FROM work_records "
        "WHERE rig='mem' AND commit_sha IS NOT NULL"
    ).fetchall()
    out = []
    for work_id, sha, title, trace in rows:
        try:
            parents = git("rev-list", "--parents", "-n", "1", sha).strip().split()[1:]
            if len(parents) != 1:
                continue
            files = git("diff", "--name-only", f"{sha}^", sha).strip().splitlines()
        except subprocess.CalledProcessError:
            continue
        if any(f.startswith("tests/") and f.endswith(".test.ts") for f in files):
            out.append((work_id, sha, title or work_id, trace))
    return out


def build_bundle(work_id: str, sha: str, title: str, trace: str | None) -> TaskBundle:
    base = git("rev-parse", f"{sha}^").strip()
    files = git("diff", "--name-only", f"{sha}^", sha).strip().splitlines()
    file_diffs = {f: git("diff", f"{sha}^", sha, "--", f) for f in files if f}
    return TaskBundle(
        work_id=work_id,
        rig="mem",
        issue_title=title,
        trace_ref=trace or f"recovered://{work_id}",
        output=ReplayResult(calls=(), file_diffs=file_diffs, replay_success_rate=1.0),
        env=BundleEnv(repo="sjarmak/mem", base_commit=base, base_image="local"),
        loo_excluded_work_ids=(work_id,),
    )


def main() -> None:
    global REPO, STORE

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repo", default=REPO, help="repo root (default: derived; env MEM_REPO)")
    ap.add_argument(
        "--store",
        default=None,
        help="path to the linked store db (default: <repo>/.mem/store-v7-linked.db; env MEM_STORE)",
    )
    args = ap.parse_args()

    REPO = args.repo
    STORE = args.store or str(Path(REPO) / ".mem" / "store-v7-linked.db")

    bundles = eligible_bundles()
    if args.limit:
        bundles = bundles[: args.limit]
    print(f"vitest-eligible mem bundles: {len(bundles)}\n", flush=True)

    valid = 0
    results = []
    with LiveReproRunner() as runner:
        for work_id, sha, title, trace in bundles:
            try:
                bundle = build_bundle(work_id, sha, title, trace)
                impl = [p for p in bundle.output.diff_by_file() if not is_test_path(p)]
                tests = [p for p in bundle.output.diff_by_file() if is_test_path(p)]
                res = validity_gate(bundle, test_runner=runner)
            except Exception as exc:
                print(f"  {work_id:14s} ERROR: {exc}", flush=True)
                results.append((work_id, "error", str(exc)[:80]))
                continue
            mark = "SOUND" if res.valid else "reject"
            if res.valid:
                valid += 1
            print(
                f"  {work_id:14s} {mark:6s} gold={res.gold_repro_passed} "
                f"empty_fail={not res.empty_repro_passed} "
                f"(impl={len(impl)},test={len(tests)})  {res.reason[:70]}",
                flush=True,
            )
            results.append((work_id, mark, res.reason))

    print(f"\n=== mem slice: {valid}/{len(bundles)} SOUND fail-to-pass oracles ===")


if __name__ == "__main__":
    sys.exit(main())
