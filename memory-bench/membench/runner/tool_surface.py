"""mem-5sht9 — the memory tool an evaluated agent can actually CALL, and a store that outlives
the between-legs cwd wipe.

Before this module there was no such tool. Verified on the shipped code: the goal leg is clamped
to ``--allowedTools Write``; ``--strict-mcp-config`` is passed with no ``--mcp-config`` beside it,
so every MCP tool is unreachable by construction; nothing under ``membench/runner`` puts ``bd`` or
``mem`` in front of the agent; a fresh sandbox ``bd`` refuses with "no beads database found"; and
``toolreq_builtin._wipe_cwd_contents`` deletes a cwd-local ``.beads/`` between the two legs. An
endogenous-use experiment run on that wiring reports ZERO memory calls for a wiring reason that is
byte-identical to the finding it would publish (arXiv 2607.20972's near-zero voluntary use). This
module exists so that null, if it comes, is about the agent.

**Bash-with-``bd``-on-PATH, not MCP.** Both were live. The choice, and what it costs:

* The shipped ``bd`` has no ``mcp`` subcommand, so the MCP route means hand-writing a stdio
  JSON-RPC server for this benchmark. Its failure mode is the one failure mode this bead exists
  to abolish: a server that does not boot, or boots under a flag combination the CLI rejects,
  produces exactly zero tool calls and looks like a result.
* ``bd remember`` / ``bd recall`` ARE the subject of beads#5877. An MCP tool named ``mem_recall``
  would measure endogenous use of a surface the MVP does not ship; the bd verbs measure the one
  it does.
* The cost is the counter: the call arrives with ``name="Bash"`` and the verb lives in the
  ``command`` argument, so counting is a CLI-grammar parse (``memory_tool_calls``) rather than a
  structured-name equality. That parse is mechanical — argv shape, not meaning — and carries its
  own unit test. It is never a read of prose.

**On naming as a treatment.** An allowlisted tool called ``mem_recall`` is itself an invitation:
the ladder experiments downstream vary GUIDANCE STRENGTH, and a tool whose name is a request to
use it would confound every rung of that dial. The surface here is deliberately neutral in the
structured stream — the tool the agent is handed is ``Bash``, the same general-purpose tool it
gets anyway — and the memory affordance is a CLI on ``PATH``, discovered the way the agent would
discover it in a real repo. So the only thing that varies across the ladder is the guidance text.

**The store.** ``bd init`` puts ``.beads/`` in the cwd, and the cwd is wiped between legs. Here the
store is minted in its own directory OUTSIDE the sandbox and pinned into every call by a ``bd``
shim on ``PATH`` that injects ``-C <store>`` (the shipped bd has no store env var; ``-C`` is the
supported knob, and it refuses a directory that is not already a beads project — hence the
provisioning ``bd init`` in that directory). ``assert_store_outside`` refuses a store that is
inside the sandbox (the wipe would eat it) or ABOVE it (``bd init`` writes ``CLAUDE.md`` and
``AGENTS.md``, which Claude Code auto-loads by walking up from the cwd — the very contamination
``sandbox.assert_neutral_ancestry`` refuses to spend on).

**Fingerprint.** ``surface_fingerprint`` hashes the tool surface as a POLICY — the command, the
verbs, the MCP config path, the store's SCOPE and prefix — never the store's realised path. The
path is a per-repeat tempdir, so hashing it would force a permanent cache MISS and re-spend real
money on a difference that moves no measurement; the same argument ``sandbox`` makes for
``TMPDIR``. What must be caught is a CHANGE OF SURFACE (a different verb set, an MCP config
appearing, a store that stops being external), and that is exactly what the policy digest moves on.

ZFC: filesystem plumbing, a subprocess spawn, and a CLI-grammar parse of argv. No model call, no
judgment about what a memory MEANS.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from membench.runner.resume_cache import digest
from membench.schemas.trace import ToolCall
from membench.spawn import Runner, run_checked

# The CLI the memory affordance IS. Not a wrapper verb of our own invention: beads#5877's MVP is
# these verbs on this binary, so an experiment about them must call them.
MEMORY_COMMAND = "bd"

# The memory verbs of bd 1.2.1. `link` is deliberately ABSENT: it is shorthand for `bd dep add`
# (an issue dependency), not a memory verb, and counting it would inflate every rate with ordinary
# issue-graph work. `bd prime` is absent for a different reason — R8. The shipped prime emits
# "## Persistent Memories (N)" followed by full bodies, so shelling it would DELIVER memory rather
# than measure a choice to read it, which is the one thing this series must not do.
MEMORY_VERBS: tuple[str, ...] = ("remember", "recall", "memories", "forget")

# The structured tool name a memory call arrives under. A tuple, and the counter matches against
# it, so a later MCP surface (whose calls arrive as `mcp__memory__recall`) extends this rather
# than forking the counter.
MEMORY_TOOL_NAMES: tuple[str, ...] = ("Bash",)

# What the step must allow for the tool to be reachable at all. The goal leg's historical
# `--allowedTools Write` clamp is precisely what made every memory call impossible.
MEMORY_ALLOWED_TOOLS: tuple[str, ...] = ("Bash", "Write")

# The store's POLICY, the part of it that is a measured input. See the module docstring on why the
# realised path is not hashed.
STORE_SCOPE = "external-per-repeat-tempdir"
STORE_PREFIX = "membench-memory-"

# The bd a shim wraps, overridable so an operator can pin a patched build (the beads_ordering rig
# pins its own binary this way) without editing code.
ENV_BD_BINARY = "MEMBENCH_BD_BINARY"

# `bd init` on a cold embedded Dolt backend takes seconds, not minutes; a bound this high means a
# wedged binary, not a slow one.
PROVISION_TIMEOUT_S = 240.0
CALL_TIMEOUT_S = 120.0

# One memory invocation inside a shell command line. Anchored on the ARGV GRAMMAR:
#
#   * the command must start a word — preceded by start-of-string, whitespace, or a shell operator
#     (`;`, `&`, `|`, `(`, backtick) — so `abd remember` and `mybd recall` do not match;
#   * an absolute or relative PATH to the binary is allowed (`/usr/local/bin/bd recall k`);
#   * only FLAG tokens may sit between the command and the verb (`bd --json recall k`). A flag with
#     a SPACE-separated value (`bd --db /x recall k`) is deliberately NOT matched: skipping
#     arbitrary tokens would make `bd issue remember` a memory call. The agent never needs such a
#     flag — the shim injects `-C` itself — so the bound costs nothing real, and it errs toward
#     UNDER-counting, which cannot manufacture the positive result this series is looking for;
#   * the verb must end a word, so `bd remembering` does not match.
_MEMORY_CALL = re.compile(
    r"(?:^|[\s;&|(`])(?:[\w./+-]*/)?" + MEMORY_COMMAND + r"(?:\s+--?[A-Za-z][\w-]*(?:=\S+)?)*"
    r"\s+(" + "|".join(MEMORY_VERBS) + r")(?![\w-])"
)


class MemoryToolError(RuntimeError):
    """The memory tool surface could not be provisioned or driven — a missing ``bd``, a failed
    ``bd init``, a store the sandbox would eat. Raised, never degraded to an empty surface: a run
    whose memory tool silently does not exist is the indistinguishable null."""


@dataclass(frozen=True)
class MemoryToolSurface:
    """One provisioned memory affordance: the store it writes to, the shim directory that puts it
    on the agent's ``PATH``, and the MCP config path (``None`` for the bd surface).

    ``env()`` is what a cell hands ``HeadlessClaudeAgent.env``; ``fingerprint()`` is what a grid
    folds into ``BaseRunIdentity.settings_fingerprint`` — like the seeded ``settings.json``, this
    reaches the agent through the FILESYSTEM and the ENV, not through the argv, so no
    ``invocation_fingerprint`` can see it."""

    store_dir: Path
    bin_dir: Path
    bd_binary: str
    mcp_config: str | None = None

    def env(self) -> dict[str, str]:
        """``PATH`` with the shim dir FIRST, so ``bd`` resolves to the store-pinned wrapper even
        where a real ``bd`` is installed. Merged over ``os.environ`` by the agent, never replacing
        it, so the OAuth token and the rest of the environment survive."""
        return {"PATH": os.pathsep.join([str(self.bin_dir), os.environ.get("PATH", "")])}

    def fingerprint(self) -> str:
        return surface_fingerprint(mcp_config=self.mcp_config)


def surface_fingerprint(*, mcp_config: str | None = None) -> str:
    """The digest of the tool surface AS A POLICY. Moves when the command, the counted verbs, the
    allowed tools, the store scope or the MCP config path change; does NOT move when a run mints
    its store in a different tempdir (see the module docstring)."""
    return digest(
        {
            "command": MEMORY_COMMAND,
            "verbs": list(MEMORY_VERBS),
            "tool_names": list(MEMORY_TOOL_NAMES),
            "allowed_tools": list(MEMORY_ALLOWED_TOOLS),
            "store_scope": STORE_SCOPE,
            "store_prefix": STORE_PREFIX,
            "mcp_config": mcp_config,
        }
    )


def settings_fingerprint(settings: object, *, mcp_config: str | None = None) -> str:
    """What a grid running this tool surface carries as ``BaseRunIdentity.settings_fingerprint``:
    the seeded ``settings.json`` AND the tool surface, hashed together.

    Together, not two fields: both are measured inputs the command line cannot carry, both reach
    the agent through the config/filesystem surface, and one identity field that moves on either is
    what keeps a grid from shipping a tool-surface change as a cache HIT."""
    return digest(
        {"settings": settings, "tool_surface": surface_fingerprint(mcp_config=mcp_config)}
    )


def resolve_bd_binary(bd_binary: str | None = None) -> str:
    """The bd a surface wraps: an explicit pin, else ``MEMBENCH_BD_BINARY``, else ``bd`` on PATH."""
    return bd_binary or os.environ.get(ENV_BD_BINARY, "") or MEMORY_COMMAND


def assert_store_outside(sandbox: Path, store_dir: Path) -> None:
    """Refuse a store the sandbox would eat, or one that would contaminate it.

    Two directions, both fatal and for different reasons. A store INSIDE the cwd is deleted by
    ``toolreq_builtin._wipe_cwd_contents`` between the legs, which silently turns the second leg's
    recall into a miss — the exact defect this module was filed to remove. A store ABOVE the cwd is
    worse: ``bd init`` writes ``CLAUDE.md`` and ``AGENTS.md`` into it, and Claude Code auto-loads
    those by walking up from the cwd with no tool call, so the sandbox stops being neutral and
    ``sandbox.assert_neutral_ancestry`` would (rightly) refuse to spend."""
    sandbox_r = sandbox.resolve()
    store_r = store_dir.resolve()
    if store_r == sandbox_r or sandbox_r in store_r.parents:
        raise MemoryToolError(
            f"memory store {store_r} sits inside the sandbox cwd {sandbox_r}, which is wiped "
            "between legs — the store must outlive the wipe or the second leg measures nothing"
        )
    if store_r in sandbox_r.parents:
        raise MemoryToolError(
            f"memory store {store_r} sits ABOVE the sandbox cwd {sandbox_r}; `bd init` writes "
            "CLAUDE.md/AGENTS.md there and Claude Code auto-loads them by walking up from the cwd, "
            "so the sandbox would no longer be neutral"
        )


def provision_memory_tool(
    root: Path,
    *,
    bd_binary: str | None = None,
    mcp_config: str | None = None,
    runner: Runner = subprocess.run,
) -> MemoryToolSurface:
    """Mint a store under ``root`` and a ``bd`` shim that pins every call to it.

    ``root`` is the caller's per-repeat directory (a ``TemporaryDirectory``), NOT the sandbox cwd:
    the whole point is a store the wipe cannot reach. The shim is a two-line ``sh`` script rather
    than an env var because the shipped bd has none — ``-C`` is the supported way to name a store,
    and it must be injected in front of whatever the agent typed."""
    store_dir = root / "store"
    bin_dir = root / "bin"
    store_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    binary = resolve_bd_binary(bd_binary)

    run_checked(
        [binary, "init", "--prefix", "mem", "--quiet"],
        what=f"{binary} init (memory tool store)",
        not_found_hint="install bd, or point MEMBENCH_BD_BINARY at it — without a store the "
        "evaluated agent has no memory tool to call and the run measures wiring, not behaviour",
        timeout_s=PROVISION_TIMEOUT_S,
        error=MemoryToolError,
        runner=runner,
        cwd=store_dir,
    )

    shim = bin_dir / MEMORY_COMMAND
    shim.write_text(f'#!/bin/sh\nexec "{binary}" -C "{store_dir}" "$@"\n', encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return MemoryToolSurface(
        store_dir=store_dir, bin_dir=bin_dir, bd_binary=binary, mcp_config=mcp_config
    )


def harness_call(
    surface: MemoryToolSurface,
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    runner: Runner = subprocess.run,
) -> str:
    """Drive the surface's shim FROM THE HARNESS — seeding a memory before a leg, or reading one
    back after it. Never a substitute for the agent's own call: an agent's memory use is a tool_use
    block in its stream, and this path produces none."""
    completed = run_checked(
        [str(surface.bin_dir / MEMORY_COMMAND), *argv],
        what=f"{MEMORY_COMMAND} {argv[0] if argv else ''} (harness-side)",
        not_found_hint="the shim was not provisioned",
        timeout_s=CALL_TIMEOUT_S,
        error=MemoryToolError,
        runner=runner,
        cwd=cwd,
    )
    return completed.stdout or ""


def memory_verbs_in_command(command: str) -> list[str]:
    """Every memory verb invoked in one shell command line, in order. The mechanical half of the
    counter, exposed so a driver can report WHICH verbs an agent reached for (read vs write is the
    whole endogenous question) rather than only how often."""
    return [match.group(1) for match in _MEMORY_CALL.finditer(command)]


def memory_tool_calls(calls: Iterable[ToolCall]) -> int:
    """How many of ``calls`` are memory calls: a tool_use whose STRUCTURED name is one of
    ``MEMORY_TOOL_NAMES`` and whose ``command`` argument invokes a memory verb.

    Blocks, not verb occurrences — one Bash call chaining two verbs is one tool call, and
    ``memory_verbs`` is where the verb-level count lives. Never a scan of the agent's prose: the
    only text read here is the ``command`` argument of a structured tool call, parsed as argv."""
    return sum(1 for call in calls if _is_memory_call(call))


def memory_verbs(calls: Iterable[ToolCall]) -> list[str]:
    """Every memory verb across ``calls``, in stream order."""
    verbs: list[str] = []
    for call in calls:
        if call.name in MEMORY_TOOL_NAMES:
            verbs.extend(memory_verbs_in_command(_command_of(call)))
    return verbs


def _is_memory_call(call: ToolCall) -> bool:
    return call.name in MEMORY_TOOL_NAMES and bool(memory_verbs_in_command(_command_of(call)))


def _command_of(call: ToolCall) -> str:
    command = call.arguments.get("command")
    return command if isinstance(command, str) else ""
