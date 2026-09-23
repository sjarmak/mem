"""parse_extraction: structural validation of a model reply.

The line this file holds: a malformed *shape* raises, an uncited *claim* does
not. An uncited claim is the finding the gate exists to produce, so it has to
survive parsing and reach the verifier.

The block transport exists because the JSON one died on real evidence: a claim
about Python source carries ``"`` and ``\\`` and the model did not escape them.
That failure is pinned below on the TEXT field, which is the only free prose
left in the format now that the model no longer copies evidence.
"""

from __future__ import annotations

import pytest

from membench.fidelity.claims import (
    Extraction,
    ExtractionFormatError,
    assert_covers,
    parse_extraction,
)

CITED = """\
CLAIM: c1
TEXT: The suite passed with 177 tests.
CITATION: PRIOR_SOURCE_PACKET.md 66-78
LITERALS: 177
"""


def test_parses_a_cited_claim() -> None:
    extraction = parse_extraction("rec", "qwen", CITED)
    assert extraction.record_id == "rec"
    assert extraction.model == "qwen"
    (claim,) = extraction.claims
    assert claim.claim_id == "c1"
    assert claim.text == "The suite passed with 177 tests."
    (citation,) = claim.citation
    assert citation.artifact == "PRIOR_SOURCE_PACKET.md"
    assert (citation.line_start, citation.line_end) == (66, 78)
    assert claim.asserted_literals == ("177",)


def test_text_full_of_json_and_backslashes_survives_verbatim() -> None:
    """The defect that killed the JSON transport, as a regression test. A claim
    about `json.loads('{"input_tokens": ...}')` is ordinary here and is exactly
    what a model fails to escape."""
    reply = """\
CLAIM: c1
TEXT: Calling json.loads('{"input_tokens": ' + '9'*5000 + '}') raises \\n a bare ValueError.
CITATION: PRIOR_TASK.md 27
LITERALS: 5000
"""
    (claim,) = parse_extraction("rec", "qwen", reply).claims
    assert claim.text.startswith("Calling json.loads('{\"input_tokens\": ' + '9'*5000 + '}')")
    assert "\\n" in claim.text


def test_a_supplied_quote_block_is_a_hard_failure() -> None:
    """The format asks for citations and nothing else. A model that answers with
    the old QUOTE block must fail loud: silently dropping the lines it did not
    ask for is how a prompt and a parser drift apart unnoticed."""
    reply = "CLAIM: c1\nTEXT: x\nCITATION: PRIOR_TASK.md 1\nQUOTE:\n177 passed\nEND-QUOTE\n"
    with pytest.raises(ExtractionFormatError, match="unrecognized line"):
        parse_extraction("rec", "qwen", reply)


@pytest.mark.parametrize("value", ["none", "None", "null", "-", ""])
def test_an_uncited_claim_parses_rather_than_raising(value: str) -> None:
    reply = f"CLAIM: c1\nTEXT: The guard was added in 3.11.\nCITATION: {value}\n"
    (claim,) = parse_extraction("rec", "qwen", reply).claims
    assert claim.citation == ()


def test_a_claim_with_no_citation_line_at_all_is_uncited() -> None:
    (claim,) = parse_extraction("rec", "qwen", "CLAIM: c1\nTEXT: unsourced.\n").claims
    assert claim.citation == ()


def test_a_single_line_citation_is_a_one_line_range() -> None:
    reply = "CLAIM: c1\nTEXT: x\nCITATION: PRIOR_TASK.md 27\n"
    (citation,) = parse_extraction("rec", "qwen", reply).claims[0].citation
    assert (citation.line_start, citation.line_end) == (27, 27)


def test_several_ranges_on_one_citation_line_all_survive() -> None:
    """Measured, not anticipated: three of nine live replies wrote more than one
    range, and the parser that rejected them threw away the whole extraction over
    a formatting preference. A bare range carries the previous artifact forward,
    which is how a model writes two places in one file."""
    reply = "CLAIM: c1\nTEXT: x\n" "CITATION: PRIOR_TASK.md 38-43, 51, PRIOR_SOURCE_PACKET.md 44\n"
    citation = parse_extraction("rec", "qwen", reply).claims[0].citation
    assert [(c.artifact, c.line_start, c.line_end) for c in citation] == [
        ("PRIOR_TASK.md", 38, 43),
        ("PRIOR_TASK.md", 51, 51),
        ("PRIOR_SOURCE_PACKET.md", 44, 44),
    ]


def test_space_separated_ranges_parse_the_same_way() -> None:
    """One live reply wrote `PRIOR_SOURCE_PACKET.md 67 73 78`. Commas are how the
    prompt asks for it; a reply without them says the same thing."""
    reply = "CLAIM: c1\nTEXT: x\nCITATION: PRIOR_SOURCE_PACKET.md 67 73 78\n"
    citation = parse_extraction("rec", "qwen", reply).claims[0].citation
    assert [c.line_start for c in citation] == [67, 73, 78]


