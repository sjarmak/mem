"""ClaimExtractor: prompt assembly and reply handling, with a stub completion.

No daemon runs here. What is under test is the contract between the extractor
and whatever model answers it: the prompt must show the line numbers a citation
is expressed in, describe the block format the parser actually reads, and a reply
the parser cannot read must raise rather than resolve to an empty (and therefore
accepted-looking) extraction.

The scripted-reply tests count model calls, so they set ``audit_unclaimed=False``:
the second pass over the set-aside lines would spend a reply out of the script and
turn every budget assertion into an off-by-one. It is wired in a run and is tested
at the end of this file and in `test_fidelity_unclaimed.py`.
"""

from __future__ import annotations

import pytest

from membench.fidelity.claims import ExtractionFormatError
from membench.fidelity.extract import ClaimExtractor
from membench.fidelity.packet import EvidencePacket
from membench.fidelity.verify import (
    MAX_CITED_LINES,
    UnsupportedReason,
    verify_extraction,
)

PACKET = EvidencePacket(
    artifacts={
        "PRIOR_TASK.md": "Bug filed 2026-07-15.\nTwo catch sites.\n",
        "PRIOR_SOURCE_PACKET.md": "Python: 3.12.6 (CPython)\n177 passed in 0.27s\n",
    },
    digests={"PRIOR_TASK.md": "a" * 64, "PRIOR_SOURCE_PACKET.md": "b" * 64},
)

REPLY = """\
CLAIM: c1
TEXT: The suite passed with 177 tests.
SOURCE: 1
CITATION: PRIOR_SOURCE_PACKET.md 2-2
LITERALS: 177
UNCLAIMED: none
"""

# One line, so a reply covering line 1 accounts for all of it.
RECORD = "The suite passed with 177 tests."


def test_extract_parses_the_reply_and_stamps_the_model() -> None:
    extractor = ClaimExtractor(complete=lambda _: REPLY, model="qwen2.5:14b")
    extraction = extractor.extract("rec", RECORD, PACKET)
    assert extraction.record_id == "rec"
    assert extraction.model == "qwen2.5:14b"
    assert extraction.claims[0].asserted_literals == ("177",)


def test_a_malformed_reply_raises_rather_than_extracting_nothing() -> None:
    """A gate that lets a write through because its audit crashed is worse than
    no gate, and an empty extraction is indistinguishable from a clean record."""
    extractor = ClaimExtractor(complete=lambda _: "the model refused", model="qwen")
    with pytest.raises(ExtractionFormatError):
        extractor.extract("rec", RECORD, PACKET)


def test_the_prompt_carries_every_artifact_with_resolvable_line_numbers() -> None:
    captured: list[str] = []

    def complete(prompt: str) -> str:
        captured.append(prompt)
        return REPLY

    ClaimExtractor(complete=complete, model="qwen").extract("rec", RECORD, PACKET)
    (prompt,) = captured
    for name in PACKET.names():
        assert f"=== SUPPLIED EVIDENCE: {name} ===" in prompt
        assert PACKET.numbered(name) in prompt
    assert "2\t177 passed in 0.27s" in prompt
    assert f"1\t{RECORD}" in prompt


def test_the_prompt_tells_the_model_that_an_absent_citation_is_correct() -> None:
    """Without this the model invents a citation for a background-knowledge claim
    and the gate's most important finding never gets reported."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    assert "CITATION: none" in prompt
    assert "background knowledge" in prompt


def test_the_prompt_specifies_the_format_the_parser_reads() -> None:
    """Prompt and parser are one contract; drift between them shows up as every
    record failing to extract, which reads like a model problem and is not."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    for field in ("CLAIM:", "TEXT:", "CITATION:", "LITERALS:"):
        assert field in prompt


