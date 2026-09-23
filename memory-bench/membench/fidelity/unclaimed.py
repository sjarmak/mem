"""The second pass: were any of the set-aside lines assertions after all?

`assert_covers` makes an omission visible without making it costly. A model that
would rather not deal with a sentence can still file it under UNCLAIMED, satisfy
the accounting, and the gate accepts a record with the sentence in it. Measured
on the first off-episode run: the go-race-map probe's injected line -- ``sync.Map
was added in Go 1.9 and would remove this race without an explicit lock`` -- was
declared unclaimed, and the record was accepted on 5 cited claims. The lure the
pair was built around walked through the gate untouched.

No mechanical rule closes that. Deciding a sentence asserts something is the
semantic half, and a code-side test for it would be the keyword matching this
design refuses. So the question goes back to the model, and the reason a second
ask is worth a call is that it is a *different* question asked in a *different*
place. In the first pass the sentence competes for attention with a dozen others
and every claim made is work; here the only lines shown are the ones already set
aside, the ask is one bit per line, and nothing is cheaper to answer than to skip.

What comes back is not a verdict and not trusted as one. A named line goes back a
second time, with the evidence, asking for the block the first pass never wrote:
the ordinary five-line shape, parsed by the ordinary parser and checked by the
ordinary checks. A line it does not name stays unclaimed, and that remains the
open hole, one pass narrower than it was.

The second call is what makes the pass usable, and it was added because skipping
it produced a false reject. Condemning a recovered line as uncited is cheaper by
one call and it treats "the first pass did not claim this" as "the evidence does
not support this", which are different things. Measured on the go-race-map pair:
the audit correctly recovered four set-aside lines of the supported record --
``pool/collect.go:88 is the only write to p.results``, the quoted assignment
under it, and two more -- every one of them sitting in the supplied evidence, and
the record inside its own support boundary was rejected on all four. Asked for a
citation instead, those lines cite; the lure's ``sync.Map was added in Go 1.9``
has nothing to cite and rejects, which is the finding the pair was built for.

The second call may also decline, and that is not a leak in it. The naming ask
over-names: measured across the three off-episode packets it returned ``##
Waiting writers (EXECUTED)`` and four more headings, three ``Source:
PRIOR_SOURCE_PACKET.md`` pointers, and several of the records' own ``the evidence
does not establish`` sentences -- every category the ask tells it to leave alone.
Written up as blocks with nothing to cite, those rejected all three supported
records. So a named line with no block written for it stays unclaimed. The two
asks disagree there and the second one wins, because it is looking at the
evidence and the naming ask is looking at a list of stripped lines.
"""

from __future__ import annotations

from collections.abc import Callable

from membench.fidelity.claims import (
    CLAIM_HEADER,
    NO_CITATION,
    UNCLAIMED_HEADER,
    Claim,
    Extraction,
    ExtractionFormatError,
)

__all__ = [
    "ASSERTING_HEADER",
    "AUDIT_MARKER",
    "RECOVERY_MARKER",
    "audit",
    "build_audit_prompt",
    "build_recovery_ask",
    "declines_every_line",
    "merge",
    "offered_lines",
    "parse_audit",
    "uncited_claims",
]

ASSERTING_HEADER = "ASSERTING"

# What a recorder reads off a prompt to tell the three asks apart. They are the
# headers the two audit prompts carry and the extraction prompt does not; a run
# stores the reply its verdict came from, and reply shape cannot supply that,
# since the recovery ask answers in the same block format as the extraction.
AUDIT_MARKER = "=== LINES SET ASIDE ==="
RECOVERY_MARKER = "and no block above accounts for them"

