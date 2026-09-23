"""Write-time memory fidelity gate.

The bd memory reliability campaign (results/bd-reliability-campaign-20260905)
persisted 7 opportunity records across 4 batches. One carried a supported factual
core; 6 asserted facts no supplied artifact establishes, most of them Python
version history. Instruction text did not prevent this at any strength tested,
including the selected evidence-card chain, which still failed 0/2 in
confirmation. This package is the untested alternative the campaign's own data
recommends: a check that runs *before* the write, not a verdict passed after it.

The ZFC split is the point. Deciding what a record claims, and which supplied
passage each claim rests on, is semantic work delegated to a model
(`extract.ClaimExtractor`). Deciding whether that citation actually resolves is
mechanical and lives in code (`verify.verify_extraction`): the artifact must be
in the packet, the line range must be in bounds, the quoted span must occur
literally in those lines, and each literal the claim asserts must occur in the
quote. No code in this package inspects a record for meaning.

What the gate cannot catch, stated plainly: a vague generalization that asserts
no checkable literal and cites a real passage passes. The gate is aimed at the
failure the campaign actually observed, which is specific unsupported tokens.
"""

from membench.fidelity.claims import Citation, Claim, Extraction, parse_extraction
from membench.fidelity.gate import FidelityGate, GateDecision
from membench.fidelity.packet import EvidencePacket, PacketIntegrityError
from membench.fidelity.verify import ClaimVerdict, UnsupportedReason, verify_extraction

__all__ = [
    "Citation",
    "Claim",
    "ClaimVerdict",
    "EvidencePacket",
    "Extraction",
    "FidelityGate",
    "GateDecision",
    "PacketIntegrityError",
    "UnsupportedReason",
    "parse_extraction",
    "verify_extraction",
]
