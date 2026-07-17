"""The one subprocess seam to the TS `mem` CLI.

`corpus.py` (store loading), `memory_systems/ours_system.py` (retrieval-v1), and
`harbor/base_rate_spike.py` (the `mem extract-errors` extractor) all shell out
to `mem ... --json` and unwrap the same success envelope (`{apiVersion, cmd,
ok, data, errors}`). This module owns that seam once, with the failure modes
the call sites should not each re-derive:

- the spawn itself (missing binary named with its fix, spawn OSErrors, timeout,
  non-zero exit) is diagnosed by `spawn.run_checked`, this module's ladder being
  one of the six that used to re-derive it (mem-o9plh);
- exit-0-but-malformed stdout is reported with the command and a stdout excerpt,
  not a bare JSONDecodeError with no context.

Every failure raises `MemCliError` (a RuntimeError) — the pipeline break is
always surfaced, never degraded to "no data".

It also owns the CLI's INPUT format (`write_ndjson`), for the same
one-definition reason: the seeders that feed `mem import-records/-lessons` are
otherwise each free to spell the wire format slightly differently.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from membench.spawn import redact_credentials, run_checked

# Generous bound: `mem query` over the full ~6.6k-record store completes in
# seconds; anything beyond this is a hung server or a wedged subprocess, not a
# slow query.
DEFAULT_TIMEOUT_S = 120.0


class MemCliError(RuntimeError):
    """A `mem` CLI invocation failed (missing binary, timeout, non-zero exit,
    malformed envelope). Carries the command for context."""


def write_ndjson(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """One JSON object per line — the import format `mem import-records/-lessons`
    reads. Terminates every line rather than joining, so an empty `rows` writes an
    empty file and not a stray blank line (which is not a valid record)."""
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def run_mem_json(
    argv: list[str], *, input: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    """Run `<argv> --json` and return the success envelope's `data`.

    `argv` is the full command including the binary path; `--json` is appended
    here so every caller goes through the envelope contract. `input`, if given,
    is piped to the process's stdin (e.g. `mem extract-errors` reads its input
    that way)."""
    cmd = " ".join(argv)
    completed = run_checked(
        [*argv, "--json"],
        what=cmd,
        not_found_hint="build the TS CLI first (npm run build at the repo root)",
        timeout_s=timeout_s,
        error=MemCliError,
        input=input,
    )
    try:
        envelope: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        # Redact before the 200-char slice, not after: the slice is a cut like any other, and
        # cutting a token first would leave a live prefix of it on the surviving side. The
        # bound here is this call's own (200, tighter than `sanitised_child_output`'s), so only
        # the redaction half is borrowed. The child exited 0 — `run_checked` never saw this text.
        raise MemCliError(
            f"{cmd} exited 0 but stdout is not a JSON envelope: "
            f"{redact_credentials(completed.stdout)[:200]!r}"
        ) from exc
    if not envelope.get("ok", False):
        raise MemCliError(f"{cmd} error: {envelope.get('errors')}")
    return cast(dict[str, Any], envelope["data"])
