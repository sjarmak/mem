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

# `sk-` and not `sk-ant-`: the callers carry more than the Anthropic shapes. The paid grid
# hands every child `sk-ant-oat01-...` (OAuth) or `sk-ant-api03-...` (API key), but
# `openwiki_system._openwiki_env` reads OPENAI_COMPATIBLE_API_KEY straight off the operator's
# environment and hands it to a `run_checked` child too -- and an OpenAI key is `sk-proj-...`,
# which `sk-ant-` does not match. Verified end-to-end by review: a stub CLI failing with that
# key on stderr surfaced it in full.
#
# The `\b` is not decoration. `sk-` is a substring of ordinary words this codebase's own
# diagnostics are full of -- taSK-types, diSK-, riSK-, briSK- -- and without the boundary the
# redaction eats them: `no task-types.json found` came out as `no ta<redacted-credential>.json`,
# swallowing the exact filename the operator needed. `--task-types` is a real flag here and
# `task-types.json` a real artifact path, so that was live, not hypothetical. A word boundary
# plus a real shared vendor prefix -- still no entropy scoring, no KEY=VALUE sniffing.
#
# It is NOT a general secret scanner. The env is inherited wholesale (`{**os.environ,
# **self.env}`), so a differently-shaped vendor token an operator has exported can still be
# echoed by a child that chooses to -- `sgp_` (Sourcegraph) is a live example, and
# `oracle/backends.py` echoes one raw today (mem-zls5s). Bounding THAT is the env-surface
# question (mem-jwp3c), not a regex's job. mem never writes a token to these channels
# itself; this fires when a CLI echoes its OWN auth back.
_CREDENTIAL = re.compile(r"\bsk-[A-Za-z0-9_-]+")
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
        raise error(
            f"{what} failed (exit {completed.returncode}): {sanitised_child_output(detail)}"
        )
    return completed


def redact_credentials(detail: str) -> str:
    """Replace every credential-shaped run in ``detail``.

    PUBLIC because a caller that imposes its own bound still needs the redaction half
    (``mem_cli.run_mem_json`` slices to 200 chars of its own). Redact BEFORE any slice:
    cutting first leaves a live prefix of the token on the surviving side."""
    return _CREDENTIAL.sub(_REDACTED, detail)


def sanitised_child_output(detail: str) -> str:
    """The child's own output, made fit for the log this diagnosis ends up in.

    PUBLIC, because ``run_checked``'s own non-zero arm was never the only site that builds
    a message out of child output: callers build their own text from a SUCCEEDING child too
    (``resolve_cli_version`` on an unrecognised banner, ``run_mem_json`` on a non-envelope
    stdout), and those messages reach the same printed SWEEP HALT. A choke point that only
    covers the raise-path is not a choke point -- it is the majority case with a name.

    It is NOT yet the choke point for that whole class, so this stops short of saying every
    such site "must" route through it -- that would describe a rule the repo does not follow.
    Five judge sites still slice an exit-0 reply into an exception raw, on a path whose env
    preserves CLAUDE_CODE_OAUTH_TOKEN verbatim (mem-rcm73 names them). Outside this change.

    Both properties live here rather than at the callers, because each of them would
    otherwise have to re-derive both: the output is redacted, and it is bounded. Order is
    fixed -- redact before truncating, so a token straddling the cut cannot survive as a
    fragment on either side of it."""
    detail = redact_credentials(detail)
    if len(detail) <= _HEAD_CHARS + _TAIL_CHARS:
        return detail
    dropped = len(detail) - _HEAD_CHARS - _TAIL_CHARS
    return f"{detail[:_HEAD_CHARS]}\n... <{dropped} chars truncated> ...\n{detail[-_TAIL_CHARS:]}"
