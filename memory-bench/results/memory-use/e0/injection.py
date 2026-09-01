"""E0b: prime-delivery share over the pinned transcript corpus (bead mem-h9pum).

E0.3 as originally specced measured ``bd prime`` FIRES: it counted prime as one
more bucket in the agent's own ``Bash`` traffic. That cannot size the R8 bet, for
three reasons this module answers one by one.

* Prime is fired by a **SessionStart hook**, not by an agent ``Bash`` call, so a
  ``tool_use``-only extraction removes almost all of it. Here the hook payload is
  read directly: the host writes it as a record of ``type == "attachment"`` whose
  ``attachment.type == "hook_success"``, carrying ``hookEvent``, the exact
  ``command``, and the hook's ``stdout``.
* The payload that would SHOW the emitted memories was discarded at the
  ``type != "tool_use"`` skip. This pass keeps it, and for the agent-invoked form
  it pairs each ``bd prime`` ``tool_use`` with its ``tool_result`` by id.
* Whether memories were actually carried depends on store state and on the
  ``prime.max-memories`` / ``max-memory-chars`` caps, none of which appear in
  argv. So delivery is not inferred from argv at all: it is read off the emitted
  text's own structural markers.

What is measured is DELIVERY, not consumption. A carried payload proves the
bodies were placed in the agent's context; it says nothing about whether they
were read or used. Mechanism-FIRES is not mechanism-CONSUMED, and a delivery is
one step further along than a fire, not the whole distance.

Standing labels that travel with every number here:

* Every rate in the E0 series is INSTRUCTED-endogenous, never spontaneous.
* The delivery being measured comes from an **R8-violating** binary. R8 asks that
  prime inject no bodies; the shipped ``bd prime`` emits a memories section
  followed by memory bodies, which is exactly why this share is measurable at
  all, and exactly why it is not a measurement of an R8-conformant surface.

ZFC boundary. Detection is format-anchored parsing of the emitted section's own
structural markers (a heading with a count, or a heading followed by
``- **key**:`` bullets) and of the host's elision banner. Nothing here reads a
memory's body text to judge whether it was useful; that judgment is semantic and
is not made in this layer.

Usage::

    uv run python results/memory-use/e0/injection.py --filelist filelist.txt --json

Add ``--per-session`` for the full per-session delivery map; the session-level
aggregates are published with or without it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cligrammar

HERE = pathlib.Path(__file__).resolve().parent
PREREG_PATH = HERE / "preregistration.json"
AMENDMENT_PATH = HERE / "preregistration-amendment-1.json"

#: The prime payload's own first line, on every build seen in the corpus. It is
#: what identifies a blob as prime output regardless of which surface carried it.
PRIME_BANNER = "[bd prime]"

#: Current build: a heading that carries its own count.
PERSISTENT_HEADER = re.compile(r"^##+\s*Persistent Memories\s*\((\d+)\)\s*$", re.M)
#: Older build: an uncounted heading followed by one bullet per memory.
LEGACY_HEADER = re.compile(r"^##+\s*Memories\s*$", re.M)
LEGACY_BULLET = re.compile(r"^-\s+\*\*[^*]+\*\*:", re.M)
#: Any following heading closes the section.
NEXT_HEADING = re.compile(r"^#{1,6}\s", re.M)
#: The host's elision banner. Its preview can cut before the memories section, so
#: a negative read off a preview alone is undetermined, never a not-carried.
ELISION = re.compile(r"^<persisted-output>", re.M)
PERSISTED_PATH = re.compile(r"[Ss]aved to:\s*(\S+)")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

FORM_PERSISTENT = "persistent_memories_header"
FORM_LEGACY = "legacy_memories_bullets"
FORM_ABSENT = "no_memories_section"
FORM_UNDETERMINED = "undetermined_truncated_payload"


@dataclass(frozen=True)
class Delivery:
    """The verdict for one prime payload.

    ``carried`` is None only when the payload reaching the transcript was
    truncated and the full text could not be recovered. Those are reported apart
    from both the carried and the not-carried counts rather than defaulted into
    either, because defaulting a truncation to "not carried" would understate
    delivery by exactly the payloads too large to inline: the ones most likely to
    be large BECAUSE they carried memories.
    """

    form: str
    carried: bool | None
    memory_count: int | None


@dataclass(frozen=True)
class PrimeEvent:
    session: str
    ts: str
    cwd: str
    origin: str
    resolution: str
    delivery: Delivery


def strip_ansi(text: str) -> str:
    """Remove SGR escapes.

    The compaction surface writes the payload with every line wrapped in a dim
    escape pair, so the section headings do not sit at the start of a line until
    the escapes are gone.
    """
    return ANSI.sub("", text)


def detect(text: str) -> Delivery:
    """Read the delivery verdict off one COMPLETE prime payload."""
    clean = strip_ansi(text)
    match = PERSISTENT_HEADER.search(clean)
    if match is not None:
        count = int(match.group(1))
        return Delivery(FORM_PERSISTENT, count > 0, count)
    legacy = LEGACY_HEADER.search(clean)
    if legacy is not None:
        rest = clean[legacy.end() :]
        end = NEXT_HEADING.search(rest)
        section = rest[: end.start()] if end is not None else rest
        bullets = len(LEGACY_BULLET.findall(section))
        return Delivery(FORM_LEGACY, bullets > 0, bullets)
    return Delivery(FORM_ABSENT, False, 0)


def resolve(full: str | None, inline: str) -> tuple[Delivery, str]:
    """Pick the most complete available text for one payload and read it.

    Order: the hook's own ``stdout`` (complete even when the inline copy is
    elided), then the file the elision banner names, then the inline copy. A
    positive read off a truncated preview still stands - a memories section
    present in the first 2KB was delivered - but a negative one does not.
    """
    if full is not None and PRIME_BANNER in full:
        return detect(full), "hook_stdout"
    clean = strip_ansi(inline)
    if ELISION.search(clean) is not None:
        named = PERSISTED_PATH.search(clean)
        if named is not None:
            try:
                text = pathlib.Path(named.group(1)).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if PRIME_BANNER in text:
                return detect(text), "persisted_file"
        preview = detect(clean)
        if preview.carried:
            return preview, "elision_preview"
        return Delivery(FORM_UNDETERMINED, None, None), "elision_preview_truncated"
    return detect(clean), "inline"


@dataclass
class Tally:
    files: int = 0
    lines: int = 0
    unreadable_files: int = 0
    excluded_after_prereg_lock: int = 0
    attachments_with_banner_but_other_type: int = 0
    agent_prime_calls: int = 0
    agent_prime_calls_unpaired: int = 0
    agent_prime_results_without_a_prime_payload: int = 0
    events: list[PrimeEvent] = field(default_factory=list)
    origins: Counter[str] = field(default_factory=Counter)
    resolutions: Counter[str] = field(default_factory=Counter)
    forms: Counter[str] = field(default_factory=Counter)


HOOK_SUCCESS = "hook_success"


def _attachment_event(rec: dict[str, Any], tally: Tally) -> tuple[str, str, str] | None:
    """Return (origin, inline, full) for a hook-carried prime payload.

    Two independent conditions must both hold, and the module's prose, the report
    and the commit message all state both: the record must be the host's
    ``hook_success`` attachment, and its payload must be a prime document (it
    carries the payload's own first-line banner). An earlier revision stated the
    type check and did not implement it. Every banner-carrying attachment in the
    pinned population is in fact ``hook_success`` (5,839 / 5,839), so enforcing it
    moves no count here; it is enforced so that a future host that carries the
    banner on some other attachment kind cannot silently enter the denominator.
    A banner-carrying attachment of any other type is counted apart, in
    ``attachments_with_banner_but_other_type``, rather than dropped in silence.
    """
    att = rec.get("attachment")
    if not isinstance(att, dict):
        return None
    inline = att.get("content")
    stdout = att.get("stdout")
    inline_text = inline if isinstance(inline, str) else ""
    full_text = stdout if isinstance(stdout, str) else ""
    if PRIME_BANNER not in inline_text and PRIME_BANNER not in full_text:
        return None
    if att.get("type") != HOOK_SUCCESS:
        tally.attachments_with_banner_but_other_type += 1
        return None
    hook = att.get("hookName") or att.get("hookEvent") or "hook"
    return (f"hook:{hook}", inline_text, full_text)


def _string_content(rec: dict[str, Any]) -> str | None:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str) and PRIME_BANNER in content:
        return content
    return None


def _result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("text")]
        return "\n".join(str(p) for p in parts)
    return ""


def is_prime_invocation(command: str) -> bool:
    """True when a shell command line contains a ``bd prime`` invocation."""
    for argv in cligrammar.bd_invocations(command):
        sub, _positionals, _flags = cligrammar.normalize(argv)
        if sub == "prime":
            return True
    return False


def scan_file(path: str, lock: str, tally: Tally) -> None:
    try:
        fh = open(path, encoding="utf-8", errors="replace")  # noqa: SIM115
    except OSError:
        tally.unreadable_files += 1
        return
    pending: dict[str, tuple[str, str, str]] = {}
    with fh:
        for raw in fh:
            tally.lines += 1
            if "prime" not in raw:
                continue
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            session = str(rec.get("sessionId") or pathlib.Path(path).stem)
            ts = str(rec.get("timestamp") or "")
            cwd = str(rec.get("cwd") or "")

            hook = _attachment_event(rec, tally)
            if hook is not None:
                origin, inline, full = hook
                _record(tally, session, ts, cwd, lock, origin, full or None, inline)
                continue

            text = _string_content(rec)
            if text is not None:
                _record(tally, session, ts, cwd, lock, "local_command_stdout", None, text)
                continue

            for block in cligrammar.tool_use_blocks(rec):
                kind = block.get("type")
                if kind == "tool_use" and block.get("name") == "Bash":
                    inp = block.get("input")
                    command = inp.get("command") if isinstance(inp, dict) else None
                    call_id = block.get("id")
                    if (
                        isinstance(command, str)
                        and isinstance(call_id, str)
                        and is_prime_invocation(command)
                    ):
                        pending[call_id] = (session, ts, cwd)
                        tally.agent_prime_calls += 1
                    continue
                if kind == "tool_result":
                    call_id = block.get("tool_use_id")
                    if not isinstance(call_id, str) or call_id not in pending:
                        continue
                    call_session, call_ts, call_cwd = pending.pop(call_id)
                    body = _result_text(block)
                    if PRIME_BANNER not in body:
                        tally.agent_prime_results_without_a_prime_payload += 1
                        continue
                    _record(
                        tally, call_session, call_ts or ts, call_cwd, lock, "agent_bash", None, body
                    )
    tally.agent_prime_calls_unpaired += len(pending)


def _record(
    tally: Tally,
    session: str,
    ts: str,
    cwd: str,
    lock: str,
    origin: str,
    full: str | None,
    inline: str,
) -> None:
    if ts and ts >= lock:
        tally.excluded_after_prereg_lock += 1
        return
    delivery, resolution = resolve(full, inline)
    tally.events.append(PrimeEvent(session, ts, cwd, origin, resolution, delivery))
    tally.origins[origin] += 1
    tally.resolutions[resolution] += 1
    tally.forms[delivery.form] += 1


def per_session(events: list[PrimeEvent]) -> dict[str, dict[str, int]]:
    sessions: dict[str, dict[str, int]] = defaultdict(
        lambda: {"deliveries": 0, "carried": 0, "not_carried": 0, "undetermined": 0}
    )
    for ev in events:
        row = sessions[ev.session]
        row["deliveries"] += 1
        if ev.delivery.carried is None:
            row["undetermined"] += 1
        elif ev.delivery.carried:
            row["carried"] += 1
        else:
            row["not_carried"] += 1
    return dict(sessions)


def summarise(tally: Tally, *, include_per_session: bool = True) -> dict[str, Any]:
    """Aggregate the tally.

    ``include_per_session`` controls only whether the per-session map is
    materialised into the result. The session-level aggregates below are computed
    from it either way, so dropping the map costs no published statistic; it costs
    the ability to read one session's row out of the artifact, which is why the
    driver keeps it behind ``--per-session`` rather than deleting it.
    """
    sessions = per_session(tally.events)
    carried = sum(1 for e in tally.events if e.delivery.carried is True)
    not_carried = sum(1 for e in tally.events if e.delivery.carried is False)
    undetermined = sum(1 for e in tally.events if e.delivery.carried is None)
    determined = carried + not_carried
    counts = [e.delivery.memory_count for e in tally.events if e.delivery.memory_count]
    sessions_with_carry = sum(1 for row in sessions.values() if row["carried"])
    result: dict[str, Any] = {
        "prime_deliveries": len(tally.events),
        "determined": determined,
        "carried": carried,
        "not_carried": not_carried,
        "undetermined_truncated": undetermined,
        "delivery_carry_share": (carried / determined) if determined else 0.0,
        "sessions_with_a_prime_delivery": len(sessions),
        "sessions_with_a_carried_delivery": sessions_with_carry,
        "session_carry_prevalence": (sessions_with_carry / len(sessions)) if sessions else 0.0,
        "memories_delivered_total": sum(counts),
        "memories_delivered_max_in_one_payload": max(counts) if counts else 0,
        "by_origin": dict(tally.origins),
        "by_resolution": dict(tally.resolutions),
        "by_form": dict(tally.forms),
    }
    if include_per_session:
        result["per_session"] = sessions
    return result


def analyse(filelist: pathlib.Path, *, include_per_session: bool = True) -> dict[str, Any]:
    prereg: dict[str, Any] = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    lock = str(prereg["locked_at_utc"])
    paths = [ln.strip() for ln in filelist.read_text(encoding="utf-8").splitlines() if ln.strip()]
    tally = Tally()
    for path in paths:
        tally.files += 1
        scan_file(path, lock, tally)
        if tally.files % 1000 == 0:
            print(f"{tally.files}/{len(paths)} files", file=sys.stderr, flush=True)

    summary = summarise(tally, include_per_session=include_per_session)
    return {
        "bead": "mem-h9pum",
        "study": "E0b",
        "preregistration_sha256": hashlib.sha256(PREREG_PATH.read_bytes()).hexdigest(),
        "preregistration_amendment_1_sha256": hashlib.sha256(
            AMENDMENT_PATH.read_bytes()
        ).hexdigest(),
        "filelist_sha256": hashlib.sha256(filelist.read_bytes()).hexdigest(),
        "population": {
            "files_in_filelist": len(paths),
            "lines_scanned": tally.lines,
            "files_in_filelist_no_longer_readable": tally.unreadable_files,
        },
        "exclusions": {
            "after_preregistration_lock": tally.excluded_after_prereg_lock,
            "attachments_with_banner_but_other_type": (
                tally.attachments_with_banner_but_other_type
            ),
        },
        "agent_typed_prime_reconciliation": {
            "note": (
                "E0a counts agent-TYPED `bd prime` invocations in Bash argv; E0b counts "
                "agent-typed prime DELIVERIES, which additionally require the paired "
                "tool_result to carry a prime payload. The two denominators are not the "
                "same event, and this block is the arithmetic between them."
            ),
            "agent_typed_prime_calls_seen_here": tally.agent_prime_calls,
            "of_those_paired_to_a_prime_payload": tally.origins.get("agent_bash", 0),
            "paired_result_carried_no_prime_payload": (
                tally.agent_prime_results_without_a_prime_payload
            ),
            "call_never_paired_to_any_tool_result": tally.agent_prime_calls_unpaired,
            "e0a_published_agent_typed_prime_invocations": 47,
            "why_e0a_can_be_higher": (
                "A call whose result never reached the transcript, or reached it without "
                "the prime banner (a non-zero exit, an empty store on a build that emits "
                "nothing, or a result dropped by the host), is an invocation for E0a and "
                "not a delivery for E0b. E0a's own count also runs under its exclusion "
                "set (help/placeholder screens), so the residual between "
                "`agent_typed_prime_calls_seen_here` and 47 is population drift plus "
                "those screens, not a disagreement about any single record."
            ),
        },
        "delivery": summary,
        "measured_quantity": (
            "DELIVERY, not consumption. A carried payload proves memory bodies were "
            "placed in the agent's context by the prime surface; it cannot show they "
            "were read or acted on."
        ),
        "r8_label": (
            "This delivery share is produced by an R8-VIOLATING binary. The shipped "
            "bd prime emits a memories section followed by full bodies; R8 asks for no "
            "automatic body loading. The share therefore sizes what the R8 bet would "
            "REMOVE from the agent's context, and is not a measurement of an "
            "R8-conformant prime surface, which does not exist in this corpus."
        ),
        "interpretation_label": (
            "INSTRUCTED-endogenous, not spontaneous. No number in the E0 series is an "
            "untreated baseline."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E0b prime-delivery share (offline).")
    ap.add_argument("--filelist", required=True, help="pinned transcript filelist")
    ap.add_argument("--json", action="store_true", help="print the analysis to stdout")
    ap.add_argument("--out", help="also write the analysis to this path")
    ap.add_argument(
        "--per-session",
        action="store_true",
        help="include the full per-session delivery map (large; ~4.5k rows on the "
        "pinned population). Session-level aggregates are published either way.",
    )
    args = ap.parse_args(argv)

    filelist = pathlib.Path(args.filelist)
    if not filelist.is_absolute():
        candidate = HERE / filelist
        if candidate.exists():
            filelist = candidate
    result = analyse(filelist, include_per_session=args.per_session)
    text = json.dumps(result, indent=2, sort_keys=False)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
