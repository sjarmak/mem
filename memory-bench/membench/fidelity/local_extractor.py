"""Running the claim extractor against the local Ollama stack.

The settings a live extraction run needs, in one place, because two callers now
need the same ones: the retro fixture builder and the off-episode probe runner.
They have to agree. A probe run sampled differently from the fixture would be
measuring a different gate than the one the retro numbers describe, and nothing
in either result would say so.

Nothing here decides anything. It pins decoding, bounds the generation, and
re-sends one specific daemon fault; every judgment stays in the model call and
every check stays in `verify`.
"""

from __future__ import annotations

from membench.bbon.comparative_judge import ComparativeJudgeError, GenerationLengthError
from membench.bbon.local_stack_judge import (
    DEFAULT_CONTEXT_TOKENS,
    LocalStackComparativeJudge,
)
from membench.fidelity.unclaimed import AUDIT_MARKER, RECOVERY_MARKER

__all__ = [
    "CONTEXT_TOKENS",
    "MAX_ATTEMPTS",
    "MAX_REPLY_TOKENS",
    "SAMPLING",
    "ReplyRecorder",
    "build_judge",
]

# Greedy decoding. Ollama's defaults for this model are temp 0.7 / top_p 0.8 /
# top_k 20, read straight out of the daemon log, and they are wrong twice over
# for this job. The reply is a rigid five-line block per claim, where sampling
# buys nothing and costs format compliance; and a sampled run is not
# reproducible, so a recorded reply could not be regenerated to the same bytes.
# Stated plainly: temperature 0 did not by itself fix the format failures
# measured under the earlier copy-the-evidence prompt -- dropping the copy did.
# It is kept because reproducibility is the property the fixtures rest on.
SAMPLING = {"temperature": 0.0, "top_p": 1.0, "top_k": 1, "seed": 0}

# A generation cap, so a decode that does loop ends in seconds with an error
# instead of holding the socket. A citation-only reply is a few short lines per
# claim and the largest record yielded 19 claims, a few hundred tokens, so this
# is an outer bound on a runaway rather than a working limit. It is deliberately
# not tightened to the observed size: a reply cut off at the cap is a truncated
# extraction, and that is a gate missing claims.
MAX_REPLY_TOKENS = 8192

# The context window the retro fixtures and the probe runs were measured under.
# Taken from the judge rather than restated, because these are not two decisions.
# Written out twice they would agree until one of them was tuned, and the run
# that noticed would be a probe run whose window quietly stopped matching the
# fixture set it is compared against -- the exact incomparability this module
# exists to prevent. A number that must not drift is a number with one home.
CONTEXT_TOKENS = DEFAULT_CONTEXT_TOKENS

# Retries for one specific, observed daemon fault: `/api/generate` returning
# HTTP 200 with `done: false`, no `done_reason` and no token counts, after the
# runner logged `cancel task`. It is intermittent -- the identical request, byte
# for byte, succeeded on the next attempt with `done_reason: stop` -- so it is a
# daemon abort, not a property of the prompt. Retried rather than tolerated,
# because a partial extraction is missing claims and a write gate missing claims
# is a gate missing rejections.
MAX_ATTEMPTS = 4


def build_judge(timeout_s: float) -> LocalStackComparativeJudge:
    """A judge pinned to greedy decoding, with the window and cap stated."""
    return LocalStackComparativeJudge(
        timeout_s=timeout_s,
        options={**SAMPLING, "num_predict": MAX_REPLY_TOKENS, "num_ctx": CONTEXT_TOKENS},
    )


class ReplyRecorder:
    """A completion that keeps every prompt and reply, so a run can sort them out.

    The extractor may ask more than once -- a reply whose line accounting is
    unreadable is sent back with the complaint -- and what a run should record is
    the reply the verdict actually came from. Keeping the whole sequence rather
    than overwriting is what makes the repair count reportable: a rising count is
    the signal that the prompt, not the gate, is what needs work.

    The prompts are kept for the same reason. Three kinds of ask now run on one
    completion, and `repairs` counted the audit's calls as repairs the moment the
    second pass was wired in, which is a number that would have been read as the
    prompt getting worse.
    """

    def __init__(self, judge: LocalStackComparativeJudge) -> None:
        self.judge = judge
        self.replies: list[str] = []
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        reply = self._complete_with_retry(prompt)
        self.prompts.append(prompt)
        self.replies.append(reply)
        return reply

    def reset(self) -> None:
        """Forget the previous record's calls, both halves of them.

        Kept here rather than left to the caller, which is how it went wrong: the
        runner cleared `replies` between the two records of a pair and the prompt
        list kept growing, so the first paired run after the audit was wired in
        died on the mismatch instead of reporting the record it had just scored.
        """
        self.prompts.clear()
        self.replies.clear()

    def phase(self, marker: str) -> list[str]:
        """The replies whose prompt carried ``marker``, in order.

        The extractor makes three kinds of call on one completion -- extract,
        name the set-aside lines, write blocks for the named ones -- and a run
        records the reply the verdict came from, not the last one. Which is which
        is read off the prompt, because the prompt is what distinguishes them: the
        two audit asks carry markers the extraction prompt does not. Guessing from
        the reply's shape does not work, since the recovery ask answers in the
        same block format as the extraction it supplements.
        """
        return [r for p, r in zip(self.prompts, self.replies, strict=True) if marker in p]

    def extraction_replies(self) -> list[str]:
        """The replies to the extraction prompt and its repairs, and no others."""
        return [
            r
            for p, r in zip(self.prompts, self.replies, strict=True)
            if AUDIT_MARKER not in p and RECOVERY_MARKER not in p
        ]

    def _complete_with_retry(self, prompt: str) -> str:
        """Run one prompt, re-sending it when the daemon abandons the generation.

        Only an unexplained daemon abort is retried. `GenerationLengthError` is
        deliberately not caught: the sampling here is greedy, so the re-send
        reproduces the same reply token for token, and the measured result was
        four identical truncations reported as one failure at the end. It needs a
        higher cap or a shorter ask, not another attempt.

        A connection failure raises `LocalStackUnavailableError` and is not
        caught either: no daemon is not a transient fault, and re-sending into a
        closed socket only delays the message. Both are separate from the
        extractor's repair round, which answers a reply the daemon finished and
        the parser could not read.
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self.judge.complete(prompt)
            except GenerationLengthError:
                raise
            except ComparativeJudgeError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                print(f"  attempt {attempt} aborted, re-sending -- {exc}")
        raise AssertionError("unreachable: the loop returns or raises")