def test_the_prompt_does_not_ask_for_copied_evidence() -> None:
    """The measured failure the format exists to avoid. Asked to reproduce a
    passage verbatim, a local 30B model on this packet emitted 1,400 lines of
    repeated pipe characters; five of nine records in a run produced no parseable
    reply. The citation names the lines and the packet supplies the text."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    instructions = prompt.split("=== SUPPLIED EVIDENCE")[0]
    assert "QUOTE:" not in instructions
    assert "END-QUOTE" not in instructions
    assert "Do not quote the evidence." in instructions


def test_the_prompt_states_the_line_cap_the_verifier_enforces() -> None:
    """A model told nothing about breadth cites whole artifacts, and every such
    claim comes back CITATION_TOO_BROAD -- a gate rejecting on a rule it never
    published. The number is read from the verifier so the two cannot drift."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    assert f"{MAX_CITED_LINES} lines" in prompt


def test_the_prompt_distinguishes_evidence_line_numbers_from_quoted_ones() -> None:
    """Measured: the model cited `236` on a claim about a stack frame at
    retrieval_extraction.py:236, an address inside the evidence text rather than
    an address in the evidence, and the citation resolved out of range."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    assert "printed to the LEFT" in prompt


def test_the_record_appears_after_the_evidence() -> None:
    """The record is the thing under audit; the evidence is the world it is
    checked against. Ordering them the other way invites the model to read the
    evidence as commentary on the record."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    assert prompt.index("=== SUPPLIED EVIDENCE") < prompt.index("=== PROPOSED MEMORY RECORD ")


def test_the_prompt_names_the_artifacts_that_may_be_cited() -> None:
    """The first live run answered `CITATION: ARTIFACT 67-68` -- it echoed the
    template's placeholder. Listing the real names is the fix, and it has to come
    from the packet rather than the instruction text."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    assert "The only names you may cite are: PRIOR_SOURCE_PACKET.md, PRIOR_TASK.md." in prompt


def test_the_prompt_asks_for_source_lines_and_a_skip_list() -> None:
    """The coverage contract has to be stated where the model can read it, or the
    parser rejects every reply for omitting a field nothing asked for."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    assert "SOURCE:" in prompt
    assert "UNCLAIMED:" in prompt
    assert "must appear exactly once" in prompt


def test_a_reply_that_skips_a_record_line_raises() -> None:
    """The defect this whole field exists for. Given a two-line record and a reply
    accounting only for line 1, the extractor refuses rather than reporting the
    remaining line as clean -- which is exactly how a live run accepted two records
    whose version-history sentences were never extracted at all."""
    extractor = ClaimExtractor(complete=lambda _: REPLY, model="qwen")
    with pytest.raises(ExtractionFormatError, match="line\\(s\\) \\[2\\]"):
        extractor.extract("rec", f"{RECORD}\nThe guard applies to Python 3.11+.", PACKET)


TWO_LINE = f"{RECORD}\nThe guard applies to Python 3.11+."

REPAIRED = REPLY.replace("UNCLAIMED: none", "UNCLAIMED: 2")


