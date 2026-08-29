"""Pass 1: extract bd invocations from agent transcripts.

Emits one JSON object per invocation. Command text is retained ONLY in this
scratchpad intermediate; published artifacts carry shapes and counts.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Iterator
from typing import Any

READ_SUBCOMMANDS = {
    "list",
    "show",
    "ready",
    "query",
    "search",
    "dep",
    "count",
    "stats",
    "blocked",
}
# recorded but reported separately; not part of the primary population
WRITE_SUBCOMMANDS = {
    "create",
    "update",
    "close",
    "reopen",
    "delete",
    "remember",
    "prime",
    "sync",
    "init",
    "import",
    "export",
    "compact",
    "collab",
    "notes",
    "label",
    "assign",
}

# split a shell line into simple commands; deliberately coarse
SPLIT = re.compile(r"&&|\|\||[;\n]|(?<!\|)\|(?!\|)")
ENV_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
WRAPPERS = {"rtk", "env", "time", "sudo", "nice", "command", "uv", "npx", "bunx"}


def simple_commands(command: str) -> Iterator[str]:
    for part in SPLIT.split(command):
        part = part.strip()
        if part:
            yield part


def argv_of(part: str) -> list[str] | None:
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
    for part in simple_commands(command):
        argv = argv_of(part)
        if not argv:
            continue
        exe = os.path.basename(argv[0])
        if exe == "bd":
            yield argv


def blocks(rec: dict[str, Any]) -> Iterator[dict[str, Any]]:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                yield b


def main(paths: list[str], out_path: str) -> None:
    n_files = n_lines = n_bash = n_inv = 0
    seen_tool_names: dict[str, int] = {}
    with open(out_path, "w", encoding="utf-8") as out:
        for path in paths:
            n_files += 1
            try:
                fh = open(path, encoding="utf-8", errors="replace")  # noqa: SIM115
            except OSError:
                continue
            with fh:
                for raw in fh:
                    n_lines += 1
                    if "bd" not in raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(rec, dict):
                        continue
                    session = rec.get("sessionId") or os.path.basename(path)[:-6]
                    ts = rec.get("timestamp")
                    cwd = rec.get("cwd") or ""
                    for b in blocks(rec):
                        if b.get("type") != "tool_use":
                            continue
                        name = str(b.get("name") or "")
                        inp = b.get("input")
                        if not isinstance(inp, dict):
                            continue
                        if name != "Bash":
                            if "bd" in name.lower() or "bead" in name.lower():
                                seen_tool_names[name] = seen_tool_names.get(name, 0) + 1
                            continue
                        command = inp.get("command")
                        if not isinstance(command, str) or "bd" not in command:
                            continue
                        n_bash += 1
                        for argv in bd_invocations(command):
                            sub = argv[1] if len(argv) > 1 else ""
                            kind = (
                                "read"
                                if sub in READ_SUBCOMMANDS
                                else "write" if sub in WRITE_SUBCOMMANDS else "other"
                            )
                            n_inv += 1
                            out.write(
                                json.dumps(
                                    {
                                        "session": session,
                                        "file": path,
                                        "ts": ts,
                                        "cwd": cwd,
                                        "argv": argv,
                                        "sub": sub,
                                        "kind": kind,
                                    }
                                )
                                + "\n"
                            )
            if n_files % 500 == 0:
                print(f"{n_files} files, {n_inv} invocations", file=sys.stderr, flush=True)
    print(
        json.dumps(
            {
                "files": n_files,
                "lines": n_lines,
                "bash_blocks_mentioning_bd": n_bash,
                "invocations": n_inv,
                "bd_named_tools": seen_tool_names,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        paths = [line.strip() for line in f if line.strip()]
    main(paths, sys.argv[2])
