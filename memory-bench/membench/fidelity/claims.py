"""The extraction schema: what a model must say about a proposed memory write.

Three fields carry the whole contract. ``text`` is the claim in the model's own
words. ``citation`` says which lines of the supplied evidence establish it.
``asserted_literals`` lists the specific tokens the claim commits to (a version
number, a count, a path, an exception name) so `verify` can check each one
against those lines mechanically.

That last field is what makes the gate bite on the campaign's actual failure. A
record asserting "insufficient on Python 3.11+" must declare the literal
``3.11``; the packet contains only ``3.12.6``; no passage of it can carry that
token; the claim is rejected without any code ever knowing what a version number
means.

**There is no quote field.** The first design had the model copy the supporting
passage verbatim so the copy could be checked against the evidence, and that is
the step a local 30B instruct model cannot do: see the note in `verify`. The
citation names lines and `EvidencePacket.span` reads them, so the excerpt is
exact by construction and the reply carries only what a model is actually needed
for.

**Why the reply is not JSON.** Structured output would make the envelope
unbreakable, and it is the obvious thing to reach for. It is worth stating why
this format survived the rewrite: a claim's TEXT is prose about Python source and
pytest output, so it routinely contains quotes and backslashes, and escaping is
the sub-task these models are worst at -- the first live run died on exactly that
with the old quote field. A line-delimited block format has nothing to escape.
Now that the reply is four short lines per claim, that is cheap insurance rather
than a constraint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CLAIM_HEADER = "CLAIM"
UNCLAIMED_HEADER = "UNCLAIMED"
NO_CITATION = frozenset({"", "none", "null", "-"})

_FIELDS = frozenset({CLAIM_HEADER, "TEXT", "CITATION", "LITERALS", "SOURCE"})
# ``L67`` is how the prompt prints an address and how a citation should be
# written. A bare ``67`` is still read: refusing it would turn a model's
# slip in transcription into an unreadable reply, when the number it wrote
# can be checked against the artifact perfectly well and rejected on its
# merits if it addresses nothing.
_RANGE = re.compile(r"^L?(?P<start>\d+)(?:-L?(?P<end>\d+))?$")
_HYPHEN = re.compile(r"\s*-\s*")
_FENCE = re.compile(r"^`+[a-zA-Z]*$")


class ExtractionFormatError(ValueError):
    """The model's reply is not a well-formed extraction."""


@dataclass(frozen=True)
class Citation:
    """A 1-based inclusive line range in one named packet artifact."""

    artifact: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class Claim:
    """One atomic factual assertion found in a proposed memory record.

    ``citation`` is a tuple because a claim can rest on two passages -- a symptom
    in one artifact and the code it points at in another -- and forcing that into
    one range would either drop half the support or span everything between them.
    An empty tuple is an uncited claim, which is the finding the gate exists to
    produce, not a parse failure.
    """

    claim_id: str
    text: str
    citation: tuple[Citation, ...]
    asserted_literals: tuple[str, ...] = field(default=())
    source_lines: tuple[int, ...] = field(default=())
    descriptive_phrases: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Extraction:
    """Every claim a model found in one record, with the model that found them.

    ``unclaimed_lines`` are the record lines the model says assert nothing --
    headings, bullet markers, source pointers. Together with each claim's
    ``source_lines`` they have to account for the whole record; `assert_covers`
    is what enforces that.
    """

    record_id: str
    model: str
    claims: tuple[Claim, ...]
    unclaimed_lines: tuple[int, ...] = field(default=())


def parse_extraction(record_id: str, model: str, reply: str) -> Extraction:
    """Parse and structurally validate a model reply into an `Extraction`.

    Structural validation only: this rejects a malformed shape, never a claim it
    disagrees with. In particular an *uncited* claim parses cleanly and reaches
    the verifier, because an uncited claim is the finding the gate exists to
    produce. What raises is a shape no reading can recover: an unparseable
    citation, a claim with no text, a reply with no claims.

    Prose and code fences before the first ``CLAIM:`` are skipped; inside a block
    every line must be a known field, so a model that starts improvising is a
    hard failure rather than a partial extraction.
    """
    blocks, unclaimed = _split_blocks(reply)
    if not blocks:
        raise ExtractionFormatError(f"reply contains no {CLAIM_HEADER}: block: {reply[:200]!r}")
    claims = tuple(_build_claim(index, block) for index, block in enumerate(blocks))
    return Extraction(record_id=record_id, model=model, claims=claims, unclaimed_lines=unclaimed)