def test_spaces_around_the_range_hyphen_are_tolerated() -> None:
    reply = "CLAIM: c1\nTEXT: x\nCITATION: PRIOR_TASK.md 38 - 43\n"
    (citation,) = parse_extraction("rec", "qwen", reply).claims[0].citation
    assert (citation.line_start, citation.line_end) == (38, 43)


def test_preamble_prose_and_code_fences_are_skipped() -> None:
    reply = f"Here is the audit.\n```\n{CITED}```\n"
    assert len(parse_extraction("rec", "qwen", reply).claims) == 1


def test_multiple_blocks_parse_in_order() -> None:
    reply = CITED + "CLAIM: c2\nTEXT: second claim.\nCITATION: none\n"
    assert [c.claim_id for c in parse_extraction("rec", "qwen", reply).claims] == ["c1", "c2"]


def test_a_blank_claim_id_falls_back_to_position() -> None:
    reply = "CLAIM:\nTEXT: first.\nCLAIM:\nTEXT: second.\n"
    assert [c.claim_id for c in parse_extraction("rec", "qwen", reply).claims] == [
        "claim-0",
        "claim-1",
    ]


@pytest.mark.parametrize(
    "reply,match",
    [
        ("the record looks fine to me", "contains no CLAIM"),
        ("CLAIM: c1\nCITATION: none\n", "has no TEXT"),
        ("CLAIM: c1\nTEXT:    \n", "has no TEXT"),
        ("CLAIM: c1\nTEXT: x\nCITATION: PRIOR_TASK.md\n", "not 'ARTIFACT start-end'"),
        ("CLAIM: c1\nTEXT: x\nCITATION: 20-30\n", "with no artifact name"),
        ("CLAIM: c1\nTEXT: x\nCONFIDENCE: high\n", "unrecognized line"),
        ("CLAIM: c1\nTEXT: x\nand also I think\n", "unrecognized line"),
    ],
)
def test_malformed_replies_raise(reply: str, match: str) -> None:
    with pytest.raises(ExtractionFormatError, match=match):
        parse_extraction("rec", "qwen", reply)


def test_literals_are_split_stripped_and_blank_dropped() -> None:
    reply = "CLAIM: c1\nTEXT: x\nLITERALS: 4300 , 3.11,, RecursionError\n"
    assert parse_extraction("rec", "qwen", reply).claims[0].asserted_literals == (
        "4300",
        "3.11",
        "RecursionError",
    )


@pytest.mark.parametrize("value", ["none", "", "-"])
def test_literals_none_forms_are_empty(value: str) -> None:
    reply = f"CLAIM: c1\nTEXT: x\nLITERALS: {value}\n"
    assert parse_extraction("rec", "qwen", reply).claims[0].asserted_literals == ()


SOURCED = """\
CLAIM: c1
TEXT: The suite passed with 177 tests.
SOURCE: 3-4
CITATION: PRIOR_SOURCE_PACKET.md 66-78
LITERALS: 177
UNCLAIMED: 1, 2
"""


def test_source_lines_and_the_unclaimed_trailer_parse() -> None:
    extraction = parse_extraction("rec", "qwen", SOURCED)
    (claim,) = extraction.claims
    assert claim.source_lines == (3, 4)
    assert extraction.unclaimed_lines == (1, 2)


def test_the_unclaimed_trailer_closes_the_last_block() -> None:
    """It is a reply-level field, not a claim field: read as part of the block it
    would put a bare line-number list where an artifact citation belongs."""
    extraction = parse_extraction("rec", "qwen", SOURCED)
    assert len(extraction.claims) == 1
    assert extraction.claims[0].citation[0].artifact == "PRIOR_SOURCE_PACKET.md"


@pytest.mark.parametrize("value", ["none", "None", "-", ""])
def test_an_empty_unclaimed_list_parses_as_no_skipped_lines(value: str) -> None:
    assert parse_extraction("rec", "qwen", CITED + f"UNCLAIMED: {value}\n").unclaimed_lines == ()


def test_a_claim_with_no_source_line_has_no_source_lines() -> None:
    """Parsing stays structural: an absent SOURCE is a coverage finding, which is
    `assert_covers`'s to report against the actual record, not the parser's."""
    assert parse_extraction("rec", "qwen", CITED).claims[0].source_lines == ()


@pytest.mark.parametrize("value", ["one to three", "3-", "PRIOR_TASK.md 3"])
def test_an_unreadable_line_list_raises(value: str) -> None:
    """Discarding a mangled list silently would make it indistinguishable from an
    honest `none`, and `none` is what lets a line go unaccounted for."""
    with pytest.raises(ExtractionFormatError):
        parse_extraction("rec", "qwen", CITED + f"UNCLAIMED: {value}\n")


RECORD = "# Heading\nThe suite passed.\n\nThe guard applies to Python 3.11+.\n"


def _covering(*, source: str, unclaimed: str) -> Extraction:
    reply = (
        "CLAIM: c1\nTEXT: t\n"
        f"SOURCE: {source}\nCITATION: none\nLITERALS: none\n"
        f"UNCLAIMED: {unclaimed}\n"
    )
    return parse_extraction("rec", "qwen", reply)


