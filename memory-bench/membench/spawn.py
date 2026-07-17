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

import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# A subprocess.run-shaped callable, injectable so tests never spawn a real
# process. The canonical definition -- import it from here rather than restating it.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# Both credential shapes that can reach this choke point: `sk-ant-oat01-...` (the OAuth
# token the paid grid hands every child) and `sk-ant-api03-...` (an API key). One pattern
# covers both, and nothing speculative is added -- these are the only credentials any of
# the callers pass. mem never puts a token on the child's output channels itself; this
# fires when a CLI echoes its OWN auth back on failure.
_CREDENTIAL = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_REDACTED = "<redacted-credential>"

# The window the child's own output gets in the diagnosis. HEAD AND TAIL, not head alone:
# `claude -p --output-format stream-json` puts a whole event stream on stdout and the
# operative line lands at the end, while a usage error lands at the start. A head-only
# window would drop exactly the line the operator needs on half the callers.
_HEAD_CHARS = 2_000
_TAIL_CHARS = 2_000


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
        #
        # Select FIRST, then sanitise: redacting or truncating before this line could
        # blank a real stderr into whitespace and silently move the fallback above --
        # the selection is the pinned rung, so nothing may run ahead of it.
        detail = (completed.stderr or "").strip() or (completed.stdout or "").strip()
        raise error(f"{what} failed (exit {completed.returncode}): {_sanitised(detail)}")
    return completed


def _sanitised(detail: str) -> str:
    """The child's own output, made fit for the log this diagnosis ends up in.

    The message built from this is PRINTED -- the paid grid drivers echo it on their
    SWEEP HALT arm -- so the child's output is untrusted text bound for a CI log, and it
    gets both properties here rather than at each of the callers: it is redacted, and it
    is bounded. Order is fixed: redact before truncating, so a token straddling the cut
    cannot survive as a fragment on either side of it.
    """
    detail = _CREDENTIAL.sub(_REDACTED, detail)
    if len(detail) <= _HEAD_CHARS + _TAIL_CHARS:
        return detail
    dropped = len(detail) - _HEAD_CHARS - _TAIL_CHARS
    return f"{detail[:_HEAD_CHARS]}\n... <{dropped} chars truncated> ...\n{detail[-_TAIL_CHARS:]}"
