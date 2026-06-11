"""Session<->bead content-scan join mechanism (mem-75t.9 PHASE 1, source a).

The store links exactly ONE session per bead (final assignee wins), so
multi-iteration work is invisible. Sessions touch their bead through `bd` tool
calls (claim/update/close/show) whose command strings carry work_ids — this
module extracts those mentions mechanically from a transcript's `tool_use`
blocks and emits per-(session, work_id) link rows with timestamps.

ZFC boundary: everything here is id-GRAMMAR extraction and structural parsing —
no semantic judgment. The legal work-id prefix set is DERIVED from the store's
distinct work_ids (read-only), never hardcoded. Mention strength is a mechanical
subcommand class: claim/update/close/comment mutate the bead (strong linkage);
show/list/anything-else merely reads it (weak).

Known mechanical limits (documented, not patched over):
- STANDALONE gc session ids (`gc-351177`) share the `gc-` grammar with gc work
  ids and can surface as mentions; callers filter rows against the store's
  actual work_ids (`in_store`) before joining. Tokens embedded in a larger
  hyphen run (`mem-worker-gc-351177`) are rejected by the grammar itself, and
  `--assignee` values are additionally skipped via flag parsing.

The dolt-history helpers (source b) parse assignee transitions read from the
ALREADY-RUNNING shared city dolt server — this module never connects anywhere;
it only transforms rows the caller fetched.
"""

import json
import re
import shlex
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Strength = Literal["strong", "weak"]

# Subcommands that MUTATE the bead — the session demonstrably worked it.
# Everything else (show/list/ready/dep/...) is a read: weak linkage.
STRONG_SUBCOMMANDS = frozenset({"claim", "update", "close", "comment", "reopen"})

# bd flags that consume the NEXT token as their value. `--assignee`/`--actor`
# values embed agent/session ids that must not read as work-id mentions; the
# rest would otherwise be parsed as the subcommand (`bd -C <dir> update ...`).
_FLAGS_WITH_VALUE = frozenset(
    {"-C", "--directory", "--db", "--actor", "--assignee", "--dolt-auto-commit"}
)

# Shell segment boundaries: compound commands, pipes, subshells, backticks.
_SEGMENT_SPLIT = re.compile(r"[;\n|`]|&&|\|\||\$\(")
_BD_INVOCATION = re.compile(r"(?:^|\s)bd\s+(\S.*)$")

# Cheap per-line probes used by the streaming scan (full JSON parse only on
# candidate lines — the corpus is ~19k transcripts / multiple GB).
_TS_RE = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')
_SID_RE = re.compile(r'"sessionId"\s*:\s*"([^"]+)"')


def derive_prefixes(work_ids: Iterable[str]) -> frozenset[str]:
    """The store-derived legal prefix set: each work_id minus its final
    hyphen-delimited token (`mem-75t.9` -> `mem`, `gascity-dashboard-4lf62` ->
    `gascity-dashboard`). Hyphenless ids carry no rig prefix and are skipped."""
    return frozenset(wid.rsplit("-", 1)[0] for wid in work_ids if "-" in wid)


def load_store_work_ids(store_path: str | Path) -> frozenset[str]:
    """All work_ids from the store, READ-ONLY (uri mode=ro — never mutates)."""
    con = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT work_id FROM work_records").fetchall()
    finally:
        con.close()
    return frozenset(r[0] for r in rows)


def work_id_pattern(prefixes: Iterable[str]) -> re.Pattern[str]:
    """Compile the work-id grammar for the given prefix set.

    A match is `<prefix>-<token>(.<token>)*` where the id is not embedded in a
    larger hyphen/dot/word run on either side: `mem-worker-gc-1` must not yield
    `mem-worker`, and `system-mem-1` must not yield `mem-1`. Longest prefix wins
    via length-sorted alternation. An empty prefix set matches nothing."""
    alts = sorted(prefixes, key=len, reverse=True)
    if not alts:
        return re.compile(r"(?!x)x")  # matches nothing, by construction
    alt = "|".join(re.escape(p) for p in alts)
    return re.compile(
        rf"(?<![\w.-])(?:{alt})-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*"
        rf"(?!\w)(?!-[A-Za-z0-9])(?!\.[A-Za-z0-9])"
    )


@dataclass(frozen=True)
class BdMention:
    """One work_id mention inside one bd invocation."""

    subcommand: str
    work_id: str

    @property
    def strength(self) -> Strength:
        return "strong" if self.subcommand in STRONG_SUBCOMMANDS else "weak"


def _segment_mentions(segment: str, pattern: re.Pattern[str]) -> Iterable[BdMention]:
    invocation = _BD_INVOCATION.search(segment)
    if invocation is None:
        return
    rest = invocation.group(1)
    try:
        tokens = shlex.split(rest)
    except ValueError:
        # Unbalanced quoting (segment splitting cuts through quoted shell text)
        # degrades to whitespace tokens — the id grammar still applies per token.
        tokens = rest.split()

    subcommand: str | None = None
    skip_value = False
    for token in tokens:
        if skip_value:
            skip_value = False
            continue
        if token.startswith("-"):
            if token.split("=", 1)[0] in _FLAGS_WITH_VALUE and "=" not in token:
                skip_value = True
            continue
        if subcommand is None:
            subcommand = token.lower()
            continue
        for work_id in pattern.findall(token):
            yield BdMention(subcommand=subcommand, work_id=work_id)