def _split_blocks(reply: str) -> tuple[list[dict[str, str]], tuple[int, ...]]:
    """Walk the reply line by line, accumulating one field map per claim."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    unclaimed: tuple[int, ...] = ()

    for line in reply.splitlines():
        stripped = line.strip()
        if not stripped or _FENCE.match(stripped):
            continue
        key, sep, value = stripped.partition(":")
        key = key.strip().upper()

        if key == CLAIM_HEADER and sep:
            if current is not None:
                blocks.append(current)
            current = {CLAIM_HEADER: value.strip()}
        elif key == UNCLAIMED_HEADER and sep:
            # The trailer, which closes the last block and belongs to the reply
            # rather than to any one claim.
            if current is not None:
                blocks.append(current)
                current = None
            unclaimed = _parse_line_numbers(UNCLAIMED_HEADER, value)
        elif current is None:
            continue  # preamble before the first claim
        elif key in _FIELDS and sep:
            current[key] = value.strip()
        else:
            raise ExtractionFormatError(f"unrecognized line inside a claim block: {line!r}")

    if current is not None:
        blocks.append(current)
    return blocks, unclaimed


def _build_claim(index: int, block: dict[str, str]) -> Claim:
    text = block.get("TEXT", "")
    if not text.strip():
        raise ExtractionFormatError(f"claim {index} has no TEXT: {block!r}")
    claim_id = block.get(CLAIM_HEADER, "") or f"claim-{index}"
    literals, phrases = _parse_literals(block.get("LITERALS", ""))
    return Claim(
        claim_id=claim_id,
        text=text,
        citation=_parse_citation(index, block.get("CITATION", "")),
        asserted_literals=literals,
        source_lines=_parse_line_numbers(f"claim {index} SOURCE", block.get("SOURCE", "")),
        descriptive_phrases=phrases,
    )


def _parse_line_numbers(where: str, raw: str) -> tuple[int, ...]:
    """A comma- or space-separated list of record line numbers and ranges.

    Same surface as a citation's ranges, minus the artifact: the record is the
    only thing these address.
    """
    value = raw.strip()
    if value.lower() in NO_CITATION:
        return ()
    lines: list[int] = []
    for token in _HYPHEN.sub("-", value).replace(",", " ").split():
        match = _RANGE.match(token)
        if match is None:
            raise ExtractionFormatError(f"{where} is not a list of line numbers: {raw!r}")
        start = int(match.group("start"))
        lines.extend(range(start, int(match.group("end") or start) + 1))
    return tuple(lines)


def _parse_citation(index: int, raw: str) -> tuple[Citation, ...]:
    """One or more ``ARTIFACT start-end`` ranges, comma-separated, or an explicit none.

    An absent citation is a finding, not a format error, so the none forms return
    an empty tuple. A citation that is present but unreadable raises: silently
    discarding it would turn a mangled citation into the same verdict as an
    honest abstention.

    A range may omit the artifact name, in which case it continues the previous
    range's artifact -- ``PRIOR_TASK.md 44-46, 51`` is how a model writes two
    ranges in one file, and reading it any other way loses the second range.
    """
    value = raw.strip()
    if value.lower() in NO_CITATION:
        return ()
    spans: list[Citation] = []
    artifact: str | None = None
    for token in _HYPHEN.sub("-", value).replace(",", " ").split():
        match = _RANGE.match(token)
        if match is None:
            artifact = token  # a non-numeric token names the artifact that follows
            continue
        if artifact is None:
            raise ExtractionFormatError(
                f"claim {index} cites lines {token!r} with no artifact name: {raw!r}"
            )
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        spans.append(Citation(artifact=artifact, line_start=start, line_end=end))
    if not spans:
        raise ExtractionFormatError(f"claim {index} citation is not 'ARTIFACT start-end': {raw!r}")
    return tuple(spans)


def assert_covers(extraction: Extraction, record_text: str) -> None:
    """Refuse an extraction that does not account for every line of the record.

    Measured, and the reason this check exists at all. With no coverage rule the
    model quietly returned 12 claims for a record whose two version-history
    sentences were the whole reason reviewers rejected it, and 7 claims for a
    record with one deliberately injected version sentence -- in both cases the
    omitted lines were exactly the ones that would have produced a rejection.
    Nothing in the reply marked them as skipped. An extraction step whose output
    decides a rejection has an incentive to fall silent, so silence has to cost
    something.

    What it can and cannot do, plainly: a model can still declare a real
    assertion unclaimed, and no code here can tell. What changes is that the
    omission becomes a statement in the reply rather than an absence, which a
    reviewer can check, a fault test can catch, and `unclaimed.audit` can put
    back in front of the model.

    A line listed as claimed *and* unclaimed is left alone, and that was decided
    against the corpus rather than on the reading. The prompt asks for every line
    exactly once, so enforcing the other half of that rule looks like free rigour
    -- it was written, and it red 5 of the 7 retro records, one of them on 25
    lines. This model habitually pads its skip list with lines it has already
    claimed. It also costs nothing: a double-listed line is in some claim's
    SOURCE, so the claim exists and is verified, and the redundant skip entry
    disposes of nothing. A round spent on it would be a round not spent on a
    fault that does. The lines that escape are the ones listed *only* as
    unclaimed, and those are `unclaimed.audit`'s job.

    Line numbers past the end of the record are ignored. That exemption is
    measured too: told to
    account for every line, this model pads its skip list out to the length of
    the *evidence* -- one reply listed lines 8 through 82 as unclaimed on a
    7-line record, and another listed line 46 of a 45-line record. An earlier
    version of this check rejected all three such replies, and it was protecting
    nothing: `accounted` is a set, so a number outside the record can never make
    a line inside it look covered. Only the missing set has teeth, so only the
    missing set is checked.

    Blank lines carry nothing and are covered for free.
    """
    lines = record_text.splitlines()
    carrying = {n for n, line in enumerate(lines, start=1) if line.strip()}
    claimed = set[int]().union(*(claim.source_lines for claim in extraction.claims), set())
    accounted = set(extraction.unclaimed_lines) | claimed
    if missing := sorted(carrying - accounted):
        raise ExtractionFormatError(
            f"extraction accounts for neither a claim nor a skip on record "
            f"line(s) {missing}: {[lines[n - 1][:60] for n in missing[:3]]}"
        )


def _parse_literals(raw: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the LITERALS list into checkable tokens and descriptive phrases.

    Comma-separated. A literal containing a comma cannot be expressed; version
    numbers, counts, paths and exception names do not contain one.

    An entry with whitespace in it is not a literal. The prompt says so -- "each
    one is a single token with no spaces in it" -- and this is where that becomes
    mechanical rather than advisory. Measured on the retro set: 19 of 257 entries
    were phrases (`reader clarity`, `independent verification`, `NOT a security
    issue`), and checking them verbatim against the evidence rejected a record
    whose unsupported sentences had been removed. That check was never sound: a
    phrase-in-prose test is verbatim quote matching, which is exactly the step
    this design dropped because the model cannot do it reliably.

    They are kept in `descriptive_phrases` rather than discarded, because a
    silently shortened list is indistinguishable from a model that named nothing.

    A model can bury a real token inside a phrase -- `Python 3.11` -- and the
    token check will not see it, which is a silent accept rather than a silent
    reject. `conf-r1` did exactly that on both of its version claims. Nothing
    here can tell a buried token from a description, so the extractor re-asks
    instead: `verify.phrase_literals` reports the entries with spaces in them and
    the model rewrites or drops each one. That leaves `LITERALS: none` still
    unpreventable, and what limits it is that the whole extraction is a recorded
    artifact: the phrases are in it, under their own field, for a reviewer to
    read.
    """
    if raw.strip().lower() in NO_CITATION:
        return (), ()
    entries = [token.strip() for token in raw.split(",") if token.strip()]
    tokens = tuple(e for e in entries if not any(ch.isspace() for ch in e))
    phrases = tuple(e for e in entries if any(ch.isspace() for ch in e))
    return tokens, phrases
