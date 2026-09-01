"""Mechanical CLI-grammar helpers for the E0a study (bead mem-e4fby).

Provenance. Every function here is lifted from the SEALED selector-expressibility
package (``results/bdp/selector-expressibility/``), whose ``manifest.json`` pins a
sha256 over each of its files. Those files are therefore never edited; the code is
copied here instead, with the source line ranges recorded per symbol:

===========================  ==========================  ==============
symbol                       source file                 lines (sealed)
===========================  ==========================  ==============
SPLIT, ENV_ASSIGN, WRAPPERS  extract.py                  47-50
simple_commands              extract.py                  53-57
argv_of                      extract.py                  60-77
bd_invocations               extract.py                  80-86  (widened)
blocks                       extract.py                  89-97
REDIRECT, PLACEHOLDER        classify.py                 223-224
strip_shell                  classify.py                 239-255
GLOBAL_VALUE_FLAGS           classify.py                 56-65
normalize                    extract-adjacent classify.py 257-272
===========================  ==========================  ==============

Two deliberate divergences from the sealed source, both recorded in
``preregistration.json``:

* ``bd_invocations`` accepts the beads_ordering rig's patched build, which ships
  under its own basename but is the same CLI grammar.
* ``normalize`` returns positionals and flags separately, because the E0a verb
  table keys on argument GRAMMAR (how many positionals, is there an explicit
  key flag) and the sealed classifier had no need for that split.

ZFC boundary. Everything in this module is argv structure: splitting a shell
line, stripping wrappers and redirections, and counting positionals. Nothing
reads the CONTENT of an argument.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from typing import Any

# extract.py:47-50
SPLIT = re.compile(r"&&|\|\||[;\n]|(?<!\|)\|(?!\|)")
ENV_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
WRAPPERS = {"rtk", "env", "time", "sudo", "nice", "command", "uv", "npx", "bunx"}

# classify.py:223-224
REDIRECT = re.compile(r"^\d*(>>|>|<)&?\d*$")
PLACEHOLDER = re.compile(r"^<.+>$|^\$|\{\{|^\.\.\.$|^%s$")

# classify.py:56-65
GLOBAL_VALUE_FLAGS = {
    "--actor",
    "--database",
    "--db",
    "-C",
    "--directory",
    "--dolt-auto-commit",
    "--mem-profile",
    "--rig",
}

# Flags that consume the following token as their value. Everything else is
# treated as boolean, which is the safe default: assuming a flag consumes a value
# would silently swallow the following POSITIONAL, and the E0a verb table keys on
# how many positionals an invocation carries. `--flag=value` needs no entry.
VALUE_FLAGS = {
    "--key",
    "-k",
    "--limit",
    "-n",
    "--tag",
    "--tags",
    "--type",
    "--scope",
    "--project",
    "--rig",
    "--actor",
    "--db",
    "--database",
    "--format",
    "--since",
    "--until",
    "--sort",
    "--content",
    "--body",
    "--file",
}

# The bd CLI ships locally as `bd`; the beads_ordering rig pins a patched build
# under its own basename. Same grammar, so both count as an invocation.
BD_EXECUTABLES = {"bd", "bd-memory-ordering-5877"}


def simple_commands(command: str) -> Iterator[str]:
    """extract.py:53-57 - split a shell line into simple commands, coarsely."""
    for part in SPLIT.split(command):
        part = part.strip()
        if part:
            yield part


def argv_of(part: str) -> list[str] | None:
    """extract.py:60-77 - shlex-parse one simple command, stripping wrappers."""
    try:
        argv = shlex.split(part)
    except ValueError:
        return None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-") and ENV_ASSIGN.fullmatch(tok):
            i += 1
            continue
        if tok in WRAPPERS:
            i += 1
            continue
        if tok == "run" and i > 0 and argv[i - 1] == "uv":
            i += 1
            continue
        break
    argv = argv[i:]
    return argv or None


def bd_invocations(command: str) -> Iterator[list[str]]:
    """extract.py:80-86, widened to BD_EXECUTABLES."""
    for part in simple_commands(command):
        argv = argv_of(part)
        if not argv:
            continue
        if os.path.basename(argv[0]) in BD_EXECUTABLES:
            yield argv


def strip_shell(argv: list[str]) -> list[str]:
    """classify.py:239-255 - drop redirection operators and their targets."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if REDIRECT.match(tok):
            # a bare operator consumes its target; `2>&1` carries its own
            if tok.endswith(("&1", "&2")) or "&" in tok:
                i += 1
            else:
                i += 2
            continue
        out.append(tok)
        i += 1
    return out


def is_skippable(argv: list[str]) -> str | None:
    """Name the preregistered exclusion this argv trips, or None.

    classify.py:390-395 applied the same two tests inline.
    """
    if any(PLACEHOLDER.search(t) for t in argv):
        return "placeholder_or_template"
    if "--help" in argv or "-h" in argv:
        return "help_invocation"
    return None


def normalize(argv: list[str]) -> tuple[str, list[str], dict[str, str | None]]:
    """Return (subcommand, positionals, flags) with global flags stripped.

    ``flags`` maps each flag name to its value, or to None when the flag is
    boolean. Values are returned so a caller can read a ``--key`` without having to
    re-parse argv; they are digested by the caller, never emitted.

    The subcommand scan is classify.py:257-272 verbatim in behaviour; the split of
    the remainder into positionals and flags is new here (see module docstring).
    A ``--flag value`` pair contributes the flag only: its value is consumed, so a
    value can never be miscounted as a positional and inflate the argument count
    the verb table keys on.
    """
    i = 1
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-"):
            break
        if "=" in tok:
            i += 1
            continue
        if tok in GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        i += 1
    if i >= len(argv):
        return "", [], {}

    sub = argv[i]
    rest = argv[i + 1 :]
    positionals: list[str] = []
    flags: dict[str, str | None] = {}
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok == "--":
            positionals.extend(rest[j + 1 :])
            break
        if tok.startswith("-") and tok != "-":
            name, _, inline = tok.partition("=")
            if _:
                flags[name] = inline
                j += 1
                continue
            if name in VALUE_FLAGS and j + 1 < len(rest):
                flags[name] = rest[j + 1]
                j += 2  # a value-taking flag consumes the next token
                continue
            flags[name] = None
            j += 1
            continue
        positionals.append(tok)
        j += 1
    return sub, positionals, flags


def tool_use_blocks(rec: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """extract.py:89-97 - yield the content blocks of one transcript record."""
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                yield b