_AUDIT = f"""\
You set these lines of a memory record aside as asserting nothing. Read them
again on their own, away from the rest of the record.

Which of them state something as true about the system, the task, or the work --
something a reader could be right or wrong about?

A line asserts something if it says what happened, what a thing is, what a value
was, or what would happen. It does not if it is a heading, a section label, a
bullet marker, a pointer at where evidence came from, or a sentence saying that
something is unknown, unverified, not established, or out of scope.

Answer with one line and nothing else:

{ASSERTING_HEADER}: the addresses of the lines that assert something, as `L12 L27`,
  or the word none

Write only addresses printed to the left of the lines below. Do not explain, do
not restate the lines, and do not add any line that is not shown here.

=== LINES SET ASIDE ===
{{lines}}
"""


def build_audit_prompt(record_text: str, unclaimed_lines: tuple[int, ...]) -> str:
    """Render the set-aside lines under their own record addresses.

    The numbering is the record's, not a fresh one starting at 1. A renumbered
    excerpt would be a third addressing scheme in a design that already had to
    stop a model confusing two of them, and the reply's addresses have to be
    checked against the extraction that produced them.
    """
    lines = record_text.splitlines()
    shown = "\n".join(
        f"L{n}\t{lines[n - 1]}"
        for n in unclaimed_lines
        if 1 <= n <= len(lines) and lines[n - 1].strip()
    )
    return _AUDIT.format(marker=AUDIT_MARKER, lines=shown)


def _addresses(raw: str, offered: tuple[int, ...]) -> tuple[int, ...]:
    named = {
        int(token.lstrip("Ll"))
        for token in raw.replace(",", " ").split()
        if token.lstrip("Ll").isdigit()
    }
    return tuple(sorted(named & set(offered)))


def _is_bare_answer(raw: str) -> bool:
    """Is this line an answer with its label dropped, or is it prose?

    Every token an address, and at least one of them. That is narrow enough that
    a sentence cannot satisfy it -- `I think line 5 is a claim.` has words in it
    -- and wide enough to read the reply this was measured on.
    """
    tokens = raw.replace(",", " ").split()
    return bool(tokens) and all(t.lstrip("Ll").isdigit() for t in tokens)


def parse_audit(reply: str, offered: tuple[int, ...]) -> tuple[int, ...]:
    """Read the answer line, keeping only addresses that were offered.

    An address the audit was not shown is dropped rather than raising. The model
    is being asked a yes/no question about a handful of lines and a stray number
    is not a fault worth spending the whole record on -- the same reasoning that
    made `assert_covers` ignore a skip list padded past the end of the record.
    What is a fault is a reply with no answer in it at all, because an empty
    result and "none of them assert anything" are the same value and must not be.

    The label is not the answer, and requiring it threw one away. Measured on the
    go-race-map lure: the audit came back ``L8 L9 L18 L28`` -- four lines the first
    pass had set aside, one of them the sentence the probe was built around -- and
    the missing ``ASSERTING:`` prefix discarded all four, so the lure was accepted
    a second time for a reason that had nothing to do with the evidence. A line
    that is nothing but addresses is read as the answer it plainly is. Prose still
    faults: one word that is not an address is enough to disqualify a line, so a
    model reasoning out loud cannot be mistaken for one answering.
    """
    for line in reply.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key.strip().upper() == ASSERTING_HEADER:
            raw = value.strip()
            return () if raw.lower() in NO_CITATION else _addresses(raw, offered)
    for line in reply.splitlines():
        raw = line.strip()
        if raw.lower() in NO_CITATION:
            return ()
        if _is_bare_answer(raw):
            return _addresses(raw, offered)
    raise ExtractionFormatError(f"audit reply has no {ASSERTING_HEADER}: line: {reply[:200]!r}")


_RECOVER = """\
These lines of the record assert something, {marker}:

{lines}

Write the blocks for them now, in the same five-line shape, using the evidence
already supplied. SOURCE names only the addresses listed here, one block per line.

Where the supplied evidence does not establish one of them, write CITATION: none.
That is the expected answer for a sentence brought from background knowledge, and
it is what this second look is for -- do not stretch a nearby passage to cover it.

If one of these lines turns out to assert nothing after all -- a heading, a
section label, a pointer at where evidence came from, or a sentence saying that
something is unknown, unverified, not established, or out of scope -- write no
block for it. Leaving it out is the correct answer for such a line.

Close with UNCLAIMED: the addresses above you wrote no block for, or none.

=== CLAIMS ===
"""


