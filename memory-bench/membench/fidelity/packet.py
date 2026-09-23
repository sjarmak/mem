"""The supplied-evidence packet a memory write is checked against.

A packet is the closed set of artifacts the agent was given. Anything outside it
is background knowledge, and background knowledge is exactly what the campaign's
6 failed records reached for. The packet is therefore the gate's whole world: a
citation that does not resolve inside it is unsupported by construction.

Artifacts are hash-verified on load. A packet whose bytes drifted since the run
would silently change every verdict computed against it, so a mismatch is a hard
failure at the boundary, never a warning.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_WHITESPACE = re.compile(r"\s+")


class PacketIntegrityError(RuntimeError):
    """An artifact is missing, or its bytes do not match the recorded digest."""


def normalize(text: str) -> str:
    """Collapse runs of whitespace and strip. The rendering-side normalization.

    An agent re-typing an excerpt reflows it, and this evidence is terminal
    output padded to a fixed width, so whitespace carries no meaning here.
    Digits, punctuation and case are left as written, so anything reporting or
    comparing text for display sees it the way the artifact spells it.
    """
    return _WHITESPACE.sub(" ", text).strip()


def fold(text: str) -> str:
    """Normalize, then case-fold. The comparison the literal check runs on.

    Case is a property of where a word sits in a sentence, not of what the
    evidence establishes. Measured off-episode: a claim reading ``reads`` was
    rejected against an artifact reading ``Reads were unaffected throughout.``,
    and the record was inside its own support boundary. A gate that rejects a
    supported record teaches the writer to paraphrase around it, which is the
    opposite of what it is for.

    What folding gives up is bounded by what it can touch. Every burial this
    corpus is made of turns on a digit -- ``3.11``, ``Phase 4``, ``177 passed``,
    ``8.2``, ``1.9``, ``60`` -- and a digit has no case, so no fold can make one
    of them match. What it can reach is a claim distinguishing an all-caps
    keyword from the same word in prose (``CONCURRENTLY`` the SQL form against
    ``concurrently`` the adverb). That is a real loss, and it is the smaller one:
    it needs the evidence to carry the word already, whereas the rejected
    ``reads`` needed only a sentence to start.
    """
    return normalize(text).casefold()


def number(text: str) -> str:
    """Render text with 1-based line numbers, `LN<tab>line`, for a prompt.

    Both the evidence and the proposed record are addressed by line, so both are
    numbered the same way and by the same function: two renderings that drifted
    apart would put the model's two kinds of address on different footings.

    The ``L`` is what stops an address being confused with a number the text
    merely contains. Numbered bare, the model read ``retrieval_extraction.py:236``
    out of a pasted stack frame and wrote ``PRIOR_SOURCE_PACKET.md 236`` as the
    citation for it -- in a 78-line artifact, and it held that citation through a
    repair round that told it the artifact's size. The prompt had already named
    that exact confusion in words and been ignored. A bare number is a plausible
    address; ``L236`` is one the evidence text never offers.
    """
    return "\n".join(f"L{i}\t{line}" for i, line in enumerate(text.splitlines(), start=1))


@dataclass(frozen=True)
class EvidencePacket:
    """The artifacts supplied to one session, addressable by 1-based line range.

    ``artifacts`` maps an artifact name (as an agent would cite it, e.g.
    ``PRIOR_TASK.md``) to its full text. ``digests`` records the sha256 each text
    was verified against, so a decision can name the exact bytes it read.
    """

    artifacts: Mapping[str, str]
    digests: Mapping[str, str]

    @classmethod
    def from_files(cls, paths: Mapping[str, Path], expected: Mapping[str, str]) -> EvidencePacket:
        """Load each artifact and verify its sha256 against ``expected``.

        Every name in ``paths`` must have an expected digest: an unverified
        artifact is not admitted, because the gate's verdicts are only as
        trustworthy as the evidence they were computed against.
        """
        missing_digest = sorted(set(paths) - set(expected))
        if missing_digest:
            raise PacketIntegrityError(
                f"no expected sha256 for artifact(s): {', '.join(missing_digest)}"
            )
        texts: dict[str, str] = {}
        digests: dict[str, str] = {}
        for name, path in paths.items():
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise PacketIntegrityError(f"cannot read artifact {name} at {path}: {exc}") from exc
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected[name]:
                raise PacketIntegrityError(
                    f"artifact {name} at {path} has sha256 {actual}, "
                    f"expected {expected[name]}; the evidence drifted since the run"
                )
            texts[name] = raw.decode("utf-8")
            digests[name] = actual
        return cls(artifacts=texts, digests=digests)

    def names(self) -> tuple[str, ...]:
        """Artifact names, sorted, for prompting and for error messages."""
        return tuple(sorted(self.artifacts))

    def line_count(self, name: str) -> int:
        """Number of addressable lines in ``name``."""
        return len(self.artifacts[name].splitlines())

    def span(self, name: str, line_start: int, line_end: int) -> str:
        """The text of lines ``line_start``..``line_end`` inclusive, 1-based.

        Raises ``KeyError`` for an unknown artifact and ``IndexError`` for a range
        outside the file. Callers in `verify` convert both into gate verdicts
        rather than letting them escape: an out-of-range citation is a finding
        about the record, not a fault in the gate.
        """
        lines = self.artifacts[name].splitlines()
        if line_start < 1 or line_end < line_start or line_end > len(lines):
            raise IndexError(f"{name} lines {line_start}-{line_end} outside 1-{len(lines)}")
        return "\n".join(lines[line_start - 1 : line_end])

    def numbered(self, name: str) -> str:
        """The artifact rendered with 1-based line numbers, for the extractor prompt.

        The model can only produce a resolvable citation if it can see the line
        numbers it is citing.
        """
        return number(self.artifacts[name])
