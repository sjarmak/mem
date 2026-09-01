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
  ``command`` argument, so counting is a CLI-grammar parse rather than a structured-name equality.
  That parse is mechanical — argv shape, not meaning — and carries its own tests. It is never a
  read of prose.

**On naming as a treatment.** An allowlisted tool called ``mem_recall`` is itself an invitation:
the ladder experiments downstream vary GUIDANCE STRENGTH, and a tool whose name is a request to
use it would confound every rung of that dial. The surface here is deliberately neutral in the
structured stream — the tool the agent is handed is ``Bash``, the same general-purpose tool it
gets anyway — and the memory affordance is a CLI on ``PATH``, discovered the way the agent would
discover it in a real repo. So the only thing that varies across the ladder is the guidance text.

**The store.** ``bd init`` puts ``.beads/`` in the cwd, and the cwd is wiped between legs. Here the
store is minted in its own directory OUTSIDE the sandbox and pinned into every call by a ``bd``
shim on ``PATH`` that injects ``-C <store>``. ``provision_memory_tool`` calls
``assert_store_outside`` ITSELF, with a REQUIRED ``sandbox`` argument (no default, so a caller
cannot omit the check by omitting a word), and no caller can provision a store the sandbox
would eat: inside the sandbox the wipe deletes it, and ABOVE the sandbox ``bd init`` writes
``CLAUDE.md`` / ``AGENTS.md`` / ``.agents`` / ``.claude`` / ``.cursor`` and a git repo into it,
which Claude Code auto-loads by walking up from the cwd — the very contamination
``sandbox.assert_neutral_ancestry`` refuses to spend on.

CORRECTION to the record of commit a52bebd, whose message asserted "the shipped bd has no such env
var" about ``BEADS_DIR``. That is FALSE and the claim is withdrawn: the shipped bd (1.3.0-rc.1)
DOES honor ``BEADS_DIR``, verified by a round-trip from a foreign cwd, and ``-C`` simply beats
``BEADS_DIR`` when both are set. ``-C`` remains what the shim injects — a flag on the argv is
visible in the executed command line the counter parses, where an inherited env var is not — but
it is a choice, not a forced hand, and the earlier message stated a fact about bd that is not true.

**The shim must not resolve back to itself.** ``env()`` puts ``bin_dir`` FIRST on ``PATH``, so a
shim whose body says ``exec bd ...`` re-execs the shim, forever, prepending another ``-C <store>``
each pass. That shipped in a52bebd and hung every call the evaluated agent made. The binary is
therefore resolved to an ABSOLUTE path at provision time, and any resolution landing inside
``bin_dir`` is REFUSED. ``test_shim_does_not_self_exec_under_surface_env`` runs the shim WITH
``env()`` applied, which is the only geometry that can catch it: every test that invokes the shim
by absolute path under the ambient ``PATH`` passes whether the surface works or not — that
fixture geometry is why a52bebd's suite was green on a surface that did not work at all.

**Fingerprint.** ``surface_fingerprint`` hashes the tool surface as a POLICY — the command, the
verbs, the denied tools, the MCP config path, the store's SCOPE and prefix — never the store's
realised path. The path is a per-repeat tempdir, so hashing it would force a permanent cache MISS
and re-spend real money on a difference that moves no measurement; the same argument ``sandbox``
makes for ``TMPDIR``. What must be caught is a CHANGE OF SURFACE (a different verb set, an MCP
config appearing, a store that stops being external), and that is what the policy digest moves on.

``settings_fingerprint`` is the seam a grid folds this into. It is NOT wired to a grid here, and
that is deliberate rather than half-done: no grid runs this surface yet. The grid that will is
E1's (mem-eg850), which owns the ladder's identity and its resume cache, so the wiring belongs in
that bead's change where a real cell can prove a surface change forces a MISS. Until then the only
callers are this module's tests and ``e1_smoke``, and no cached paid cell can resume across a
tool-surface change because no cached paid cell uses the surface.

**Host exposure, stated plainly.** ``--allowedTools Bash`` is a real shell on the operator's host;
the sandbox bounds the CWD, not the process table. When the self-exec shim hung, the evaluated
agent reached for a host-wide ``pkill -9 bd``, which would have killed the shared city bd. Two
things are done about that here: the hang is fixed at its root, and ``HOST_DENIED_TOOLS`` is
passed as ``--disallowedTools`` so the signal verbs are refused by the CLI's own permission layer.
What REMAINS EXPOSED: ``--disallowedTools`` is a rule engine, not a kernel boundary — an agent that
writes a script and runs it, or spells the same call another way, is not stopped, and the run still
shares the host's process table, network and ``$HOME``. Real confinement is a container or a user
namespace, and it is not built here.

