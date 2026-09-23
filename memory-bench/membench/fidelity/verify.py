"""The mechanical half: does a model-declared citation actually support the claim?

Nothing here reads a claim for meaning. Each check is a bounds test, an
arithmetic comparison, or a substring search over data the model supplied:

1. the claim carries a citation at all;
2. every cited artifact is in the packet;
3. every cited line range exists in that artifact;
4. the citation is narrow enough to be a citation;
5. every literal the claim asserts occurs in the cited artifact.

Check 5 is what catches the campaign's actual failure. A record asserting Python
3.11 behaviour has to declare the literal ``3.11``, and a packet establishing
only 3.12.6 does not contain it anywhere, so the claim is rejected without any
code ever knowing what a version number means.

**The model does not supply the text.** An earlier version asked it to copy the
supporting passage verbatim and checked the copy against the evidence. Measured
against a local 30B instruct model, that is the step it cannot do: on a packet
whose sealed evidence is a hard-wrapped terminal capture, reproducing one passage
collapsed into 1,400 lines of repeated pipe characters, and five of nine records
in a run yielded no parseable reply at all. The copy was never load-bearing --
the citation names lines and the packet holds them.

**Check 5 is scoped to the artifact, not to the cited lines, and that is a
measured retreat.** Scoping it to the lines is the stronger rule and it was
tried: it rejected all 9 records of the retro set, the supported one included,
because this model's line numbers are routinely off by several lines on wrapped
terminal text -- it cited PRIOR_SOURCE_PACKET.md 77 for a path on line 68 and
line 27 for a message that wraps across 27-28. Widening the haystack from the
cited lines to the cited artifact changed nothing else in the matrix: the same
6 records rejected, on the same claims. So the address is checked for existence
and breadth, and the token is checked against the artifact the model named. What
that gives up, stated: a claim can now pass while pointing a reader at the wrong
passage of the right file. What it keeps is the check that does the work, which
is that a token absent from the evidence cannot be asserted.

**Check 5 compares case-folded text**, for the reason `packet.fold` records: the
first off-episode run rejected a record inside its own support boundary over
`reads` against a sentence-initial `Reads`. Folding cannot reach the tokens that
do the work, because every one of them carries a digit and a digit has no case.

Check 4 remains for the reader, not for the token check: a citation spanning half
an artifact tells a reviewer nothing, and the cap is policy applied in code with
no reading of the text involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from membench.fidelity.claims import Citation, Claim, Extraction
from membench.fidelity.packet import EvidencePacket, fold, normalize

__all__ = [
    "MAX_CITED_LINES",
    "ClaimVerdict",
    "UnsupportedReason",
    "fold",
    "normalize",
    "overbroad_citations",
    "phrase_literals",
    "unrecorded_literals",
    "unresolvable_citations",
    "verify_claim",
    "verify_extraction",
]

# The width past which a citation stops being a citation. Set against the packet
# it runs on, whose two artifacts are 78 and 82 lines: at 20 a citation still
# names a passage a reader could check. It is a threshold, so it is arbitrary in
# the way every threshold is. What keeps it honest is that it is a bound on the
# address, never on the text -- widening it can only admit claims, never reject
# one, so a gate tuned by raising it would show up immediately as a gate that
# stops rejecting.
MAX_CITED_LINES = 20


class UnsupportedReason(Enum):
    """Why a claim failed. Each value names a mechanical check, not a judgment."""

    NO_CITATION = "no_citation"
    UNKNOWN_ARTIFACT = "unknown_artifact"
    CITATION_OUT_OF_RANGE = "citation_out_of_range"
    CITATION_TOO_BROAD = "citation_too_broad"
    LITERAL_ABSENT_FROM_EVIDENCE = "literal_absent_from_evidence"


@dataclass(frozen=True)
class ClaimVerdict:
    """One claim's outcome. ``reason`` is None exactly when the claim is supported."""

    claim_id: str
    claim_text: str
    supported: bool
    reason: UnsupportedReason | None = None
    detail: str = ""

    @property
    def unsupported(self) -> bool:
        return not self.supported


def verify_claim(claim: Claim, packet: EvidencePacket) -> ClaimVerdict:
    """Run the checks over one claim, returning at the first failure."""
    if not claim.citation:
        return _fail(
            claim,
            UnsupportedReason.NO_CITATION,
            "claim cites no passage of the supplied evidence",
        )

    cited_artifacts: list[str] = []
    for cited in claim.citation:
        if cited.artifact not in packet.artifacts:
            return _fail(
                claim,
                UnsupportedReason.UNKNOWN_ARTIFACT,
                f"cited {cited.artifact!r}, packet has {', '.join(packet.names())}",
            )
        try:
            packet.span(cited.artifact, cited.line_start, cited.line_end)
        except IndexError as exc:
            return _fail(claim, UnsupportedReason.CITATION_OUT_OF_RANGE, str(exc))
        if cited.artifact not in cited_artifacts:
            cited_artifacts.append(cited.artifact)

    cited_lines = sum(c.line_end - c.line_start + 1 for c in claim.citation)
    if cited_lines > MAX_CITED_LINES:
        return _fail(
            claim,
            UnsupportedReason.CITATION_TOO_BROAD,
            f"cites {cited_lines} lines ({_render_citation(claim.citation)}); a passage "
            f"supporting one claim is at most {MAX_CITED_LINES}",
        )

    evidence = fold("\n".join(packet.artifacts[name] for name in cited_artifacts))
    absent = [lit for lit in claim.asserted_literals if fold(lit) not in evidence]
    if absent:
        return _fail(
            claim,
            UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE,
            f"asserted literal(s) absent from {', '.join(cited_artifacts)}: "
            f"{', '.join(absent)}",
        )
    return ClaimVerdict(claim_id=claim.claim_id, claim_text=claim.text, supported=True)


