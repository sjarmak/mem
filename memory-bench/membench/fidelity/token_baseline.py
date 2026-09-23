"""The competitor the fidelity gate has to beat: one string search, no model.

The retro set's 9/9 was never evidence the gate was worth its cost, because a
one-line matcher scored 7/7 on the same records. That tie was itself an artifact:
`corpus.json` names `discriminating_literals ['3.11']` on six of seven records, so
the matcher was searching for the token the corpus was built around. Any baseline
tuned to a corpus measures the corpus.

This one is not tuned to anything. It knows no packet, no literal list, and no
domain: it rejects a record when the record spells a version-SHAPED token -- two or
more dot-separated numbers -- that appears nowhere in the evidence it was given.
That is the whole rule, it fits on a line of grep, and it costs nothing to run.

Two properties make it the right competitor rather than a strawman:

* It is corpus-independent, so a score it earns on one packet transfers.
* It is *structurally* blind to a claim carrying no such token. Not weak at
  catching one -- unable to, by construction, whatever the record says. The
  fidelity gate's citation check can reach exactly those, so a token-free lure is
  where the two can be told apart, and that separation is what
  `membench/fidelity/stop_rule.py` requires before any further spend.

It over-rejects on purpose. A record quoting a duration, a file path or a release
train trips it, and nothing here tries to tell those apart from a version claim:
guessing at meaning is what the gate is for. Over-rejection only makes the
competitor stronger, which is the direction a competitor should err in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from membench.fidelity.packet import EvidencePacket, fold

__all__ = ["VERSION_SHAPE", "BaselineDecision", "baseline_decide", "novel_version_tokens"]

# Two or more dot-separated numbers. The shape a version has when written down.
VERSION_SHAPE = re.compile(r"\b\d+\.\d+(?:\.\d+)*\b")


@dataclass(frozen=True)
class BaselineDecision:
    """What the baseline did to one record, and the tokens it did it over."""

    accepted: bool
    novel_tokens: tuple[str, ...]

    def summary(self) -> str:
        if self.accepted:
            return "baseline ACCEPT -- no version-shaped token the evidence lacks"
        return f"baseline REJECT -- {', '.join(self.novel_tokens)} absent from the evidence"


def novel_version_tokens(text: str, packet: EvidencePacket) -> tuple[str, ...]:
    """Version-shaped tokens in ``text`` that no artifact in ``packet`` contains.

    Compared case-folded and whitespace-normalized, the same way the gate's
    literal check compares, so a difference between the two is a difference in
    what they check and not in how they spell it.
    """
    evidence = fold("\n".join(packet.artifacts[name] for name in packet.names()))
    return tuple(
        sorted({token for token in VERSION_SHAPE.findall(text) if fold(token) not in evidence})
    )


def baseline_decide(text: str, packet: EvidencePacket) -> BaselineDecision:
    """Accept unless the record spells a version-shaped token the evidence lacks."""
    novel = novel_version_tokens(text, packet)
    return BaselineDecision(accepted=not novel, novel_tokens=novel)