class _Replies:
    """A completion that answers a scripted sequence and records what it was asked."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies[min(len(self.prompts), len(self.replies)) - 1]


def _extract_only(replies: _Replies) -> ClaimExtractor:
    """An extractor with the second pass off, so the script is the whole budget.

    Pinned to one round per fault rather than the shipped default. These tests
    ask what a budget does when it runs out, and a helper that tracked the
    default would need a longer reply script every time the default moved, which
    is how a budget test quietly stops testing the budget.
    """
    return ClaimExtractor(complete=replies, model="qwen", repair_rounds=1, audit_unclaimed=False)


def test_an_unreadable_reply_is_sent_back_once_and_the_second_answer_is_used() -> None:
    """Measured: this model drops headings and indented code lines from its
    accounting, which 6 of 9 records on the retro set needed a re-ask for. That is
    a well-formedness failure, not a disagreement about the evidence, so it is
    worth one re-ask."""
    replies = _Replies(REPLY, REPAIRED)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", TWO_LINE, PACKET)
    assert extraction.unclaimed_lines == (2,)
    assert len(replies.prompts) == 2


def test_the_repair_prompt_names_the_unaccounted_lines_and_quotes_the_first_reply() -> None:
    """A re-ask that only repeats the original prompt re-rolls the same omission."""
    replies = _Replies(REPLY, REPAIRED)
    _extract_only(replies).extract("rec", TWO_LINE, PACKET)
    _, repair = replies.prompts
    assert "line(s) [2]" in repair
    assert REPLY in repair


def test_the_repair_prompt_asks_for_the_accounting_not_a_different_verdict() -> None:
    """The boundary that keeps a repair round from becoming a retry until the gate
    likes the answer. It may complain about the format; it may not suggest what to
    conclude about the evidence."""
    replies = _Replies(REPLY, REPAIRED)
    _extract_only(replies).extract("rec", TWO_LINE, PACKET)
    _, repair = replies.prompts
    complaint = repair.split("not usable, for this reason:")[1]
    assert "Keep the claims you already made" in complaint
    assert "not in your judgment" in complaint


def test_a_reply_that_is_still_unreadable_after_the_repair_raises() -> None:
    replies = _Replies(REPLY, REPLY)
    extractor = _extract_only(replies)
    with pytest.raises(ExtractionFormatError, match=r"line\(s\) \[2\]"):
        extractor.extract("rec", TWO_LINE, PACKET)
    assert len(replies.prompts) == 2


def test_repair_rounds_zero_raises_on_the_first_reply() -> None:
    replies = _Replies(REPLY, REPAIRED)
    extractor = ClaimExtractor(
        complete=replies, model="qwen", repair_rounds=0, audit_unclaimed=False
    )
    with pytest.raises(ExtractionFormatError):
        extractor.extract("rec", TWO_LINE, PACKET)
    assert len(replies.prompts) == 1


def test_the_prompt_says_indented_quoted_lines_are_record_lines_too() -> None:
    """Measured: on the longest record the model accounted for the sentence
    introducing a pasted stack trace and skipped the indented frames under it,
    leaving 3 lines unaccounted for through a repair round. The lines it dropped
    were quoted output; the instruction now names that case."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r", PACKET)
    assert "indented lines inside a quoted block" in prompt


def test_the_prompt_states_how_many_lines_the_record_has() -> None:
    """Measured, and the reason this is here: told every line must be accounted
    for and never told where the record ends, the model emitted `UNCLAIMED: 2 10
    11 ...` and counted to 2662 on a 9-line record, running the generation into
    its cap. Nothing downstream could recover that reply -- it is truncated, so
    it has no closing accounting at all. The bound is cheap and it is the model's
    only way to know the last number."""
    record = "one\ntwo\nthree\n"
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt(record, PACKET)
    assert "PROPOSED MEMORY RECORD (3 lines, numbered 1-3)" in prompt


def test_the_prompt_forbids_line_numbers_past_the_end_of_the_record() -> None:
    """The count alone is a fact; this is the instruction that uses it. Without
    it the header reads as a description rather than a bound."""
    prompt = ClaimExtractor(complete=lambda _: REPLY, model="qwen").build_prompt("r\n", PACKET)
    assert "There is no line after the last one" in prompt


OUT_OF_RANGE = REPLY.replace("PRIOR_SOURCE_PACKET.md 2-2", "PRIOR_SOURCE_PACKET.md 236")


def test_a_citation_that_addresses_no_line_is_sent_back_once() -> None:
    """An address outside the artifact says nothing about the evidence: the model
    has misread which numbering it is writing in. Measured -- the one supported
    record in the retro set cited `PRIOR_SOURCE_PACKET.md 236` for a stack frame
    at `retrieval_extraction.py:236`, in a 78-line artifact, and that single slip
    was the whole difference between the gate accepting it and rejecting it. Its
    other 23 citations resolved."""
    replies = _Replies(OUT_OF_RANGE, REPLY)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", RECORD, PACKET)
    assert extraction.claims[0].citation[0].line_start == 2
    assert len(replies.prompts) == 2
    assert "PRIOR_SOURCE_PACKET.md 236" in replies.prompts[1]


