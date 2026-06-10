"""The one subprocess seam to the TS `mem` CLI.

`corpus.py` (store loading) and `memory_systems/ours_system.py` (retrieval-v1)
both shell out to `mem ... --json` and unwrap the same success envelope
(`{apiVersion, cmd, ok, data, errors}`). This module owns that seam once, with
the failure modes the call sites should not each re-derive:

- a missing binary names the fix (build the TS CLI), not a bare FileNotFoundError;
- a hang is bounded by a timeout and surfaces as a loud error, never a stuck run;
- exit-0-but-malformed stdout is reported with the command and a stdout excerpt,
  not a bare JSONDecodeError with no context.

Every failure raises `MemCliError` (a RuntimeError) — the pipeline break is
always surfaced, never degraded to "no data".
"""

import json
import subprocess
from typing import Any, cast

# Generous bound: `mem query` over the full ~6.6k-record store completes in
# seconds; anything beyond this is a hung server or a wedged subprocess, not a
# slow query.
DEFAULT_TIMEOUT_S = 120.0


class MemCliError(RuntimeError):
    """A `mem` CLI invocation failed (missing binary, timeout, non-zero exit,
    malformed envelope). Carries the command for context."""


def run_mem_json(argv: list[str], *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run `<argv> --json` and return the success envelope's `data`.

    `argv` is the full command including the binary path; `--json` is appended
    here so every caller goes through the envelope contract."""
    cmd = " ".join(argv)
    try:
        completed = subprocess.run(
            [*argv, "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise MemCliError(
            f"{argv[0]!r} not found — build the TS CLI first (npm run build at the repo root)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MemCliError(f"{cmd} timed out after {timeout_s:.0f}s") from exc

    if completed.returncode != 0:
        raise MemCliError(
            f"{cmd} failed (exit {completed.returncode}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    try:
        envelope: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MemCliError(
            f"{cmd} exited 0 but stdout is not a JSON envelope: {completed.stdout[:200]!r}"
        ) from exc
    if not envelope.get("ok", False):
        raise MemCliError(f"{cmd} error: {envelope.get('errors')}")
    return cast(dict[str, Any], envelope["data"])