ZFC: filesystem plumbing, a subprocess spawn, and a CLI-grammar parse of argv. No model call, no
judgment about what a memory MEANS.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from membench.runner.resume_cache import digest
from membench.schemas.trace import ToolCall
from membench.spawn import Runner, run_checked

# The CLI the memory affordance IS. Not a wrapper verb of our own invention: beads#5877's MVP is
# these verbs on this binary, so an experiment about them must call them.
MEMORY_COMMAND = "bd"

# The memory verbs of bd 1.3.0-rc.1 (the host build; a52bebd's "bd 1.2.1" was a stale pin).
# `link` is deliberately ABSENT: it is shorthand for `bd dep add` (an issue dependency), not a
# memory verb, and counting it would inflate every rate with ordinary issue-graph work. `bd prime`
# is absent for a different reason — R8. The shipped prime emits "## Persistent Memories (N)"
# followed by full bodies, so shelling it would DELIVER memory rather than measure a choice to
# read it, which is the one thing this series must not do.
MEMORY_VERBS: tuple[str, ...] = ("remember", "recall", "memories", "forget")

# The structured tool name a memory call arrives under. A tuple, and the counter matches against
# it, so a later MCP surface (whose calls arrive as `mcp__memory__recall`) extends this rather
# than forking the counter.
MEMORY_TOOL_NAMES: tuple[str, ...] = ("Bash",)

# What the step must allow for the tool to be reachable at all. The goal leg's historical
# `--allowedTools Write` clamp is precisely what made every memory call impossible.
MEMORY_ALLOWED_TOOLS: tuple[str, ...] = ("Bash", "Write")

# Passed as `--disallowedTools` wherever this surface is handed to a real agent. NOT a sandbox:
# see the module docstring's host-exposure paragraph for what it does not cover. It exists because
# a hung surface once drove the evaluated agent to a host-wide `pkill -9 bd`, which would have
# killed the shared city bd — the signal verbs are the ones whose blast radius leaves the sandbox.
HOST_DENIED_TOOLS: tuple[str, ...] = (
    "Bash(pkill:*)",
    "Bash(kill:*)",
    "Bash(killall:*)",
    "Bash(systemctl:*)",
    "Bash(shutdown:*)",
    "Bash(reboot:*)",
)

# bd's GLOBAL flags that take a SPACE-separated value (`bd --help`, 1.3.0-rc.1). The counter skips
# both the flag and its value to see the verb behind it: `bd -C /tmp recall k` IS a memory call,
# and a52bebd's regex missed it — an under-count in exactly the direction that manufactures the
# near-zero null this series exists to rule out. The list is explicit rather than a generic "skip
# whatever follows a flag", because that rule would eat the verb out of `bd --json recall k`.
BD_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-C",
        "--directory",
        "--db",
        "--database",
        "--actor",
        "--dolt-auto-commit",
        "--mem-profile",
    }
)

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

# Shell metacharacters that END one command and begin the next. A memory call is recognised per
# COMMAND SEGMENT: `echo bd recall x` is one segment whose command word is `echo` (not a memory
# call), while `cd /tmp && bd recall x` is two (the second one is).
#
# The BACKTICK is here for the same reason `(` is: `` `bd recall k` `` is a command substitution
# whose bd really executes. d9809a2 broke the segment on `$(` (via the `(`) but not on the
# backtick form, so every backticked memory call was MISSED — the under-count direction, which is
# the one that manufactures the near-zero null this series exists to rule out.
_SEGMENT_BREAKS = frozenset(";&|()\n<>`")

# `{` and `}` break a segment only when they stand ALONE as shell grouping keywords.
#
# CORRECTION to this comment as it shipped in 5e45493, which claimed d9809a2's unconditional break
# "lost the call" on `xargs -I{} bd recall {}`. That is FALSE and the claim is withdrawn: replayed
# against d9809a2 the command returns ['recall'], because the break splits `-I{}` into `[xargs,
# -I]` and then starts a fresh `[bd, recall]` segment whose command word is still bd. What the
# unconditional break actually did was fabricate segments the shell never makes, which is a
# correctness problem in its own right — an attached brace is literal text — but not an under-count,
# and this series' whole argument rests on being exact about which direction an instrument errs in.
_BRACES = frozenset("{}")

# `NAME=value` prefixes may precede the command word: `BEADS_ACTOR=bot bd recall k`.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")

