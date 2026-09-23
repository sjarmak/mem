"""verify_claim: the five mechanical checks, one test per failure mode.

The packet here mirrors the campaign's decisive property: it establishes Python
3.12.6 and nothing about 3.11. Every check is exercised against real-shaped text
rather than placeholders, so a claim that passes is one that could have been made.

Check 5 reads its haystack from the cited artifact, not the cited lines, for the
reason `verify`'s docstring records. Two tests below pin what that costs rather
than leaving it as prose: a claim pointing at the wrong passage of the right file
passes, and it is a test that asserts the retreat, so narrowing the rule again
will red it and say so.
"""

from __future__ import annotations

import pytest

from membench.fidelity.claims import Citation, Claim, Extraction
from membench.fidelity.packet import EvidencePacket
from membench.fidelity.verify import (
    MAX_CITED_LINES,
    UnsupportedReason,
    fold,
    normalize,
    overbroad_citations,
    phrase_literals,
    unrecorded_literals,
    unresolvable_citations,
    verify_claim,
    verify_extraction,
)

PACKET_TEXT = (
    "# Executed evidence\n"  # 1
    "Python: 3.12.6 (CPython); pytest 8.4.2\n"  # 2
    "E RecursionError: maximum recursion depth exceeded\n"  # 3
    "Result: 4 failed, 173 deselected in 0.43s\n"  # 4
    "177 passed in 0.27s\n"  # 5
)

# Long enough that a whole-artifact citation exceeds the cap, so the breadth
# check can be exercised against a range that really exists.
WIDE_TEXT = PACKET_TEXT + "".join(f"trace frame {i}\n" for i in range(6, 41))

TASK_TEXT = (
    "# Task\n"  # 1
    "Fix the parser so scripts/cost_tracker.py stops raising.\n"  # 2
)


@pytest.fixture
def packet() -> EvidencePacket:
    return EvidencePacket(
        artifacts={"PRIOR_SOURCE_PACKET.md": PACKET_TEXT},
        digests={"PRIOR_SOURCE_PACKET.md": "0" * 64},
    )


@pytest.fixture
def two_artifact_packet() -> EvidencePacket:
    return EvidencePacket(
        artifacts={"PRIOR_SOURCE_PACKET.md": PACKET_TEXT, "PRIOR_TASK.md": TASK_TEXT},
        digests={"PRIOR_SOURCE_PACKET.md": "0" * 64, "PRIOR_TASK.md": "1" * 64},
    )


@pytest.fixture
def wide_packet() -> EvidencePacket:
    return EvidencePacket(
        artifacts={"PRIOR_SOURCE_PACKET.md": WIDE_TEXT},
        digests={"PRIOR_SOURCE_PACKET.md": "0" * 64},
    )


def _claim(
    *,
    text: str = "The suite passed with 177 tests.",
    artifact: str | None = "PRIOR_SOURCE_PACKET.md",
    line_start: int = 5,
    line_end: int = 5,
    literals: tuple[str, ...] = ("177",),
) -> Claim:
    citation = () if artifact is None else (Citation(artifact, line_start, line_end),)
    return Claim(claim_id="c1", text=text, citation=citation, asserted_literals=literals)


def test_a_cited_claim_whose_literals_appear_in_that_artifact_is_supported(
    packet: EvidencePacket,
) -> None:
    verdict = verify_claim(_claim(), packet)
    assert verdict.supported
    assert verdict.reason is None


def test_no_citation(packet: EvidencePacket) -> None:
    verdict = verify_claim(_claim(artifact=None), packet)
    assert verdict.reason is UnsupportedReason.NO_CITATION


def test_unknown_artifact(packet: EvidencePacket) -> None:
    verdict = verify_claim(_claim(artifact="CPYTHON_CHANGELOG.md"), packet)
    assert verdict.reason is UnsupportedReason.UNKNOWN_ARTIFACT
    assert "PRIOR_SOURCE_PACKET.md" in verdict.detail


def test_citation_out_of_range(packet: EvidencePacket) -> None:
    verdict = verify_claim(_claim(line_start=40, line_end=44), packet)
    assert verdict.reason is UnsupportedReason.CITATION_OUT_OF_RANGE
    assert "outside 1-5" in verdict.detail


