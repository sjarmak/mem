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

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
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
MEMORY_ALLOWED_TOOLS: tuple[str, ...] = ("Bash", "Write", "Read", "Edit")

# The SECOND memory affordance, and the one the model reaches for first. mem-gj0pc: handed a `bd`
# shim and nothing else, the evaluated agent's first move was a Read of Claude Code's NATIVE
# MEMORY.md. Counting only bd verbs scored that reach as ZERO, which is exactly the disposition
# null this series exists to rule out. Both surfaces count, so `Read` and `Edit` are allowed and
# the file-path recognizer below sits beside the argv one. The native path is reachable only
# because `MemoryToolSurface.env()` pins CLAUDE_CONFIG_DIR under the surface root; without that
# pin these tool names would admit a read of the OPERATOR's memory index, not the agent's.
NATIVE_MEMORY_TOOL_NAMES: tuple[str, ...] = ("Read", "Write", "Edit", "NotebookEdit")

# Which of those WRITE. `Read` reads; the rest mutate.
NATIVE_MEMORY_WRITE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "NotebookEdit"})

# The tool arguments that name a path. Structural, like every other rule in this module: the
# recognizer reads the ARGUMENT, never the agent's prose about what it opened.
NATIVE_MEMORY_PATH_ARGS: tuple[str, ...] = ("file_path", "path", "notebook_path")

# The directory segment Claude Code keeps a project's memory under
# (`<config>/projects/<slug>/memory/...`). Required IN ADDITION to containment in the pinned
# config dir, so reading `settings.json` is not scored as a memory call.
NATIVE_MEMORY_SEGMENT = "memory"

# The THIRD door to the same files: a shell command (mem-zfm0m). `cat .../memory/MEMORY.md` and
# `echo '- note' >> .../memory/MEMORY.md` reach the native memory exactly as `Read` and `Write`
# do, and the argv recognizer above cannot see them because they carry no `bd` verb.
#
# The ACCESS is decided by the PATH alone (review F2): any token of the command that resolves
# under the pinned config dir's memory segment — as a whole word, embedded in a `key=value` or
# an inline interpreter string, spelled through `$CLAUDE_CONFIG_DIR`, or relative to a `cd`
# earlier in the same command — is an access, whatever the command word is. A recognizer that
# allowlisted command names scored `rg`, `wc`, `jq`, `dd if=`, `python3 -c "open(...)"` as
# nothing at all, and the miss ran in the direction of the finding it was meant to measure. The
# command word and the redirect shape decide READ vs WRITE only. Never the agent's prose about
# memory — a command that mentions "memory" in a comment and touches no pinned path counts for
# nothing.
NATIVE_MEMORY_BASH_TOOL = "Bash"

# Commands that WRITE their target: `tee` writes every path operand; `cp`/`mv` write the LAST
# operand and read the others (a copy OUT of the memory dir is a read of it); `sed` writes under
# `-i`/`--in-place`. Every other command word reads the paths it names.
NATIVE_MEMORY_BASH_WRITE_COMMANDS: tuple[str, ...] = ("tee", "cp", "mv")
NATIVE_MEMORY_BASH_SED_IN_PLACE_FLAGS: tuple[str, ...] = ("-i", "--in-place")

# Wrapper words that precede the real command word without changing what it does to its paths.
# Skipped, with their own option words (and `env`'s assignments), before the direction is read.
NATIVE_MEMORY_BASH_WRAPPERS: tuple[str, ...] = ("command", "sudo", "env", "time", "nice", "exec")

# Redirect operators, as `shlex` tokenises them with punctuation_chars. A write redirect's target
# is written; `<`'s target is read. `<<`/`<<<` name a delimiter or a string, not a file.
NATIVE_MEMORY_BASH_WRITE_REDIRECTS: tuple[str, ...] = (">", ">>", ">|", "&>", "&>>")
NATIVE_MEMORY_BASH_READ_REDIRECTS: tuple[str, ...] = ("<",)

# Where one shell command ends and the next begins, as `shlex` tokenises them.
NATIVE_MEMORY_BASH_SEGMENT_BREAKS: frozenset[str] = frozenset(
    {"|", "||", "|&", "&&", ";", ";;", "&", "(", ")"}
)

# What ends a path embedded inside a larger token (`open('/cfg/.../MEMORY.md').read()`,
# `if=/cfg/.../MEMORY.md`): the scan starts at the pinned config dir's own spelling and runs to
# the first of these.
NATIVE_MEMORY_BASH_PATH_TERMINATORS: frozenset[str] = frozenset(" \t\n'\"`()<>|&;,")