def unresolvable_citations(extraction: Extraction, packet: EvidencePacket) -> str | None:
    """Describe every citation that does not address a line of the artifact it names.

    Not a verdict, and that is the point of separating it. An address outside the
    artifact says nothing about the evidence: the model has misread which
    numbering it is writing in, and the claim underneath may be perfectly well
    supported. Measured -- the one supported record in the retro set cited
    ``PRIOR_SOURCE_PACKET.md 236`` for a stack frame at
    ``retrieval_extraction.py:236``, a line number printed inside the evidence
    text rather than beside it. That artifact has 78 lines. Every other citation
    in the record resolved, and that single slip was the whole difference between
    the gate accepting the record and rejecting it.

    So the caller re-asks rather than concludes. The line counts are in the
    complaint because they are what the model got wrong and cannot recover by
    thinking harder; nothing here says which passage to cite instead, and a
    citation that is still unresolvable after the re-ask stays a rejection.

    Returns None when every citation resolves.
    """
    faults = [
        f"{cited.artifact} {cited.line_start}"
        + (f"-{cited.line_end}" if cited.line_end != cited.line_start else "")
        for claim in extraction.claims
        for cited in claim.citation
        if cited.artifact in packet.artifacts and not _resolves(cited, packet)
    ]
    if not faults:
        return None
    sizes = ", ".join(
        f"{name} has {len(packet.artifacts[name].splitlines())} lines" for name in packet.names()
    )
    return f"these citations name lines that do not exist: {'; '.join(faults)} -- {sizes}"


def overbroad_citations(extraction: Extraction, packet: EvidencePacket) -> str | None:
    """Describe every citation wider than the cap, so it can be re-asked.

    The cap is stated in the prompt and was still missed, and the reason is that
    a model cannot count lines it is looking at. Measured off-episode on the
    go-race-map supported record: the claim ``race_detected_at_write_site`` cited
    a 26-line contiguous race report, four lines over. The claim is true, the
    passage is the right one, and a narrower range carrying it existed six lines
    away -- the write site itself. Nothing in the run told the model the block it
    picked was over.

    This is the same kind of fault as an address outside the artifact: the
    citation is unreadable as a citation rather than wrong about the evidence, and
    the claim underneath may be perfectly supported. It was the only one of that
    kind with no re-ask, so a record could be rejected for a formatting rule it
    was never told it had broken. Now it earns a round, and the complaint carries
    the number of lines cited and the cap, because those are exactly what the
    model got wrong and cannot recover by thinking harder.

    What it must not do is say which passage to cite instead. Nominating a
    narrower range would be the gate telling the model where the support is, and
    a citation still over the cap after the re-ask stays a rejection --
    `verify_claim` reports it as CITATION_TOO_BROAD either way.

    Returns None when every citation is within the cap.
    """
    faults = []
    for claim in extraction.claims:
        width = sum(c.line_end - c.line_start + 1 for c in claim.citation)
        if width > MAX_CITED_LINES:
            faults.append(
                f"{claim.claim_id} cites {width} lines ({_render_citation(claim.citation)})"
            )
    if not faults:
        return None
    return (
        f"these citations are wider than the {MAX_CITED_LINES}-line limit: "
        f"{'; '.join(faults)} -- cite the narrowest range that carries the claim, "
        "or split it into the claims the passage actually contains"
    )