def test_a_citation_pointing_at_the_wrong_lines_of_the_right_file_passes(
    packet: EvidencePacket,
) -> None:
    """What the artifact-scoped rule gives up, pinned as a test rather than prose.

    Line 2 is the Python version and carries no 177; the token is four lines
    further down. Under a span-scoped rule this claim is rejected, and that rule
    was tried and abandoned because this model's line numbers are routinely off by
    a few lines on wrapped terminal text -- it rejected the retro set's one
    supported record too. So a claim can point a reader at the wrong passage of
    the right file and still pass. Narrowing the rule again should red this test,
    which is the point of writing it in this direction.
    """
    assert verify_claim(_claim(line_start=2, line_end=2), packet).supported


def test_a_citation_wide_enough_to_be_no_citation_fails(wide_packet: EvidencePacket) -> None:
    """The one mechanical escape left once the model no longer supplies the text:
    name a range big enough that the literals fall inside it somewhere."""
    verdict = verify_claim(_claim(line_start=1, line_end=MAX_CITED_LINES + 1), wide_packet)
    assert verdict.reason is UnsupportedReason.CITATION_TOO_BROAD
    assert str(MAX_CITED_LINES + 1) in verdict.detail


def test_breadth_is_summed_across_ranges(wide_packet: EvidencePacket) -> None:
    """Splitting one over-wide citation into two narrow-looking halves buys nothing."""
    half = MAX_CITED_LINES // 2 + 1
    claim = Claim(
        claim_id="c1",
        text="The suite passed with 177 tests.",
        citation=(
            Citation("PRIOR_SOURCE_PACKET.md", 1, half),
            Citation("PRIOR_SOURCE_PACKET.md", half + 1, half * 2),
        ),
        asserted_literals=("177",),
    )
    assert verify_claim(claim, wide_packet).reason is UnsupportedReason.CITATION_TOO_BROAD


def test_a_citation_at_the_cap_is_still_a_citation(wide_packet: EvidencePacket) -> None:
    """The boundary is inclusive; without this the cap could drift by one unnoticed."""
    verdict = verify_claim(_claim(line_start=1, line_end=MAX_CITED_LINES), wide_packet)
    assert verdict.supported


def test_the_campaign_failure_shape_is_caught(packet: EvidencePacket) -> None:
    """The 6 rejected records asserted 3.11 while their support established 3.12.6.
    The citation resolves; the literal the claim commits to is not in those lines."""
    verdict = verify_claim(
        _claim(
            text="The integer guard applies to CPython 3.11 and later.",
            line_start=2,
            line_end=2,
            literals=("3.11",),
        ),
        packet,
    )
    assert verdict.reason is UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE
    assert "3.11" in verdict.detail


def test_a_claim_asserting_no_literals_rides_on_the_citation_alone(
    packet: EvidencePacket,
) -> None:
    """Stated limitation: a generalization that commits to no checkable token and
    cites a real, narrow passage passes. The literal check is a filter, not a proof."""
    verdict = verify_claim(_claim(text="Deep nesting is generally a problem.", literals=()), packet)
    assert verdict.supported


def test_a_literal_reflowed_across_a_line_break_still_matches(packet: EvidencePacket) -> None:
    """The evidence is hard-wrapped terminal output and an agent re-typing an
    excerpt reflows it, so whitespace runs carry no meaning on either side."""
    reflowed = verify_claim(
        _claim(line_start=4, line_end=5, literals=("0.43s 177 passed",)), packet
    )
    assert reflowed.supported


def test_a_literal_differing_only_in_case_matches(packet: EvidencePacket) -> None:
    """Measured off-episode: a claim reading `reads` against evidence reading a
    sentence-initial `Reads` was rejected, and the record was inside its own
    support boundary. Capitalization is a property of where a word sits in a
    sentence, not of whether the evidence establishes it."""
    assert verify_claim(_claim(literals=("177 PASSED",)), packet).supported
    lowered = verify_claim(_claim(literals=("recursionerror",), line_start=3, line_end=3), packet)
    assert lowered.supported


def test_case_folding_does_not_reach_the_tokens_that_do_the_work(
    packet: EvidencePacket,
) -> None:
    """What the fold gives up is bounded by what it can touch. Every burial this
    corpus is made of -- `3.11`, `Phase 4`, `177 passed` -- turns on a digit, and
    a digit has no case, so folding cannot make one of them match."""
    verdict = verify_claim(_claim(literals=("3.11",)), packet)
    assert verdict.reason is UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE


def test_a_word_the_evidence_does_not_carry_at_all_still_fails(packet: EvidencePacket) -> None:
    """The fold widens the haystack by case alone, not by meaning: an alphabetic
    token absent from the artifact in every casing is still absent."""
    verdict = verify_claim(_claim(literals=("CONCURRENTLY",)), packet)
    assert verdict.reason is UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE


def test_normalize_collapses_runs_and_strips() -> None:
    assert normalize("  a\n\t b  ") == "a b"


def test_normalize_leaves_case_alone_for_every_other_caller() -> None:
    """`fold` is the comparison used by check 5, not a new contract for
    `normalize`. Rendering and reporting still see the text as it was written."""
    assert normalize("Reads Were Unaffected") == "Reads Were Unaffected"
    assert fold("Reads Were Unaffected") == "reads were unaffected"


def test_verify_extraction_preserves_claim_order(packet: EvidencePacket) -> None:
    extraction = Extraction(
        record_id="rec",
        model="qwen",
        claims=(_claim(), Claim("c2", "uncited", (), ()), _claim(text="third")),
    )
    verdicts = verify_extraction(extraction, packet)
    assert [v.claim_id for v in verdicts] == ["c1", "c2", "c1"]
    assert [v.supported for v in verdicts] == [True, False, True]


def test_literals_may_be_spread_across_the_cited_artifacts(
    two_artifact_packet: EvidencePacket,
) -> None:
    """A claim resting on two artifacts is supported by their union, so a literal
    carried by either one counts. This is why the haystack is a join and not a
    per-citation loop: a symptom in one artifact and the path it points at in
    another is one claim, and checking each range alone would reject it."""
    claim = Claim(
        claim_id="c1",
        text="scripts/cost_tracker.py raised RecursionError.",
        citation=(
            Citation("PRIOR_TASK.md", 2, 2),
            Citation("PRIOR_SOURCE_PACKET.md", 3, 3),
        ),
        asserted_literals=("scripts/cost_tracker.py", "RecursionError"),
    )
    assert verify_claim(claim, two_artifact_packet).supported


def test_a_literal_carried_only_by_an_uncited_artifact_fails(
    two_artifact_packet: EvidencePacket,
) -> None:
    """The scope retreat stopped at the artifact, not at the packet. A claim citing
    the task file cannot borrow a token that only the source packet establishes,
    so the citation still has to name the right file to be worth anything."""
    claim = Claim(
        claim_id="c1",
        text="The suite passed with 177 tests.",
        citation=(Citation("PRIOR_TASK.md", 2, 2),),
        asserted_literals=("177",),
    )
    verdict = verify_claim(claim, two_artifact_packet)
    assert verdict.reason is UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE
    assert "PRIOR_TASK.md" in verdict.detail


def test_an_unreal_artifact_in_a_second_range_still_fails(packet: EvidencePacket) -> None:
    """Every cited range is checked, not just the one that happens to resolve."""
    claim = Claim(
        claim_id="c1",
        text="The suite passed with 177 tests.",
        citation=(
            Citation("PRIOR_SOURCE_PACKET.md", 5, 5),
            Citation("CPYTHON_CHANGELOG.md", 1, 1),
        ),
        asserted_literals=("177",),
    )
    assert verify_claim(claim, packet).reason is UnsupportedReason.UNKNOWN_ARTIFACT


def _one(claim: Claim) -> Extraction:
    return Extraction(record_id="rec", model="stub", claims=(claim,), unclaimed_lines=())


def test_a_citation_inside_the_artifact_is_not_reported_as_unresolvable(
    packet: EvidencePacket,
) -> None:
    assert unresolvable_citations(_one(_claim()), packet) is None


def test_an_address_past_the_end_of_the_artifact_is_reported_with_its_size(
    packet: EvidencePacket,
) -> None:
    """The complaint carries the line count because that is the fact the model got
    wrong: it wrote a number it read inside the evidence text as if it were a
    number printed beside it. Measured on the retro set -- `PRIOR_SOURCE_PACKET.md
    236` for a stack frame at `retrieval_extraction.py:236`, in a 78-line file."""
    complaint = unresolvable_citations(_one(_claim(line_start=236, line_end=236)), packet)
    assert complaint is not None
    assert "PRIOR_SOURCE_PACKET.md 236" in complaint
    assert "PRIOR_SOURCE_PACKET.md has 5 lines" in complaint


