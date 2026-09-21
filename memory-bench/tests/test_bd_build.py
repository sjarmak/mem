"""The bd build a fire measures, pinned by what the binary IS (mem-0wpq8.2).

The results record carried the CLI version, the corpus fingerprint and the recognizer version
and NOT the bd it wrapped: two fires against two beads builds would have resumed into one grid.
The flywheel's whole variable per turn is the beads build, so it has to be the one thing the
identity cannot miss.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from membench.runner.bd_build import BdBuild, resolve_bd_build
from membench.runner.tool_surface import ENV_BD_BINARY, MemoryToolError


def a_bd(tmp_path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    binary = tmp_path / "bd"
    binary.write_text(body, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


def version_runner(payload: object) -> object:
    def run(argv: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        argv_list = list(argv)  # type: ignore[call-overload]
        assert argv_list[1:] == ["version", "--json"], argv_list
        return subprocess.CompletedProcess(argv_list, 0, json.dumps(payload), "")

    return run


def test_the_build_is_the_binarys_bytes_and_its_own_stated_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = a_bd(tmp_path)
    monkeypatch.setenv(ENV_BD_BINARY, str(binary))
    build = resolve_bd_build(
        runner=version_runner({"build": "e9d2f1778", "version": "1.3.0-rc.1", "branch": "main"})
    )
    assert build == BdBuild(
        binary=str(binary.resolve()),
        sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        commit="e9d2f1778",
        version="1.3.0-rc.1",
    )
    assert build.identity() == {"bd_binary_sha256": build.sha256, "bd_commit": "e9d2f1778"}


def test_a_binary_that_cannot_name_its_commit_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank commit would match another blank commit: a build that cannot say what it is
    must not be pooled with one that can, or with another that cannot."""
    monkeypatch.setenv(ENV_BD_BINARY, str(a_bd(tmp_path)))
    with pytest.raises(MemoryToolError, match="commit"):
        resolve_bd_build(runner=version_runner({"version": "1.3.0-rc.1", "build": ""}))
    with pytest.raises(MemoryToolError):
        resolve_bd_build(runner=version_runner({"version": "1.3.0-rc.1"}))


def test_a_binary_whose_version_call_fails_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_BD_BINARY, str(a_bd(tmp_path)))

    def failing(argv: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 1, "", "boom")  # type: ignore[call-overload]

    with pytest.raises(MemoryToolError):
        resolve_bd_build(runner=failing)


def test_two_builds_of_the_same_commit_differ_by_their_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuilt binary at the same commit (different toolchain, a local patch not committed) is a
    different instrument; the commit alone would call it the same one."""
    payload = {"build": "abc1234", "version": "1.3.0"}
    first_dir = tmp_path / "one"
    first_dir.mkdir()
    monkeypatch.setenv(ENV_BD_BINARY, str(a_bd(first_dir, "#!/bin/sh\nexit 0\n")))
    first = resolve_bd_build(runner=version_runner(payload))
    second_dir = tmp_path / "two"
    second_dir.mkdir()
    monkeypatch.setenv(ENV_BD_BINARY, str(a_bd(second_dir, "#!/bin/sh\nexit 1\n")))
    second = resolve_bd_build(runner=version_runner(payload))
    assert first.commit == second.commit
    assert first.sha256 != second.sha256
    assert first.identity() != second.identity()


def test_the_real_binary_is_read_when_nothing_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Against whatever bd is on PATH, if any: the sha is the file's, the commit is bd's own."""
    monkeypatch.delenv(ENV_BD_BINARY, raising=False)
    try:
        build = resolve_bd_build()
    except MemoryToolError as exc:
        pytest.skip(f"no bd on PATH: {exc}")
    assert os.path.isabs(build.binary)
    assert build.sha256 == hashlib.sha256(Path(build.binary).read_bytes()).hexdigest()
    assert build.commit