# Shell KEYWORDS that may stand in front of a segment's command word. `if bd recall k; then ...`
# is a real memory call; d9809a2 read `if` as the command word and returned nothing. `for` /
# `while` / `until` / `case` cover the HEADER segment (whose command word is a variable name, never
# bd), and `do` / `then` / `else` / `elif` cover the BODY segments, which is where an agent's loop
# actually calls bd.
_SHELL_KEYWORDS: frozenset[str] = frozenset(
    {
        "!",
        "case",
        "do",
        "done",
        "elif",
        "else",
        "esac",
        "fi",
        "for",
        "if",
        "in",
        "then",
        "until",
        "while",
    }
)

# Wrappers that EXECUTE the command word following them, so the bd behind them is a real call:
# `sudo bd recall k`, `timeout 30 bd recall k`, `env bd recall k`. Each is skipped along with its
# own option words, and the next non-option word must still be `bd` — `timeout 30 echo bd recall k`
# stays a non-call because the scan stops at the first non-option word and it is `echo`.
_TRANSPARENT_WRAPPERS: frozenset[str] = frozenset(
    {
        "command",
        "doas",
        "env",
        "exec",
        "ionice",
        "nice",
        "nohup",
        "setsid",
        "stdbuf",
        "sudo",
        "time",
        "timeout",
        "watch",
        "xargs",
    }
)

# Interpreters whose `-c` argument is a COMMAND STRING. `bash -c 'bd recall k'` executes bd, and
# d9809a2 dropped the string silently. The string is re-tokenized and re-scanned.
_SHELL_INTERPRETERS: frozenset[str] = frozenset(
    {"ash", "bash", "dash", "ksh", "script", "sh", "zsh"}
)

# `eval` joins its remaining words into one command string, so it is scanned with the whole tail
# as that string.
_EVAL = "eval"

# A bare duration/count a transparent wrapper may take before the command word (`timeout 30`,
# `timeout 1m`, `nice 10`).
_DURATION = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")

# Wrapper options that take a SPACE-separated value, so the value is not mistaken for the command
# word: `sudo -u bot bd recall k`, `xargs -I ARG bd recall ARG`, `timeout -k 5 30 bd recall k`.
# One flat set rather than per-wrapper tables: over-skipping a word can only ever cost a call whose
# command word is itself the value of an option, which no real invocation has.
_WRAPPER_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "--adjustment",
        "--kill-after",
        "--signal",
        "-C",
        "-E",
        "-I",
        "-L",
        "-P",
        "-S",
        "-U",
        "-a",
        "-c",
        "-d",
        "-e",
        "-g",
        "-h",
        "-i",
        "-k",
        "-n",
        "-o",
        "-p",
        "-r",
        "-s",
        "-t",
        "-u",
    }
)

# How deep a `-c` command string is followed. `bash -c "sh -c 'bd recall k'"` is already
# pathological; the bound stops a crafted command from recursing without end.
_MAX_NESTING = 4

# Characters that terminate a heredoc DELIMITER word: `cat <<EOF` and `cat <<-'EOF' | tee f`.
_DELIMITER_END = frozenset(" \t\n;&|()<>")


# The READ half of ``MEMORY_VERBS``, split out because E3b grades reads and writes as separate
# endogenous choices. ``forget`` is neither: it is a deletion, and folding it into either half
# would let a delete masquerade as evidence the agent consulted or recorded anything.
MEMORY_READ_VERBS: tuple[str, ...] = ("recall", "memories")
MEMORY_WRITE_VERBS: tuple[str, ...] = ("remember",)

# ``bd memories`` with NO search operand lists every memory in the store. That is the unbounded
# enumerate E3b's read seam must not expose: a leg that can enumerate does not have to REMEMBER
# where it put something, so ``closure_rate`` would saturate for a reason that is not memory use
# and the endogenous read measurement would report a ceiling it did not earn. The verb is not
# removed from ``MEMORY_VERBS`` — an agent that reaches for it has still made a memory call, and
# the count of that choice is data — it is refused at the read seam, and only in its bare form:
# ``bd memories dolt`` is a bounded search and stays available. ``bd recall <key>`` needs no bound
# at all; it is keyed, so one invocation yields one memory by construction.
ENUMERATE_VERB = "memories"


