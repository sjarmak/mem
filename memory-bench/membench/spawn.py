"""The one spawn-and-diagnose choke point (mem-o9plh).

A raising subprocess site needs the same four rungs, in this exact order:

1. ``FileNotFoundError`` — the binary is absent; the message must name the FIX,
   not just the fact (build the TS CLI, ``npm i -g openwiki``, install harbor);
2. ``OSError`` — the REST of the spawn-failure family (PermissionError, ENOEXEC,
   EACCES on cwd). Must come AFTER FileNotFoundError, which is a subclass: the
   reverse order makes the specific rung dead code;
3. ``subprocess.TimeoutExpired`` — not an OSError, so it needs a clause of its
   own or a hang escapes as a raw traceback;
4. a non-zero exit — a failed run is never silently a clean result.

Rung 2's ordering constraint is knowledge each hand-rolled ladder had to
re-derive, and demonstrably did not (mem-o9plh audited the copies). Knowledge
that must be re-derived per site, and is not, belongs in one function.

``error`` is why one ladder can serve callers with different error contracts: it
is an exception FACTORY, lifted from ``judge_config.run_isolated_claude``, so
the ladder unifies the ORDER and COMPLETENESS of the rungs while each site keeps
its own exception TYPE. What stays per-site is only what is genuinely per-site:
``what`` (the label the messages are built around) and ``not_found_hint`` (the
fix to name when the binary is missing). The message FRAME is deliberately
uniform — it is the shape, and the shape is the point.

Scope: RAISING spawn sites. A site whose contract is report-and-continue rather
than raise (``oracle/backends.py`` returns ``BackendResult(available=False)`` —
a backend that drops out is reported, not fatal) is NOT a caller: a factory
unifies which exception is raised, not raise-vs-return.

NOT yet swept: mem-o9plh converted the six sites that had a ladder to share.
Several raising git/docker spawns (``harbor/env_recon``, ``bundle/replay``,
``harbor/probe_gate``, ``harbor/base_image``, the ftp_* modules) still catch
NOTHING around the spawn, so a missing or unspawnable binary there is still a
raw traceback. They already type their seam as ``Runner``, so routing them here
is mechanical — see mem-o9plh's follow-up.

ZFC: pure plumbing — process spawn, exception ordering, message assembly. No
semantic judgment.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# A subprocess.run-shaped callable, injectable so tests never spawn a real
# process. The canonical definition: modules import it from here rather than
# restating it (it had been copied five times). `runner.headless_agent.CliRunner`
# is the same shape under an older name, kept for now -- it spans modules with no
# spawn-ladder involvement, so folding it in is a rename, not this bead's concern.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def run_checked(
    argv: Sequence[str],
    *,
    what: str,
    not_found_hint: str,
    timeout_s: float | None = None,
    error: Callable[[str], BaseException] = RuntimeError,
    runner: Runner = subprocess.run,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Spawn ``argv`` and return the completed process, or raise ``error(...)``
    with the root cause attached.

    Captures stdout/stderr as text and never raises ``CalledProcessError``
    (``check=False``): a non-zero exit is diagnosed here with the command's own
    stderr, which says more than the exception would.

    ``what`` names the command in prose ("harbor run", "claude -p for the
    comparative judge"); ``not_found_hint`` names the fix for a missing binary.
    ``timeout_s`` of None is subprocess's own unbounded sentinel — only pass it
    where no bound is meaningful. ``cwd``/``env``/``input`` are explicit rather
    than ``**kwargs`` so mypy --strict keeps checking the call; ``cwd`` takes
    ``str`` as well as ``Path`` because subprocess does and callers hold it both
    ways.

    The timeout message quotes ``exc.timeout`` — the bound the exception
    actually carries — rather than re-deriving it from ``timeout_s``, so it
    stays accurate even where the caller's bound is None.
    """
    try:
        completed = runner(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
            cwd=cwd,
            env=env,
            input=input,
        )
    except FileNotFoundError as exc:
        # Ordered before OSError -- FileNotFoundError is a subclass, so the
        # reverse order would make this specific wording dead code.
        raise error(f"{argv[0]!r} not found — {not_found_hint}") from exc
    except OSError as exc:
        # The rest of the family -- including EACCES on the cwd, which fails the
        # spawn without the binary itself being at fault.
        raise error(f"could not spawn {what}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise error(f"{what} did not finish within {exc.timeout}s") from exc
    if completed.returncode != 0:
        # `.strip() or .strip()`, not `(a or b).strip()`: a whitespace-only stderr
        # must still fall through to stdout, or the diagnosis loses the one line
        # that says what went wrong. Four of the six converted sites had it this way.
        raise error(
            f"{what} failed (exit {completed.returncode}): "
            f"{(completed.stderr or '').strip() or (completed.stdout or '').strip()}"
        )
    return completed
