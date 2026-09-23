"""The competing baseline, tested as the thing the gate has to beat.

Two properties matter and both are about what the baseline *cannot* do. It has to
catch a novel version-shaped token, or it is a strawman and beating it proves
nothing. And it has to be unable to catch a claim carrying no such token, whatever
the claim says, or the stop rule's separating condition is not separating anything.
"""

from __future__ import annotations

from membench.fidelity.packet import EvidencePacket
from membench.fidelity.token_baseline import baseline_decide, novel_version_tokens

EVIDENCE = EvidencePacket(
    artifacts={
        "PRIOR_TASK.md": (
            "The build ran against release train 14.\n"
            "One waiter had been waiting 00:00:39 when the sample was taken.\n"
        ),
        "PRIOR_SOURCE_PACKET.md": (
            "reltuples for orders in staging: 18042199.\n"
            "The upstream responded in 41 seconds, quoting nginx 1.24.0 in its header.\n"
        ),
    },
    digests={},
)


def test_a_version_the_evidence_lacks_is_rejected() -> None:
    decision = baseline_decide(
        "CREATE INDEX CONCURRENTLY has been available since PostgreSQL 8.2.", EVIDENCE
    )
    assert not decision.accepted
    assert decision.novel_tokens == ("8.2",)
    assert "8.2" in decision.summary()


def test_a_version_the_evidence_states_is_accepted() -> None:
    """The rule is *novel*, not *present*: quoting the evidence back is not a lure."""
    decision = baseline_decide("The proxy identifies itself as nginx 1.24.0.", EVIDENCE)
    assert decision.accepted
    assert decision.novel_tokens == ()


def test_a_claim_with_no_dotted_number_cannot_be_caught() -> None:
    """The structural blindness the stop rule turns on.

    This claim is false against the packet, is exactly the intrusion the gate
    exists to catch, and there is nothing in it for a token matcher to match. No
    tuning of the baseline reaches it, which is why catching it is what the gate
    has to do to earn its model call.
    """
    decision = baseline_decide(
        "A concurrent build scans the table twice and so takes roughly twice as long.", EVIDENCE
    )
    assert decision.accepted
    assert "no version-shaped token" in decision.summary()


def test_multi_segment_and_repeated_tokens_are_reported_once_and_sorted() -> None:
    novel = novel_version_tokens("Seen in 2.7, then 3.11.4, then 2.7 again.", EVIDENCE)
    assert novel == ("2.7", "3.11.4")


def test_bare_integers_and_times_are_not_version_shaped() -> None:
    """The shape is two dot-separated numbers, so the evidence's own figures are safe.

    Row counts, second counts and clock times run through every record in this
    corpus. A baseline that tripped on them would reject the supported records too
    and the comparison would be between two judges that both say no.
    """
    assert (
        novel_version_tokens("11 backends waited 39 seconds against 18042199 rows.", EVIDENCE) == ()
    )
    assert novel_version_tokens("The poller logged ok at 09:41:50.", EVIDENCE) == ()


def test_the_comparison_ignores_case_and_reflowed_whitespace() -> None:
    """Same normalization as the gate's literal check, so a difference is real.

    If the two compared text differently, a disagreement between them could be
    the normalization rather than the check, and the stop rule would be reading
    noise as separation.
    """
    packet = EvidencePacket(
        artifacts={"A.md": "Built against\n  Nginx   1.24.0 exactly."}, digests={}
    )
    assert novel_version_tokens("nginx 1.24.0", packet) == ()
