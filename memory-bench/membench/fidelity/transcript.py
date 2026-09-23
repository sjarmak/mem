"""Every reply the gate drew from the model, in order, with what it was asked.

A recorded run is only a test of the gate if replaying it drives the code the run
drove. The first version of this fixture stored one reply and replayed it into
`FidelityGate.decide`, skipping `ClaimExtractor.extract` entirely -- so the repair
loop and the two second-pass calls that close mem-ewgiz were never exercised, and
a regression in either could not turn the suite red. Measured on
`dev03-explicit__version-history-injected`: the live run rejected on the 3.11
claim and a replay of that same recorded reply accepted, because the replay never
made the calls that produced the rejection.

What is stored is therefore the whole call sequence. On replay a scripted
completion hands the extractor the recorded replies in order, and refuses any
call the recording does not cover. That refusal is the point: if the extractor
stops asking for the audit, or asks for a repair the run never needed, the phases
stop lining up and the suite says so instead of quietly scoring a different gate.

The phase is read off the prompt rather than the reply, because the prompt is what
distinguishes the asks -- the recovery ask answers in the same block format as the
extraction it supplements, so its reply is not identifiable on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from membench.fidelity.unclaimed import AUDIT_MARKER, RECOVERY_MARKER

__all__ = [
    "AUDIT",
    "EXTRACT",
    "RECOVERY",
    "REPAIR",
    "REPAIR_MARKER",
    "ScriptedCompletion",
    "Transcript",
    "TranscriptMismatchError",
    "Turn",
    "load_transcript",
    "phase_of",
    "save_transcript",
]

EXTRACT = "extract"
REPAIR = "repair"
AUDIT = "audit"
RECOVERY = "recovery"

# The opening line of `extract._REPAIR`. Named here rather than imported to keep
# `extract` free of a dependency on the recording layer; the pairing is covered
# by a test that builds a real repair prompt and asks this module to classify it,
# so the two cannot drift apart unnoticed.
REPAIR_MARKER = "Your reply above is not usable"


class TranscriptMismatchError(RuntimeError):
    """The replayed extractor asked something the recording does not answer.

    Deliberately not an `ExtractionFormatError`: the extractor catches that one
    and carries on, which is right for a model that wrote a bad reply and wrong
    for a fixture that cannot answer the call being made. A mismatch has to reach
    the test.
    """


def phase_of(prompt: str) -> str:
    """Which of the four asks this prompt is.

    Checked repair-first because a repair prompt quotes the model's own previous
    reply back to it, and a reply is arbitrary model text: it could contain
    anything, including the audit's marker. The repair marker is in text this
    module writes, above the quoted reply, so it is the one signal a quoted reply
    cannot forge.
    """
    if REPAIR_MARKER in prompt:
        return REPAIR
    if AUDIT_MARKER in prompt:
        return AUDIT
    if RECOVERY_MARKER in prompt:
        return RECOVERY
    return EXTRACT


@dataclass(frozen=True)
class Turn:
    phase: str
    reply: str


@dataclass(frozen=True)
class Transcript:
    """One record's run: the model that answered and every turn it took."""

    model: str
    turns: tuple[Turn, ...]

    @property
    def repairs(self) -> int:
        """Re-asks spent on an unreadable reply, which is the number worth watching.

        The audit's two calls are not repairs. Counting them as such is exactly
        the mistake this field is separated out to avoid: the moment the second
        pass was wired in every record grew two calls, and a repair count read off
        the raw call total would have looked like the prompt getting worse.
        """
        return sum(1 for t in self.turns if t.phase == REPAIR)

    @property
    def phases(self) -> tuple[str, ...]:
        return tuple(t.phase for t in self.turns)

    def extraction_reply(self) -> str:
        """The reply the claims were parsed from: the last of the extract loop.

        Not `turns[-1]`. With the second pass wired in the last reply is the
        recovery ask's, which carries only the blocks for the set-aside lines, and
        parsing that as the record's extraction reads a fraction of the record.
        """
        replies = [t.reply for t in self.turns if t.phase in (EXTRACT, REPAIR)]
        if not replies:
            raise TranscriptMismatchError("transcript has no extraction turn")
        return replies[-1]


def record(prompts: list[str], replies: list[str], *, model: str) -> Transcript:
    """Pair a recorder's prompts with its replies and label each turn."""
    return Transcript(
        model=model,
        turns=tuple(
            Turn(phase=phase_of(p), reply=r) for p, r in zip(prompts, replies, strict=True)
        ),
    )


def save_transcript(path: Path, transcript: Transcript) -> None:
    path.write_text(
        json.dumps(
            {
                "model": transcript.model,
                "repairs": transcript.repairs,
                "turns": [{"phase": t.phase, "reply": t.reply} for t in transcript.turns],
            },
            indent=1,
        )
        + "\n"
    )


def load_transcript(path: Path) -> Transcript:
    """Read a recording, refusing the single-reply shape this format replaced.

    An old fixture is not readable as a short transcript: it holds the reply the
    run ended on, which since the second pass is the recovery ask's, and replaying
    that as an extraction would score a fraction of the record with no error
    anywhere. Refusing names what to do about it.
    """
    raw = json.loads(path.read_text())
    if "turns" not in raw:
        raise TranscriptMismatchError(
            f"{path.name} is a single-reply recording from before the phases were "
            "kept; re-run build_capture_fidelity_fixture.py --extract"
        )
    return Transcript(
        model=raw["model"],
        turns=tuple(Turn(phase=t["phase"], reply=t["reply"]) for t in raw["turns"]),
    )


class ScriptedCompletion:
    """Answers the extractor from a recording, and refuses to improvise.

    Every departure from the recorded sequence raises. An extra call, a missing
    one, or the same count in a different order all mean the replayed run is not
    the recorded run, and a fixture that papered over that -- by returning the
    next reply regardless, or an empty string past the end -- would be back to
    certifying a gate nobody measured.
    """

    def __init__(self, transcript: Transcript) -> None:
        self._turns = transcript.turns
        self._index = 0

    def __call__(self, prompt: str) -> str:
        asked = phase_of(prompt)
        if self._index >= len(self._turns):
            raise TranscriptMismatchError(
                f"call {self._index + 1} is a {asked} ask and the recording ends at "
                f"{len(self._turns)} turns ({', '.join(t.phase for t in self._turns)})"
            )
        turn = self._turns[self._index]
        if turn.phase != asked:
            raise TranscriptMismatchError(
                f"call {self._index + 1} is a {asked} ask; the recording has a "
                f"{turn.phase} ask there ({', '.join(t.phase for t in self._turns)})"
            )
        self._index += 1
        return turn.reply

    @property
    def spent(self) -> int:
        return self._index

    def assert_spent(self) -> None:
        """Every recorded turn was asked for. A short replay is a mismatch too."""
        if self._index != len(self._turns):
            raise TranscriptMismatchError(
                f"replay stopped after {self._index} of {len(self._turns)} recorded "
                f"turns ({', '.join(t.phase for t in self._turns)})"
            )