def extract_bd_mentions(command: str, pattern: re.Pattern[str]) -> tuple[BdMention, ...]:
    """All (subcommand, work_id) mentions in one shell command string.

    The command is split on shell segment boundaries so each bd invocation is
    classified by ITS OWN subcommand even inside compound commands."""
    return tuple(
        mention
        for segment in _SEGMENT_SPLIT.split(command)
        for mention in _segment_mentions(segment, pattern)
    )


@dataclass(frozen=True)
class WorkIdLink:
    """One (session, work_id) link: strength plus mention/timestamp evidence."""

    work_id: str
    strength: Strength
    t_first: str | None
    t_last: str | None
    n_strong: int
    n_weak: int


@dataclass(frozen=True)
class SessionScan:
    """One transcript's scan result: identity, time bounds, and bead links."""

    session_id: str | None
    session_start: str | None
    session_end: str | None
    links: tuple[WorkIdLink, ...]


@dataclass
class _LinkAcc:
    n_strong: int = 0
    n_weak: int = 0
    timestamps: list[str] = field(default_factory=list)


def _command_strings(event: Mapping[str, Any]) -> Iterable[str]:
    """The `command` inputs of every tool_use block in one transcript event."""
    message = event.get("message")
    if not isinstance(message, Mapping):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, Mapping) or block.get("type") != "tool_use":
            continue
        block_input = block.get("input")
        command = block_input.get("command") if isinstance(block_input, Mapping) else None
        if isinstance(command, str) and command:
            yield command


def scan_transcript_lines(lines: Iterable[str], pattern: re.Pattern[str]) -> SessionScan:
    """Stream one transcript (jsonl lines) and extract its bead links.

    Cheap regex probes establish session id and time bounds on every line; full
    JSON decoding happens only on lines that can carry a bd tool_use. Malformed
    lines are skipped (transcripts are external data, truncation happens)."""
    session_id: str | None = None
    start: str | None = None
    end: str | None = None
    acc: dict[str, _LinkAcc] = {}

    for line in lines:
        if not line or line.isspace():
            continue
        ts_match = _TS_RE.search(line)
        if ts_match:
            ts = ts_match.group(1)
            if start is None or ts < start:
                start = ts
            if end is None or ts > end:
                end = ts
        if session_id is None:
            sid_match = _SID_RE.search(line)
            if sid_match:
                session_id = sid_match.group(1)
        if '"tool_use"' not in line or "bd" not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        event_ts = event.get("timestamp")
        for command in _command_strings(event):
            for mention in extract_bd_mentions(command, pattern):
                link = acc.setdefault(mention.work_id, _LinkAcc())
                if mention.strength == "strong":
                    link.n_strong += 1
                else:
                    link.n_weak += 1
                if isinstance(event_ts, str):
                    link.timestamps.append(event_ts)

    links = tuple(
        WorkIdLink(
            work_id=work_id,
            strength="strong" if link.n_strong > 0 else "weak",
            t_first=min(link.timestamps) if link.timestamps else None,
            t_last=max(link.timestamps) if link.timestamps else None,
            n_strong=link.n_strong,
            n_weak=link.n_weak,
        )
        for work_id, link in sorted(acc.items())
    )
    return SessionScan(session_id=session_id, session_start=start, session_end=end, links=links)


# --- dolt-history assignee parsing (source b) ---------------------------------

# A session id embedded in an assignee, e.g. `gc-335825`, with an optional role
# prefix (`polecat-gc-335825`, `mem-worker-gc-340057`). Port of the TS
# ASSIGNEE_RE in src/ingest/beads.ts — the two must classify identically.
_ASSIGNEE_RE = re.compile(r"^(?:(.+)-)?([a-z][a-z0-9]*-\d+)$")
_SESSION_AGENT_RE = re.compile(r"^[a-z][a-z0-9]*-\d+$")


def parse_assignee(raw: str) -> tuple[str | None, str] | None:
    """Decompose a bead assignee into (role, agent_id); None for blank input.

    When the assignee embeds a session id the session becomes agent_id and the
    prefix the role; otherwise the whole assignee is the agent_id (`sjarmak`)."""
    assignee = raw.strip()
    if not assignee:
        return None
    match = _ASSIGNEE_RE.match(assignee)
    if match:
        return (match.group(1), match.group(2))
    return (None, assignee)


def assignee_sessions(rows: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    """Per-bead ordered distinct SESSION agents from dolt-history assignee rows.

    `rows` carry `id`, `assignee`, `first_seen` (the server-side aggregation of
    `dolt_history_issues`). Non-session assignees (human actors like `sjarmak`)
    are excluded — they carry no transcript. Order is by first_seen."""
    per_bead: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        assignee = row.get("assignee")
        if not isinstance(assignee, str):
            continue
        parsed = parse_assignee(assignee)
        if parsed is None:
            continue
        _, agent_id = parsed
        if not _SESSION_AGENT_RE.match(agent_id):
            continue
        per_bead.setdefault(str(row["id"]), []).append((str(row.get("first_seen", "")), agent_id))

    result: dict[str, tuple[str, ...]] = {}
    for work_id, entries in per_bead.items():
        seen: dict[str, None] = {}
        for _, agent_id in sorted(entries):
            seen.setdefault(agent_id)
        result[work_id] = tuple(seen)
    return result