@dataclass(frozen=True)
class MemoryInvocation:
    """One observed ``bd`` memory call: the verb and the operands it was given.

    Immutable and operand-carrying because the three endogenous questions are answered by argv
    shape and nothing else — whether the call READ or WROTE, whether a read was bounded, and which
    ids it named."""

    verb: str
    operands: tuple[str, ...] = ()

    @property
    def is_read(self) -> bool:
        return self.verb in MEMORY_READ_VERBS

    @property
    def is_write(self) -> bool:
        return self.verb in MEMORY_WRITE_VERBS

    @property
    def is_enumerate(self) -> bool:
        """A bare list-all. ``bd memories dolt`` is a bounded search and is not one."""
        return self.verb == ENUMERATE_VERB and not self.operands

    @property
    def requested_ids(self) -> tuple[str, ...]:
        """The ids a KEYED read named. Empty for a search: its operand is a query, not an id."""
        return self.operands if self.verb == "recall" else ()


class MemoryToolError(RuntimeError):
    """The memory tool surface could not be provisioned or driven — a missing ``bd``, a failed
    ``bd init``, a store the sandbox would eat, a ``bd`` that resolves back to the shim. Raised,
    never degraded to an empty surface: a run whose memory tool silently does not exist is the
    indistinguishable null."""


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
        it, so the OAuth token and the rest of the environment survive.

        Shim-first is exactly why ``bd_binary`` must be an absolute path outside ``bin_dir``; see
        ``resolve_bd_binary``."""
        return {"PATH": os.pathsep.join([str(self.bin_dir), os.environ.get("PATH", "")])}

    def fingerprint(self) -> str:
        return surface_fingerprint(mcp_config=self.mcp_config)


def surface_fingerprint(*, mcp_config: str | None = None) -> str:
    """The digest of the tool surface AS A POLICY. Moves when the command, the counted verbs, the
    allowed or denied tools, the store scope or the MCP config path change; does NOT move when a
    run mints its store in a different tempdir (see the module docstring)."""
    return digest(
        {
            "command": MEMORY_COMMAND,
            "verbs": list(MEMORY_VERBS),
            "tool_names": list(MEMORY_TOOL_NAMES),
            "allowed_tools": list(MEMORY_ALLOWED_TOOLS),
            "denied_tools": list(HOST_DENIED_TOOLS),
            "store_scope": STORE_SCOPE,
            "store_prefix": STORE_PREFIX,
            "mcp_config": mcp_config,
        }
    )


def settings_fingerprint(settings: object, *, mcp_config: str | None = None) -> str:
    """What a grid running this surface would carry as ``BaseRunIdentity.settings_fingerprint``:
    the seeded ``settings.json`` AND the tool surface, hashed together.

    Together, not two fields: both are measured inputs the command line cannot carry, both reach
    the agent through the config/filesystem surface, and one identity field that moves on either is
    what would keep a grid from shipping a tool-surface change as a cache HIT.

    NOT wired to a grid by this change — see the module docstring. The seam is here; E1 (mem-eg850)
    owns the grid that consumes it."""
    return digest(
        {"settings": settings, "tool_surface": surface_fingerprint(mcp_config=mcp_config)}
    )


def resolve_bd_binary(bd_binary: str | None = None, *, refuse_under: Path | None = None) -> str:
    """The ABSOLUTE path of the bd a surface wraps: an explicit pin, else ``MEMBENCH_BD_BINARY``,
    else ``bd`` on the ambient ``PATH``.

    Absolute, never the bare name, and never a path under ``refuse_under`` (the shim's own
    ``bin_dir``). A shim body that says ``exec bd`` re-execs itself forever once ``env()`` puts
    ``bin_dir`` first on ``PATH``; that shipped, hung every call, and is the defect this argument
    exists to make unrepresentable. Resolution failure RAISES rather than falling back to the bare
    name: a surface that silently cannot spawn bd is the indistinguishable zero."""
    requested = bd_binary or os.environ.get(ENV_BD_BINARY, "") or MEMORY_COMMAND
    found: str | None
    if os.path.sep in requested:
        candidate = str(Path(requested).expanduser().resolve())
        executable = os.path.isfile(candidate) and os.access(candidate, os.X_OK)
        found = candidate if executable else None
    else:
        # `which` against the AMBIENT PATH. The shim dir is not on it at provision time, but
        # `refuse_under` is the guard that does not depend on that being true.
        located = shutil.which(requested)
        found = str(Path(located).resolve()) if located else None
    if found is None:
        raise MemoryToolError(
            f"no executable {requested!r} found — install bd, or point {ENV_BD_BINARY} at it. "
            "Without a store the evaluated agent has no memory tool to call and the run measures "
            "wiring, not behaviour."
        )
    if refuse_under is not None:
        refuse_r = refuse_under.resolve()
        found_p = Path(found)
        if found_p == refuse_r or refuse_r in found_p.parents:
            raise MemoryToolError(
                f"bd resolved to {found}, which is inside the shim directory {refuse_r}. The shim "
                "would exec itself forever (env() puts the shim dir first on PATH), prepending a "
                "-C on every pass until the call hangs. Refusing to provision."
            )
    return found


def assert_store_outside(sandbox: Path, store_dir: Path) -> None:
    """Refuse a store the sandbox would eat, or one that would contaminate it.

    Two directions, both fatal and for different reasons. A store INSIDE the cwd is deleted by
    ``toolreq_builtin._wipe_cwd_contents`` between the legs, which silently turns the second leg's
    recall into a miss — the exact defect this module was filed to remove. A store ABOVE the cwd is
    worse: ``bd init`` writes ``CLAUDE.md``, ``AGENTS.md``, ``.agents/``, ``.claude/``, ``.cursor/``
    and a git repo into it, and Claude Code auto-loads the context files by walking up from the cwd
    with no tool call, so the sandbox stops being neutral and ``sandbox.assert_neutral_ancestry``
    would (rightly) refuse to spend.

    Called by ``provision_memory_tool`` whenever it is given a sandbox — not a helper a caller may
    forget."""
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
    sandbox: Path | None,
    bd_binary: str | None = None,
    mcp_config: str | None = None,
    runner: Runner = subprocess.run,
) -> MemoryToolSurface:
    """Mint a store under ``root`` and a ``bd`` shim that pins every call to it.

    ``root`` is the caller's per-repeat directory (a ``TemporaryDirectory``), NOT the sandbox cwd:
    the whole point is a store the wipe cannot reach. ``sandbox`` is REQUIRED — keyword-only with
    no default — and is CHECKED here (``assert_store_outside``) before ``bd init`` writes anything.
    It was defaulted to ``None`` in d9809a2, which left the check firing only by caller discipline:
    a cell that simply forgot the argument got a store the wipe eats and a second leg that recalls
    nothing, with no error anywhere. Passing ``None`` is still allowed but must now be WRITTEN, and
    it means one thing only: there is no sandbox in this context (the harness-side fixtures that
    mint a store under ``tmp_path`` and never run an agent).

    The shim is a two-line ``sh`` script rather than an env var because ``-C`` lands on the argv
    where the counter can see it (``BEADS_DIR`` would work too — see the docstring's correction —
    but an inherited env var is invisible in the executed command line)."""
    store_dir = root / "store"
    bin_dir = root / "bin"
    store_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    if sandbox is not None:
        assert_store_outside(sandbox, store_dir)
    binary = resolve_bd_binary(bd_binary, refuse_under=bin_dir)

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
    block in its stream, and this path produces none.

    Invokes the shim by ABSOLUTE path under the ambient environment, which is what a harness wants
    and what NO test may treat as evidence the agent's path works: the agent reaches the shim
    through ``surface.env()``, and only a test that applies ``env()`` exercises that."""
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


