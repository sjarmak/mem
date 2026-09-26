from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests import paths


def _configure_cli_tree(monkeypatch: pytest.MonkeyPatch, root: Path) -> tuple[Path, Path]:
    source_root = root / "src"
    source_root.mkdir()
    artifact = root / "dist" / "main.js"
    artifact.parent.mkdir()
    mem_bin = root / "bin" / "mem"
    mem_bin.parent.mkdir()
    mem_bin.touch()
    (root / "node_modules").mkdir()
    monkeypatch.setattr(paths, "REPO_ROOT", root)
    monkeypatch.setattr(paths, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(paths, "MEM_BIN", mem_bin)
    monkeypatch.setattr(paths.shutil, "which", lambda executable: f"/tools/{executable}")
    return source_root, artifact


def test_require_mem_cli_skips_without_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.shutil, "which", lambda executable: None)

    with pytest.raises(pytest.skip.Exception, match="node not available"):
        paths.require_mem_cli(paths.DIST_MAIN)


def test_require_mem_cli_skips_without_node_modules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(paths.shutil, "which", lambda executable: f"/tools/{executable}")

    with pytest.raises(pytest.skip.Exception, match="TS runtime deps missing"):
        paths.require_mem_cli(tmp_path / "dist" / "main.js")


def test_require_mem_cli_fails_when_build_artifact_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root, artifact = _configure_cli_tree(monkeypatch, tmp_path)
    (source_root / "main.ts").touch()

    with pytest.raises(pytest.fail.Exception, match="TS build missing"):
        paths.require_mem_cli(artifact)


def test_require_mem_cli_fails_when_build_artifact_is_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root, artifact = _configure_cli_tree(monkeypatch, tmp_path)
    source = source_root / "main.ts"
    source.touch()
    artifact.touch()
    os.utime(artifact, ns=(1_000_000_000, 1_000_000_000))
    os.utime(source, ns=(2_000_000_000, 2_000_000_000))

    with pytest.raises(pytest.fail.Exception, match="TS build stale"):
        paths.require_mem_cli(artifact)


def test_require_mem_cli_accepts_artifact_newer_than_all_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root, artifact = _configure_cli_tree(monkeypatch, tmp_path)
    source = source_root / "main.ts"
    source.touch()
    artifact.touch()
    os.utime(source, ns=(1_000_000_000, 1_000_000_000))
    os.utime(artifact, ns=(2_000_000_000, 2_000_000_000))

    assert paths.require_mem_cli(artifact) == "/tools/node"


def test_python_ci_builds_typescript_before_pytest() -> None:
    workflow_path = paths.REPO_ROOT / ".github" / "workflows" / "ci.yml"
    workflow: dict[str, Any] = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["python"]["steps"]
    commands = [shlex.split(str(step.get("run", ""))) for step in steps]
    setup_node = next(
        (
            index
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("actions/setup-node@")
        ),
        None,
    )
    npm_ci = next(
        (index for index, command in enumerate(commands) if command == ["npm", "ci"]), None
    )
    npm_build = next(
        (index for index, command in enumerate(commands) if command == ["npm", "run", "build"]),
        None,
    )
    pytest_run = next(
        (index for index, command in enumerate(commands) if command[:1] == ["pytest"]),
        None,
    )

    assert setup_node is not None
    assert npm_ci is not None
    assert npm_build is not None
    assert pytest_run is not None
    assert setup_node < npm_ci < npm_build < pytest_run
