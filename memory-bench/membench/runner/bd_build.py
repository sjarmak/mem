"""The bd build a fire measures, pinned by what the binary IS (mem-0wpq8.2).

The three-arm results record carried the CLI version, the corpus fingerprint and the recognizer
version, and not the bd it wrapped: two fires against two beads builds would have resumed into
one grid. The flywheel's one variable per turn is the beads build, so the identity has to key on
it, and on two things rather than one: the commit the binary reports, because that is what a
turn names, and the bytes on disk, because a rebuilt binary at the same commit (a different
toolchain, an uncommitted local patch) is a different instrument that the commit alone would
call the same one.

Free, like ``resolve_cli_version``: ``bd version --json`` prints and exits.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from membench.runner.tool_surface import CALL_TIMEOUT_S, MemoryToolError, resolve_bd_binary
from membench.spawn import Runner, run_checked


@dataclass(frozen=True)
class BdBuild:
    binary: str
    sha256: str
    commit: str
    version: str

    def identity(self) -> dict[str, str]:
        """The two fields a resume identity keys on. The path and the version string stay out:
        the same bytes at another path are the same instrument, and the version string is what
        the commit already says, less precisely."""
        return {"bd_binary_sha256": self.sha256, "bd_commit": self.commit}


def resolve_bd_build(bd_binary: str | None = None, *, runner: Runner = subprocess.run) -> BdBuild:
    """The bd the surfaces will wrap, read off the binary itself. Raises ``MemoryToolError`` when
    it cannot be identified: a run that cannot name the build it measures must not spend."""
    binary = resolve_bd_binary(bd_binary)
    sha256 = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    completed = run_checked(
        [binary, "version", "--json"],
        what=f"{binary} version --json",
        not_found_hint="a run cannot name the bd build it would measure",
        timeout_s=CALL_TIMEOUT_S,
        error=MemoryToolError,
        runner=runner,
    )
    try:
        payload: Any = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MemoryToolError(f"{binary} version --json printed no JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MemoryToolError(
            f"{binary} version --json printed {type(payload).__name__}, not an object"
        )
    commit = str(payload.get("build") or "")
    if not commit:
        raise MemoryToolError(
            f"{binary} version --json names no build commit ({completed.stdout.strip()!r}); a "
            "build that cannot say what it is cannot be pooled with one that can"
        )
    return BdBuild(
        binary=binary, sha256=sha256, commit=commit, version=str(payload.get("version", ""))
    )


__all__ = ["BdBuild", "resolve_bd_build"]
