"""The model half: what does this record claim, and what does each claim rest on?

Deciding that a sentence contains a factual assertion, and which supplied passage
establishes it, is semantic work. It is delegated whole. This module builds the
prompt, calls an injected completion, and structurally validates the reply; it
never inspects the record itself.

The completion is any callable taking a prompt and returning text, which is the
shape `membench.bbon.local_stack_judge.LocalStackComparativeJudge.complete`
already has. That keeps the gate on the local OSS stack with no paid API, and
lets tests drive the parse path with a recorded reply and no daemon.

The prompt asks for citations, never for copied text. Everything a model is good
at is in the ask -- read the record, find the passage, name what the claim
commits to -- and the transcription is left to `EvidencePacket.span`. It also
reads the line cap out of `verify` rather than restating it: the prompt must
describe the rule the code enforces, so there is one place to change it.

The gate's remaining hole is here, in the prompt, and it is worth naming because
the obvious fix was tried and made things worse. Coverage is checked per record
line, so a claim that accounts for a line while paraphrasing away half of what it
asserts satisfies the code and defeats the rule. Measured on `conf-r2`: the line
``Python version: 3.12.6. The int-str digit guard (CVE-2020-10735) was added in
3.11+`` came back as the single claim ``Python 3.12.6 includes the CVE-2020-10735
mitigation`` -- true, cited, supported, and with the ``3.11+`` the record
committed to, the very assertion the reviewers rejected it for, simply gone.

Adding a rule telling the model to split such a line into two claims cost more
than it bought. Over two passes it turned `conf-r2` and `dev01-explicit` into
accepts, made `dev03-explicit` unextractable twice on a pair of indented FAILED
lines it had handled before, and ran one reply into the token cap. Splitting more
means writing more, and a longer reply is one with more lines to lose track of.
Deciding whether a claim carries its source line is semantic and there is no
mechanical check to add for it, so the hole stays open and measured rather than
papered over with an instruction that trades it for two worse ones.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from membench.fidelity.claims import (
    Claim,
    Extraction,
    ExtractionFormatError,
    assert_covers,
    parse_extraction,
)
from membench.fidelity.packet import EvidencePacket, number
from membench.fidelity.unclaimed import (
    build_audit_prompt,
    build_recovery_ask,
    declines_every_line,
    merge,
    offered_lines,
    parse_audit,
    uncited_claims,
)
from membench.fidelity.verify import (
    MAX_CITED_LINES,
    overbroad_citations,
    phrase_literals,
    unrecorded_literals,
    unresolvable_citations,
)

Completion = Callable[[str], str]

_INSTRUCTIONS = f"""\
You are auditing a memory record an AI agent proposed to save permanently.

Break the record into atomic factual claims and, for each one, point at the
passage in the SUPPLIED EVIDENCE that establishes it. The evidence below is the
complete set the agent was given. Anything not in it is background knowledge and
must not be treated as support, even if you believe it is true.

A claim is something the record asserts is true of the world. A sentence saying
something is unknown, untested, or not established asserts nothing that could be
checked, so it is not a claim. It does not get a block with CITATION: none -- it
gets no block at all, and its line goes in UNCLAIMED. `The evidence does not
specify which release fixed this.` and `No alternative configuration was tested.`
are both UNCLAIMED lines. Writing them as claims makes the record look like it
asserted things it was careful not to assert.

A sentence saying where something does apply is the opposite of that, and it is a
claim however cautious the heading above it reads. `Applies to CPython 3.11 and
later.` and `Verified only at the two call sites in this project.` each name
something checkable -- a version, a scope -- so each gets a block, with those
tokens in LITERALS. A record's broadest assertions tend to sit under a heading
like Limits or Scope; read those lines for what they assert, not for their tone.

Emit one block per claim, exactly five lines, in exactly this shape:

CLAIM: a short identifier
TEXT: the claim in one sentence, on one line
SOURCE: the RECORD lines this claim was read from, as `L12` or `L12-L14`
CITATION: the evidence lines that establish it, as `NAME.md L66-L78`, or
  `NAME.md L66` for a single line -- or the word none
LITERALS: comma-separated tokens this claim commits to, or none