# --------------------------------------------------------------------------------------
# the counter: a shell-word scan of the EXECUTED command, never a substring of prose
# --------------------------------------------------------------------------------------


def _read_substitution(command: str, start: int) -> tuple[str, int]:
    """Read one command substitution beginning at ``start`` and return (inner text, index after).

    ``$(`` is matched to its balanced ``)`` so a nested substitution survives; a backtick runs to
    the next backtick. An unterminated form yields the rest of the line, which is what the shell
    would complain about and what an agent could otherwise hide a call behind."""
    if command[start] == "`":
        end = command.find("`", start + 1)
        if end == -1:
            return command[start + 1 :], len(command)
        return command[start + 1 : end], end + 1
    depth = 0
    i = start + 1  # sits on the "(" of "$("
    while i < len(command):
        if command[i] == "(":
            depth += 1
        elif command[i] == ")":
            depth -= 1
            if depth == 0:
                return command[start + 2 : i], i + 1
        i += 1
    return command[start + 2 :], len(command)


def command_segments(command: str) -> list[list[str]]:
    """Split one shell command line into its command segments, each a list of words.

    A deliberately small POSIX-shaped scanner, and every part of it is load-bearing for the count:

    * a quoted run stays ONE word, so ``echo "bd recall x"`` is ``[echo, 'bd recall x']`` — the
      quoted blob can never be read as a command word plus a verb;
    * but a command SUBSTITUTION inside a double-quoted run is scanned, because the shell runs it:
      ``v="$(bd recall k)"`` executes bd exactly as ``v=$(bd recall k)`` does. 5e45493 copied
      characters straight through a double-quoted run, so capturing a recall into a variable —
      the canonical agent spelling — and quoting it — the habitual one — counted as NO call. That
      miss direction is byte-identical to the near-zero-voluntary-use null E1 exists to rule out.
      A single-quoted run is still literal: the shell does not expand there;
    * a ``#`` starting a word drops the rest of the line, so a commented-out call is not a call;
    * a heredoc BODY is skipped to its delimiter, so prose inside ``<<EOF ... EOF`` is not scanned;
    * ``;``, ``&``, ``|``, ``(``, ``)``, ``{``, ``}``, ``<``, ``>`` and newline start a new
      segment, so ``cd /tmp && bd recall k`` yields the bd segment while ``echo bd recall k``
      does not.

    Mechanical throughout: this reads argv SHAPE. It never looks at what a memory says."""
    segments: list[list[str]] = []
    substituted: list[list[str]] = []
    words: list[str] = []
    buf: list[str] = []
    has_word = False
    quote: str | None = None
    pending_heredocs: list[str] = []
    i = 0
    n = len(command)

    def end_word() -> None:
        nonlocal buf, has_word
        if has_word:
            words.append("".join(buf))
        buf = []
        has_word = False

    def end_segment() -> None:
        nonlocal words
        end_word()
        if words:
            segments.append(words)
        words = []

    while i < n:
        ch = command[i]
        if quote is not None:
            if quote == '"' and (command[i : i + 2] == "$(" or ch == "`"):
                inner, i = _read_substitution(command, i)
                substituted.extend(command_segments(inner))
                continue
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            else:
                buf.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            has_word = True
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            if command[i + 1] == "\n":  # line continuation: neither a word nor a break
                i += 2
                continue
            buf.append(command[i + 1])
            has_word = True
            i += 2
            continue
        if ch == "<" and command[i : i + 2] == "<<":
            # A heredoc redirection. Read its delimiter, arm the body skip, and emit no word:
            # the body is prose the shell never executes, so it must not reach the scan.
            end_word()
            i += 2
            if i < n and command[i] == "-":
                i += 1
            while i < n and command[i] in " \t":
                i += 1
            delimiter: list[str] = []
            while i < n and command[i] not in _DELIMITER_END:
                if command[i] in ("'", '"'):
                    i += 1
                    continue
                delimiter.append(command[i])
                i += 1
            if delimiter:
                pending_heredocs.append("".join(delimiter))
            continue
        if ch == "#" and not has_word:
            while i < n and command[i] != "\n":
                i += 1
            continue
        if ch == "\n" and pending_heredocs:
            end_segment()
            i += 1
            while pending_heredocs and i <= n:
                line_end = command.find("\n", i)
                line = command[i:] if line_end == -1 else command[i:line_end]
                i = n if line_end == -1 else line_end + 1
                if line.strip() == pending_heredocs[0]:
                    pending_heredocs.pop(0)
                if line_end == -1:
                    break
            continue
        if ch in _BRACES:
            # Grouping keyword only when it stands alone (`{ bd recall k; }`). Attached to a word
            # it is literal text — `xargs -I{} bd recall {}` must stay one segment.
            standalone = not has_word and (
                i + 1 >= n or command[i + 1].isspace() or command[i + 1] in _SEGMENT_BREAKS
            )
            if standalone:
                end_segment()
                i += 1
                continue
            buf.append(ch)
            has_word = True
            i += 1
            continue
        if ch in _SEGMENT_BREAKS:
            end_segment()
            i += 1
            continue
        if ch.isspace():
            end_word()
            i += 1
            continue
        buf.append(ch)
        has_word = True
        i += 1

    end_segment()
    return segments + substituted