def test_an_unknown_artifact_is_left_to_the_verdict_rather_than_the_complaint(
    packet: EvidencePacket,
) -> None:
    """A citation naming a file that is not in the packet is a different failure
    with a different answer: there is no line count to correct, and the claim rests
    on evidence the agent was never given. Re-asking would invite the model to
    reattach the claim to whatever artifact is at hand, so it stays a verdict."""
    claim = _claim(artifact="CPYTHON_CHANGELOG.md")
    assert unresolvable_citations(_one(claim), packet) is None
    assert verify_claim(claim, packet).reason is UnsupportedReason.UNKNOWN_ARTIFACT


def test_a_token_list_of_tokens_draws_no_complaint() -> None:
    """Nothing to re-ask about: every entry is checkable as written."""
    assert phrase_literals(_one(_claim(literals=("177", "3.12.6")))) is None


def test_a_literal_with_a_space_is_reported() -> None:
    """`Python 3.11` is filed as a description and dropped from the token check,
    which is the one fault of the three that makes the gate more permissive. The
    complaint names the entry and says only what is mechanically true about it."""
    claim = Claim(
        claim_id="c1",
        text="The guard applies to Python 3.11+.",
        citation=(Citation("PRIOR_SOURCE_PACKET.md", 5, 5),),
        asserted_literals=(),
        descriptive_phrases=("Python 3.11",),
    )
    complaint = phrase_literals(_one(claim))
    assert complaint is not None
    assert "`Python 3.11`" in complaint
    assert "drop it" in complaint


def test_a_phrase_with_no_digit_in_it_is_left_alone() -> None:
    """The narrowing that a verdict paid for. Asked about every phrase, the model
    snake-cased the descriptions instead of dropping them -- `reader clarity` came
    back `reader_clarity`, a token that appears in no artifact -- and the stripped
    record, which must accept, rejected on three such inventions. Prose carries no
    digit, so the complaint never reaches it."""
    claim = Claim(
        claim_id="c1",
        text="The fix was kept for reader clarity.",
        citation=(Citation("PRIOR_SOURCE_PACKET.md", 5, 5),),
        asserted_literals=(),
        descriptive_phrases=("reader clarity", "not a security issue"),
    )
    assert phrase_literals(_one(claim)) is None


def test_the_complaint_does_not_say_which_half_is_the_token() -> None:
    """Which word of a reported entry the claim commits to is a reading of the
    claim, and the code has no business deciding it. Splitting on whitespace and
    checking every word would resurrect the quote-matching this design dropped."""
    claim = Claim(
        claim_id="c1",
        text="The suite reported 177 passed.",
        citation=(Citation("PRIOR_SOURCE_PACKET.md", 5, 5),),
        asserted_literals=(),
        descriptive_phrases=("177 passed",),
    )
    complaint = phrase_literals(_one(claim))
    assert complaint is not None
    assert "passed" not in complaint.replace("`177 passed`", "")


def test_the_complaint_says_not_to_invent_a_token() -> None:
    """`reader_clarity` is what a description becomes when the model is told to
    rewrite it and takes that literally."""
    claim = Claim(
        claim_id="c1",
        text="The guard applies to Python 3.11+.",
        citation=(Citation("PRIOR_SOURCE_PACKET.md", 5, 5),),
        asserted_literals=(),
        descriptive_phrases=("Python 3.11",),
    )
    complaint = phrase_literals(_one(claim))
    assert complaint is not None
    assert "`reader_clarity`" in complaint


def test_the_complaint_shows_the_corrected_form_with_its_separator() -> None:
    """Leaving the separator implicit cost a verdict. Told to "write each as the
    single token", the model answered `LITERALS: 4300 CVE-2020-10735 3.11` -- one
    entry with spaces in it, dropped again, and `dev03-current` came back an accept
    with its version claim unchecked."""
    claim = Claim(
        claim_id="c1",
        text="The guard applies to Python 3.11+.",
        citation=(Citation("PRIOR_SOURCE_PACKET.md", 5, 5),),
        asserted_literals=(),
        descriptive_phrases=("Python 3.11",),
    )
    complaint = phrase_literals(_one(claim))
    assert complaint is not None
    assert "comma-separated" in complaint
    assert "`LITERALS: 3.11, 4300`" in complaint


def test_a_citation_within_the_cap_is_not_reported_as_overbroad(
    wide_packet: EvidencePacket,
) -> None:
    """The complaint builder is silent on a record with nothing to complain about,
    which is what lets the extractor use it as a branch rather than a filter."""
    extraction = Extraction(record_id="r", model="m", claims=(_claim(),))
    assert overbroad_citations(extraction, wide_packet) is None


