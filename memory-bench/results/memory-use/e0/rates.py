"""E0a: memory-verb base rate over the pinned transcript corpus (bead mem-e4fby).

Offline, mechanical, zero model calls. Streams the transcript files named in a
pinned filelist, reduces every bd invocation to its CLI grammar (``verbs.py``),
and emits the preregistered statistics.

Usage::

    uv run python results/memory-use/e0/rates.py --filelist filelist.txt --json
    uv run python results/memory-use/e0/rates.py --filelist filelist.txt --out analysis.json

Every rate emitted here is INSTRUCTED-endogenous, not spontaneous: the org's
standing agent instructions teach the capture verb, and so does the shipped
``bd prime``. The emitted JSON repeats that label so a number cannot travel
without it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cligrammar
import verbs

HERE = pathlib.Path(__file__).resolve().parent
PREREG_PATH = HERE / "preregistration.json"
#: The locked preregistration is never edited. Corrections are appended here, and
#: both digests ship with every number so a published rate names its exact rule set.
AMENDMENT_PATH = HERE / "preregistration-amendment-1.json"


@dataclass(frozen=True)
class Event:
    """One bucketed invocation, retained only for the read-after-write join."""

    ts: str
    session: str
    cwd: str
    bucket: str
    key_digest: str


@dataclass
class Tally:
    """Everything the statistics are computed from."""

    files: int = 0
    lines: int = 0
    bash_blocks: int = 0
    invocations: int = 0
    excluded_after_prereg_lock: int = 0
    #: Files named in the pinned filelist that no longer open. A file loss, not an
    #: invocation-level exclusion, so it is reported apart from both other groups.
    unreadable_files: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    buckets: Counter[str] = field(default_factory=Counter)
    per_session: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    events: list[Event] = field(default_factory=list)


def digest(text: str) -> str:
    """Key identity for the join. The literal is never emitted."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def load_lock() -> str:
    prereg: dict[str, Any] = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    lock = prereg["locked_at_utc"]
    assert isinstance(lock, str)
    return lock


def scan_file(path: str, lock: str, tally: Tally) -> None:
    try:
        fh = open(path, encoding="utf-8", errors="replace")  # noqa: SIM115
    except OSError:
        tally.unreadable_files += 1
        return
    with fh:
        for raw in fh:
            tally.lines += 1
            if "bd" not in raw:
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
            for block in cligrammar.tool_use_blocks(rec):
                if block.get("type") != "tool_use" or block.get("name") != "Bash":
                    continue
                inp = block.get("input")
                if not isinstance(inp, dict):
                    continue
                command = inp.get("command")
                if not isinstance(command, str) or "bd" not in command:
                    continue
                tally.bash_blocks += 1
                for argv in cligrammar.bd_invocations(command):
                    record_invocation(argv, ts, session, cwd, lock, tally)


def record_invocation(
    argv: list[str], ts: str, session: str, cwd: str, lock: str, tally: Tally
) -> None:
    # Self-exclusion runs FIRST. Anything at or after the preregistration lock is
    # this study's own traffic (or later) and is not in the pinned population, so it
    # must not be able to move any other exclusion count. Screening for help and
    # placeholder invocations before the lock made those counts drift between runs
    # as new sessions landed on disk (964 -> 966 on re-run); ordering the lock first
    # freezes every published exclusion count against corpus growth.
    if ts and ts >= lock:
        tally.excluded_after_prereg_lock += 1
        return

    reason = cligrammar.is_skippable(argv)
    if reason is not None:
        tally.skipped[reason] += 1
        return

    result = verbs.classify(argv)
    tally.invocations += 1
    tally.buckets[result.bucket] += 1
    counts = tally.per_session[session]
    counts["total"] += 1
    counts[result.bucket] += 1
    if result.bucket == verbs.MEMORY_WRITE:
        counts["write_unambiguous" if result.unambiguous else "write_ambiguous"] += 1
    if result.browse_from_bare_targeted:
        counts["browse_from_bare_targeted"] += 1
        tally.buckets["browse_from_bare_targeted"] += 1

    joinable = result.bucket in (verbs.TARGETED_READ, verbs.MEMORY_WRITE)
    if joinable and result.key is not None:
        if ts:
            tally.events.append(Event(ts, session, cwd, result.bucket, digest(result.key)))
        else:
            tally.skipped["keyed_event_without_timestamp"] += 1


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def session_rate(tally: Tally, numerator_keys: tuple[str, ...]) -> dict[str, float]:
    """Session-averaged share, plus the prevalence that share can hide.

    A session-averaged rate can be near zero while a large minority of sessions use
    the verb at all, so prevalence ships beside it, never instead of it.
    """
    shares: list[float] = []
    present = 0
    for counts in tally.per_session.values():
        total = counts["total"]
        if not total:
            continue
        num = sum(counts[k] for k in numerator_keys)
        shares.append(num / total)
        if num:
            present += 1
    sessions = len(shares)
    return {
        "session_averaged_share": mean(shares),
        "sessions_with_at_least_one": present,
        "session_prevalence": (present / sessions) if sessions else 0.0,
    }


def write_band(tally: Tally) -> dict[str, Any]:
    """E0.1 as a band, never a point estimate (see preregistration ambiguity_band)."""
    low = session_rate(tally, ("write_unambiguous",))
    high = session_rate(tally, ("write_unambiguous", "write_ambiguous"))
    return {
        "unambiguous": low,
        "ambiguity_band_high": high,
        "counts": {
            "unambiguous": sum(c["write_unambiguous"] for c in tally.per_session.values()),
            "ambiguous": sum(c["write_ambiguous"] for c in tally.per_session.values()),
        },
        "note": (
            "The band's low end counts only writes that NAME the memory they store, "
            "which on the shipped CLI means an explicit key flag and nothing else: the "
            "positional argument is the content and the key is auto-generated from it. "
            "The high end adds every unkeyed write. Both ends are write counts; the band "
            "is over key resolvability, and only the low end can enter the join."
        ),
    }