def test_a_citation_still_unresolvable_after_the_repair_is_returned_as_a_claim() -> None:
    """Not raised. Raising would discard every other claim in the record over one
    mis-numbered line; returning it lets the verifier reject that one claim as
    CITATION_OUT_OF_RANGE and report the rest."""
    replies = _Replies(OUT_OF_RANGE, OUT_OF_RANGE)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", RECORD, PACKET)
    assert extraction.claims[0].citation[0].line_start == 236
    assert len(replies.prompts) == 2


def test_the_repair_prompt_states_the_citation_rule_as_well_as_the_coverage_one() -> None:
    """One repair prompt now answers two kinds of unreadable reply, so it has to
    name both rules -- and neither may say what to conclude about the evidence."""
    extractor = ClaimExtractor(complete=lambda _: REPLY, model="qwen")
    prompt = extractor.build_repair_prompt(RECORD, PACKET, OUT_OF_RANGE, "some complaint")
    assert "names lines that exist in the artifact" in prompt
    assert "not in your judgment" in prompt


REPAIRED_OUT_OF_RANGE = REPAIRED.replace("PRIOR_SOURCE_PACKET.md 2-2", "PRIOR_SOURCE_PACKET.md 236")


def test_a_coverage_repair_does_not_spend_the_citation_repair() -> None:
    """The two faults get separate budgets, and this is the record that proved they
    have to. Sharing one round, `dev03-explicit` spent it on the coverage complaint
    and its `PRIOR_SOURCE_PACKET.md 236` was then returned unrepaired -- the one
    record the citation repair was written for, and the sole accept in the retro
    set. Answering one complaint says nothing about the other."""
    replies = _Replies(REPLY, REPAIRED_OUT_OF_RANGE, REPAIRED)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[0].citation[0].line_start == 2
    assert extraction.unclaimed_lines == (2,)
    assert len(replies.prompts) == 3
    assert "line(s) [2]" in replies.prompts[1]
    assert "PRIOR_SOURCE_PACKET.md 236" in replies.prompts[2]


def test_neither_budget_is_unbounded() -> None:
    """A model that answers every repair with the same fault stops being asked. The
    loop spends one budget per pass, so it cannot outlive the two of them."""
    replies = _Replies(REPLY, REPAIRED_OUT_OF_RANGE, REPAIRED_OUT_OF_RANGE)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[0].citation[0].line_start == 236
    assert len(replies.prompts) == 3


REPAIRED_PHRASE = REPAIRED.replace("LITERALS: 177", "LITERALS: Python 3.11")


def test_a_literal_wearing_a_word_is_sent_back() -> None:
    """`Python 3.11` has a space, so it is filed as a description and the token
    check never runs on it -- a silent accept. `conf-r1` declared exactly that on
    both of its version claims and was rejected for two unrelated sentences
    instead of the version claim the reviewers named."""
    replies = _Replies(REPLY, REPAIRED_PHRASE, REPAIRED)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[0].asserted_literals == ("177",)
    assert extraction.claims[0].descriptive_phrases == ()
    assert len(replies.prompts) == 3
    assert "`Python 3.11`" in replies.prompts[2]


def test_the_phrase_repair_has_its_own_budget() -> None:
    """Counted separately for the same reason the others are: answering one
    complaint says nothing about the others, and a shared round lets the fault that
    surfaces first starve the ones underneath it."""
    replies = _Replies(REPLY, REPAIRED_OUT_OF_RANGE, REPAIRED_PHRASE, REPAIRED)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[0].asserted_literals == ("177",)
    assert len(replies.prompts) == 4
    assert "line(s) [2]" in replies.prompts[1]
    assert "PRIOR_SOURCE_PACKET.md 236" in replies.prompts[2]
    assert "`Python 3.11`" in replies.prompts[3]