def build_recovery_ask(record_text: str, asserting: tuple[int, ...]) -> str:
    """The second ask: blocks for the lines the audit named, and nothing else."""
    lines = record_text.splitlines()
    shown = "\n".join(f"L{n}\t{lines[n - 1]}" for n in asserting)
    return _RECOVER.format(marker=RECOVERY_MARKER, lines=shown)


def uncited_claims(record_text: str, asserting: tuple[int, ...]) -> tuple[Claim, ...]:
    """What a named line becomes when no block can be got for it.

    The fallback, not the design. It carries the record line verbatim rather than
    a paraphrase, so a rejection quotes the record back at its writer and the
    sentence gets no second chance to shed the token it commits to. Reached only
    when the recovery ask comes back unreadable: the audit has already said the
    line asserts something, and a write gate that cannot obtain a citation for an
    assertion refuses rather than accepts.
    """
    lines = record_text.splitlines()
    return tuple(
        Claim(
            claim_id=f"unclaimed-L{n}",
            text=lines[n - 1].strip(),
            citation=(),
            source_lines=(n,),
        )
        for n in asserting
    )


def declines_every_line(reply: str) -> bool:
    """Is this a recovery reply that wrote no block and said so?

    A full decline has no CLAIM block in it, which is the one reply shape the
    ordinary parser refuses, so it has to be recognised before parsing or the
    answer "none of these assert anything after all" arrives as an unreadable
    reply and condemns every line it was asked about -- the exact false reject the
    decline exists to prevent. What separates it from a model that wandered off is
    the closing accounting: a decline still answers in the format, with an
    UNCLAIMED line and nothing above it.
    """
    keys = [line.strip().partition(":") for line in reply.splitlines()]
    headers = {key.strip().upper() for key, sep, _ in keys if sep}
    return CLAIM_HEADER not in headers and UNCLAIMED_HEADER in headers


def merge(extraction: Extraction, recovered: tuple[Claim, ...]) -> Extraction:
    """Add the recovered claims and stop calling *their* lines unclaimed.

    Which lines those are is read off the claims, not off what the naming ask
    said. A line the naming ask offered and the recovery ask wrote no block for
    stays unclaimed, which is the second ask overruling the first -- and the
    second is the better-informed one, having the evidence in front of it.
    """
    named = {n for claim in recovered for n in claim.source_lines}
    return Extraction(
        record_id=extraction.record_id,
        model=extraction.model,
        claims=extraction.claims + recovered,
        unclaimed_lines=tuple(n for n in extraction.unclaimed_lines if n not in named),
    )


def offered_lines(extraction: Extraction, record_text: str) -> tuple[int, ...]:
    """The set-aside lines worth asking about: inside the record, and not blank."""
    lines = record_text.splitlines()
    return tuple(
        n for n in extraction.unclaimed_lines if 1 <= n <= len(lines) and lines[n - 1].strip()
    )


def audit(
    extraction: Extraction,
    record_text: str,
    complete: Callable[[str], str],
) -> Extraction:
    """Ask which set-aside lines assert something; return them as uncited claims.

    The one-call form, kept for callers with no evidence packet in hand. A run
    uses `ClaimExtractor`, which follows the naming ask with a recovery ask so a
    recovered line can cite what supports it.

    A record with nothing set aside is returned unchanged and costs no call.
    """
    offered = offered_lines(extraction, record_text)
    if not offered:
        return extraction
    asserting = parse_audit(complete(build_audit_prompt(record_text, offered)), offered)
    if not asserting:
        return extraction
    return merge(extraction, uncited_claims(record_text, asserting))
