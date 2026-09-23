"""Which daemon failures a live extraction run re-sends, and which it must not.

Both rules were learned from a run, not from reading the client. They live with
the recorder rather than with either caller because the retro fixture builder and
the off-episode probe runner share it, and a re-send policy that differed between
them would make two results incomparable without saying so.
"""

from __future__ import annotations

from typing import cast

import pytest

from membench.bbon.comparative_judge import ComparativeJudgeError, GenerationLengthError
from membench.bbon.local_stack_judge import LocalStackComparativeJudge
from membench.fidelity.local_extractor import MAX_ATTEMPTS, ReplyRecorder
from membench.fidelity.unclaimed import (
    AUDIT_MARKER,
    RECOVERY_MARKER,
    build_audit_prompt,
    build_recovery_ask,
)


class _Judge:
    """A judge that fails a fixed number of times before answering."""

    def __init__(self, *failures: Exception) -> None:
        self.failures = list(failures)
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= len(self.failures):
            raise self.failures[self.calls - 1]
        return "an answer"


def _recorder(judge: _Judge) -> ReplyRecorder:
    return ReplyRecorder(cast(LocalStackComparativeJudge, judge))


def test_an_abandoned_generation_is_re_sent() -> None:
    """The observed daemon fault: HTTP 200 with done=false, no reason, and the
    identical request finishing normally on the next attempt. A partial reply is
    a judge missing claims, so it is retried rather than tolerated."""
    judge = _Judge(ComparativeJudgeError("the daemon stopped part-way"))
    assert _recorder(judge)("a prompt") == "an answer"
    assert judge.calls == 2


def test_a_reply_cut_off_at_the_token_cap_is_not_re_sent() -> None:
    """Sampling here is greedy, so the re-send reproduces the same reply token for
    token. Measured: a loop retrying on the parent class burned every attempt on
    identical truncations before reporting a failure it could have reported at
    once. The fix is a higher cap or a shorter ask, and neither is another try."""
    judge = _Judge(*[GenerationLengthError("cut off at the cap")] * MAX_ATTEMPTS)
    with pytest.raises(GenerationLengthError):
        _recorder(judge)("a prompt")
    assert judge.calls == 1


def test_every_reply_is_kept_so_a_repair_round_is_visible() -> None:
    """The recorder exists to store the reply a verdict came from, and to say how
    many asks it took. Overwriting would make a record that needed a repair look
    like one that answered cleanly."""
    judge = _Judge(ComparativeJudgeError("the daemon stopped part-way"))
    recorder = _recorder(judge)
    recorder("a prompt")
    recorder("another prompt")
    assert recorder.replies == ["an answer", "an answer"]


def test_reset_forgets_both_halves_of_the_record() -> None:
    """Measured: the runner cleared `replies` between the two records of a pair and
    the prompt list kept growing, so the first paired run after the second pass was
    wired in died on the mismatch instead of reporting the record it had scored."""
    recorder = _recorder(_Judge())
    recorder("first prompt")
    recorder.reset()
    recorder("second prompt")
    assert recorder.extraction_replies() == ["an answer"]


def test_the_audit_calls_are_not_counted_as_extraction_attempts() -> None:
    """`repairs` is read as "the prompt is getting worse". Three kinds of ask share
    one completion, so the two audit calls have to be told apart from a re-ask, and
    the prompt is what tells them apart -- the recovery ask answers in the same
    block format as the extraction it supplements."""
    recorder = _recorder(_Judge())
    recorder("extract this record")
    recorder(build_audit_prompt("a\nb\n", (2,)))
    recorder(build_recovery_ask("a\nb\n", (2,)))
    assert len(recorder.extraction_replies()) == 1
    assert len(recorder.phase(AUDIT_MARKER)) == 1
    assert len(recorder.phase(RECOVERY_MARKER)) == 1