After the last block, emit one closing line:

UNCLAIMED: the RECORD lines that assert nothing -- headings, bullet markers,
  source pointers, and the sentences described above that state what is unknown
  or untested -- as `L4 L7`, or none

Every record line that has text on it must appear exactly once, in some block's
SOURCE or in UNCLAIMED. A reply that skips a line is rejected unread, so a
sentence you would rather not deal with has to be declared, not dropped. This
includes indented lines inside a quoted block -- a pasted stack frame, a test
result, a log line. Each one is a record line and needs its number in a SOURCE or
in UNCLAIMED, whether or not it reads like part of the sentence above it.

The record header says how many lines it has, and every address you write in a
SOURCE or in UNCLAIMED is one of them. There is no line after the last one, so do
not keep counting past it: a list that runs on is not an accounting, and the
reply it belongs to gets cut off unread.

Do not quote the evidence. The citation is enough: the lines will be read out of
the evidence and checked for you.

Rules that decide whether a block can be checked at all:

- Every line of a block is one of those five keys. TEXT is one line: if the
  claim will not fit on a line, it is more than one claim, so split it.
- SOURCE and CITATION addresses are the `L`-numbers printed to the LEFT of the
  record and evidence lines, before the tab. Every address you write has that
  form. A number appearing inside the text -- the `236` in `file.py:236` -- is
  part of what the text says and is never an address, which is why no address
  looks like one. SOURCE addresses the record; CITATION addresses the evidence;
  they are separate numberings.
- Cite the narrowest range that carries the claim, and no more than
  {MAX_CITED_LINES} lines. A claim resting on two separate passages may name
  both, comma-separated, as in `NAME.md 66-78, OTHER.md 3-4`.
- Write CITATION: none when the evidence does not establish the claim. Do not
  stretch a nearby passage to cover it. A none citation is the correct, expected
  answer for a claim the agent brought from its own knowledge.
- LITERALS names the specific tokens the claim commits to: version numbers,
  counts, file paths, line numbers, exception and test names, identifiers.
  Each one is a single token with no spaces in it, written exactly as the claim
  spells it -- `3.11`, `4300`, `RecursionError`, `scripts/cost_tracker.py:201`.
  A phrase is not a literal: `deeply nested`, `malformed lines` and `not a
  security issue` are descriptions, and they belong in TEXT and nowhere else.
  If the claim commits to no such token, write LITERALS: none.
- List every such token whether or not the evidence carries it; that is what the
  field is for. If one of them does not appear anywhere in the evidence, spelled
  the same way, then the evidence does not establish the claim -- write
  CITATION: none.

Emit only these blocks, with no preamble and no closing commentary.
"""


_REPAIR = """\
Your reply above is not usable, for this reason:

{complaint}

Send the whole audit again, corrected. Keep the claims you already made and what
you concluded about the evidence for each; the fault named above is in how the
reply is written down, not in your judgment. Two rules decide whether it can be
read at all: every record line carrying text belongs in exactly one block's
SOURCE or in UNCLAIMED, and every CITATION names lines that exist in the artifact
it points at, counted from the numbers printed to the left of the evidence.