# The env var the surface pins the config dir under. Named once: `env()` sets it, the Bash
# recognizer expands `$CLAUDE_CONFIG_DIR` in a path against it, and the fingerprint carries it.
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"

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
_GRAMMAR_SEGMENT_BREAKS = frozenset(";&|()\n<>`")

# `{` and `}` break a segment only when they stand ALONE as shell grouping keywords.
#
# CORRECTION to this comment as it shipped in 5e45493, which claimed d9809a2's unconditional break
# "lost the call" on `xargs -I{} bd recall {}`. That is FALSE and the claim is withdrawn: replayed
# against d9809a2 the command returns ['recall'], because the break splits `-I{}` into `[xargs,
# -I]` and then starts a fresh `[bd, recall]` segment whose command word is still bd. What the
# unconditional break actually did was fabricate segments the shell never makes, which is a
# correctness problem in its own right — an attached brace is literal text — but not an under-count,
# and this series' whole argument rests on being exact about which direction an instrument errs in.
_GRAMMAR_BRACES = frozenset("{}")

# `NAME=value` prefixes may precede the command word: `BEADS_ACTOR=bot bd recall k`.
_GRAMMAR_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")

# Shell KEYWORDS that may stand in front of a segment's command word. `if bd recall k; then ...`
# is a real memory call; d9809a2 read `if` as the command word and returned nothing. `for` /
# `while` / `until` / `case` cover the HEADER segment (whose command word is a variable name, never
# bd), and `do` / `then` / `else` / `elif` cover the BODY segments, which is where an agent's loop
# actually calls bd.
_GRAMMAR_SHELL_KEYWORDS: frozenset[str] = frozenset(
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
_GRAMMAR_TRANSPARENT_WRAPPERS: frozenset[str] = frozenset(
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
_GRAMMAR_SHELL_INTERPRETERS: frozenset[str] = frozenset(
    {"ash", "bash", "dash", "ksh", "script", "sh", "zsh"}
)

# `eval` joins its remaining words into one command string, so it is scanned with the whole tail
# as that string.
_GRAMMAR_EVAL = "eval"

# A bare duration/count a transparent wrapper may take before the command word (`timeout 30`,
# `timeout 1m`, `nice 10`).
_GRAMMAR_DURATION = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")

# Wrapper options that take a SPACE-separated value, so the value is not mistaken for the command
# word: `sudo -u bot bd recall k`, `xargs -I ARG bd recall ARG`, `timeout -k 5 30 bd recall k`.
# One flat set rather than per-wrapper tables: over-skipping a word can only ever cost a call whose
# command word is itself the value of an option, which no real invocation has.
_GRAMMAR_WRAPPER_VALUE_FLAGS: frozenset[str] = frozenset(
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
_GRAMMAR_MAX_NESTING = 4

# Characters that terminate a heredoc DELIMITER word: `cat <<EOF` and `cat <<-'EOF' | tee f`.
_GRAMMAR_DELIMITER_END = frozenset(" \t\n;&|()<>")


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

# The flag under which ``bd remember`` takes an explicit key. Its value is consumed into
# ``MemoryInvocation.key`` rather than left among the operands, because it is not content.
BD_KEY_FLAG = "--key"

# bd's OWN acknowledgement of a stored memory, as the shipped binary (1.3.0-rc.1) prints it:
# ``Remembered [<key>]: <value>`` for a new key, ``Updated [<key>]: <value>`` for an existing one,
# and ``{"action": "remembered"}`` / ``{"action": "updated"}`` under ``--json``. Format-anchored on
# the tool's output line, the way ``parse/runners`` anchors on a test runner's summary line: this
# is a match on a fixed acknowledgement shape, never a reading of what the memory says.
#
# Why the RESULT decides (mem-8fv4t): a verb token on the argv is not an operation. The 160-leg
# staged fire scored ``bd remember list`` as its only endogenous write, and bd REFUSED it
# (``Error: "list" looks like a command``); ``bd remember <bare-existing-key>`` is a RECALL by bd's
# own documented convenience, and ``bd remember <bare-unknown-token>`` is refused. Argv shape
# cannot separate those from a stored write; the acknowledgement can, and it is POSITIVE evidence:
# an absent result (truncated stream), a refusal, a redirected-away stderr are each not a write.
#
# The key inside the brackets is captured so that ONE Bash call chaining two bd invocations
# (one tool_result, review F1) can hand each invocation its own acknowledgement line: a line is
# matched to the invocation that named its key with ``--key``, and an unkeyed invocation takes
# the next line nobody claimed. One line acknowledges at most one invocation.
_BD_REMEMBER_ACK = re.compile(r"^(?:Remembered|Updated) \[(?P<key>[^\]]*)\]", re.MULTILINE)
_BD_REMEMBER_ACK_ACTIONS: frozenset[str] = frozenset({"remembered", "updated"})

# bd's own marker for the bare-existing-key convenience: ``(recalled "<key>" -- a bare existing key
# READS. ...)`` on the text path, ``{"action": "recalled"}`` under ``--json``.
_BD_REMEMBER_RECALLED = re.compile(r'^\(recalled "(?P<key>[^"]*)"', re.MULTILINE)
_BD_REMEMBER_RECALLED_ACTION = "recalled"


def _bd_json_object(candidate: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("action"), str):
        return dict(parsed)
    return None


def _bd_json_action(result: str) -> str | None:
    """The ``action`` field of a ``--json`` bd result, or ``None`` when the result is not one.

    The whole result is tried first (bd may pretty-print), then each line (a pipe may have
    appended a cwd-reset notice after the object)."""
    for candidate in (result, *result.splitlines()):
        parsed = _bd_json_object(candidate)
        if parsed is not None:
            return str(parsed["action"])
    return None


@dataclass(frozen=True)
class _BdAck:
    """One acknowledgement (stored or recalled) bd printed, and the key it names ("" when the
    line carries none)."""

    text: str
    key: str


def _bd_acks(result: str) -> list[_BdAck]:
    """Every acknowledgement line in ``result``, in stream order. A refusal is not one."""
    whole = _bd_json_object(result)
    if whole is not None and "\n" in result.strip():
        # A pretty-printed single object: one acknowledgement for the whole result.
        key = whole.get("key")
        return [_BdAck(result, key if isinstance(key, str) else "")]
    acks: list[_BdAck] = []
    for line in result.splitlines():
        matched = _BD_REMEMBER_ACK.match(line) or _BD_REMEMBER_RECALLED.match(line)
        if matched:
            acks.append(_BdAck(line, matched.group("key")))
            continue
        parsed = _bd_json_object(line)
        if parsed is not None and (
            parsed["action"] in _BD_REMEMBER_ACK_ACTIONS
            or parsed["action"] == _BD_REMEMBER_RECALLED_ACTION
        ):
            key = parsed.get("key")
            acks.append(_BdAck(line, key if isinstance(key, str) else ""))
    return acks


def _attribute_result(
    invocations: Sequence[MemoryInvocation], result: str | None
) -> list[MemoryInvocation]:
    """Hand each invocation of ONE tool call the part of the call's result that is its own.

    One invocation owns the whole result (the join is exact). Several share one result, and
    each acknowledgement line in it is matched to at most one WRITE invocation: first by key,
    to the invocation that named it with ``--key``; then in stream order, to the unkeyed
    invocations, and last to keyed invocations whose key the line does not state (a ``--json``
    object without a ``key`` field). A write nothing acknowledges gets an empty result, which is
    not an acceptance. Read invocations keep the whole result; nothing grades them on it."""
    if result is None or len(invocations) <= 1:
        return [replace(invocation, result=result) for invocation in invocations]
    acks = _bd_acks(result)
    claimed: set[int] = set()
    owned: dict[int, str] = {}

    def _claim(index: int, wanted: str | None) -> None:
        for position, ack in enumerate(acks):
            if position in claimed or (wanted is not None and ack.key != wanted):
                continue
            claimed.add(position)
            owned[index] = ack.text
            return

    writes = [(i, inv) for i, inv in enumerate(invocations) if inv.is_write]
    for index, invocation in writes:
        if invocation.key:
            _claim(index, invocation.key)
    for index, invocation in writes:
        if index not in owned:
            _claim(index, "" if invocation.key else None)
    return [
        replace(invocation, result=owned.get(i, "") if invocation.is_write else result)
        for i, invocation in enumerate(invocations)
    ]


def remember_was_accepted(result: str | None) -> bool:
    """Whether a ``bd remember`` result carries bd's acknowledgement that the memory was STORED.

    ``None`` (no tool_result joined — the stream ended before the tool returned) is not an
    acceptance: the evidence is positive or it is absent."""
    if result is None:
        return False
    if _BD_REMEMBER_ACK.search(result):
        return True
    return _bd_json_action(result) in _BD_REMEMBER_ACK_ACTIONS


def remember_was_a_recall(result: str | None) -> bool:
    """Whether a ``bd remember`` result shows bd took the bare-existing-key path and READ."""
    if result is None:
        return False
    if _BD_REMEMBER_RECALLED.search(result):
        return True
    return _bd_json_action(result) == _BD_REMEMBER_RECALLED_ACTION


@dataclass(frozen=True)
class MemoryInvocation:
    """One observed ``bd`` memory call: the verb, the operands it was given, the explicit key (if
    any), and the tool_result the stream answered it with.

    Immutable and operand-carrying because the endogenous questions are answered by argv shape —
    whether a read was bounded, which ids it named — and, for whether a WRITE happened, by the
    tool's own acknowledgement (``result``), never by the verb alone."""

    verb: str
    operands: tuple[str, ...] = ()
    key: str = ""
    result: str | None = None

    @property
    def is_read(self) -> bool:
        return self.verb in MEMORY_READ_VERBS

    @property
    def is_write(self) -> bool:
        """The verb is a write verb. This is the ARGV-level view E3b grades content under (a
        write it cannot see the result of is still graded on what it stored); the E1 counter
        uses ``is_accepted_write``, which requires the tool's acknowledgement."""
        return self.verb in MEMORY_WRITE_VERBS

    @property
    def is_accepted_write(self) -> bool:
        """A write bd ACKNOWLEDGED: a write verb, content on the argv (an explicit key or at least
        one operand), and a result carrying bd's own stored line. A refused, recalled, truncated
        or silenced (``2>/dev/null``) remember is not one."""
        return (
            self.is_write and bool(self.key or self.operands) and remember_was_accepted(self.result)
        )

    @property
    def is_recall_by_result(self) -> bool:
        """A write verb bd answered as a READ: ``bd remember <bare-existing-key>``."""
        return self.is_write and remember_was_a_recall(self.result)

    @property
    def is_enumerate(self) -> bool:
        """A bare list-all. ``bd memories dolt`` is a bounded search and is not one."""
        return self.verb == ENUMERATE_VERB and not self.operands

    @property
    def stored_content(self) -> tuple[str, ...]:
        """The operand words a WRITE stored, MINUS the key it stored them under.

        ``bd remember <key> <content...>``: the first operand is the key the agent chose for
        itself, and it is exactly what an endogenous write grade must not read — grading a write
        by its key measures id-naming discipline, which is the one thing an endogenous write is
        free to decide. With an explicit ``--key`` the key is already out of the operands and
        every operand is content. Empty for a read: a read stores nothing."""
        if not self.is_write:
            return ()
        return self.operands if self.key else self.operands[1:]

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
    config_dir: Path | None = None

    def env(self) -> dict[str, str]:
        """``PATH`` with the shim dir FIRST, so ``bd`` resolves to the store-pinned wrapper even
        where a real ``bd`` is installed. Merged over ``os.environ`` by the agent, never replacing
        it, so the OAuth token and the rest of the environment survive.

        Shim-first is exactly why ``bd_binary`` must be an absolute path outside ``bin_dir``; see
        ``resolve_bd_binary``.

        ``CLAUDE_CONFIG_DIR`` is pinned alongside it, and for a reason the first paid E1 cycle
        made concrete (mem-gj0pc): handed only ``PATH``, the evaluated agent reached for Claude
        Code's NATIVE memory file and resolved it against the OPERATOR's real account home. Only
        the ``allowedTools`` clamp stopped the read. Pinning the config dir under this surface's
        own root puts that path INSIDE the measured surface, so a native reach lands somewhere the
        harness owns and can see instead of somewhere it must not reach at all."""
        env = {"PATH": os.pathsep.join([str(self.bin_dir), os.environ.get("PATH", "")])}
        if self.config_dir is not None:
            env[CONFIG_DIR_ENV] = str(self.config_dir)
        return env

    def fingerprint(self) -> str:
        return surface_fingerprint(mcp_config=self.mcp_config)


# Every module-level name under one of these prefixes is a constant a recognizer READS: the bd
# argv grammar (`MEMORY_*`, `BD_*`, `ENUMERATE_*`, `_GRAMMAR_*`), the acknowledgement grammar
# (`_BD_*`), the native recognizer (`NATIVE_MEMORY_*`, `CONFIG_DIR_*`), and the surface the agent
# is handed (`HOST_*`, `STORE_*`). `recognizer_policy` enumerates them mechanically, so a constant
# added under a prefix is in the fingerprint without anyone remembering to list it, and a constant
# added OUTSIDE every prefix fails the test that names the few deliberate exceptions (timeouts, the
# bd-binary env override), which do not change what a leg scores.
_POLICY_PREFIXES: tuple[str, ...] = (
    "MEMORY_",
    "NATIVE_MEMORY_",
    "BD_",
    "_BD_",
    "ENUMERATE_",
    "CONFIG_DIR_",
    "HOST_",
    "STORE_",
    "_GRAMMAR_",
)


def _policy_value(name: str, value: object) -> object:
    """``value`` in the JSON shape `digest` hashes, deterministic for every kind a recognizer
    constant takes. A kind not handled here is a loud error: hashing its repr would move the
    fingerprint on an interpreter detail instead of on the policy."""
    if isinstance(value, re.Pattern):
        return {"pattern": value.pattern, "flags": int(value.flags)}
    if isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return [_policy_value(name, item) for item in value]
    if isinstance(value, frozenset | set):
        return sorted(_policy_value(name, item) for item in value)  # type: ignore[type-var]
    raise TypeError(
        f"{name}: a {type(value).__name__} is not a recognizer policy value `_policy_value` can "
        "hash; extend it, or name the constant outside the policy prefixes"
    )


def recognizer_policy() -> dict[str, object]:
    """Every constant the recognizers read, by name, read at call time (a monkeypatched constant
    moves the fingerprint). Enumerated from the module under `_POLICY_PREFIXES`, never listed by
    hand."""
    module = globals()
    return {
        name: _policy_value(name, module[name])
        for name in sorted(module)
        if name.startswith(_POLICY_PREFIXES)
    }


def surface_fingerprint(*, mcp_config: str | None = None) -> str:
    """The digest of the tool surface AS A POLICY. Moves when the command, the counted verbs, the
    allowed or denied tools, the store scope, the MCP config path, or any constant a recognizer
    reads changes (the recognizers decide what a leg SCORES, mem-zfm0m item 6: a resume against an
    artifact counted under a different recognizer would pool two measurements under one identity);
    does NOT move when a run mints its store in a different tempdir (see the module docstring)."""
    return digest({"mcp_config": mcp_config, "policy": recognizer_policy()})


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
    config_dir = root / "config"
    store_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
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
        store_dir=store_dir,
        bin_dir=bin_dir,
        bd_binary=binary,
        mcp_config=mcp_config,
        config_dir=config_dir,
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
            while i < n and command[i] not in _GRAMMAR_DELIMITER_END:
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
        if ch in _GRAMMAR_BRACES:
            # Grouping keyword only when it stands alone (`{ bd recall k; }`). Attached to a word
            # it is literal text — `xargs -I{} bd recall {}` must stay one segment.
            standalone = not has_word and (
                i + 1 >= n or command[i + 1].isspace() or command[i + 1] in _GRAMMAR_SEGMENT_BREAKS
            )
            if standalone:
                end_segment()
                i += 1
                continue
            buf.append(ch)
            has_word = True
            i += 1
            continue
        if ch in _GRAMMAR_SEGMENT_BREAKS:
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
        while index < len(words) and _GRAMMAR_ASSIGNMENT.match(words[index]):
            index += 1
            moved = True
        if index < len(words) and words[index] in _GRAMMAR_SHELL_KEYWORDS:
            index += 1
            moved = True
            continue
        if index < len(words) and PurePosixPath(words[index]).name in _GRAMMAR_TRANSPARENT_WRAPPERS:
            index += 1
            moved = True
            # The wrapper's own options, and a bare duration/count (`timeout 30`). The loop then
            # re-checks assignments and keywords, so `sudo env FOO=1 timeout 5 bd recall k` walks
            # all the way through.
            while index < len(words) and (
                _is_option(words[index]) or _GRAMMAR_DURATION.match(words[index])
            ):
                flag = words[index]
                index += (
                    2
                    if _is_option(flag) and "=" not in flag and flag in _GRAMMAR_WRAPPER_VALUE_FLAGS
                    else 1
                )
    return index


def _interpreter_command_string(words: Sequence[str], index: int) -> str | None:
    """The command STRING an interpreter segment would execute, or ``None``.

    ``bash -c 'bd recall k'`` and ``sh -lc "bd recall k"`` both yield ``bd recall k``; ``eval``
    yields its whole joined tail. Returning the string rather than a verb keeps the recursion in
    one place."""
    name = PurePosixPath(words[index]).name
    if name == _GRAMMAR_EVAL:
        tail = " ".join(words[index + 1 :])
        return tail or None
    if name not in _GRAMMAR_SHELL_INTERPRETERS:
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
    if depth < _GRAMMAR_MAX_NESTING:
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
    key = ""
    operands: list[str] = []
    tail = list(words[index + 1 :])
    cursor = 0
    while cursor < len(tail):
        word = tail[cursor]
        if word == BD_KEY_FLAG and cursor + 1 < len(tail):
            key = tail[cursor + 1]
            cursor += 2
            continue
        if word.startswith(f"{BD_KEY_FLAG}="):
            key = word[len(BD_KEY_FLAG) + 1 :]
        elif not _is_option(word):
            operands.append(word)
        cursor += 1
    return [MemoryInvocation(verb=verb, operands=tuple(operands), key=key)]


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
            # One Bash call has one tool_result however many bd invocations it chained; each
            # invocation gets the acknowledgement that is its own (``_attribute_result``).
            invocations.extend(
                _attribute_result(memory_invocations_in_command(_command_of(call)), call.result)
            )
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


def observed_written_content(calls: Iterable[ToolCall]) -> str:
    """Everything the agent STORED across ``calls``, as one text, chosen keys excluded.

    The harness's own reading of argv (the write-side twin of ``observed_requested_ids``), so an
    endogenous write can be graded on whether the required literal is recoverable from the stored
    CONTENT under whatever key the agent chose, rather than on whether it guessed the harness's
    id. Words are joined with newlines so two separate writes never fuse into a token run neither
    of them stated."""
    return "\n".join(
        word for invocation in memory_invocations(calls) for word in invocation.stored_content
    )


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


@dataclass(frozen=True)
class NativeMemoryAccess:
    """One observed access to Claude Code's NATIVE memory files: the tool, the path, the direction.

    Deliberately NOT a ``MemoryInvocation``: that type's verb comes from the bd verb tables and its
    operands are argv words, and forcing a file path through it would put a path where every
    consumer expects a key. The two are summed by the caller, never merged here."""

    tool: str
    path: str
    is_write: bool
    # Which tool call (index into the leg's call list) this access came from. One Bash command
    # can touch several paths; the CALL count is over distinct indices, matching the
    # block-not-verb rule ``endogenous_memory_tool_calls`` counts bd under.
    call_index: int
    # The shell tokenizer refused the command (an unterminated quote) and the path scan ran on
    # a whitespace split instead. The access stands — the path is in the command — but a reader
    # knows the direction and segment structure were not vouched for by the shell's own grammar.
    tokenizer_failed: bool = False

    @property
    def is_read(self) -> bool:
        return not self.is_write

    @property
    def verb(self) -> str:
        """The verb name the E1 rows publish, namespaced so a native access can never be read as a
        bd verb in a verb histogram."""
        return "native_write" if self.is_write else "native_read"


def _path_of(call: ToolCall) -> str:
    for key in NATIVE_MEMORY_PATH_ARGS:
        value = call.arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _is_native_memory_path(path: str, *, config_dir: Path) -> bool:
    """Containment in the PINNED config dir AND a ``memory`` path segment.

    Both, not either: containment alone would score a settings read as a memory call, and the
    segment alone would score any path anywhere that happens to contain the word."""
    if not path:
        return False
    candidate = Path(path)
    if not candidate.is_absolute():
        return False
    try:
        relative = candidate.resolve().relative_to(Path(config_dir).resolve())
    except ValueError:
        return False
    return NATIVE_MEMORY_SEGMENT in relative.parts


def _bash_tokens(command: str) -> tuple[list[str], bool]:
    """The shell's own tokenisation, with operators as their own tokens, and whether it FAILED.

    An unterminated quote is a command the shell would refuse too, but the path is still in the
    text and a model writes such a command by accident: the scan falls back to a whitespace
    split rather than attributing nothing, and the failure is flagged on every access found."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer), False
    except ValueError:
        return command.split(), True


def _bash_segments(tokens: Sequence[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in NATIVE_MEMORY_BASH_SEGMENT_BREAKS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _is_write_redirect(token: str) -> bool:
    return token in NATIVE_MEMORY_BASH_WRITE_REDIRECTS


@dataclass(frozen=True)
class _BashPathUse:
    """One token a shell segment may name a path in, whether the segment writes it, and whether
    a RELATIVE spelling may be anchored on the segment's cwd (an operand or a redirect target
    may; an option word never names a relative file)."""

    token: str
    is_write: bool
    anchorable: bool = True


def _split_redirects(segment: Sequence[str]) -> tuple[list[str], list[_BashPathUse]]:
    """The segment's WORDS (command + operands) apart from its redirect targets."""
    words: list[str] = []
    uses: list[_BashPathUse] = []
    i = 0
    while i < len(segment):
        token = segment[i]
        if _is_write_redirect(token) or token in NATIVE_MEMORY_BASH_READ_REDIRECTS:
            if i + 1 < len(segment):
                uses.append(_BashPathUse(segment[i + 1], is_write=_is_write_redirect(token)))
            i += 2
            continue
        words.append(token)
        i += 1
    return _unwrapped(words), uses


def _is_assignment(word: str) -> bool:
    return "=" in word and not word.startswith("-")


def _unwrapped(words: Sequence[str]) -> list[str]:
    """``words`` with leading ``VAR=value`` assignments and wrapper words (each with its own
    option words) removed, so the first word left is the command whose semantics decide the
    direction."""
    rest = list(words)
    while rest and _is_assignment(rest[0]):
        rest.pop(0)
    while rest and PurePosixPath(rest[0]).name in NATIVE_MEMORY_BASH_WRAPPERS:
        wrapper = PurePosixPath(rest.pop(0)).name
        while rest and (rest[0].startswith("-") or (wrapper == "env" and _is_assignment(rest[0]))):
            rest.pop(0)
    return rest


def _command_path_uses(words: Sequence[str]) -> list[_BashPathUse]:
    """Every word after the command word, with the direction the command word gives it. The
    words are all candidates — the access is decided by the path, not by the name — and only
    the direction is the command's business."""
    if len(words) < 2:
        return []
    name = PurePosixPath(words[0]).name
    rest = words[1:]
    flags = [w for w in rest if w.startswith("-")]
    operands = [w for w in rest if not w.startswith("-")]
    in_place = name == "sed" and any(
        flag == f or flag.startswith(f + ("" if f == "-i" else "="))
        for flag in flags
        for f in NATIVE_MEMORY_BASH_SED_IN_PLACE_FLAGS
    )
    written: set[str] = set()
    if name == "tee" or in_place:
        written = set(rest)
    elif name in NATIVE_MEMORY_BASH_WRITE_COMMANDS and len(operands) >= 2:
        written = {operands[-1]}
    return [
        _BashPathUse(word, is_write=word in written, anchorable=not word.startswith("-"))
        for word in rest
    ]


_GRAMMAR_CONFIG_DIR_SPELLINGS = re.compile(
    r"\$\{" + CONFIG_DIR_ENV + r"(?::[?\-=+][^}]*)?\}|\$" + CONFIG_DIR_ENV + r"(?![A-Za-z0-9_])"
)


def _expand_pinned(text: str, *, config_dir: Path) -> str:
    """Substitute the ONE variable the harness itself pinned, in every spelling the shell gives
    it (`$VAR`, `${VAR}`, `${VAR:?msg}`, `${VAR:-default}`). No other expansion: `~` and `$HOME`
    name the operator's tree."""
    return _GRAMMAR_CONFIG_DIR_SPELLINGS.sub(lambda _: str(config_dir), text)


def _path_shaped(token: str) -> bool:
    """A relative token is anchored on a ``cd`` only when it carries path syntax (a separator
    or a dot): after ``cd <memory dir>`` every operand of every command would otherwise resolve
    under the memory dir, and ``echo x`` is not a read of ``memory/x``."""
    return "/" in token or "." in token


def _whole_token_path(use: _BashPathUse, *, config_dir: Path, cwd: str) -> str:
    """The path the whole token names, or "" when the token is not a path on its own."""
    expanded = _expand_pinned(use.token, config_dir=config_dir)
    if PurePosixPath(expanded).is_absolute():
        return expanded
    if cwd and use.anchorable and _path_shaped(expanded):
        return str(PurePosixPath(cwd) / expanded)
    return ""


def _embedded_runs(text: str, *, prefix: str) -> list[str]:
    """Every run in ``text`` that starts at ``prefix`` and ends at a path terminator, once."""
    runs: dict[str, None] = {}
    position = text.find(prefix)
    while position >= 0:
        run_end = position
        while run_end < len(text) and text[run_end] not in NATIVE_MEMORY_BASH_PATH_TERMINATORS:
            run_end += 1
        runs[text[position:run_end]] = None
        position = text.find(prefix, run_end)
    return list(runs)


def _pinned_paths(
    use: _BashPathUse, *, config_dir: Path, cwd: str, tokenizer_failed: bool
) -> list[str]:
    """Every path ``use.token`` names under the pinned config dir.

    When the tokenizer vouched for the token and the whole token (anchored on ``cwd`` when
    relative) is such a path, that is the one path it names. Otherwise every run inside the
    token that starts at the config dir's own spelling and ends at a terminator is a candidate
    (``if=<path>``, ``open('<path>')``, and every token of a whitespace-split fallback, whose
    tokens still carry the quote and separator characters shlex would have consumed)."""
    if not tokenizer_failed:
        whole = _whole_token_path(use, config_dir=config_dir, cwd=cwd)
        if whole and _is_native_memory_path(whole, config_dir=config_dir):
            return [whole]
    expanded = _expand_pinned(use.token, config_dir=config_dir)
    return [
        run
        for run in _embedded_runs(expanded, prefix=str(config_dir))
        if _is_native_memory_path(run, config_dir=config_dir)
    ]


def _bash_accesses(command: str, *, config_dir: Path, call_index: int) -> list[NativeMemoryAccess]:
    tokens, tokenizer_failed = _bash_tokens(command)
    found: list[NativeMemoryAccess] = []
    cwd = ""
    for segment in _bash_segments(tokens):
        words, uses = _split_redirects(segment)
        if words and words[0] == "cd":
            target = _expand_pinned(words[1], config_dir=config_dir) if len(words) > 1 else ""
            if target and cwd and not PurePosixPath(target).is_absolute():
                target = str(PurePosixPath(cwd) / target)
            cwd = target
            continue
        for use in [*_command_path_uses(words), *uses]:
            for path in _pinned_paths(
                use, config_dir=config_dir, cwd=cwd, tokenizer_failed=tokenizer_failed
            ):
                found.append(
                    NativeMemoryAccess(
                        tool=NATIVE_MEMORY_BASH_TOOL,
                        path=path,
                        is_write=use.is_write,
                        call_index=call_index,
                        tokenizer_failed=tokenizer_failed,
                    )
                )
    return found


def native_memory_accesses(
    calls: Iterable[ToolCall], *, config_dir: Path | None
) -> list[NativeMemoryAccess]:
    """Every native-memory access across ``calls``, in stream order — through the file tools
    (``Read``/``Write``/``Edit``) and through a shell command that names the pinned path.

    ``config_dir=None`` means the surface pins no config dir, so there is no path the harness owns
    and nothing can be attributed — it returns nothing rather than guessing, because a recognizer
    that fell back to matching ``~/.claude`` would count a read of the OPERATOR's memory as the
    agent's own."""
    if config_dir is None:
        return []
    found: list[NativeMemoryAccess] = []
    for index, call in enumerate(calls):
        if call.name == NATIVE_MEMORY_BASH_TOOL:
            found.extend(_bash_accesses(_command_of(call), config_dir=config_dir, call_index=index))
            continue
        if call.name not in NATIVE_MEMORY_TOOL_NAMES:
            continue
        path = _path_of(call)
        if not _is_native_memory_path(path, config_dir=config_dir):
            continue
        found.append(
            NativeMemoryAccess(
                tool=call.name,
                path=path,
                is_write=call.name in NATIVE_MEMORY_WRITE_TOOLS,
                call_index=index,
            )
        )
    return found


def native_memory_calls(calls: Iterable[ToolCall], *, config_dir: Path | None) -> int:
    """How many tool calls reached the native memory surface. Calls, not paths: one Edit is one
    call and one Bash command touching two files is one call, matching
    ``endogenous_memory_tool_calls``'s block-not-verb rule so the two are summable.
    """
    return len({a.call_index for a in native_memory_accesses(calls, config_dir=config_dir)})


def memory_reaching_calls(calls: Sequence[ToolCall], *, config_dir: Path | None) -> int:
    """Tool calls that reached EITHER affordance — the bd shim or the native memory files — each
    counted ONCE. A Bash command that runs `bd recall` and cats MEMORY.md is one reach, not two:
    the ladder's endpoint is whether the agent reached for memory, and one tool call is one
    decision to."""
    native = {a.call_index for a in native_memory_accesses(calls, config_dir=config_dir)}
    bd = {index for index, call in enumerate(calls) if _is_memory_call(call)}
    return len(native | bd)
