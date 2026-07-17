"""mem-rx11w — the neutral-sandbox seam for every paid ``claude -p`` cwd.

A cell's sandbox is minted to be NEUTRAL: native memory (or the arm's surfaced
``available_memory``) is meant to be the sole continuity channel, and
``headless_agent``'s cwd contract says so outright — the cwd "MUST be an isolated, neutral
sandbox, never a mem worktree", or the repo's ``CLAUDE.md`` / project memory "would both
fail the session and confound the none/ours/builtin memory variable".

Emptying the cwd does not establish that. Claude Code auto-loads ``CLAUDE.md`` by WALKING
UP the directory tree from cwd at launch, with NO tool call — so an ``--allowedTools``
clamp cannot close the channel, and neither can ``toolreq_builtin._wipe_cwd_contents``,
which iterates ``cwd.iterdir()`` and by construction never ascends. The sandbox is rooted
at the ambient ``TMPDIR`` (``tempfile`` resolves it), so the OPERATOR'S ENVIRONMENT decides
the whole ancestor chain: point ``TMPDIR`` at a workspace for disk space — routine — and
every "neutral" sandbox silently inherits whatever ``CLAUDE.md`` sits above it. The
accounting cannot see it. In the builtin arm ``engaged`` is read off the establish leg and
``leaked`` only fires on (pass AND NOT engaged), so a scavenged pass publishes as a clean
SEPARATES: the mechanism under test reads as WORKING when it did not.

So the guard is FAIL-CLOSED and refuses to spend, rather than recording the chain and
spending anyway. That choice is what keeps the sandbox's location OUT of the cache
identity: a run can only complete with an EMPTY ancestor set, so the auto-loaded context is
pinned to nothing by construction, and ``TMPDIR`` — which reaches no argv
(``invocation_fingerprint``), and which the scored artifact's cwd-relative path ignores —
cannot vary a measurement. An input that cannot vary needs no fingerprint, and hashing the
root PATH would be worse than idle: two CLEAN sweeps under different ``TMPDIR`` measure the
same thing, so it would force a false MISS and re-spend real money on a difference that
moves nothing. What the guard DOES invalidate is every cell measured BEFORE it existed —
those assert nothing about their ancestor chain and record no ``TMPDIR``, so they are
unauditable after the fact. That is ``BaseRunIdentity.protocol``'s job, which names this
exact case ("the engagement check or the sandbox firewall"), and each grid bumps its own
``EXECUTION_PROTOCOL`` for it.

ZFC: filesystem plumbing and a structural path check. No model call, no judgment.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# What Claude Code auto-loads from a directory on its upward walk, and hence what makes an
# ancestor non-neutral. ``CLAUDE.md`` is the verified vector and the one the docs describe;
# ``CLAUDE.local.md`` is its documented per-developer variant; ``AGENTS.md`` is over-broad
# (the docs do not list it as auto-loaded) but is named by the builtin arm's own threat
# model and costs one stat to refuse — for a fail-closed guard, over-broad is the safe side.
#
# NOT here: ``.claude/``. An ancestor project ``.claude/`` carries settings and SessionStart
# hooks and is a real hazard, but the user config dir is literally ``~/.claude``, so
# including the name would refuse EVERY ``TMPDIR`` under a home directory — including clean
# ones — to defend a vector that is not this one (the user scope is cwd-INDEPENDENT, and the
# builtin arm already relocates it via ``CLAUDE_CONFIG_DIR``). It wants its own guard and
# its own reasoning about the ``$HOME`` boundary; mem-f819h.
AUTO_LOADED_CONTEXT_FILES: tuple[str, ...] = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


class SandboxContaminationError(RuntimeError):
    """A paid sandbox's ancestor chain carries agent context the harness cannot account for.

    Raised rather than recorded: see this module's docstring for why refusing beats
    fingerprinting. A refused measurement is the cheap end of this failure — at construction
    nothing is spent at all, and after the establish leg the calls are made but NOT written,
    which still beats publishing a number whose provenance the harness cannot describe."""


def assert_neutral_ancestry(sandbox: Path) -> None:
    """Refuse ``sandbox`` if any directory ABOVE it carries auto-loaded agent context.

    Resolves first: ``tempfile`` hands back a path under whatever ``TMPDIR`` names, but the
    kernel's cwd is an inode, so a symlinked ``TMPDIR`` makes the LEXICAL parents clean while
    the child walks the REAL chain. A guard that skipped this would be green exactly when it
    is wrong.

    Walks to ``/`` INCLUSIVE. Claude Code's stopping boundary is undocumented, and this
    codebase has already learned (``BaseRunIdentity.cli_version``) not to encode an asserted
    CLI behavior it does not observe; the asymmetry decides it, since including ``/`` costs
    one stat and a refusal that is near-impossible to trigger, while excluding it wrongly
    costs a silently contaminated paid sweep. Checks ancestors ONLY — the sandbox itself is
    minted empty, and its contents are the cwd firewall's business, not this one's."""
    for ancestor in sandbox.resolve().parents:
        for name in AUTO_LOADED_CONTEXT_FILES:
            found = ancestor / name
            if found.exists():  # follows symlinks: a CLAUDE.md -> AGENTS.md link is context too
                raise SandboxContaminationError(
                    f"{found} sits above the sandbox {sandbox}, and Claude Code auto-loads it "
                    f"by walking up from the cwd at launch — with no tool call to clamp, so "
                    f"this sandbox is not neutral and its result could not be told apart from "
                    f"a real one. Refusing to spend. Set TMPDIR to a directory with no "
                    f"{'/'.join(AUTO_LOADED_CONTEXT_FILES)} in any parent."
                )


@contextmanager
def paid_sandbox(prefix: str) -> Iterator[Path]:
    """A neutral cwd for a paid ``claude -p`` cell, guaranteed clean or not handed out.

    Honors ``TMPDIR`` deliberately (see the module docstring): the guard is on
    CONTAMINATION, never on WHERE the sandbox lives, so pointing ``TMPDIR`` at a roomier
    disk keeps working and a contaminated one fails loudly instead of quietly.

    Yields the RESOLVED path — the same chain the guard checked and the kernel reports, so
    nothing downstream can re-introduce the symlink gap the guard just closed."""
    with tempfile.TemporaryDirectory(prefix=prefix) as raw:
        sandbox = Path(raw).resolve()
        assert_neutral_ancestry(sandbox)
        yield sandbox
