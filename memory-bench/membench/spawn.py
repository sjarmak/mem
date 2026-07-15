"""The one spawn-and-diagnose choke point for RAISING subprocess sites.

Such a site needs the same four rungs — binary absent, spawn failed, timed out,
non-zero exit — in an order with a subclass trap in it (see ``run_checked``).
Every hand-rolled copy has to re-derive that order, and re-derivation is how it
goes wrong: route new raising spawns here instead.

``error`` is an exception FACTORY rather than a fixed type, which is what lets
one ladder serve callers with different error contracts: it unifies the ORDER
and COMPLETENESS of the rungs while each site keeps its own exception TYPE. Only
``what`` and ``not_found_hint`` are per-site.

A site whose contract is report-and-continue rather than raise
(``oracle/backends.py`` returns ``BackendResult(available=False)`` — a backend
that drops out is reported, not fatal) is NOT a caller: a factory unifies which
exception is raised, not raise-vs-return.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# A subprocess.run-shaped callable, injectable so tests never spawn a real
# process. The canonical definition -- import it from here rather than restating it.
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

    ``check=False``: a non-zero exit is diagnosed here with the command's own
    stderr rather than as a bare ``CalledProcessError``.

    ``what`` names the command in prose ("harbor run", "claude -p for the
    comparative judge"); ``not_found_hint`` names the fix for a missing binary.
    ``timeout_s`` of None is subprocess's own unbounded sentinel — only pass it
    where no bound is meaningful. ``cwd``/``env``/``input`` are explicit rather
    than ``**kwargs`` so mypy --strict keeps checking the call; ``cwd`` takes
    ``str`` as well as ``Path`` because subprocess does and callers hold it both
    ways.

    The timeout message quotes ``exc.timeout`` rather than ``timeout_s`` so it
    stays accurate where the caller's bound is None.
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
        # that says what went wrong.
        raise error(
            f"{what} failed (exit {completed.returncode}): "
            f"{(completed.stderr or '').strip() or (completed.stdout or '').strip()}"
        )
    return completed
