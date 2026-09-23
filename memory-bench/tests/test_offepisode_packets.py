"""The authoring invariants of the off-episode packets (mem-xj9si).

The bead asks for packets "whose supplied artifacts contain no version claims at
all, verified by inspection before the run". Inspection done once, by the person
who wrote the artifacts, is not a property a later reader can rely on: an edit
six months from now would quietly reintroduce the thing being controlled for.
These tests are that inspection, mechanized and re-run on every commit.

They test the fixtures, not the gate. Nothing here calls a model. Each check is a
property of authored data:

* the artifacts still hash to what the manifest sealed;
* no artifact carries a version anywhere, in any of the domains;
* every token a lure would be betrayed by is genuinely absent, so a record
  asserting that lure fails the literal check rather than slipping through;
* every literal the manifest calls supported is genuinely present, so the packet
  can accept a correct record and not only reject a wrong one;
* each packet's token-free lure record really carries no version-shaped token, so
  the competing baseline is blind to it by construction rather than by luck.

That third point is the point. A packet where nothing is checkable rejects
everything and measures nothing; a packet whose lures are already in the evidence
accepts everything and measures nothing. Both failures are silent at run time and
cost a provider session to discover, which is why they are asserted here, before
any session is bought.

The last is the same class of failure aimed at the stop rule rather than at one
packet. The rule asks whether the fidelity gate catches lures a free string search
cannot; if a token-free lure record ever picked up a dotted decimal, the search
would catch it too, the pair would stop separating them, and the rule would go on
reporting whatever it reported.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from membench.fidelity.offepisode_corpus import (
    EVIDENCE_ARTIFACTS,
    OffEpisodePacket,
    ProbePair,
    load_all,
)
from membench.fidelity.packet import normalize
from membench.fidelity.token_baseline import novel_version_tokens

PACKET_ROOT = Path(__file__).resolve().parent / "fixtures" / "offepisode_packets"

# A dotted decimal is what a version looks like written down. Matching the shape
# rather than reading the text is deliberate: this is a structural property of
# authored fixtures, not a judgment about meaning, and it is checked here rather
# than anywhere in the gate. Nothing in the verdict path pattern-matches for
# version numbers -- a gate that did would pass the retro set by cheating.
_DOTTED_DECIMAL = re.compile(r"\d+\.\d+")

# The campaign packet's two artifacts are 78 and 82 lines. Staying in that band
# keeps the 20-line citation cap meaning the same thing here as it does there:
# on a much shorter artifact a legal citation could cover most of the evidence.
_MIN_LINES = 40
_MAX_LINES = 100

PACKETS = load_all(PACKET_ROOT)
BY_ID = {packet.packet_id: packet for packet in PACKETS}

# Every (packet, probe) pair, so a second probe over the same packet is checked as
# thoroughly as the first rather than riding on its packet's invariants.
PROBES = [(packet, probe) for packet in PACKETS for probe in packet.probes]
PROBE_IDS = [f"{packet.packet_id}/{probe.probe_id}" for packet, probe in PROBES]


def _joined(packet: OffEpisodePacket) -> str:
    return normalize("\n".join(packet.packet.artifacts[name] for name in packet.packet.names()))


def test_every_authored_packet_loads_against_its_seal() -> None:
    """`load_all` hash-verifies, so reaching here means no artifact drifted.

    Editing an artifact without re-running scripts/seal_offepisode_packets.py
    fails at import, which is the intended prompt to re-read these invariants
    rather than reseal past them.
    """
    assert sorted(BY_ID) == [
        "go-race-map",
        "inode-exhaustion",
        "nginx-proxy-timeout",
        "pg-index-lock",
        "redis-oom-noeviction",
    ]


@pytest.mark.parametrize("packet", PACKETS, ids=lambda p: p.packet_id)
def test_no_artifact_states_a_version(packet: OffEpisodePacket) -> None:
    """The control the whole measurement rests on.

    If an artifact carried a version, a record repeating it would be supported
    and the run could not distinguish reading from remembering. The campaign
    packet's seed discussed CPython behaviour, which is the confound these
    packets exist to remove, so the absence has to hold in the evidence itself
    and not merely in the intent.
    """
    for name in packet.packet.names():
        found = sorted(set(_DOTTED_DECIMAL.findall(packet.packet.artifacts[name])))
        assert found == [], f"{packet.packet_id}/{name} carries version-shaped token(s) {found}"


@pytest.mark.parametrize("packet", PACKETS, ids=lambda p: p.packet_id)
def test_every_betraying_token_is_absent_from_the_evidence(packet: OffEpisodePacket) -> None:
    """A lure only works if the token that gives it away is not already there.

    The check mirrors the gate's: same normalization, same substring test. A
    token that turned out to be present would make its lure unscoreable while
    still reading as covered in the manifest, which is the quietest way for this
    corpus to stop measuring anything.
    """
    evidence = _joined(packet)
    for lure in packet.lures:
        for literal in lure.betraying_literals:
            assert normalize(literal) not in evidence, (
                f"{packet.packet_id}: {literal!r} is in the evidence, so the lure "
                f"{lure.claim!r} cannot be caught by the literal check"
            )


@pytest.mark.parametrize("packet", PACKETS, ids=lambda p: p.packet_id)
def test_every_supported_literal_is_present(packet: OffEpisodePacket) -> None:
    """The other direction, and the one a reject-everything gate would fail.

    Each of these is a token a correct record would commit to. If none of them
    were in the evidence the packet could only produce rejections, and a session
    spent on it would confirm nothing.
    """
    evidence = _joined(packet)
    for literal in packet.supported_literals:
        assert (
            normalize(literal) in evidence
        ), f"{packet.packet_id}: {literal!r} is declared supported but is not in the evidence"


@pytest.mark.parametrize("packet", PACKETS, ids=lambda p: p.packet_id)
def test_each_packet_offers_a_version_claim_to_reach_for(packet: OffEpisodePacket) -> None:
    """Version history is the narrower category the bead scores separately.

    A packet with no reachable version claim cannot answer whether the campaign's
    specific failure shape recurs off-episode; it could only speak to intrusion
    in general. Each domain therefore carries at least one, and it is detectable
    by token rather than only by reading.
    """
    version_lures = packet.version_lures()
    assert version_lures, f"{packet.packet_id} offers no version claim to reach for"
    assert any(lure.token_detectable for lure in version_lures)


@pytest.mark.parametrize("packet", PACKETS, ids=lambda p: p.packet_id)
def test_a_lure_without_a_token_is_declared_rather_than_hidden(packet: OffEpisodePacket) -> None:
    """`token_detectable` has to match the manifest's own literals.

    Some of the likeliest intrusions carry no distinctive token: "Go maps are not
    safe for concurrent use" is the strongest claim an agent could add to the
    race packet and there is nothing in it for the literal check to catch. Those
    are scoreable by a reader and by the citation check, and marking them keeps a
    per-packet score from implying uniform mechanical coverage.
    """
    for lure in packet.lures:
        assert lure.token_detectable == bool(lure.betraying_literals), (
            f"{packet.packet_id}: {lure.claim!r} declares token_detectable="
            f"{lure.token_detectable} with {len(lure.betraying_literals)} literal(s)"
        )


@pytest.mark.parametrize("packet", PACKETS, ids=lambda p: p.packet_id)
def test_the_artifacts_are_the_shape_the_instruction_expects(packet: OffEpisodePacket) -> None:
    """Two artifacts, the campaign's own names, at the campaign's own scale.

    The frozen capture instruction names PRIOR_TASK.md and PRIOR_SOURCE_PACKET.md
    directly. Keeping the names means the instruction runs with one noun changed
    (README.md records it), so what differs between this run and the campaign's
    is the evidence and nothing else.
    """
    assert packet.packet.names() == tuple(sorted(EVIDENCE_ARTIFACTS))
    for name in packet.packet.names():
        lines = packet.packet.line_count(name)
        assert _MIN_LINES <= lines <= _MAX_LINES, f"{packet.packet_id}/{name} is {lines} lines"


@pytest.mark.parametrize(("packet", "probe"), PROBES, ids=PROBE_IDS)
def test_the_lure_record_is_the_supported_record_plus_one_line(
    packet: OffEpisodePacket, probe: ProbePair
) -> None:
    """The whole design of the pair, asserted rather than maintained by hand.

    Two separately written records would differ in a dozen places, and a verdict
    split between them could be attributed to any of them. Removing the declared
    line from the lure record has to give back the supported record exactly, or
    the pair has stopped being a controlled comparison and nobody would notice.
    """
    supported = probe.supported.splitlines()
    lure = probe.lure.splitlines()
    assert lure.count(probe.injected_line) == 1, (
        f"{packet.packet_id}: the declared injected line appears "
        f"{lure.count(probe.injected_line)} times in the lure record"
    )
    index = lure.index(probe.injected_line)
    assert lure[:index] + lure[index + 1 :] == supported


@pytest.mark.parametrize(("packet", "probe"), PROBES, ids=PROBE_IDS)
def test_the_injected_line_carries_the_tokens_it_claims_to(
    packet: OffEpisodePacket, probe: ProbePair
) -> None:
    """The lure record has to be rejectable for the stated reason.

    A gate that rejects it over some other token would look like a pass while
    testing nothing about the intrusion this pair was built to catch.

    A token-free probe declares no tokens, so there is nothing here to check and
    nothing to assert the absence of either. What makes *that* pair a controlled
    comparison is the separate structural property below, which is the one the
    stop rule leans on.
    """
    if not probe.token_detectable:
        assert probe.betraying_literals == ()
        return
    assert probe.betraying_literals, f"{packet.packet_id}: the probe declares no betraying token"
    injected = normalize(probe.injected_line)
    for literal in probe.betraying_literals:
        assert (
            normalize(literal) in injected
        ), f"{packet.packet_id}: {literal!r} is declared betraying but is not in the injected line"
    evidence = _joined(packet)
    for literal in probe.betraying_literals:
        assert normalize(literal) not in evidence, (
            f"{packet.packet_id}: {literal!r} is in the evidence, so the injected "
            f"line is supported and the lure record is not a lure"
        )


@pytest.mark.parametrize(("packet", "probe"), PROBES, ids=PROBE_IDS)
def test_a_token_free_lure_is_invisible_to_the_competing_baseline(
    packet: OffEpisodePacket, probe: ProbePair
) -> None:
    """The structural property the stop rule rests on, checked in the fixture.

    The rule asks whether the gate catches lures the token baseline is blind to.
    That question is only meaningful if the baseline really cannot see this lure --
    not "usually misses it", but cannot, whatever it is tuned to. The baseline's
    entire input is version-shaped tokens the evidence lacks, so the check is that
    the token-free lure record contains none. If one ever appeared here, the pair
    would quietly stop separating the two and the rule would still report a pass.
    """
    if probe.token_detectable:
        pytest.skip("this pair exists to be visible to the baseline")
    novel = novel_version_tokens(probe.lure, packet.packet)
    assert novel == (), (
        f"{packet.packet_id}: the token-free lure record spells {list(novel)}, which the "
        f"baseline catches, so this pair no longer separates the gate from a string search"
    )


@pytest.mark.parametrize(("packet", "probe"), PROBES, ids=PROBE_IDS)
def test_the_supported_record_is_acceptable_to_both_judges(
    packet: OffEpisodePacket, probe: ProbePair
) -> None:
    """The other half of the control, and the easier one to get wrong.

    If the supported record already carried a betraying token it would be
    rejected too, both halves of the pair would fail, and the run would read as
    "the gate rejects everything off-episode" when the fixture was at fault. The
    same applies to the baseline: a supported record it rejects would make the
    comparison a race between two broken judges.
    """
    supported = normalize(probe.supported)
    for literal in probe.betraying_literals:
        assert normalize(literal) not in supported, (
            f"{packet.packet_id}: {literal!r} is in the supported record, which "
            f"should be acceptable against this packet"
        )
    assert novel_version_tokens(probe.supported, packet.packet) == ()


@pytest.mark.parametrize(("packet", "probe"), PROBES, ids=PROBE_IDS)
def test_the_probe_reaches_for_a_lure_the_manifest_declared(
    packet: OffEpisodePacket, probe: ProbePair
) -> None:
    """The pair tests one of the anticipated intrusions, not a fresh invention.

    Tying it back to a declared lure keeps the probe result readable next to the
    per-packet scoring, and stops the pair from drifting into a claim the packet
    never argued an agent would reach for.
    """
    claims = [lure.claim.lower() for lure in packet.lures]
    assert any(probe.reaches_for.lower() in claim for claim in claims), (
        f"{packet.packet_id}: probe reaches for {probe.reaches_for!r}, "
        f"which is not one of the declared lures"
    )


@pytest.mark.parametrize(("packet", "probe"), PROBES, ids=PROBE_IDS)
def test_the_probe_matches_the_kind_of_lure_it_reaches_for(
    packet: OffEpisodePacket, probe: ProbePair
) -> None:
    """`token_detectable` on the probe has to agree with the lure it draws from.

    The two are declared in different places in the manifest and it would be easy
    to flip one. A probe marked token-free that reached for a token-detectable
    lure would be counted toward the stop rule's token-free quorum while actually
    being catchable by grep, which is the exact confound the rule exists to rule
    out.
    """
    matched = [lure for lure in packet.lures if probe.reaches_for.lower() in lure.claim.lower()]
    assert matched, f"{packet.packet_id}: probe reaches for an undeclared lure"
    assert all(lure.token_detectable == probe.token_detectable for lure in matched), (
        f"{packet.packet_id}: probe {probe.probe_id!r} declares "
        f"token_detectable={probe.token_detectable} but reaches for a lure that does not"
    )


@pytest.mark.parametrize("packet", PACKETS, ids=lambda p: p.packet_id)
def test_each_packet_carries_both_kinds_of_probe(packet: OffEpisodePacket) -> None:
    """One of each, per packet, which is what makes the stop rule's quorum reachable.

    The rule needs at least three token-free pairs and at least six pairs total.
    Three packets carrying one of each was exactly that, and exactly that is a
    corpus with no margin: one unusable pair drops the run below its own quorum,
    which is what happened on 2026-09-06. Packets past the third are slack, and
    they keep the token-free quorum spread across domains rather than resting on
    whichever three were authored first.
    """
    by_id = {probe.probe_id: probe for probe in packet.probes}
    assert sorted(by_id) == ["token-free", "version-token"]
    assert by_id["version-token"].token_detectable is True
    assert by_id["token-free"].token_detectable is False
    assert by_id["token-free"].supported == by_id["version-token"].supported


def test_the_probes_write_their_recordings_to_separate_directories() -> None:
    """Two probes over one packet each write a `supported.json`.

    A shared recordings path would have the second overwrite the first, and the
    loss would be silent: the file exists, it parses, and it is filed under a
    verdict it did not produce.
    """
    roots = [probe.recordings_root for _, probe in PROBES]
    assert len(set(roots)) == len(roots)


def test_no_two_packets_share_a_technology() -> None:
    """Repeated draws from one domain would measure that domain, not the model.

    Reading (b) is that the campaign's packet invited version reasoning. Ruling
    it out needs the invitation withdrawn in more than one way, so the domains
    are a database, a compiled language runtime, a network proxy, an in-memory
    cache, and a filesystem.
    """
    assert len({packet.domain for packet in PACKETS}) == len(PACKETS)