def _is_option(word: str) -> bool:
    return word.startswith("-") and word != "-"


def _skip_prefixes(words: Sequence[str], index: int) -> int:
    """Advance past everything that can stand BETWEEN the start of a segment and the command word
    that actually runs: ``NAME=value`` assignments, shell keywords, and transparent wrappers with
    their own options.

    d9809a2 skipped assignments only, so ``if bd recall k``, ``do bd recall $k``, ``! bd recall k``,
    ``sudo bd ...``, ``env bd ...``, ``timeout 30 bd ...`` and ``command bd ...`` all returned the
    keyword or wrapper as the command word and counted as NO call. Every one of those is an
    under-count of the primary endpoint.

    Mechanical throughout: membership of a word in a fixed name set and the ``-``-prefix shape of
    argv. Nothing here reads what a memory says."""
    moved = True
    while moved and index < len(words):
        moved = False
        while index < len(words) and _ASSIGNMENT.match(words[index]):
            index += 1
            moved = True
        if index < len(words) and words[index] in _SHELL_KEYWORDS:
            index += 1
            moved = True
            continue
        if index < len(words) and PurePosixPath(words[index]).name in _TRANSPARENT_WRAPPERS:
            index += 1
            moved = True
            # The wrapper's own options, and a bare duration/count (`timeout 30`). The loop then
            # re-checks assignments and keywords, so `sudo env FOO=1 timeout 5 bd recall k` walks
            # all the way through.
            while index < len(words) and (
                _is_option(words[index]) or _DURATION.match(words[index])
            ):
                flag = words[index]
                index += (
                    2
                    if _is_option(flag) and "=" not in flag and flag in _WRAPPER_VALUE_FLAGS
                    else 1
                )
    return index


