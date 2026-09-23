"""The second pass over the lines the first pass set aside.

The record here is the shape the hole was measured in: some real structure that
genuinely asserts nothing, and one factual sentence filed alongside it.
"""

from __future__ import annotations

import pytest

from membench.fidelity.claims import Claim, Extraction, ExtractionFormatError
from membench.fidelity.unclaimed import (
    audit,
    build_audit_prompt,
    declines_every_line,
    parse_audit,
)

RECORD = (
    "# Worker pool race\n"  # 1
    "The write at pool/collect.go:88 is unguarded.\n"  # 2
    "\n"  # 3
    "Source: PRIOR_SOURCE_PACKET.md, race report.\n"  # 4
    "sync.Map was added in Go 1.9 and would remove this race.\n"  # 5
)


def _extraction(unclaimed: tuple[int, ...]) -> Extraction:
    return Extraction(
        record_id="rec",
        model="qwen",
        claims=(Claim(claim_id="c1", text="unguarded write", citation=(), source_lines=(2,)),),
        unclaimed_lines=unclaimed,
    )


def test_a_line_the_model_names_becomes_an_uncited_claim() -> None:
    """The measured hole, closed in the direction it failed: the injected sentence
    was set aside, and it comes back as a claim resting on nothing."""
    result = audit(_extraction((1, 4, 5)), RECORD, lambda _: "ASSERTING: L5\n")
    recovered = [c for c in result.claims if c.claim_id == "unclaimed-L5"]
    assert len(recovered) == 1
    assert recovered[0].citation == ()
    assert "Go 1.9" in recovered[0].text


def test_the_recovered_claim_carries_the_record_line_verbatim() -> None:
    """Asking for a paraphrase would give the sentence a second chance to shed the
    token it commits to, which is the silent under-claiming this design already
    measured once. The line is quoted instead."""
    result = audit(_extraction((5,)), RECORD, lambda _: "ASSERTING: L5\n")
    assert result.claims[-1].text == "sync.Map was added in Go 1.9 and would remove this race."


def test_a_recovered_line_stops_being_unclaimed() -> None:
    """It cannot be both, and `assert_covers` now says so."""
    result = audit(_extraction((1, 4, 5)), RECORD, lambda _: "ASSERTING: L5\n")
    assert result.unclaimed_lines == (1, 4)


def test_structure_the_model_leaves_alone_stays_unclaimed() -> None:
    """A heading and a source pointer assert nothing, and a pass that recovered
    them would reject every record for its own formatting."""
    result = audit(_extraction((1, 4)), RECORD, lambda _: "ASSERTING: none\n")
    assert result.claims == _extraction((1, 4)).claims
    assert result.unclaimed_lines == (1, 4)


def test_a_record_with_nothing_set_aside_costs_no_call() -> None:
    def _refuse(prompt: str) -> str:
        raise AssertionError("the audit asked about an empty list")

    assert audit(_extraction(()), RECORD, _refuse).claims == _extraction(()).claims


def test_a_blank_line_is_not_worth_asking_about() -> None:
    """Line 3 is empty and is unclaimed for free. Showing it would ask the model
    whether nothing asserts something."""
    calls: list[str] = []

    def _record(prompt: str) -> str:
        calls.append(prompt)
        return "ASSERTING: none\n"

    audit(_extraction((3,)), RECORD, _record)
    assert calls == []


def test_the_prompt_addresses_lines_by_their_record_numbers() -> None:
    """A renumbered excerpt would be a third addressing scheme, and the reply's
    addresses have to be checkable against the extraction that produced them."""
    shown = build_audit_prompt(RECORD, (4, 5)).split("=== LINES SET ASIDE ===")[1]
    assert "L4\tSource: PRIOR_SOURCE_PACKET.md, race report." in shown
    assert "L5\tsync.Map was added in Go 1.9" in shown
    assert "unguarded" not in shown


def test_an_address_that_was_never_offered_is_dropped() -> None:
    """The model is answering a yes/no question about a handful of lines; a stray
    number is not worth spending the record on. Same reasoning as the skip list
    padded past the end of the record."""
    assert parse_audit("ASSERTING: L5 L99\n", offered=(4, 5)) == (5,)


def test_a_reply_with_no_answer_line_is_a_format_fault() -> None:
    """An empty result and 'none of them assert anything' are the same value, so a
    reply that says neither must not be read as the second."""
    with pytest.raises(ExtractionFormatError):
        parse_audit("I think line 5 is a claim.\n", offered=(5,))


@pytest.mark.parametrize("value", ["none", "None", "-", ""])
def test_the_ways_a_model_says_nothing_here(value: str) -> None:
    assert parse_audit(f"ASSERTING: {value}\n", offered=(4, 5)) == ()


def test_bare_and_prefixed_addresses_both_parse() -> None:
    """`L5` is what the prompt prints; a bare `5` is a transcription slip, and
    refusing it would turn that into an unreadable reply for no gain."""
    assert parse_audit("ASSERTING: 4, L5\n", offered=(4, 5)) == (4, 5)


def test_an_answer_with_the_label_dropped_is_still_an_answer() -> None:
    """Measured on the go-race-map lure: the audit answered `L8 L9 L18 L28` -- four
    set-aside lines, one of them the injected sentence -- and requiring the label
    discarded all four, so the lure was accepted for a formatting reason. The
    addresses are the answer; the prefix is decoration."""
    assert parse_audit("L4 L5\n", offered=(4, 5)) == (4, 5)


def test_prose_without_the_label_is_still_a_format_fault() -> None:
    """The widening has to stop where reasoning-out-loud starts, or a model
    thinking about line 5 becomes a model naming it."""
    with pytest.raises(ExtractionFormatError):
        parse_audit("Line 5 asserts something about Go.\n", offered=(4, 5))


def test_a_bare_none_with_no_label_reads_as_none() -> None:
    assert parse_audit("none\n", offered=(4, 5)) == ()


def test_the_labelled_line_wins_over_a_stray_number_above_it() -> None:
    """A reply that reasons first and answers last must be read by its answer."""
    assert parse_audit("4\nASSERTING: L5\n", offered=(4, 5)) == (5,)


def test_a_reply_that_writes_no_block_and_closes_the_accounting_is_a_decline() -> None:
    """The one reply shape the ordinary parser refuses is the one a full decline
    takes, so it has to be recognised before parsing. Otherwise the answer "none of
    these assert anything after all" arrives as unreadable and condemns every line
    it was asked about."""
    assert declines_every_line("UNCLAIMED: 4 5\n")


def test_a_reply_that_wrote_blocks_is_not_a_decline() -> None:
    assert not declines_every_line("CLAIM: r1\nTEXT: x\nSOURCE: 5\nUNCLAIMED: 4\n")


def test_a_reply_that_answers_nothing_at_all_is_not_a_decline() -> None:
    """A model that wandered off has not declined anything; it has failed to
    answer, and that falls through to the fallback that refuses the write."""
    assert not declines_every_line("I do not think any of these are claims.\n")
