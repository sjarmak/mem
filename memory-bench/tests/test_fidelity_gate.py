"""FidelityGate: the accept/reject rule over verified claims."""

from __future__ import annotations

from membench.fidelity.claims import Citation, Claim, Extraction
from membench.fidelity.extract import ClaimExtractor
from membench.fidelity.gate import FidelityGate
from membench.fidelity.packet import EvidencePacket
from membench.fidelity.verify import UnsupportedReason

PACKET = EvidencePacket(
    artifacts={"PRIOR_SOURCE_PACKET.md": "Python: 3.12.6 (CPython)\n177 passed in 0.27s\n"},
    digests={"PRIOR_SOURCE_PACKET.md": "b" * 64},
)

SUPPORTED = Claim(
    claim_id="c1",
    text="The suite passed with 177 tests.",
    citation=(Citation("PRIOR_SOURCE_PACKET.md", 2, 2),),
    asserted_literals=("177",),
)
UNSUPPORTED = Claim(
    claim_id="c2",
    text="The guard was introduced in CPython 3.11.",
    citation=(Citation("PRIOR_SOURCE_PACKET.md", 1, 1),),
    asserted_literals=("3.11",),
)


def _gate() -> FidelityGate:
    return FidelityGate(extractor=ClaimExtractor(complete=lambda _: "", model="stub"))


def _decide(*claims: Claim):
    extraction = Extraction(record_id="rec", model="stub", claims=claims)
    return _gate().decide(extraction, PACKET)


def test_all_claims_supported_accepts() -> None:
    decision = _decide(SUPPORTED)
    assert decision.accepted
    assert decision.unsupported == ()
    assert "accept" in decision.summary()


def test_one_unsupported_claim_among_supported_ones_rejects() -> None:
    """The campaign's 6 failures were each mostly accurate text spoiled by a few
    specific assertions. Any 'mostly supported' rule would have passed all of them."""
    decision = _decide(SUPPORTED, UNSUPPORTED, SUPPORTED)
    assert not decision.accepted
    assert [v.claim_id for v in decision.unsupported] == ["c2"]
    assert decision.unsupported[0].reason is UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE


def test_an_empty_extraction_rejects() -> None:
    """A record that reads as claim-free is far more likely an extractor failure
    than a memory worth persisting; the safe direction for a write gate is refuse."""
    assert not _decide().accepted


def test_the_summary_names_the_offending_claim_and_its_reason() -> None:
    summary = _decide(SUPPORTED, UNSUPPORTED).summary()
    assert "CPython 3.11" in summary
    assert UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE.value in summary


def test_the_decision_carries_the_model_that_produced_the_extraction() -> None:
    """A verdict is only interpretable next to the auditor that reached it."""
    decision = _decide(SUPPORTED)
    assert decision.model == "stub"
    assert decision.record_id == "rec"


def test_evaluate_runs_the_extractor_then_the_same_rule() -> None:
    reply = """\
CLAIM: c2
TEXT: The guard was introduced in CPython 3.11.
SOURCE: 1
CITATION: PRIOR_SOURCE_PACKET.md 1-1
LITERALS: 3.11
UNCLAIMED: none
"""
    gate = FidelityGate(extractor=ClaimExtractor(complete=lambda _: reply, model="qwen"))
    decision = gate.evaluate("rec", "The guard was introduced in CPython 3.11.", PACKET)
    assert not decision.accepted
    assert decision.model == "qwen"
    assert decision.unsupported[0].reason is UnsupportedReason.LITERAL_ABSENT_FROM_EVIDENCE


def test_decide_is_deterministic_over_a_recorded_extraction() -> None:
    """Splitting decide() from evaluate() is what makes a stored verdict
    reproducible without a model call. If it drifted, CI would be measuring the
    daemon rather than the gate."""
    extraction = Extraction("rec", "stub", (SUPPORTED, UNSUPPORTED))
    first = _gate().decide(extraction, PACKET)
    second = _gate().decide(extraction, PACKET)
    assert first == second


def test_the_summary_says_how_much_of_the_record_was_set_aside() -> None:
    """An accept that read half the record is not the same result as an accept
    that read all of it, and the first off-episode run produced exactly the first
    while printing the second: 5 cited claims, the injected sentence in UNCLAIMED,
    and a summary with nothing in it to suggest a line had been set aside."""
    extraction = Extraction(
        record_id="rec", model="stub", claims=(SUPPORTED,), unclaimed_lines=(3, 7, 11)
    )
    decision = _gate().decide(extraction, PACKET)
    assert decision.accepted
    assert "skipped 3 lines" in decision.summary()


def test_a_rejection_carries_the_census_too() -> None:
    """A rejection naming one claim while six lines went unread is a different
    finding from one that read the whole record, and the reasons list hides it."""
    extraction = Extraction(
        record_id="rec", model="stub", claims=(UNSUPPORTED,), unclaimed_lines=(2, 4)
    )
    decision = _gate().decide(extraction, PACKET)
    assert not decision.accepted
    assert "skipped 2 lines" in decision.summary()


def test_a_record_with_nothing_set_aside_says_nothing_about_skips() -> None:
    """The census is a signal, so it stays absent when there is none to give."""
    assert "skipped" not in _decide(SUPPORTED).summary()