def _interpreter_command_string(words: Sequence[str], index: int) -> str | None:
    """The command STRING an interpreter segment would execute, or ``None``.

    ``bash -c 'bd recall k'`` and ``sh -lc "bd recall k"`` both yield ``bd recall k``; ``eval``
    yields its whole joined tail. Returning the string rather than a verb keeps the recursion in
    one place."""
    name = PurePosixPath(words[index]).name
    if name == _EVAL:
        tail = " ".join(words[index + 1 :])
        return tail or None
    if name not in _SHELL_INTERPRETERS:
        return None
    cursor = index + 1
    while cursor < len(words):
        word = words[cursor]
        if not _is_option(word):
            return None
        # `-c`, and clustered short forms like `-lc`; never a long option (`--posix`).
        if not word.startswith("--") and "c" in word[1:]:
            return words[cursor + 1] if cursor + 1 < len(words) else None
        cursor += 1
    return None


def _invocations_of_segment(words: Sequence[str], depth: int = 0) -> list[MemoryInvocation]:
    """The memory invocations this ONE command segment makes, verb AND operands.

    Usually at most one — a segment is one invocation — but an interpreter's ``-c`` string is a
    whole command line of its own, so ``sh -c 'bd recall a; bd remember b'`` yields two.

    Matches the command word by BASENAME (so ``/usr/local/bin/bd`` counts and ``abd`` does not),
    then walks bd's global flags — consuming the value of a value-taking flag — to the first
    non-flag token, which is the subcommand. ``bd issue remember`` therefore does not count:
    ``issue`` is the subcommand.

    The operands are carried because two of the endogenous measurements need them and neither can
    be recovered from a bare verb: ``bd memories`` with no search term is an ENUMERATE while
    ``bd memories dolt`` is a bounded search, and ``bd recall <key>`` is the only place the ids the
    agent actually asked for appear. Operand words after the verb are taken verbatim, minus bd's
    own options — this is argv shape, not a reading of what the agent meant."""
    index = _skip_prefixes(words, 0)
    if index >= len(words):
        return []
    if depth < _MAX_NESTING:
        nested = _interpreter_command_string(words, index)
        if nested is not None:
            return [
                invocation
                for segment in command_segments(nested)
                for invocation in _invocations_of_segment(segment, depth + 1)
            ]
    if PurePosixPath(words[index]).name != MEMORY_COMMAND:
        return []
    index += 1
    while index < len(words) and _is_option(words[index]):
        flag = words[index]
        index += 2 if "=" not in flag and flag in BD_VALUE_FLAGS else 1
    if index >= len(words) or words[index] not in MEMORY_VERBS:
        return []
    verb = words[index]
    operands = tuple(word for word in words[index + 1 :] if not _is_option(word))
    return [MemoryInvocation(verb=verb, operands=operands)]


def _verbs_of_segment(words: Sequence[str], depth: int = 0) -> list[str]:
    """The memory verbs this ONE command segment invokes — ``_invocations_of_segment`` without
    the operands. Kept as the narrow view the counters want."""
    return [invocation.verb for invocation in _invocations_of_segment(words, depth)]


def memory_verbs_in_command(command: str) -> list[str]:
    """Every memory verb invoked in one shell command line, in order. The mechanical half of the
    counter, exposed so a driver can report WHICH verbs an agent reached for (read vs write is the
    whole endogenous question) rather than only how often."""
    return [verb for segment in command_segments(command) for verb in _verbs_of_segment(segment)]