def test_assert_covers_accepts_an_extraction_that_accounts_for_every_line() -> None:
    assert_covers(_covering(source="2, 4", unclaimed="1"), RECORD)


def test_assert_covers_rejects_a_dropped_line_and_names_it() -> None:
    """The defect this exists for. Line 4 is the version sentence, which is exactly
    the kind the model omitted when omission cost nothing."""
    with pytest.raises(ExtractionFormatError, match=r"line\(s\) \[4\]"):
        assert_covers(_covering(source="2", unclaimed="1"), RECORD)


def test_assert_covers_quotes_the_dropped_line_so_the_omission_is_readable() -> None:
    with pytest.raises(ExtractionFormatError, match=r"Python 3\.11"):
        assert_covers(_covering(source="2", unclaimed="1"), RECORD)


def test_blank_lines_need_no_account() -> None:
    """Line 3 is blank and is covered by nobody. Requiring it would make every
    reply carry a list of nothings, which is noise a reviewer has to read past."""
    assert_covers(_covering(source="2, 4", unclaimed="1"), RECORD)


def test_a_line_number_past_the_end_of_the_record_is_ignored() -> None:
    """Measured: told to account for every line, this model pads its skip list to
    the length of the evidence -- 8 through 82 on a 7-line record. Rejecting that
    cost three of nine records and protected nothing, because a number outside the
    record can never make a line inside it look covered."""
    assert_covers(_covering(source="2, 4", unclaimed="1, 9, 82"), RECORD)


def test_declaring_a_real_assertion_unclaimed_still_passes_the_accounting() -> None:
    """The stated limit of the accounting, pinned so it is not mistaken for a proof.
    Line 4 is a factual claim; a model that lists it under UNCLAIMED satisfies the
    line count. What this rule buys is that the omission is now a statement in the
    reply rather than an absence. Catching it needs a reader, or the second pass in
    `unclaimed.py`, and neither is this function."""
    assert_covers(_covering(source="2", unclaimed="1, 4"), RECORD)


def test_a_line_both_claimed_and_declared_unclaimed_is_tolerated() -> None:
    """The other half of "exactly once", left unenforced on purpose, so the next
    reader does not re-derive the check that was already written and measured.

    Line 2 is here read as a claim and also listed as asserting nothing. Rejecting
    that looks free -- the prompt does ask for each line once -- and it is not:
    this model pads its skip list with lines it has already claimed on 5 of the 7
    retro records, `conf-r2` on 25 of them. It also costs no coverage. The line is
    in a claim's SOURCE, so the claim was built, cited and verified; the redundant
    skip entry disposes of nothing. A repair round spent on it is a round not spent
    on a fault that loses something. The lines that do escape are the ones listed
    *only* as unclaimed, and `unclaimed.audit` is what looks at those."""
    assert_covers(_covering(source="2, 4", unclaimed="1, 2"), RECORD)


def test_a_multi_word_entry_is_kept_as_a_phrase_not_a_checkable_literal() -> None:
    """The prompt says a literal is one token with no spaces. Measured: 19 of 257
    entries on the retro set were phrases, and checking them verbatim rejected a
    record whose unsupported sentences had been removed -- a false reject produced
    by re-introducing the quote matching this design dropped."""
    reply = "CLAIM: c1\nTEXT: x\nLITERALS: 3.11, independent verification, 4300\n"
    (claim,) = parse_extraction("rec", "qwen", reply).claims
    assert claim.asserted_literals == ("3.11", "4300")
    assert claim.descriptive_phrases == ("independent verification",)


def test_a_token_buried_in_a_phrase_escapes_the_literal_check() -> None:
    """The stated hole, pinned so it is not mistaken for a guarantee. `3.11` here
    is never checked against the evidence. The phrase is recorded rather than
    dropped, which is the whole of what code can do about it."""
    reply = "CLAIM: c1\nTEXT: x\nLITERALS: CPython 3.11 guard\n"
    (claim,) = parse_extraction("rec", "qwen", reply).claims
    assert claim.asserted_literals == ()
    assert claim.descriptive_phrases == ("CPython 3.11 guard",)


def test_a_citation_is_read_in_the_form_the_prompt_prints() -> None:
    """`L66-L78` is what the numbering shows and what a citation should say."""
    reply = "CLAIM: c1\nTEXT: x\nCITATION: PRIOR_TASK.md L2-L4\n"
    (citation,) = parse_extraction("rec", "qwen", reply).claims[0].citation
    assert (citation.artifact, citation.line_start, citation.line_end) == ("PRIOR_TASK.md", 2, 4)


def test_a_bare_number_is_still_read_as_an_address() -> None:
    """Refusing it would turn a transcription slip into an unreadable reply, when
    the number can be checked against the artifact and rejected on its merits."""
    reply = "CLAIM: c1\nTEXT: x\nCITATION: PRIOR_TASK.md 2\n"
    (citation,) = parse_extraction("rec", "qwen", reply).claims[0].citation
    assert (citation.line_start, citation.line_end) == (2, 2)