def join(tally: Tally) -> dict[str, Any]:
    """E0.4 read-after-write, published twice (cross-session and cross-directory)."""
    events = sorted(tally.events, key=lambda e: (e.ts, e.session))
    writers: dict[str, list[Event]] = defaultdict(list)
    reads = 0
    hits_any = 0
    hits_cross_session = 0
    hits_cross_cwd = 0
    for ev in events:
        if ev.bucket == verbs.MEMORY_WRITE:
            writers[ev.key_digest].append(ev)
            continue
        reads += 1
        prior = writers.get(ev.key_digest)
        if not prior:
            continue
        hits_any += 1
        if any(w.session != ev.session for w in prior):
            hits_cross_session += 1
        if any(w.cwd and ev.cwd and w.cwd != ev.cwd for w in prior):
            hits_cross_cwd += 1
    return {
        "keyed_targeted_reads": reads,
        "raw_any": (hits_any / reads) if reads else 0.0,
        "raw_cross_session": (hits_cross_session / reads) if reads else 0.0,
        "raw_cross_working_directory": (hits_cross_cwd / reads) if reads else 0.0,
        "hits": {
            "any": hits_any,
            "cross_session": hits_cross_session,
            "cross_working_directory": hits_cross_cwd,
        },
        "interpretation": (
            "RAW is a CLI-EXPRESSIBILITY measurement, not a reuse measurement. It bounds "
            "how much keyed read traffic COULD refer to something captured earlier in the "
            "corpus. It cannot show that the retrieved body was read or acted on: "
            "mechanism-FIRES is not mechanism-CONSUMED. Publishing it twice lets "
            "same-directory continuations be read off rather than assumed away."
        ),
        "denominator_note": (
            "Only the targeted read bucket enters this denominator. The search and "
            "list-all buckets carry no key and can never join, so folding them in would "
            "deflate RAW by construction."
        ),
    }


def analyse(filelist: pathlib.Path) -> dict[str, Any]:
    lock = load_lock()
    paths = [ln.strip() for ln in filelist.read_text(encoding="utf-8").splitlines() if ln.strip()]
    tally = Tally()
    for path in paths:
        tally.files += 1
        scan_file(path, lock, tally)
        if tally.files % 1000 == 0:
            print(f"{tally.files}/{len(paths)} files", file=sys.stderr, flush=True)

    sessions = sum(1 for c in tally.per_session.values() if c["total"])
    return {
        "bead": "mem-e4fby",
        "study": "E0a",
        "preregistration_sha256": hashlib.sha256(PREREG_PATH.read_bytes()).hexdigest(),
        "preregistration_amendment_1_sha256": hashlib.sha256(
            AMENDMENT_PATH.read_bytes()
        ).hexdigest(),
        "filelist_sha256": hashlib.sha256(filelist.read_bytes()).hexdigest(),
        "population": {
            "files_in_filelist": len(paths),
            "lines_scanned": tally.lines,
            "bash_blocks_mentioning_bd": tally.bash_blocks,
            "counted_invocations": tally.invocations,
            "sessions_with_bd_traffic": sessions,
            "files_in_filelist_no_longer_readable": tally.unreadable_files,
        },
        "exclusions": {
            "drifting": {
                "after_preregistration_lock": tally.excluded_after_prereg_lock,
                "note": (
                    "Grows as the corpus grows. It is the only exclusion that can, "
                    "because it is screened before every other one."
                ),
            },
            "frozen_at_the_preregistration_lock": dict(tally.skipped),
        },
        "bucket_counts": dict(tally.buckets),
        "E0.1_memory_write_rate": write_band(tally),
        "E0.2_memory_read_rates": {
            "targeted_read": session_rate(tally, (verbs.TARGETED_READ,)),
            "search_read": session_rate(tally, (verbs.SEARCH_READ,)),
            "browse_read": session_rate(tally, (verbs.BROWSE_READ,)),
            "note": (
                "Never summed into one read rate. The list-all bucket carries no key and "
                "is join-ineligible by construction, so pooling it inflates apparent "
                "retrieval."
            ),
        },
        "E0.4_read_after_write": join(tally),
        "E0.5_memory_verb_share_of_bd_traffic": session_rate(tally, verbs.MEMORY_BUCKETS),
        "reference_buckets": {
            "injection": session_rate(tally, (verbs.INJECTION,)),
            "dep_write": session_rate(tally, (verbs.DEP_WRITE,)),
            "note": (
                "Neither is a memory verb and neither enters E0.5. The dependency bucket "
                "is here because the shorthand form has been miscounted as a memory verb."
            ),
        },
        "interpretation_label": (
            "INSTRUCTED-endogenous, not spontaneous. The org's standing agent "
            "instructions direct every agent to capture, and the shipped prime surface "
            "teaches the verb in its own emitted text. No rate here is an untreated "
            "baseline."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E0a memory-verb base rate (offline).")
    ap.add_argument("--filelist", required=True, help="pinned transcript filelist")
    ap.add_argument("--json", action="store_true", help="print the analysis to stdout")
    ap.add_argument("--out", help="also write the analysis to this path")
    args = ap.parse_args(argv)

    filelist = pathlib.Path(args.filelist)
    if not filelist.is_absolute():
        candidate = HERE / filelist
        if candidate.exists():
            filelist = candidate
    result = analyse(filelist)
    text = json.dumps(result, indent=2, sort_keys=False)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
