#!/usr/bin/env python3
"""mem-bxhh.3 (Option A) -- build the REAL ``ours`` retrieval substrate for the
codeprobe ftp anchors.

The paid 3-arm ftp smoke (``run_grid_3arm_ftp.py``) STOPPED because the ``ours``
arm had nothing to retrieve: the 6 codeprobe ftp targets are *eval anchors*
(materialized bundles), absent from every work-record store, so
``mem retrieve <codeprobe-sha>`` raised "No record for work_id". Stub-injecting
empty payloads with fabricated timestamps is invalid. This builds a genuine
substrate instead: each codeprobe LANDING COMMIT becomes a closed work-record
whose ``trace.errors`` are the REAL fail-to-pass failures it fixed (the
SWE-bench parent-leg red, parsed by the same pytest signature the store fires
on), with a lesson distilled from its commit message. The 6 ftp targets are a
subset of this corpus; the surrounding earlier commits are the retrievable
neighbors, ordered by real commit-graph time so temporal leave-one-out (D6)
holds by construction.

FIREWALL = POST-CLOSE VALUE RE-SCAN (validity fork answer B, not structural+LOO):
a commit is admitted as a substrate record ONLY if a post-close re-scan of its
own parent->landing isolation actually surfaces ≥1 failure it fixed -- i.e. the
record carries real fixed-failure value, independent of any target. A commit
that merely reformatted a test file (parent leg green) carries no value signal
and is dropped, so the corpus is not padded with answer-correlated noise.

The curation is the SAME container isolation the oracle was curated with
(``harbor.ftp_curate``): landing test modules overlaid onto the parent tree, run
in the cached ``codeprobe-base:py3.11`` image. LOCAL/FREE -- git + Docker pytest
only, no agent tokens. Output: NDJSON records + lessons for ``mem import-records``
/ ``mem import-lessons``.

Usage:
    # default corpus = the 6 ftp anchors + LOO-valid predecessors touching their
    # test files; emits .mem/codeprobe-substrate/{records,lessons}.ndjson
    uv run python scripts/build_codeprobe_substrate.py

    # explicit commit list (one sha per line)
    uv run python scripts/build_codeprobe_substrate.py --commits-file shas.txt

ZFC: pure mechanism -- git introspection, container exec, structural parse. The
"lesson" is the commit's own message verbatim; no model judgment is invented.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from membench.harbor.base_image import CODEPROBE_CACHED_IMAGE
from membench.harbor.env_recon import DEFAULT_RIG_REPOS
from membench.harbor.ftp_curate import (
    FLAKY_TEST_SUBSTRINGS,
    FTP_WORKTREE_PREFIX,
    INSTALL_PACKAGES,
    _changed_paths,
    _fresh_worktree,
    _overlay_paths,
    _parent_shas,
    select_pytest_modules,
    single_parent,
)
from membench.harbor.probe_gate import _remove_worktree
from membench.harbor.repro_live import TEST_TIMEOUT_SEC
from membench.spawn import Runner

RIG = "codeprobe"

# ``--tb=line`` prints, per failure, the REPO crash line ``/app/<file>:<line>:
# <ExceptionRepr>`` (carrying the real exception class even when the short-summary
# reprcrash is empty, e.g. CliRunner assertions). The ``/app/`` prefix is required
# so stdlib frames (``/usr/local/lib/.../importlib/__init__.py: in import_module``)
# are NOT mistaken for the failure -- without that guard every collection error
# matches every other on the import machinery, a meaningless signature.
_TB_REPO_CRASH = re.compile(r"^/app/(?P<file>\S+?\.py):\d+: (?P<msg>.+)$")
# A collection error (feature-presence: a new test module fails to import at the
# parent) is reported as a header naming the test module, then an ``E
# <ExceptionType>: ...`` line; the exception carries no file of its own, so it is
# attributed to the most recent repo file (a crash frame OR this header).
_IMPORT_HEADER = re.compile(r"while importing test module '(?:/app/)?(?P<file>\S+?\.py)'")
_E_EXCEPTION = re.compile(r"^E\s+(?P<msg>(?:\w+\.)*\w*(?:Error|Exception|Failed)\b.*)$")

# Anchor data paths to the repo root (parents[2] of memory-bench/scripts/), so a
# run from any cwd resolves the same .mem/ the driver (run_grid_3arm_ftp) uses.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BUNDLES = PROJECT_ROOT / ".mem/bundles-codeprobe"
_DEFAULT_OUT = PROJECT_ROOT / ".mem/codeprobe-substrate"
# Per target test file, how many most-recent LOO-valid predecessor commits to
# curate as neighbors. Bounds the Docker run count; the cap is logged, never silent.
_PREDECESSORS_PER_FILE = 6


def parse_pytest_failures(stdout: str) -> list[dict[str, object]]:
    """The real per-failure ``file`` + exception ``message`` as TraceError dicts
    (tool=pytest, severity=error, line=0). ``line`` is 0 by design -- the store's
    pytest recurrence axis is line-invariant (``tool:basename:error_class``), so
    pinning 0 lets two commits failing the same way in one file share a signature.
    Env-flaky tests are deselected at the run level (``-k``), so no nodeid filter
    is needed here. Duplicate ``file:message`` pairs collapse to one error so a
    record's signature set is the distinct failure modes it fixed."""
    errors: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add(file: str, message: str) -> None:
        key = (file, message)
        if key in seen:
            return
        seen.add(key)
        errors.append(
            {"tool": "pytest", "severity": "error", "message": message, "file": file, "line": 0}
        )

    current_repo_file: str | None = None
    for line in stdout.splitlines():
        header = _IMPORT_HEADER.search(line)
        if header is not None:
            current_repo_file = header.group("file")
            continue
        crash = _TB_REPO_CRASH.match(line)
        if crash is not None:
            file, msg = crash.group("file"), crash.group("msg").strip()
            current_repo_file = file
            # A bare frame marker ("in <module>", "in import_module") is the import
            # chain, not the failure -- the real exception arrives on an E-line.
            if not msg.startswith("in "):
                add(file, msg)
            continue
        exc = _E_EXCEPTION.match(line)
        if exc is not None and current_repo_file is not None:
            add(current_repo_file, exc.group("msg").strip())
    return errors


