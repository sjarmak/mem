"""Shared test helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` (check=True) and return raw stdout."""
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def fake_mem(tmp_path: Path, body: str) -> str:
    """Write an executable stand-in for the `mem` binary and return its path."""
    script = tmp_path / "fake-mem"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)
