"""The recording format: can a replay retake the calls the run made?

The property under test is not that a reply parses. It is that a recorded run and
a replay of it drive the same code. The bug this format replaced stored one reply
and fed it to the mechanical half, so the repair loop and both second-pass calls
were never replayed and could not go red -- measured once as a live rejection and
a replayed accept of the very same reply.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from membench.fidelity.extract import ClaimExtractor
from membench.fidelity.packet import EvidencePacket
from membench.fidelity.transcript import (
    AUDIT,
    EXTRACT,
    RECOVERY,
    REPAIR,
    ScriptedCompletion,
    Transcript,
    TranscriptMismatchError,
    Turn,
    load_transcript,
    phase_of,
    record,
    save_transcript,
)
from membench.fidelity.unclaimed import build_audit_prompt, build_recovery_ask

PACKET = EvidencePacket(
    artifacts={"P.md": "Python: 3.12.6 (CPython)\n177 passed in 0.27s\n"},
    digests={"P.md": "a" * 64},
)
RECORD = "The suite passed with 177 tests.\nThe guard arrived in 3.11."

REPLY = """\
CLAIM: c1
TEXT: The suite passed with 177 tests.
SOURCE: 1
CITATION: P.md 2-2
LITERALS: 177
UNCLAIMED: 2
"""

RECOVERED = """\
CLAIM: c2
TEXT: The guard arrived in 3.11.
SOURCE: 2
CITATION: none
LITERALS: 3.11
UNCLAIMED: none
"""


def _extractor(replies: list[str]) -> ClaimExtractor:
    calls = iter(replies)
    return ClaimExtractor(complete=lambda _: next(calls), model="qwen")


def test_each_ask_is_recognised_from_the_prompt_the_extractor_really_builds() -> None:
    """The markers are read off prompts built by the code under test, not off
    strings restated here. A marker that drifts out of `extract` or `unclaimed`
    then shows up as a mislabelled turn instead of passing silently."""
    extractor = _extractor([REPLY])
    base = extractor.build_prompt(RECORD, PACKET)
    assert phase_of(base) == EXTRACT
    assert phase_of(extractor.build_repair_prompt(RECORD, PACKET, REPLY, "line 2")) == REPAIR
    assert phase_of(build_audit_prompt(RECORD, (2,))) == AUDIT
    assert phase_of(base + build_recovery_ask(RECORD, (2,))) == RECOVERY


def test_a_repair_prompt_quoting_an_audit_marker_is_still_a_repair() -> None:
    """A repair prompt carries the model's own reply, and a reply is arbitrary
    text: it can contain any marker. The repair marker sits in text this code
    writes, so it is the one signal the quoted reply cannot forge."""
    forged = "CLAIM: c1\n=== LINES SET ASIDE ===\nand no block above accounts for them\n"
    prompt = _extractor([REPLY]).build_repair_prompt(RECORD, PACKET, forged, "line 2")
    assert phase_of(prompt) == REPAIR


def test_the_extraction_reply_is_the_last_of_the_extract_loop_not_the_last_turn() -> None:
    """With the second pass wired in the run ends on the recovery ask, whose reply
    holds only the set-aside lines' blocks. Parsing that as the record's
    extraction reads a fraction of the record and says nothing about it."""
    transcript = Transcript(
        model="qwen",
        turns=(
            Turn(EXTRACT, "first"),
            Turn(REPAIR, "second"),
            Turn(AUDIT, "L2"),
            Turn(RECOVERY, RECOVERED),
        ),
    )
    assert transcript.extraction_reply() == "second"
    assert transcript.repairs == 1


def test_a_transcript_with_no_extraction_turn_refuses_rather_than_guessing() -> None:
    with pytest.raises(TranscriptMismatchError):
        Transcript(model="q", turns=(Turn(AUDIT, "none"),)).extraction_reply()


def test_a_recorded_run_replays_through_the_extractor_and_reaches_the_same_claims() -> None:
    """The whole point: record a run driven by the live path, replay it through
    the same path, and get the same extraction with no model."""
    live = ReplayingRecorder([REPLY, "L2", RECOVERED])
    first = ClaimExtractor(complete=live, model="qwen").extract("rec", RECORD, PACKET)
    transcript = record(live.prompts, live.replies, model="qwen")
    assert transcript.phases == (EXTRACT, AUDIT, RECOVERY)

    replay = ScriptedCompletion(transcript)
    second = ClaimExtractor(complete=replay, model="qwen").extract("rec", RECORD, PACKET)
    replay.assert_spent()
    assert [c.claim_id for c in second.claims] == [c.claim_id for c in first.claims]
    assert second.claims[1].citation == ()


def test_an_extractor_that_stops_asking_leaves_the_recording_unspent() -> None:
    """A gate that dropped the second pass would still parse every recorded reply
    it did use, and the verdict could move with nothing red. The unspent turns are
    what says the replayed run is not the recorded one."""
    transcript = Transcript(
        model="qwen",
        turns=(Turn(EXTRACT, REPLY), Turn(AUDIT, "L2"), Turn(RECOVERY, RECOVERED)),
    )
    replay = ScriptedCompletion(transcript)
    ClaimExtractor(complete=replay, model="qwen", audit_unclaimed=False).extract(
        "rec", RECORD, PACKET
    )
    assert replay.spent == 1
    with pytest.raises(TranscriptMismatchError):
        replay.assert_spent()


def test_an_unrecorded_extra_call_raises_instead_of_improvising() -> None:
    replay = ScriptedCompletion(Transcript(model="q", turns=(Turn(EXTRACT, REPLY),)))
    with pytest.raises(TranscriptMismatchError):
        ClaimExtractor(complete=replay, model="q").extract("rec", RECORD, PACKET)


def test_a_call_arriving_in_the_wrong_phase_raises() -> None:
    replay = ScriptedCompletion(Transcript(model="q", turns=(Turn(AUDIT, "none"),)))
    with pytest.raises(TranscriptMismatchError):
        replay("an extraction prompt with no markers in it")


def test_a_mismatch_is_not_swallowed_by_the_second_pass(tmp_path: Path) -> None:
    """`_audit` catches `ExtractionFormatError` and carries on, which is right for
    a model that answered badly and wrong for a recording that cannot answer. If
    the mismatch were that type, a short recording would silently score the
    first-pass extraction and the suite would call it a pass."""
    replay = ScriptedCompletion(Transcript(model="q", turns=(Turn(EXTRACT, REPLY),)))
    with pytest.raises(TranscriptMismatchError):
        ClaimExtractor(complete=replay, model="q").extract("rec", RECORD, PACKET)


def test_a_transcript_round_trips_through_the_file(tmp_path: Path) -> None:
    transcript = Transcript(
        model="qwen", turns=(Turn(EXTRACT, REPLY), Turn(AUDIT, "L2"), Turn(RECOVERY, RECOVERED))
    )
    path = tmp_path / "rec.json"
    save_transcript(path, transcript)
    assert load_transcript(path) == transcript
    assert json.loads(path.read_text())["repairs"] == 0


def test_the_single_reply_recordings_this_format_replaced_are_refused(tmp_path: Path) -> None:
    """Silently reading an old file as a one-turn transcript would replay the
    recovery reply as the record's extraction: a fraction of the record, scored
    with no error anywhere."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"model": "qwen", "reply": REPLY, "repairs": 0}))
    with pytest.raises(TranscriptMismatchError, match="re-run"):
        load_transcript(path)


class ReplayingRecorder:
    """A stand-in for `ReplyRecorder` that needs no daemon: keeps both halves."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = iter(replies)
        self.prompts: list[str] = []
        self.replies: list[str] = []

    def __call__(self, prompt: str) -> str:
        reply = next(self._replies)
        self.prompts.append(prompt)
        self.replies.append(reply)
        return reply