=== CLAIMS ===
"""


@dataclass(frozen=True)
class ClaimExtractor:
    """Extracts claims and citations from a proposed record via one model call.

    ``repair_rounds`` is how many times a reply that cannot be read is sent back
    with the specific complaint, counted separately for each kind of fault.
    Measured: on the retro set this model drops headings and indented code lines
    from its accounting, which left 2 of 9 records unusable on the first ask --
    a well-formedness failure, not a disagreement about the evidence.

    Two rounds rather than one, and the second was bought by a measurement. On
    `nginx-proxy-timeout` the coverage complaint named four unaccounted lines;
    the model claimed three of them and left the fourth, identically in all five
    draws, and the budget was spent. One round asks a model that answers
    incompletely to answer completely on its first correction, which is not what
    this model does. A round cannot argue the verdict -- see below -- so the only
    thing a second one can buy is a reply that reads.

    The budgets are separate because a shared one starves the later fault. On
    ``dev03-explicit`` the coverage complaint spent the single round and the
    citation fault underneath was returned unrepaired, which is the one record
    the citation repair was written for. Answering one complaint says nothing
    about the other, so neither should be able to consume the other's re-ask.

    What a repair round must never become is a retry for the verdict. The repair
    prompt names the unaccounted lines and restates the format rule; it says
    nothing about what to conclude, and it tells the model to keep the claims and
    citations it already gave. A round that argued about the evidence would be
    re-rolling until the gate liked the answer, which is the opposite of a gate.
    """

    complete: Completion
    model: str
    repair_rounds: int = 2
    audit_unclaimed: bool = True

    def extract(self, record_id: str, record_text: str, packet: EvidencePacket) -> Extraction:
        """Run the model over ``record_text`` and parse its reply.

        A reply that stays unreadable through the repair rounds raises
        `ExtractionFormatError` rather than yielding an empty extraction: an empty
        claim list is indistinguishable from a clean record, and a gate that
        passes a write because its audit crashed is worse than no gate.

        The loop terminates because every pass either returns, raises, or spends
        one of the five budgets, so it runs at most ``5 * repair_rounds + 1``
        times.
        """
        prompt = self.build_prompt(record_text, packet)
        unread = self.repair_rounds
        misaddressed = self.repair_rounds
        overbroad = self.repair_rounds
        undeclared = self.repair_rounds
        invented = self.repair_rounds
        while True:
            reply = self.complete(prompt)
            try:
                extraction = parse_extraction(record_id=record_id, model=self.model, reply=reply)
                assert_covers(extraction, record_text)
            except ExtractionFormatError as exc:
                if unread == 0:
                    raise
                unread -= 1
                prompt = self.build_repair_prompt(record_text, packet, reply, str(exc))
                continue
            # An address outside the artifact is the same kind of fault as a
            # skipped line -- the reply is unreadable, not wrong -- so it earns
            # its own re-ask. It differs in what happens when the re-ask fails:
            # the extraction is returned and verified anyway, and the bad address
            # comes out as CITATION_OUT_OF_RANGE on that one claim. Raising here
            # instead would throw away every other claim in the record over one
            # mis-numbered line, which is how a single slip turned a record with
            # 23 resolving citations into no result at all.
            complaint = unresolvable_citations(extraction, packet)
            if complaint is not None and misaddressed > 0:
                misaddressed -= 1
                prompt = self.build_repair_prompt(record_text, packet, reply, complaint)
                continue
            # A citation wider than the cap is the same shape of fault as an
            # address outside the artifact -- unreadable as a citation, silent
            # about whether the claim holds -- and it was the only one of that
            # shape with no re-ask. Measured off-episode: a true claim resting on
            # a 26-line race report was rejected for a rule the model was told
            # once and had no way to check itself. Like the address fault it does
            # not raise; an unrepaired citation is still verified and comes out
            # as CITATION_TOO_BROAD on that one claim.
            complaint = overbroad_citations(extraction, packet)
            if complaint is not None and overbroad > 0:
                overbroad -= 1
                prompt = self.build_repair_prompt(record_text, packet, reply, complaint)
                continue
            # A LITERALS entry with a space in it is dropped from the token check,
            # so the claim is verified against a shorter list than the one the
            # model wrote. Alone among the three faults this one makes the gate
            # more permissive rather than less, which is why it earns a round even
            # though most phrases are descriptions that were filed correctly.
            complaint = phrase_literals(extraction)
            if complaint is not None and undeclared > 0:
                undeclared -= 1
                prompt = self.build_repair_prompt(record_text, packet, reply, complaint)
                continue
            # A literal absent from the record was never asserted by it, so
            # checking it against the evidence rejects a record over a sentence
            # it does not contain. This is the round that undoes a token the
            # model manufactured while answering the complaint above; like the
            # citation faults it does not raise, and an entry that survives the
            # re-ask is verified exactly as it would have been without it.
            complaint = unrecorded_literals(extraction, record_text)
            if complaint is None or invented == 0:
                return self._audit(extraction, record_text, packet)
            invented -= 1
            prompt = self.build_repair_prompt(record_text, packet, reply, complaint)

    def _audit(
        self, extraction: Extraction, record_text: str, packet: EvidencePacket
    ) -> Extraction:
        """Put the set-aside lines back in front of the model, then ask for blocks.

        Two calls, and only for a record that set text-carrying lines aside. The
        first asks which of them assert something -- one bit per line, in a place
        where nothing competes for attention. The second asks for the claim blocks
        those lines needed, with the evidence, so a recovered line can cite what
        supports it instead of being condemned for the first pass's omission.

        Off by default only in tests that drive the parse path with a fixed reply
        and would otherwise need two more. In a run it is on, because a record
        whose skipped lines were never re-read is the case the pass exists for.

        Neither call raises and neither is a repair round. The first pass already
        produced a usable extraction and this is a narrowing on top of it, so a
        failed narrowing leaves the gate where it already was rather than throwing
        away a record's claims over a stray answer. The one thing it will not do
        is drop a line the audit named: if the recovery ask comes back unreadable
        the line becomes an uncited claim and the record is refused, because an
        assertion no citation could be obtained for is what a write gate is for.
        """
        if not self.audit_unclaimed:
            return extraction
        offered = offered_lines(extraction, record_text)
        if not offered:
            return extraction
        try:
            asserting = parse_audit(
                self.complete(build_audit_prompt(record_text, offered)), offered
            )
        except ExtractionFormatError:
            return extraction
        if not asserting:
            return extraction
        return merge(extraction, self._recover(record_text, packet, asserting))

    def _recover(
        self, record_text: str, packet: EvidencePacket, asserting: tuple[int, ...]
    ) -> tuple[Claim, ...]:
        """Ask for the blocks the named lines needed, keeping only those blocks.

        A reply that wanders back over the whole record would re-litigate claims
        already verified, so a block whose SOURCE is not one of the named lines is
        dropped rather than merged. The addresses are the record's own, which is
        why the ask can be checked against them at all.

        A reply with no block for one of the named lines has declined it, and that
        is an answer: the line stays unclaimed. Only a reply that cannot be read at
        all falls back to condemning every named line, because there the model has
        said a line asserts something and then said nothing further, and a write
        gate holding an assertion it could not get a citation for refuses.
        """
        base = self.build_prompt(record_text, packet).removesuffix("=== CLAIMS ===\n")
        reply = self.complete(base + build_recovery_ask(record_text, asserting))
        if declines_every_line(reply):
            return ()
        try:
            claims = parse_extraction(record_id="recovery", model=self.model, reply=reply).claims
        except ExtractionFormatError:
            return uncited_claims(record_text, asserting)
        named = set(asserting)
        return tuple(c for c in claims if c.source_lines and set(c.source_lines) <= named)

    def build_repair_prompt(
        self, record_text: str, packet: EvidencePacket, reply: str, complaint: str
    ) -> str:
        """Re-ask, quoting the model its own reply and the reason it was rejected."""
        return f"{self.build_prompt(record_text, packet)}{reply}\n\n" + _REPAIR.format(
            complaint=complaint
        )

    def build_prompt(self, record_text: str, packet: EvidencePacket) -> str:
        """Assemble instructions, line-numbered evidence, and the proposed record."""
        blocks = [
            f"=== SUPPLIED EVIDENCE: {name} ===\n{packet.numbered(name)}" for name in packet.names()
        ]
        evidence = "\n\n".join(blocks)
        # Naming the artifacts explicitly, rather than only in the instruction
        # template, is what stopped the first live run echoing the placeholder
        # word back as the artifact name.
        citable = "The only names you may cite are: " + ", ".join(packet.names()) + "."
        # The record's length, stated where the model answers rather than left to
        # be counted off the numbering. Told every line must be accounted for and
        # never told where the record ends, this model emitted an UNCLAIMED list
        # that counted to 2662 on a 9-line record and ran the generation into its
        # token cap -- a reply with no closing accounting at all, unrecoverable
        # rather than merely wrong.
        lines = len(record_text.splitlines())
        return (
            f"{_INSTRUCTIONS}\n{citable}\n\n{evidence}\n\n"
            f"=== PROPOSED MEMORY RECORD ({lines} lines, numbered 1-{lines}) ===\n"
            f"{number(record_text)}\n\n"
            "=== CLAIMS ===\n"
        )