def _deselect_flaky_expr() -> list[str]:
    """``-k`` expression that deselects the oracle's env-flaky tests by name, so
    they never enter the run (the count is deterministic, matching curate-ftp)."""
    if not FLAKY_TEST_SUBSTRINGS:
        return []
    expr = " and ".join(f"not {sub}" for sub in FLAKY_TEST_SUBSTRINGS)
    return ["-k", expr]


def _container_stdout_cmd(test_files: list[str], *, chown_to: str | None) -> str:
    """``bash -lc`` string: install the rig (deps baked in the cached image, so
    this is fast), then run the scoped tests with ``--tb=line`` so each failure's
    repo crash line + exception surfaces on stdout. No junit -- stdout is the signal.

    The container runs as root, so ``pip install -e .`` writes a root-owned
    ``*.egg-info``/``__pycache__`` into the mounted worktree; ``chown_to``
    (``uid:gid``) hands the tree back to the host user as the final step (with
    ``;`` not ``&&`` so a nonzero pytest exit -- the normal red case -- still
    restores ownership) or host-side ``git worktree remove`` fails on the
    root-owned files and the checkout leaks (mirrors ftp_curate.container_command)."""
    install = "pip install " + shlex.join(INSTALL_PACKAGES) + " -q"
    # --tb=line prints one ``file:line: ExceptionRepr`` traceback per failure (no
    # -q, which would suppress that section); that line carries the real exception
    # class even when the short-summary reprcrash is empty. Flaky tests are
    # deselected by name so they never run.
    pytest = shlex.join(
        ["pytest", *test_files, "--tb=line", "-p", "no:cacheprovider", *_deselect_flaky_expr()]
    )
    cmd = f"{install} && {pytest}"
    if chown_to is not None:
        cmd += f" ; chown -R --no-dereference {shlex.quote(chown_to)} /app"
    return cmd