def phrase_literals(extraction: Extraction) -> str | None:
    """Describe every LITERALS entry that is a phrase rather than a token.

    The prompt asks for single tokens with no spaces, and `_parse_literals` files
    anything with a space under `descriptive_phrases`, where the token check
    never reaches it. That is the right place for a description -- checking
    `reader clarity` verbatim against prose is the quote-matching this design
    dropped -- but it is the wrong place for a token wearing a word, and a
    dropped entry is a silent accept.

    Measured on `conf-r1`: two claims committed to Python 3.11 and both declared
    `LITERALS: Python 3.11`. The space made them phrases, the check never ran,
    and the record was rejected for two unrelated sentences instead of the
    version claim the reviewers named. Written `3.11`, both claims fail check 5
    against their own citation, which carries no such token.

    Only entries carrying a digit are reported, and that narrowing was bought at
    the cost of a verdict. Asked about every phrase, the model did not drop the
    descriptions -- it snake-cased them, and `reader clarity`, `JSON parser` and
    `input boundary` came back as `reader_clarity`, `JSON_parser` and
    `input_boundary`: manufactured tokens that appear in no artifact and reject
    the claims carrying them. The record with its unsupported sentences stripped,
    which must accept, came back a reject on three of them. A re-ask that invents
    the thing it checks is worse than the hole it closes.

    A digit is what the burials in this corpus are made of -- `Python 3.11`,
    `Phase 4`, `177 passed` -- and prose does not carry one, so the narrowed
    complaint never reaches the descriptions it was mangling. The hole it leaves,
    stated: a path or an exception name buried in words is still missed. That is
    the safe direction for a check whose false rejections cost more than its
    misses, and this model already writes paths as tokens unaided.

    Which half of a reported entry is the token stays the model's call -- `cost
    reporting` and `3.11 4300` are the same shape to code -- and it is told it can
    drop the entry entirely. The complaint spells the corrected form out with
    commas because leaving that implicit cost a verdict too: told to "write each
    as the single token", the model answered `LITERALS: 4300 CVE-2020-10735 3.11`,
    three tokens with no separator, parsed as one spacey entry, dropped again, and
    `dev03-current` came back an accept with its version claim unchecked.

    Returns None when every entry with a digit in it is a token.
    """
    faults = sorted(
        {
            phrase
            for claim in extraction.claims
            for phrase in claim.descriptive_phrases
            if any(ch.isdigit() for ch in phrase)
        }
    )
    if not faults:
        return None
    listed = "; ".join(f"`{phrase}`" for phrase in faults)
    return (
        f"these LITERALS entries contain spaces, so they are not tokens: {listed} -- "
        f"rewrite each as the single token the claim commits to, or drop it from "
        f"LITERALS if it is a description and belongs in TEXT. The list stays "
        f"comma-separated: `LITERALS: 3.11, 4300` is two tokens, "
        f"`LITERALS: 3.11 4300` is one entry with a space in it and is the same "
        f"fault again. Do not make a token by joining words: `reader clarity` is a "
        f"description, it belongs in TEXT, and `reader_clarity` is not a token but "
        f"an invented one"
    )


def unrecorded_literals(extraction: Extraction, record_text: str) -> str | None:
    """Describe every asserted literal that the record itself does not contain.

    A claim's literals are the tokens *the record* commits to, so a token absent
    from the record was never asserted by it, and checking one against the
    evidence answers a question nobody asked. It fails, of course -- a token
    nothing wrote appears nowhere -- and the record is rejected for a sentence it
    does not contain.

    Measured on `pg-index-lock`, 5 draws of 5: told that `release train 14` has a
    space in it and is therefore not a token, this model answered
    `release_train_14`, and carried the habit to a claim that had drawn no
    complaint, turning `CREATE INDEX idx_orders_created_at ON orders
    (created_at)` into `CREATE_INDEX_idx_orders_created_at_ON_orders_created_at`.
    Both manufactured tokens then failed check 5 and rejected a record inside its
    own support boundary, in every draw. `phrase_literals` names the joining
    habit and asks the model not to; that is the second time a narrowing of that
    re-ask has failed to hold, so this one is a check rather than a sentence.

    It cannot make the gate more permissive, and that is deliberate: it produces
    a re-ask and nothing else. An entry that survives the re-ask is still
    verified exactly as before, so a mangled token still rejects its claim. What
    the round buys is the chance to spell the token the way the record spells it,
    which is the only spelling check 5 was ever meant to test.

    A lure is out of its reach in the direction that matters. The injected
    sentence is part of the record, so its betraying token is in the record too,
    and no complaint here ever names one.

    Returns None when every asserted literal occurs in the record.
    """
    folded = fold(record_text)
    faults = sorted(
        {
            literal
            for claim in extraction.claims
            for literal in claim.asserted_literals
            if fold(literal) not in folded
        }
    )
    if not faults:
        return None
    listed = "; ".join(f"`{literal}`" for literal in faults)
    return (
        f"these LITERALS entries do not appear anywhere in the record: {listed} -- "
        f"a literal is a token the record itself writes, so copy it from the "
        f"record exactly, character for character, or drop it from LITERALS. "
        f"Joining words with underscores makes a token the record never wrote and "
        f"is the same fault, not a fix for it"
    )


def _resolves(cited: Citation, packet: EvidencePacket) -> bool:
    try:
        packet.span(cited.artifact, cited.line_start, cited.line_end)
    except (IndexError, ValueError, KeyError):
        return False
    return True


def verify_extraction(extraction: Extraction, packet: EvidencePacket) -> tuple[ClaimVerdict, ...]:
    """Verify every claim. Order is preserved so a report can cite claim position."""
    return tuple(verify_claim(claim, packet) for claim in extraction.claims)


def _render_citation(citation: tuple[Citation, ...]) -> str:
    return "; ".join(f"{c.artifact} {c.line_start}-{c.line_end}" for c in citation)


def _fail(claim: Claim, reason: UnsupportedReason, detail: str) -> ClaimVerdict:
    return ClaimVerdict(
        claim_id=claim.claim_id,
        claim_text=claim.text,
        supported=False,
        reason=reason,
        detail=detail,
    )