def endogenous_memory_tool_calls(calls: Iterable[ToolCall]) -> int:
    """How many of ``calls`` are memory calls the AGENT CHOSE to make: a tool_use whose STRUCTURED
    name is one of ``MEMORY_TOOL_NAMES`` and whose ``command`` argument invokes a memory verb.

    ``endogenous_`` is not decoration. ``EfficiencyMetrics.memory_tool_calls``
    (``schemas/metrics.py``) already exists and counts something else entirely: normalized MEMORY
    EVENTS the harness performed on the arm's behalf (``metrics/scorers.py``). The two must never
    share a name, and neither may the EMITTED row keys — ``e1_smoke`` publishes
    ``endogenous_memory_tool_calls`` / ``endogenous_memory_verbs``, not the colliding strings.
    ``runner/metrics.py`` no longer passes ``len(agent_result.tool_calls)`` as
    ``non_memory_tool_calls``; it splits with ``partition_memory_calls`` first, so a Bash-wrapped
    memory call is not scored as a non-memory call the moment a grid wires this surface.

    Blocks, not verb occurrences — one Bash call chaining two verbs is one tool call, and
    ``endogenous_memory_verbs`` is where the verb-level count lives. Never a scan of the agent's
    prose: the only text read here is the ``command`` argument of a structured tool call, parsed as
    shell words."""
    return sum(1 for call in calls if _is_memory_call(call))


def partition_memory_calls(calls: Iterable[ToolCall]) -> tuple[list[ToolCall], list[ToolCall]]:
    """``(memory_calls, non_memory_calls)`` over one step's tool calls.

    ``runner/metrics.py`` needs the split, not just the count: it reports both a COUNT and a
    summed LATENCY for non-memory tool calls, and a Bash-wrapped memory call must leave both."""
    memory: list[ToolCall] = []
    other: list[ToolCall] = []
    for call in calls:
        (memory if _is_memory_call(call) else other).append(call)
    return memory, other


def endogenous_memory_verbs(calls: Iterable[ToolCall]) -> list[str]:
    """Every memory verb across ``calls``, in stream order."""
    verbs: list[str] = []
    for call in calls:
        if call.name in MEMORY_TOOL_NAMES:
            verbs.extend(memory_verbs_in_command(_command_of(call)))
    return verbs


def memory_invocations_in_command(command: str) -> list[MemoryInvocation]:
    """Every memory invocation in one shell command line, in order — ``memory_verbs_in_command``
    with the operands kept."""
    return [
        invocation
        for segment in command_segments(command)
        for invocation in _invocations_of_segment(segment)
    ]


def memory_invocations(calls: Iterable[ToolCall]) -> list[MemoryInvocation]:
    """Every memory invocation across ``calls``, in stream order. The harness-OBSERVED record of
    what the agent asked memory for: parsed from the ``command`` argument of structured tool calls,
    never from the agent's prose or its own account of what it did."""
    invocations: list[MemoryInvocation] = []
    for call in calls:
        if call.name in MEMORY_TOOL_NAMES:
            invocations.extend(memory_invocations_in_command(_command_of(call)))
    return invocations


def observed_requested_ids(calls: Iterable[ToolCall]) -> list[str]:
    """The memory ids the agent asked for, in stream order, deduplicated.

    Only keyed ``recall`` operands count. A bounded ``bd memories <term>`` names a QUERY, not an
    id, and treating a search term as a requested id would credit an agent with asking for
    whatever the search happened to return. This is the harness's own reading of argv, which is
    why ``runner/metrics.py`` can use it as ``available_ids`` on an endogenous-read step without
    trusting the agent's self-report."""
    seen: list[str] = []
    for invocation in memory_invocations(calls):
        for memory_id in invocation.requested_ids:
            if memory_id not in seen:
                seen.append(memory_id)
    return seen


def enumerate_invocations(calls: Iterable[ToolCall]) -> list[MemoryInvocation]:
    """Every unbounded list-all invocation among ``calls`` — see ``ENUMERATE_VERB``."""
    return [invocation for invocation in memory_invocations(calls) if invocation.is_enumerate]


def assert_recall_is_bounded(calls: Iterable[ToolCall]) -> None:
    """Raise if any observed invocation enumerates the whole store.

    Called at the read seam rather than trusted to a deny-list: ``--disallowedTools`` matches a
    command PREFIX, and the difference between the refused ``bd memories`` and the permitted
    ``bd memories dolt`` is the presence of an operand, which a prefix pattern cannot express."""
    unbounded = enumerate_invocations(calls)
    if unbounded:
        raise MemoryToolError(
            f"unbounded memory enumeration: {len(unbounded)} bare `{MEMORY_COMMAND} "
            f"{ENUMERATE_VERB}` call(s) would list the whole store, so a later leg could reach a "
            "value without having recorded where it put it"
        )


def _is_memory_call(call: ToolCall) -> bool:
    return call.name in MEMORY_TOOL_NAMES and bool(memory_verbs_in_command(_command_of(call)))


def _command_of(call: ToolCall) -> str:
    command = call.arguments.get("command")
    return command if isinstance(command, str) else ""
