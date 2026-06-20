"""Cached base-image build (mem-bxhh.3.1): bake a rig's dependency closure into an
image so graded/curation runs find deps already in site-packages.

The Dockerfile rendering is pure; the build is exercised with an injected runner
(no real docker) that stands in for `git archive` + `docker build` -- the
env_recon / ftp_curate idiom.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from membench.harbor.base_image import (
    CODEPROBE_BASE_IMAGE,
    CODEPROBE_CACHED_IMAGE,
    CODEPROBE_INSTALL_STEPS,
    build_base_image,
    build_codeprobe_base_image,
)
from membench.harbor.env_recon import Runner, render_dockerfile


def test_render_dockerfile_without_install_steps_is_unchanged() -> None:
    df = render_dockerfile("python:3.11-bookworm")
    assert df.startswith("FROM python:3.11-bookworm\n")
    assert "tar -xf" in df
    assert "RUN pip install" not in df  # no install layer when none requested


def test_render_dockerfile_appends_install_steps_after_extract() -> None:
    df = render_dockerfile(
        "python:3.11-bookworm", install_steps=("pip install -e . pytest -q", "echo done")
    )
    extract_at = df.index("tar -xf")
    install_at = df.index("RUN pip install -e . pytest -q")
    echo_at = df.index("RUN echo done")
    # The install layer runs in the extracted /app, so it must come AFTER the
    # archive extraction, and the steps keep their given order.
    assert extract_at < install_at < echo_at


def _recording_runner(calls: list[list[str]]) -> Runner:
    """A Runner that records argvs and writes a stand-in tar for git-archive."""

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        if argv[:2] == ["git", "-C"]:
            dest = argv[argv.index("-o") + 1]
            Path(dest).write_bytes(b"tar-bytes")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def test_build_base_image_writes_context_and_runs_docker_build(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    fake_runner = _recording_runner(calls)

    tag = build_base_image(
        repo=Path("/home/ds/projects/codeprobe"),
        commit="deadbeef",
        tag="codeprobe-base:py3.11",
        base_image="python:3.11-bookworm",
        install_steps=("pip install -e . pytest -q",),
        context_root=tmp_path,
        runner=fake_runner,
    )
    assert tag == "codeprobe-base:py3.11"

    archive_calls = [c for c in calls if c[:2] == ["git", "-C"] and "archive" in c]
    build_calls = [c for c in calls if c[:2] == ["docker", "build"]]
    assert len(archive_calls) == 1
    assert len(build_calls) == 1
    # docker build is tagged and points at a context dir.
    assert "-t" in build_calls[0] and "codeprobe-base:py3.11" in build_calls[0]
    # The build context is removed on the success path (no /tmp litter).
    assert not any(tmp_path.iterdir())


def test_build_base_image_raises_on_docker_failure_and_cleans_up(tmp_path: Path) -> None:
    def failing_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["git", "-C"]:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"x")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "no space left on device")

    with pytest.raises(RuntimeError, match="docker build"):
        build_base_image(
            repo=Path("/repo"),
            commit="c",
            tag="t:1",
            base_image="python:3.11-bookworm",
            install_steps=("pip install -e .",),
            context_root=tmp_path,
            runner=failing_runner,
        )
    # The `finally` removes the context even when the build fails -- no litter.
    assert not any(tmp_path.iterdir())


def test_build_codeprobe_base_image_uses_the_codeprobe_constants(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    tag = build_codeprobe_base_image(
        Path("/home/ds/projects/codeprobe"),
        "deadbeef",
        context_root=tmp_path,
        runner=_recording_runner(calls),
    )
    assert tag == CODEPROBE_CACHED_IMAGE
    build = next(c for c in calls if c[:2] == ["docker", "build"])
    assert build[build.index("-t") + 1] == CODEPROBE_CACHED_IMAGE


def test_codeprobe_constants_are_consistent() -> None:
    # The opt-in cached image + its bake steps are exposed for callers (curate-ftp
    # --base-image, the grid materializer) -- NOT wired as a silent default.
    assert ":" in CODEPROBE_CACHED_IMAGE  # a tagged image ref, not a bare name
    assert CODEPROBE_BASE_IMAGE.startswith("python:")  # codeprobe is a python rig
    assert any("pip install -e ." in step for step in CODEPROBE_INSTALL_STEPS)
