"""The pinned-build path: what `--bd-ref <remote> <sha>` fetches, builds, caches and refuses.

Nothing here spawns git or go. The point of every test is the DECISION the module makes about a
binary, and a real build would test the Go toolchain rather than that decision.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from membench.runner.bd_ref import build_bd_ref
from membench.runner.tool_surface import MemoryToolError

SHA = "5b9e938b5"


def _a_binary(path: Path) -> None:
    """A file that `resolve_bd_binary` will accept: present and EXECUTABLE. The exec bit matters --
    the resolver refuses a non-executable path, which is the whole reason it cannot be fooled by a
    stray file sitting at the cache key."""
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


def a_runner(
    *, commit: str = SHA, build_writes: bool = True, source: Path | None = None
) -> tuple[object, list[list[str]]]:
    """A runner that answers `bd version --json` with `commit` and, on `make build`, writes a
    binary into the tree. Returns it with the log of argvs it was asked to run."""
    seen: list[list[str]] = []

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        stdout = ""
        if len(argv) >= 2 and argv[1] == "version":
            stdout = json.dumps({"build": commit, "version": "1.3.0-rc.1"})
        if argv[0] == "make" and build_writes and source is not None:
            _a_binary(source / "bd")
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")

    return run, seen


def test_a_branch_name_is_refused_before_anything_is_fetched(tmp_path: Path) -> None:
    """A turn names an immutable instrument or it names nothing. `main` is different code on
    different days, so an artifact recording it cannot be gone back to by the name it was given --
    and the refusal has to come before the fetch, or the run has already measured something it
    cannot describe."""
    run, seen = a_runner()
    with pytest.raises(MemoryToolError) as caught:
        build_bd_ref("origin", "main", cache_dir=tmp_path, runner=run)  # type: ignore[arg-type]
    assert "immutable commit" in str(caught.value)
    assert seen == [], "a refused ref must not reach git"


def test_a_pinned_commit_is_fetched_built_and_identified(tmp_path: Path) -> None:
    source = tmp_path / SHA / "src"
    run, seen = a_runner(source=source)

    build = build_bd_ref("git@github.com:sjarmak/beads", SHA, cache_dir=tmp_path, runner=run)  # type: ignore[arg-type]

    assert build.commit == SHA
    assert Path(build.binary) == tmp_path / SHA / "bd"
    assert build.sha256, "the artifact keys on the bytes, not only the commit"
    programs = [argv[0] for argv in seen]
    assert programs.count("make") == 1, "built once, with the project's own Makefile"
    assert "git" in programs


def test_a_build_that_reports_another_commit_is_refused(tmp_path: Path) -> None:
    """The tree that was built is not the tree that was asked for. Serving it would attribute a
    whole turn -- every cell, every dollar -- to a build that never ran."""
    source = tmp_path / SHA / "src"
    run, _ = a_runner(commit="deadbeef", source=source)
    with pytest.raises(MemoryToolError) as caught:
        build_bd_ref("origin", SHA, cache_dir=tmp_path, runner=run)  # type: ignore[arg-type]
    assert "does not report that commit" in str(caught.value)


def test_a_build_that_produced_no_binary_never_falls_back_to_the_ambient_bd(
    tmp_path: Path,
) -> None:
    """The failure mode this module exists to prevent. The ambient bd on this machine is a
    different build from any commit a turn pins, so a silent fallback would measure the wrong
    instrument while the artifact named the right one."""
    source = tmp_path / SHA / "src"
    run, _ = a_runner(build_writes=False, source=source)
    with pytest.raises(MemoryToolError) as caught:
        build_bd_ref("origin", SHA, cache_dir=tmp_path, runner=run)  # type: ignore[arg-type]
    assert "refusing to fall back" in str(caught.value).lower()


def test_the_second_turn_against_one_commit_reuses_the_cached_build(tmp_path: Path) -> None:
    """A turn fires many cells against one commit. Rebuilding per fire would be slow, and worse:
    two fires in one grid could disagree about what they measured."""
    source = tmp_path / SHA / "src"
    run, seen = a_runner(source=source)
    first = build_bd_ref("origin", SHA, cache_dir=tmp_path, runner=run)  # type: ignore[arg-type]
    builds_after_first = [argv for argv in seen if argv[0] == "make"]

    second = build_bd_ref("origin", SHA, cache_dir=tmp_path, runner=run)  # type: ignore[arg-type]

    assert second.binary == first.binary
    assert second.sha256 == first.sha256
    assert [
        argv for argv in seen if argv[0] == "make"
    ] == builds_after_first, "the cached commit was rebuilt"


def test_a_cached_binary_that_reports_the_wrong_commit_is_rebuilt_not_served(
    tmp_path: Path,
) -> None:
    """The cache is keyed on the commit alone, so a stale or hand-placed file can sit at that key.
    Trusting the key would hand back a binary that is not what it claims; the check is on what the
    binary SAYS, so the wrong one costs a rebuild rather than a turn."""
    source = tmp_path / SHA / "src"
    (tmp_path / SHA).mkdir(parents=True)
    _a_binary(tmp_path / SHA / "bd")

    answers = iter(["deadbeef", SHA])
    seen: list[list[str]] = []

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        stdout = ""
        if len(argv) >= 2 and argv[1] == "version":
            stdout = json.dumps({"build": next(answers), "version": "x"})
        if argv[0] == "make":
            source.mkdir(parents=True, exist_ok=True)
            _a_binary(source / "bd")
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")

    build = build_bd_ref("origin", SHA, cache_dir=tmp_path, runner=run)  # type: ignore[arg-type]

    assert build.commit == SHA
    assert [argv[0] for argv in seen].count("make") == 1, "the stale entry was served, not rebuilt"