# What this model does when told a phrase is not a token: it joins the words.
# `release_train_14` and `CREATE_INDEX_idx_orders_created_at_ON_orders_created_at`
# came back that way on `pg-index-lock`, and both then failed check 5 and rejected
# a record inside its own support boundary.
REPAIRED_INVENTED = REPAIRED.replace("LITERALS: 177", "LITERALS: Python_3.11")


def test_a_literal_the_record_never_wrote_is_sent_back() -> None:
    """A token absent from the record was not asserted by the record.

    Checking it against the evidence answers a question nobody asked, and it can
    only fail, so the record is rejected over a sentence it does not contain.
    """
    replies = _Replies(REPLY, REPAIRED_INVENTED, REPAIRED)
    extraction = _extract_only(replies).extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[0].asserted_literals == ("177",)
    assert len(replies.prompts) == 3
    assert "`Python_3.11`" in replies.prompts[2]
    assert "do not appear anywhere in the record" in replies.prompts[2]


def test_an_invented_literal_that_survives_its_round_is_still_verified() -> None:
    """The round buys a spelling, never a verdict.

    An entry the model refuses to correct is verified exactly as it would have
    been without the round, so this check cannot make the gate more permissive
    than it was -- which is the whole argument for adding it after a measurement
    rather than before one.
    """
    replies = _Replies(REPLY, REPAIRED_INVENTED, REPAIRED_INVENTED)
    extraction = _extract_only(replies).extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[0].asserted_literals == ("Python_3.11",)
    verdicts = verify_extraction(extraction, PACKET)
    assert verdicts[0].reason is UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE


def test_the_invented_literal_repair_has_its_own_budget() -> None:
    """The fault it exists to undo is one the phrase round causes, so a shared
    budget would be spent by the complaint that provokes it every single time."""
    replies = _Replies(REPLY, REPAIRED_PHRASE, REPAIRED_INVENTED, REPAIRED)
    extraction = _extract_only(replies).extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[0].asserted_literals == ("177",)
    assert len(replies.prompts) == 4
    assert "`Python 3.11`" in replies.prompts[2]
    assert "`Python_3.11`" in replies.prompts[3]


def test_the_shipped_default_answers_a_second_incomplete_correction() -> None:
    """Two rounds, and the second is what `nginx-proxy-timeout` needed.

    Named four unaccounted lines, this model claimed three and left the fourth,
    identically in all five draws. One round asks a model that answers
    incompletely to answer completely on its first correction.
    """
    replies = _Replies(REPLY, REPLY, REPAIRED)
    extractor = ClaimExtractor(complete=replies, model="qwen", audit_unclaimed=False)
    extraction = extractor.extract("rec", TWO_LINE, PACKET)
    assert extraction.unclaimed_lines == (2,)
    assert len(replies.prompts) == 3


# An artifact long enough that a citation can be both resolvable and over the
# cap. The two-line packet above cannot express the fault at all: every range
# wide enough to break the cap runs off the end of the artifact and is caught as
# an unresolvable address first.
WIDE_PACKET = EvidencePacket(
    artifacts={
        "PRIOR_SOURCE_PACKET.md": "".join(f"race report line {i}\n" for i in range(1, 31)),
    },
    digests={"PRIOR_SOURCE_PACKET.md": "c" * 64},
)

WIDE = REPLY.replace("PRIOR_SOURCE_PACKET.md 2-2", "PRIOR_SOURCE_PACKET.md 1-26")
NARROWED = REPLY.replace("PRIOR_SOURCE_PACKET.md 2-2", "PRIOR_SOURCE_PACKET.md 1-6")


