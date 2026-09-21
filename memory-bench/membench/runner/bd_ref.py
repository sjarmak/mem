"""Build the ONE pinned beads commit a flywheel turn is about (mem-0wpq8.4).

The subject under test is a prototype: memory beads have not shipped, so the thing a turn
measures is a commit on a fork, not whatever ``bd`` the machine happens to have installed. A turn
that measured the ambient binary would be measuring a moving target, and the binary on this
machine has already drifted ahead of every published tag.

So a fire names its instrument as ``(remote, sha)`` and this module turns that into a binary:
fetch the commit, check it out, build it with the project's own Makefile, and hand back a path.
The build is CACHED BY SHA, because a turn fires many cells against one commit and rebuilding per
cell would be both slow and a way for two cells in one grid to disagree about what they measured.

The cache is keyed on the commit ALONE, and that is deliberate even though two builds of one
commit can differ in their bytes (a different toolchain, a dirty tree). ``resolve_bd_build`` hashes
whatever this returns and puts that hash in the artifact, so a cache that served the wrong bytes is
VISIBLE in the identity rather than silent -- and a resume against a different hash is refused by
the identity check that already exists. What this module must never do is serve a cached binary
that does not report the requested commit, which is why it verifies before returning one.

``MEMBENCH_BD_BINARY`` remains the escape hatch: a fire given no ``--bd-ref`` resolves the ambient
binary exactly as before.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from membench.runner.bd_build import BdBuild, resolve_bd_build
from membench.runner.tool_surface import MemoryToolError
from membench.spawn import Runner, run_checked

# A clone and a Go build, not a bd call: minutes, not the seconds a bd invocation gets.
FETCH_TIMEOUT_S = 600
BUILD_TIMEOUT_S = 900

# Where built binaries live between fires. Outside any sandbox and outside the repo, because a
# 30MB binary per turn does not belong in the artifact tree and the sandbox is wiped between legs.
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "membench" / "bd-builds"


def _is_sha(ref: str) -> bool:
    return len(ref) >= 7 and all(c in "0123456789abcdef" for c in ref.lower())


def build_bd_ref(
    remote: str,
    sha: str,
    *,
    cache_dir: Path | None = None,
    runner: Runner = subprocess.run,
) -> BdBuild:
    """Fetch ``sha`` from ``remote``, build it, and return the ``BdBuild`` for the result.

    ``sha`` must be a full or abbreviated commit hash, never a branch or tag name. A turn names an
    immutable instrument or it names nothing: ``main`` resolves to different code on different
    days, and the artifact would record a commit that the next reader cannot get back to by the
    name the run was given.

    Returns the cached build when one exists for this commit AND reports that commit. A cached
    binary that reports something else is rebuilt rather than trusted."""
    if not _is_sha(sha):
        raise MemoryToolError(
            f"--bd-ref was given {sha!r}, which is not a commit hash. A turn must pin an "
            "immutable commit; a branch or tag resolves to different code on different days and "
            "the artifact would name a build the next reader cannot get back to."
        )
    root = (cache_dir or DEFAULT_CACHE_DIR) / sha
    binary = root / "bd"

    if binary.is_file():
        cached = _identify(str(binary), sha, runner=runner)
        if cached is not None:
            return cached
        # Reported the wrong commit, so the cache entry is not what it claims. Rebuild over it
        # rather than serve it: a wrong binary here is a whole turn attributed to the wrong build.
        shutil.rmtree(root, ignore_errors=True)

    source = root / "src"
    source.mkdir(parents=True, exist_ok=True)
    _fetch(remote, sha, source, runner=runner)
    _make(source, runner=runner)

    built = source / "bd"
    if not built.is_file():
        raise MemoryToolError(
            f"`make build` in {source} left no binary at {built}. Nothing to measure; refusing "
            "to fall back to the ambient bd, which is a different instrument."
        )
    shutil.copy2(built, binary)

    build = _identify(str(binary), sha, runner=runner)
    if build is None:
        raise MemoryToolError(
            f"the binary built from {remote} at {sha} does not report that commit. The build did "
            "not come from the requested tree, so the artifact would name the wrong instrument."
        )
    return build


def _identify(binary: str, sha: str, *, runner: Runner) -> BdBuild | None:
    """The build at ``binary``, or ``None`` when it does not report ``sha``. Abbreviations match
    in either direction: the Makefile stamps a short hash and a caller may pin a full one."""
    try:
        build = resolve_bd_build(binary, runner=runner)
    except MemoryToolError:
        return None
    reported = build.commit.lower()
    wanted = sha.lower()
    if reported.startswith(wanted) or wanted.startswith(reported):
        return build
    return None


def _fetch(remote: str, sha: str, source: Path, *, runner: Runner) -> None:
    """Put ``sha`` in ``source`` as a detached checkout, from an empty directory or an existing one.

    `git init` + `fetch <remote> <sha>` rather than `clone`, so the same path serves a first fetch
    and a later commit without a special case, and so only the one commit's history is transferred.
    """
    for argv, what in (
        (["git", "init", "--quiet", "."], "git init (bd build tree)"),
        (["git", "fetch", "--quiet", "--depth", "1", remote, sha], f"git fetch {remote} {sha}"),
        (["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], "git checkout (bd build tree)"),
    ):
        run_checked(
            argv,
            what=what,
            not_found_hint="install git — a pinned bd build cannot be fetched without it",
            timeout_s=FETCH_TIMEOUT_S,
            error=MemoryToolError,
            runner=runner,
            cwd=source,
        )


def _make(source: Path, *, runner: Runner) -> None:
    """Build with the project's OWN Makefile, never a hand-rolled `go build`.

    The Makefile sets the build tags and stamps `main.Build` from `git rev-parse --short HEAD`,
    which is what `bd version --json` reports and therefore what this module verifies against. A
    local `go build` would produce a binary that reports no commit at all, and `resolve_bd_build`
    refuses those -- correctly, but after the build rather than instead of it."""
    run_checked(
        ["make", "build"],
        what=f"make build ({source})",
        not_found_hint="install make and a Go toolchain — the pinned bd is built from source",
        timeout_s=BUILD_TIMEOUT_S,
        error=MemoryToolError,
        runner=runner,
        cwd=source,
    )


__all__ = ["DEFAULT_CACHE_DIR", "build_bd_ref"]
