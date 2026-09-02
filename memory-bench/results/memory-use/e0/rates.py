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
AMENDMENT_2_PATH = HERE / "preregistration-amendment-2.json"
AMENDMENT_3_PATH = HERE / "preregistration-amendment-3.json"


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
    #: Invocations that were COUNTED and bucketed but could not enter the join.
    #: Not an exclusion: nothing was screened out of the population, a single
    #: statistic's eligibility was lost. Filing it under exclusions (as the first
    #: two runs did) overstates what the screens removed.
    join_eligibility_drops: Counter[str] = field(default_factory=Counter)
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
                for argv, raw_argv in cligrammar.bd_invocations(command):
                    record_invocation(argv, ts, session, cwd, lock, tally, raw_argv)


def record_invocation(
    argv: list[str],
    ts: str,
    session: str,
    cwd: str,
    lock: str,
    tally: Tally,
    raw_argv: list[str] | None = None,
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

    # The screen runs on BOTH forms, but not with the same rule. On the analysed
    # argv it is the full preregistered alternation. On the pre-strip tokenization
    # it is narrowed to `<...>`, the one shape `strip_redirections` erases and
    # therefore the only one the stripped form can have lost (A1.5, as corrected by
    # A1.7). Running the full alternation on the raw form judged tokens the
    # analysed form does not contain: a redirection TARGET survives there, and
    # `^\$` fired on `$SP/allbeads.json`, discarding 20 invocations - including two
    # executed keyed reads - for carrying a `$` the strip had already removed.
    reason = cligrammar.is_skippable(argv)
    if reason is None and raw_argv is not None:
        reason = cligrammar.is_skippable_raw(raw_argv)
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

    joinable = result.bucket in (
        verbs.TARGETED_READ,
        verbs.MEMORY_WRITE,
        verbs.ATTEMPTED_READ_VIA_WRITE_VERB,
    )
    if joinable and result.key is not None:
        if ts:
            tally.events.append(Event(ts, session, cwd, result.bucket, digest(result.key)))
        else:
            tally.join_eligibility_drops["keyed_event_without_timestamp"] += 1


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
    """E0.1 over TWO nested bands, never a point estimate.

    The inner band is the preregistered one and is over KEY RESOLVABILITY: both of
    its ends are invocations the shipped grammar accepts as writes, and they differ
    only in whether the stored memory is named. The outer band is over WHETHER IT IS
    A WRITE AT ALL, and it exists because the shipped help documents a bare
    single-positional capture as a RECALL when the argument names an existing
    memory. Nesting them keeps the two kinds of uncertainty from being read as one.
    """
    low = session_rate(tally, ("write_unambiguous",))
    high = session_rate(tally, ("write_unambiguous", "write_ambiguous"))
    outer = session_rate(tally, ("write_unambiguous", "write_ambiguous", verbs.BARE_KEY_AMBIGUOUS))
    total = sum(c[verbs.BARE_KEY_AMBIGUOUS] for c in tally.per_session.values())
    return {
        "unambiguous": low,
        "ambiguity_band_high": high,
        "bare_key_band_high": outer,
        "counts": {
            "unambiguous": sum(c["write_unambiguous"] for c in tally.per_session.values()),
            "ambiguous": sum(c["write_ambiguous"] for c in tally.per_session.values()),
            "bare_key_ambiguous": total,
            "rejected_by_shipped_grammar": sum(
                c[verbs.REJECTED_BY_SHIPPED_GRAMMAR] for c in tally.per_session.values()
            ),
        },
        "note": (
            "The inner band's low end counts only writes that NAME the memory they "
            "store, which on the shipped CLI means an explicit key flag and nothing "
            "else: the positional argument is the content and the key is auto-generated "
            "from it. Its high end adds the accepted writes that supply no key. Only "
            "the low end can enter the join. The OUTER end adds the bare "
            "single-positional captures, which the shipped help says are RECALLED "
            "rather than stored when the argument names an existing memory - a fact "
            "about the store, which no transcript records. No end contains an "
            "invocation the shipped binary refuses, whether on an undeclared flag "
            "(published in E0.2 as attempted reads) or on positional arity (published "
            "in its own bucket); an invocation that is rejected stored nothing."
        ),
    }


def join(tally: Tally) -> dict[str, Any]:
    """E0.4 read-after-write, over TWO denominators and three locality tests.

    The primary denominator is the preregistered one: keyed invocations of the
    targeted read verb, the reads the shipped binary actually executes. The widened
    denominator adds the keyed attempted reads spelled with the write verb -
    invocations the binary rejects on an undeclared flag, so no stored body ever
    reached the agent.

    Both ship because the rule that moved those invocations out of the write bucket
    is the same rule that decides whether they belong in this denominator, and a
    null must not be published over whichever denominator flatters it. Which one is
    primary is argued in the report, not decided by silence here.
    """
    events = sorted(tally.events, key=lambda e: (e.ts, e.session))
    writers: dict[str, list[Event]] = defaultdict(list)
    keys = ("reads", "any", "cross_session", "cross_cwd")
    counters: dict[str, dict[str, int]] = {
        "executed_keyed_reads": dict.fromkeys(keys, 0),
        "attempted_keyed_reads": dict.fromkeys(keys, 0),
    }
    for ev in events:
        if ev.bucket == verbs.MEMORY_WRITE:
            writers[ev.key_digest].append(ev)
            continue
        group = (
            "executed_keyed_reads" if ev.bucket == verbs.TARGETED_READ else "attempted_keyed_reads"
        )
        counts = counters[group]
        counts["reads"] += 1
        prior = writers.get(ev.key_digest)
        if not prior:
            continue
        counts["any"] += 1
        if any(w.session != ev.session for w in prior):
            counts["cross_session"] += 1
        if any(w.cwd and ev.cwd and w.cwd != ev.cwd for w in prior):
            counts["cross_cwd"] += 1

    def rates_of(*groups: str) -> dict[str, Any]:
        reads = sum(counters[g]["reads"] for g in groups)
        hits = {k: sum(counters[g][k] for g in groups) for k in keys[1:]}
        return {
            "keyed_reads": reads,
            "raw_any": (hits["any"] / reads) if reads else 0.0,
            "raw_cross_session": (hits["cross_session"] / reads) if reads else 0.0,
            "raw_cross_working_directory": (hits["cross_cwd"] / reads) if reads else 0.0,
            "hits": {
                "any": hits["any"],
                "cross_session": hits["cross_session"],
                "cross_working_directory": hits["cross_cwd"],
            },
        }

    primary = rates_of("executed_keyed_reads")
    widened = rates_of("executed_keyed_reads", "attempted_keyed_reads")
    return {
        # The preregistered field name is kept so this run lines up against the
        # withdrawn ones; it is the primary denominator's read count.
        "keyed_targeted_reads": primary["keyed_reads"],
        **{k: v for k, v in primary.items() if k != "keyed_reads"},
        "denominator_choice": {
            "primary": "executed keyed reads only (preregistered): the keyed read verb",
            "widened": (
                "primary plus keyed attempted reads spelled with a write verb, which "
                "the shipped binary rejects on the undeclared flag"
            ),
            "widened_result": widened,
            "rationale": (
                "The primary denominator answers 'did a read that RAN recover an "
                "earlier capture'. A rejected invocation retrieved nothing, so it "
                "cannot evidence reuse, and admitting it would deflate RAW by "
                "construction. The widened denominator answers the INTENT question "
                "this statistic is a proxy for - how often an agent named a key it "
                "expected to be there - which is the reading the same rule change "
                "supports. Both are published; neither is hidden."
            ),
        },
        "join_eligibility_drops": dict(tally.join_eligibility_drops),
        "join_eligibility_drops_note": (
            "COUNTED invocations that could not enter the join (an undated keyed "
            "event has no position in corpus time). Not an exclusion: nothing was "
            "screened out of the population, so it is reported here and not beside "
            "the screens."
        ),
        "interpretation": (
            "RAW is a CLI-EXPRESSIBILITY measurement, not a reuse measurement. It bounds "
            "how much keyed read traffic COULD refer to something captured earlier in the "
            "corpus. It cannot show that the retrieved body was read or acted on: "
            "mechanism-FIRES is not mechanism-CONSUMED. Publishing it twice over locality "
            "lets same-directory continuations be read off rather than assumed away."
        ),
        "denominator_note": (
            "The search and list-all buckets enter neither denominator. They carry no "
            "key and can never join, so folding them in would deflate RAW by construction."
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
        "preregistration_amendment_2_sha256": hashlib.sha256(
            AMENDMENT_2_PATH.read_bytes()
        ).hexdigest(),
        "preregistration_amendment_3_sha256": hashlib.sha256(
            AMENDMENT_3_PATH.read_bytes()
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
                    "Screened before every other exclusion, so corpus GROWTH lands "
                    "here and cannot move the counts below it."
                ),
            },
            "frozen_against_corpus_growth": dict(tally.skipped),
            "frozen_against_corpus_growth_note": (
                "Lock-first ordering freezes these against corpus GROWTH only. It does "
                "not freeze them against ATTRITION: a transcript file named in the "
                "pinned filelist can be deleted or rotated away, and the invocations it "
                "held leave every count that is not the filelist itself. Re-running this "
                "analysis unchanged has moved help_invocation 962 -> 937 -> 922, "
                "dep_write 481 -> 477 -> 474 and files_in_filelist_no_longer_readable "
                "37 -> 66 -> 191 across three reruns of the same pinned filelist. The "
                "earlier claim that the after-lock exclusion is 'the only exclusion "
                "that can drift' was false in the attrition direction and is withdrawn "
                "(amendment A1.10). The pinned filelist and its digest are what make a "
                "run reproducible in POPULATION; they cannot make the files persist."
            ),
        },
        "bucket_counts": dict(tally.buckets),
        "E0.1_memory_write_rate": write_band(tally),
        "E0.2_memory_read_rates": {
            "targeted_read": session_rate(tally, (verbs.TARGETED_READ,)),
            "search_read": session_rate(tally, (verbs.SEARCH_READ,)),
            "browse_read": session_rate(tally, (verbs.BROWSE_READ,)),
            "attempted_read_via_write_verb": session_rate(
                tally, (verbs.ATTEMPTED_READ_VIA_WRITE_VERB,)
            ),
            "targeted_read_bare_key_band_high": session_rate(
                tally, (verbs.TARGETED_READ, verbs.BARE_KEY_AMBIGUOUS)
            ),
            "bare_key_ambiguous": session_rate(tally, (verbs.BARE_KEY_AMBIGUOUS,)),
            "bare_key_ambiguous_note": (
                "A bare single-positional capture. The shipped help says such an "
                "argument is RECALLED, not stored, when it names an existing memory. "
                "The same count therefore bands E0.1 from above and the targeted-read "
                "rate from above; it is inside neither point estimate."
            ),
            "rejected_by_shipped_grammar": session_rate(
                tally, (verbs.REJECTED_BY_SHIPPED_GRAMMAR,)
            ),
            "rejected_by_shipped_grammar_note": (
                "A write verb carrying a positional count its own usage line refuses "
                "(zero, including the keyed-but-contentless form, or two). Neither a "
                "write nor a read: nothing was stored and nothing was retrieved."
            ),
            "attempted_read_note": (
                "A write verb carrying a flag the shipped bd 1.3.0-rc.1 does not "
                "declare. The binary rejects these, so nothing was stored and nothing "
                "was retrieved: they are read ATTEMPTS, published beside E0.1 rather "
                "than inside it."
            ),
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