def _run_parent_leg(worktree: Path, test_files: list[str], image: str, runner: Runner) -> str:
    """Run ``test_files`` over the mounted parent worktree (landing tests already
    overlaid) and return raw stdout. A nonzero pytest exit is the NORMAL case
    (the tests are red), so the exit code is not asserted; only an infra failure
    (timeout, no output at all) raises."""
    chown_to = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
    argv = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{worktree}:/app",
        "-w",
        "/app",
        image,
        "bash",
        "-lc",
        _container_stdout_cmd(test_files, chown_to=chown_to),
    ]
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=TEST_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pytest in {image} timed out after {TEST_TIMEOUT_SEC}s") from exc
    out = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if not out.strip():
        raise RuntimeError(f"pytest produced no output (install/collection likely failed): {argv}")
    return out


def _git_lines(clone: Path, args: list[str]) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(clone), *args], capture_output=True, text=True, check=True
    )
    return [line for line in completed.stdout.splitlines() if line]


def _git_field(clone: Path, sha: str, fmt: str) -> str:
    return subprocess.run(
        ["git", "-C", str(clone), "show", "-s", f"--format={fmt}", sha],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def curate_commit_record(
    landing_sha: str,
    clone: Path,
    *,
    image: str,
    runner: Runner = subprocess.run,
    worktree_root: Path = Path("/tmp"),
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Curate ONE landing commit into a (work_record, lesson) pair, or None when
    the post-close value re-scan finds no fixed failures (no test-touching diff,
    or a green parent leg). Worktrees are always cleaned up, even on error."""
    parent_sha = single_parent(_parent_shas(clone, landing_sha, runner))
    changed = _changed_paths(clone, parent_sha, landing_sha, runner)
    run_files = select_pytest_modules(changed)
    if not run_files:
        return None

    wt_parent = worktree_root / f"{FTP_WORKTREE_PREFIX}{RIG}-{parent_sha[:12]}-sub"
    _fresh_worktree(clone, parent_sha, wt_parent, runner)
    try:
        _overlay_paths(wt_parent, landing_sha, run_files, runner)
        stdout = _run_parent_leg(wt_parent, run_files, image, runner)
    finally:
        _remove_worktree(clone, wt_parent, runner)

    errors = parse_pytest_failures(stdout)
    if not errors:
        # Post-close value re-scan: parent leg green -> this commit fixed nothing
        # the oracle can see; not a retrievable neighbor.
        return None

    work_id = f"{RIG}-{landing_sha[:12]}"
    parent_date = _git_field(clone, parent_sha, "%cI")
    landing_date = _git_field(clone, landing_sha, "%cI")
    subject = _git_field(clone, landing_sha, "%s")
    body = _git_field(clone, landing_sha, "%b")
    files = _git_lines(clone, ["show", "--name-only", "--format=", landing_sha])

    record: dict[str, object] = {
        "work_id": work_id,
        "rig": RIG,
        "repo": RIG,
        "title": subject,
        "labels": ["codeprobe-substrate"],
        "lifecycle": {
            "created": parent_date,
            "started": parent_date,
            "closed": landing_date,
            "status": "closed",
        },
        "outcome": {"commit_sha": landing_sha},
        "trace": {
            "jsonl_path": f"git:{RIG}/{landing_sha[:12]}",
            "errors": errors,
        },
    }
    facts = [f"fixed {len(errors)} fail-to-pass test(s) in {len(files)} changed file(s)"]
    facts += [f"touched {p}" for p in files if p.startswith("tests/")][:8]
    lesson: dict[str, object] = {
        "work_id": work_id,
        "extracted_at": landing_date,
        "commit_sha": landing_sha,
        "payload": {
            "subtitle": subject,
            "facts": facts,
            "narrative": body or subject,
            "concepts": ["what-changed", "problem-solution"],
        },
    }
    return record, lesson


def _target_shas(bundles_dir: Path) -> list[str]:
    """The full landing SHAs of the materialized ftp anchor bundles."""
    shas: list[str] = []
    for path in sorted(bundles_dir.glob("*.json")):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        oracle = bundle.get("verification", {}).get("ftp_oracle")
        if oracle:
            shas.append(oracle["commit"])
    return shas


def _select_corpus(clone: Path, bundles_dir: Path, *, log: Callable[[str], None]) -> list[str]:
    """Targets + LOO-valid predecessors touching each target's ftp test files.

    A predecessor qualifies if it modified one of the target's test files and its
    commit date is strictly before the TARGET's parent date (the D6 boundary the
    record's ``started`` will carry). Capped per file (logged), de-duped, ordered
    oldest->newest so the import is deterministic."""
    seen: dict[str, None] = {}
    targets: list[str] = []
    for path in sorted(bundles_dir.glob("*.json")):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        oracle = bundle.get("verification", {}).get("ftp_oracle")
        if not oracle:
            continue
        landing = oracle["commit"]
        parent = oracle["parent"]
        targets.append(landing)
        seen.setdefault(landing, None)
        parent_epoch = int(_git_field(clone, parent, "%ct"))
        test_files = sorted(
            {t.split("::")[0].replace(".", "/") + ".py" for t in oracle["ftp_tests"]}
        )
        for tf in test_files:
            entries = _git_lines(clone, ["log", "--format=%H|%ct", "--follow", "--", tf])
            kept = 0
            for entry in entries:
                sha, ct = entry.split("|")
                if int(ct) >= parent_epoch:
                    continue  # not strictly before the target's started boundary
                if sha in seen:
                    continue
                seen.setdefault(sha, None)
                kept += 1
                if kept >= _PREDECESSORS_PER_FILE:
                    log(f"  {tf}: capped predecessors at {_PREDECESSORS_PER_FILE}")
                    break
    log(f"corpus: {len(targets)} target(s) + {len(seen) - len(targets)} predecessor(s)")
    # Oldest first by commit time for a stable import order.
    ordered = sorted(seen, key=lambda s: int(_git_field(clone, s, "%ct")))
    return ordered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, default=_DEFAULT_BUNDLES)
    parser.add_argument("--commits-file", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    parser.add_argument("--image", default=CODEPROBE_CACHED_IMAGE)
    parser.add_argument("--clone", type=Path, default=DEFAULT_RIG_REPOS[RIG])
    args = parser.parse_args(argv)

    clone = args.clone
    if args.commits_file is not None:
        shas = [line.strip() for line in args.commits_file.read_text().splitlines() if line.strip()]
        print(f"corpus: {len(shas)} commit(s) from {args.commits_file}")
    else:
        if not args.bundles_dir.exists():
            print(f"error: bundles dir not found: {args.bundles_dir}", file=sys.stderr)
            return 1
        shas = _select_corpus(clone, args.bundles_dir, log=print)

    args.out.mkdir(parents=True, exist_ok=True)
    records_path = args.out / "records.ndjson"
    lessons_path = args.out / "lessons.ndjson"

    records: list[dict[str, object]] = []
    lessons: list[dict[str, object]] = []
    skipped = 0
    errored = 0
    for sha in shas:
        try:
            curated = curate_commit_record(sha, clone, image=args.image)
        except (RuntimeError, ValueError) as exc:
            errored += 1
            print(f"{sha[:12]}: uncurate-able -- {exc}")
            continue
        if curated is None:
            skipped += 1
            print(f"{sha[:12]}: no fixed-failure value (skipped)")
            continue
        record, lesson = curated
        records.append(record)
        lessons.append(lesson)
        n_err = len(record["trace"]["errors"])  # type: ignore[index]
        print(f"{sha[:12]}: {n_err} fixed-failure(s) -> {record['work_id']}")

    records_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    lessons_path.write_text("\n".join(json.dumps(le) for le in lessons) + "\n", encoding="utf-8")
    print(
        f"\n{len(shas)} commit(s) -> {len(records)} substrate record(s) "
        f"({skipped} no-value, {errored} errored)"
    )
    print(f"records -> {records_path}")
    print(f"lessons -> {lessons_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
