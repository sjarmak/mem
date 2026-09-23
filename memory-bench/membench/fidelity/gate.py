"""The gate itself: extract, verify, decide.

A record is accepted only when every claim in it resolves to the supplied
evidence. One unsupported claim rejects the write. That threshold is deliberate
and comes from the campaign: of 7 persisted records, the 6 rejected ones were
each spoiled by a small number of specific unsupported assertions sitting inside
otherwise accurate text, so any rule that tolerated "mostly supported" would have
accepted all of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from membench.fidelity.claims import Extraction
from membench.fidelity.extract import ClaimExtractor
from membench.fidelity.packet import EvidencePacket
from membench.fidelity.verify import ClaimVerdict, verify_extraction


@dataclass(frozen=True)
class GateDecision:
    """The verdict on one proposed write, with every claim's outcome retained.

    The unsupported verdicts are the actionable part: they name which claim to
    drop or qualify, so a rejection is a repair instruction rather than a wall.
    """

    record_id: str
    model: str
    accepted: bool
    verdicts: tuple[ClaimVerdict, ...]
    unclaimed_lines: tuple[int, ...] = ()

    @property
    def unsupported(self) -> tuple[ClaimVerdict, ...]:
        return tuple(v for v in self.verdicts if v.unsupported)

    def summary(self) -> str:
        """One line: the verdict, its reasons, and how much of the record it read.

        The skip count is in the accept line because an accept is where it can
        mislead. Measured on the first off-episode run: a record was accepted on
        5 cited claims while the sentence the probe had injected sat in UNCLAIMED,
        and the summary said `accept (5 claims, all cited)` with nothing to
        suggest a line had been set aside. A reader seeing `skipped 6 lines` has a
        reason to open the reply; a reader seeing only the claim count does not.
        """
        skipped = f", skipped {len(self.unclaimed_lines)} lines" if self.unclaimed_lines else ""
        if self.accepted:
            return f"{self.record_id}: accept ({len(self.verdicts)} claims, all cited{skipped})"
        reasons = "; ".join(
            f"{v.claim_text} [{v.reason.value if v.reason else 'unknown'}]"
            for v in self.unsupported
        )
        return f"{self.record_id}: reject ({len(self.unsupported)} unsupported{skipped}) {reasons}"


@dataclass(frozen=True)
class FidelityGate:
    """Runs the extractor, then the mechanical checks, over a proposed record."""

    extractor: ClaimExtractor

    def evaluate(self, record_id: str, record_text: str, packet: EvidencePacket) -> GateDecision:
        extraction = self.extractor.extract(record_id, record_text, packet)
        return self.decide(extraction, packet)

    def decide(self, extraction: Extraction, packet: EvidencePacket) -> GateDecision:
        """Apply the mechanical checks to an already-produced extraction.

        Split out from `evaluate` so a recorded extraction can be re-verified with
        no model call: the deterministic half of the gate is then testable in CI,
        and a stored decision can be reproduced exactly.

        An extraction with no claims at all is rejected rather than accepted. A
        record that reads as claim-free is far more likely to be an extractor
        failure than a memory worth persisting, and the safe direction for a
        write gate is to refuse.
        """
        verdicts = verify_extraction(extraction, packet)
        accepted = len(verdicts) > 0 and all(v.supported for v in verdicts)
        return GateDecision(
            record_id=extraction.record_id,
            model=extraction.model,
            accepted=accepted,
            verdicts=verdicts,
            unclaimed_lines=extraction.unclaimed_lines,
        )