def test_a_citation_wider_than_the_cap_earns_a_re_ask() -> None:
    """Measured off-episode: a true claim citing a 26-line race report was
    rejected as citation_too_broad, and nothing in the run had told the model the
    block was over. It is the same shape as an address outside the artifact --
    unreadable as a citation, silent about whether the claim holds -- and it was
    the only one of that shape going straight to a rejection."""
    replies = _Replies(WIDE, NARROWED)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", RECORD, WIDE_PACKET)

    assert len(replies.prompts) == 2
    assert "26 lines" in replies.prompts[1]
    assert str(MAX_CITED_LINES) in replies.prompts[1]
    cited = extraction.claims[0].citation[0]
    assert (cited.line_start, cited.line_end) == (1, 6)


def test_the_re_ask_does_not_nominate_a_narrower_passage() -> None:
    """The complaint carries the width and the cap, which are what the model got
    wrong and cannot recover by counting. It must not say where the support is:
    a gate that hands over the answer is measuring its own suggestion."""
    replies = _Replies(WIDE, NARROWED)
    _extract_only(replies).extract("rec", RECORD, WIDE_PACKET)

    _, _, tail = replies.prompts[1].partition("these citations are wider")
    complaint = tail.partition("\n\n")[0]
    assert complaint, "the width complaint is not in the re-ask at all"
    assert "race report line" not in complaint
    assert "1-6" not in complaint


def test_a_citation_still_too_broad_after_its_round_is_returned_not_raised() -> None:
    """Same disposal as an unrepaired address: the extraction is returned and
    verified anyway, and the width comes out as a rejection on that one claim.
    Raising would throw away every other claim in the record over one wide range,
    which is how a record with 23 resolving citations became no result at all."""
    replies = _Replies(WIDE, WIDE)
    extraction = _extract_only(replies).extract("rec", RECORD, WIDE_PACKET)

    assert len(replies.prompts) == 2
    verdict = verify_extraction(extraction, WIDE_PACKET)[0]
    assert verdict.reason is UnsupportedReason.CITATION_TOO_BROAD


def test_the_width_repair_has_its_own_budget() -> None:
    """Four faults now, counted separately for the reason the first three are:
    answering one complaint says nothing about the others, and a shared round lets
    whichever surfaces first starve the ones underneath it."""
    wide_and_phrased = WIDE.replace("LITERALS: 177", "LITERALS: Python 3.11")
    replies = _Replies(wide_and_phrased, wide_and_phrased, NARROWED)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", RECORD, WIDE_PACKET)

    assert len(replies.prompts) == 3
    assert "26 lines" in replies.prompts[1]
    assert "`Python 3.11`" in replies.prompts[2]
    assert extraction.claims[0].asserted_literals == ("177",)


def test_a_phrase_that_survives_its_round_is_returned_not_raised() -> None:
    """The extraction is still worth verifying. A description filed in the wrong
    field is not a reason to throw away every claim in the record, and most phrases
    here are descriptions the model was right to keep out of the token list."""
    replies = _Replies(REPLY, REPAIRED_PHRASE, REPAIRED_PHRASE)
    extractor = _extract_only(replies)
    extraction = extractor.extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[0].descriptive_phrases == ("Python 3.11",)
    assert len(replies.prompts) == 3


RECOVERED = """\
CLAIM: r1
TEXT: The guard applies to Python 3.11+.
SOURCE: 2
CITATION: none
LITERALS: 3.11
UNCLAIMED: none
"""


def test_the_set_aside_lines_go_back_to_the_model_after_a_clean_extraction() -> None:
    """The audit is wired to the success path, not to a repair round. `REPLY` on
    `TWO_LINE` leaves line 2 unaccounted, so the repaired reply files it under
    UNCLAIMED -- and that is exactly the disposal the second pass exists to
    re-open. Two calls: which lines assert something, then the blocks for them."""
    replies = _Replies(REPLY, REPAIRED, "ASSERTING: L2\n", RECOVERED)
    extraction = ClaimExtractor(complete=replies, model="qwen").extract("rec", TWO_LINE, PACKET)
    assert len(replies.prompts) == 4
    assert extraction.unclaimed_lines == ()
    assert extraction.claims[-1].text == "The guard applies to Python 3.11+."
    assert extraction.claims[-1].citation == ()