def test_a_citation_over_the_cap_is_reported_with_its_width(
    wide_packet: EvidencePacket,
) -> None:
    """The width and the cap are what the model got wrong and cannot recover by
    counting, so they are what the complaint carries. Measured off-episode: a true
    claim on a 26-line race report, four lines over, rejected for a rule stated
    once in a prompt the model had no way to check itself against."""
    claim = _claim(line_start=1, line_end=26)
    extraction = Extraction(record_id="r", model="m", claims=(claim,))
    complaint = overbroad_citations(extraction, wide_packet)

    assert complaint is not None
    assert "26 lines" in complaint
    assert str(MAX_CITED_LINES) in complaint


def test_width_is_summed_across_a_split_citation(
    two_artifact_packet: EvidencePacket,
) -> None:
    """A claim may name two passages, and the cap is on the total: `verify_claim`
    sums them, so the complaint has to sum them the same way. Two ranges of 3 and
    2 lines are inside the cap and must not be reported, or the re-ask would fire
    on a citation the verdict path accepts -- a gate arguing with itself."""
    claim = Claim(
        claim_id="c1",
        text="The parser raises on the cost tracker.",
        citation=(
            Citation("PRIOR_SOURCE_PACKET.md", 1, 3),
            Citation("PRIOR_TASK.md", 1, 2),
        ),
        asserted_literals=(),
    )
    extraction = Extraction(record_id="r", model="m", claims=(claim,))
    assert overbroad_citations(extraction, two_artifact_packet) is None


def test_the_claim_that_is_over_is_the_one_named(wide_packet: EvidencePacket) -> None:
    """A record's other claims are not the model's problem to re-examine. Naming
    only the wide one keeps the re-ask narrow, which is the same reason the
    address complaint lists addresses rather than asking for the whole audit
    again."""
    narrow = _claim()
    wide = Claim(
        claim_id="c2",
        text="A race was detected at the write site.",
        citation=(Citation("PRIOR_SOURCE_PACKET.md", 6, 40),),
        asserted_literals=(),
    )
    extraction = Extraction(record_id="r", model="m", claims=(narrow, wide))
    complaint = overbroad_citations(extraction, wide_packet)

    assert complaint is not None
    assert "c2" in complaint
    assert "c1" not in complaint


RECORD_TEXT = "The suite passed with 177 tests.\nThe guard applies to Python 3.11+.\n"


def test_a_literal_the_record_writes_draws_no_complaint() -> None:
    """Both tokens are in the record, so both are assertions it actually made."""
    claim = _claim(literals=("177", "3.11"))
    assert unrecorded_literals(_one(claim), RECORD_TEXT) is None


def test_a_literal_the_record_never_wrote_is_reported() -> None:
    """The joined form is the one this model produces when told a phrase is not a
    token. It appears in no artifact, so check 5 rejects the claim carrying it,
    and the sentence the record actually wrote was never in question."""
    complaint = unrecorded_literals(_one(_claim(literals=("Python_3.11",))), RECORD_TEXT)
    assert complaint is not None
    assert "`Python_3.11`" in complaint
    assert "drop it" in complaint


def test_only_the_absent_entries_are_named() -> None:
    """The re-ask stays as narrow as the address one: a token the record wrote is
    not the model's problem to re-examine."""
    complaint = unrecorded_literals(_one(_claim(literals=("177", "Python_3.11"))), RECORD_TEXT)
    assert complaint is not None
    assert "`Python_3.11`" in complaint
    assert "`177`" not in complaint


def test_the_comparison_folds_case_like_check_five_does() -> None:
    """Same reason `packet.fold` exists: a sentence-initial capital is not a
    different token, and a complaint about one would send the model to correct a
    spelling that was already right."""
    assert unrecorded_literals(_one(_claim(literals=("SUITE",))), RECORD_TEXT) is None


def test_an_injected_lure_token_is_never_named() -> None:
    """The direction that would matter. A lure's betraying token is written into
    the record, so it is present by construction and no complaint reaches it --
    the check cannot talk a model out of a rejection it has earned."""
    lure = f"{RECORD_TEXT}The guard was added in Python 3.11.\n"
    assert unrecorded_literals(_one(_claim(literals=("3.11",))), lure) is None
