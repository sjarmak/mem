"""Approximate environment reconstruction (mem-apg.3.1, D17).

Commit resolution + archiving run against a REAL temp git repo (not a mocked runner),
so the git contract is genuinely exercised. Dockerfile rendering is a pure string check.
"""

import subprocess
import tarfile
from pathlib import Path

import pytest

from membench.harbor.env_recon import (
    DEFAULT_BASE_IMAGE,
    reconstruct_env,
    reconstruct_env_for_record,
    render_dockerfile,
    resolve_base_commit,
    write_repo_archive,
)


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _commit(repo: Path, name: str, content: str, when: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    base = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", name, env=base)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _commit(r, "old.txt", "old", "2026-01-01T00:00:00")
    _commit(r, "new.txt", "new", "2026-03-01T00:00:00")
    return r


# --- resolve_base_commit: timestamp -> commit (approximate, never a future one) --


def test_resolves_commit_at_or_before_timestamp(repo: Path):
    # A boundary in February sees only the January commit -> old.txt exists, new.txt not.
    commit = resolve_base_commit(repo, "2026-02-01T00:00:00")
    files = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "--name-only", commit],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "old.txt" in files
    assert "new.txt" not in files


def test_resolves_later_boundary_sees_both(repo: Path):
    commit = resolve_base_commit(repo, "2026-06-01T00:00:00")
    files = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "--name-only", commit],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert {"old.txt", "new.txt"} <= set(files)


def test_no_commit_before_boundary_raises(repo: Path):
    with pytest.raises(RuntimeError, match="no commit"):
        resolve_base_commit(repo, "2020-01-01T00:00:00")


def test_base_ref_selects_a_different_branch(repo: Path):
    # A side branch with a later-but-distinct file; resolving against it (not HEAD)
    # must pick that branch's commit. Guards the stale-HEAD bug: the host's gascity
    # HEAD is a months-old PR-check ref, so the resolver must honor base_ref.
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side.txt", "s", "2026-04-01T00:00:00")
    _git(repo, "checkout", "-q", "-")
    commit = resolve_base_commit(repo, "2026-05-01T00:00:00", base_ref="side")
    files = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "--name-only", commit],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "side.txt" in files


# --- write_repo_archive: snapshot at a commit, no .git history -------------------


def test_archive_contains_commit_tree_not_history(repo: Path, tmp_path: Path):
    commit = resolve_base_commit(repo, "2026-02-01T00:00:00")
    dest = tmp_path / "out" / "repo.tar"
    write_repo_archive(repo, commit, dest)
    with tarfile.open(dest) as tar:
        names = tar.getnames()
    assert "old.txt" in names
    assert "new.txt" not in names
    assert not any(n.startswith(".git") for n in names)


# --- render_dockerfile: lands the archive at /app -------------------------------


def test_dockerfile_extracts_archive_to_app():
    df = render_dockerfile("golang:1.23-bookworm")
    assert df.startswith("FROM golang:1.23-bookworm\n")
    assert "WORKDIR /app" in df
    assert "COPY repo.tar" in df
    assert "tar -xf /tmp/repo.tar -C /app" in df


# --- reconstruct_env: writes environment/{Dockerfile,repo.tar} ------------------


def test_reconstruct_env_writes_both_artifacts(repo: Path, tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    commit = resolve_base_commit(repo, "2026-02-01T00:00:00")
    env_dir = reconstruct_env(task_dir, repo=repo, commit=commit, base_image="ubuntu:24.04")
    assert (env_dir / "Dockerfile").exists()
    assert (env_dir / "repo.tar").exists()
    assert env_dir == task_dir / "environment"


# --- reconstruct_env_for_record: rig + started_at -> env ------------------------


def test_for_record_resolves_rig_and_timestamp(repo: Path, tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    record = {
        "work_id": "x-1",
        "rig": "myrig",
        "lifecycle": {"started": "2026-02-01T00:00:00"},
    }
    env_dir = reconstruct_env_for_record(
        task_dir, record, rig_repos={"myrig": repo}, base_images={}, base_ref="HEAD"
    )
    assert (env_dir / "Dockerfile").read_text().startswith(f"FROM {DEFAULT_BASE_IMAGE}\n")
    with tarfile.open(env_dir / "repo.tar") as tar:
        assert "old.txt" in tar.getnames()


def test_for_record_unknown_rig_raises(tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    record = {
        "work_id": "x-1",
        "rig": "ghost",
        "lifecycle": {"started": "2026-02-01T00:00:00"},
    }
    with pytest.raises(RuntimeError, match="no local repo mapped"):
        reconstruct_env_for_record(task_dir, record, rig_repos={}, base_images={}, base_ref="HEAD")


def test_for_record_missing_timestamp_raises(repo: Path, tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    record = {"work_id": "x-1", "rig": "myrig"}
    with pytest.raises(RuntimeError, match=r"no lifecycle\.started"):
        reconstruct_env_for_record(task_dir, record, rig_repos={"myrig": repo}, base_ref="HEAD")