def test_a_recovered_line_may_cite_evidence_rather_than_being_condemned() -> None:
    """The reason the recovery ask exists. Measured on the go-race-map supported
    record: the audit named four set-aside lines that every one of them sat in the
    supplied evidence, and treating a recovered line as automatically uncited
    rejected a record inside its own support boundary on all four."""
    cited = RECOVERED.replace("CITATION: none", "CITATION: PRIOR_SOURCE_PACKET.md 1-1")
    replies = _Replies(REPLY, REPAIRED, "ASSERTING: L2\n", cited)
    extraction = ClaimExtractor(complete=replies, model="qwen").extract("rec", TWO_LINE, PACKET)
    (citation,) = extraction.claims[-1].citation
    assert (citation.artifact, citation.line_start, citation.line_end) == (
        "PRIOR_SOURCE_PACKET.md",
        1,
        1,
    )


def test_a_recovery_block_for_a_line_nobody_named_is_dropped() -> None:
    """A reply that wanders back over the whole record would re-litigate claims
    already verified. Only the named lines are merged, and line 2, which got no
    block of its own, is left where the first pass put it."""
    stray = RECOVERED.replace("SOURCE: 2", "SOURCE: 1")
    replies = _Replies(REPLY, REPAIRED, "ASSERTING: L2\n", stray)
    extraction = ClaimExtractor(complete=replies, model="qwen").extract("rec", TWO_LINE, PACKET)
    assert [c.claim_id for c in extraction.claims] == ["c1"]
    assert extraction.unclaimed_lines == (2,)


def test_the_recovery_ask_may_decline_a_line_the_naming_ask_offered() -> None:
    """The naming ask over-names. Measured across the three off-episode packets it
    returned five `##` headings, three `Source:` pointers and several of the
    records' own `the evidence does not establish` sentences -- every category it
    is told to leave alone -- and writing those up as uncitable blocks rejected all
    three supported records. The ask holding the evidence gets the last word."""
    declined = "UNCLAIMED: 2\n"
    replies = _Replies(REPLY, REPAIRED, "ASSERTING: L2\n", declined)
    extraction = ClaimExtractor(complete=replies, model="qwen").extract("rec", TWO_LINE, PACKET)
    assert [c.claim_id for c in extraction.claims] == ["c1"]
    assert extraction.unclaimed_lines == (2,)


def test_an_unreadable_recovery_reply_refuses_rather_than_forgets() -> None:
    """The audit has already said the line asserts something. A gate that could
    not obtain a citation for an assertion refuses; it does not quietly drop the
    line back into the skip list it just took it out of."""
    replies = _Replies(REPLY, REPAIRED, "ASSERTING: L2\n", "I could not write that.")
    extraction = ClaimExtractor(complete=replies, model="qwen").extract("rec", TWO_LINE, PACKET)
    assert extraction.claims[-1].claim_id == "unclaimed-L2"
    assert extraction.claims[-1].citation == ()


def test_an_unreadable_audit_reply_leaves_the_extraction_alone() -> None:
    """A failed narrowing must not throw away a record's claims. The first pass
    already produced a usable extraction; the audit is a refinement on top of it,
    so a stray answer costs the refinement and nothing else."""
    replies = _Replies(REPLY, REPAIRED, "I could not decide.\n")
    extraction = ClaimExtractor(complete=replies, model="qwen").extract("rec", TWO_LINE, PACKET)
    assert extraction.unclaimed_lines == (2,)
    assert len(extraction.claims) == 1
