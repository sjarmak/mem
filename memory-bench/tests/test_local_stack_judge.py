"""Tests for `membench.bbon.local_stack_judge.LocalStackComparativeJudge`: the §4.1
local OSS-judge backend.

Hermetic: every test injects a fake POST (and, for preflight, a fake tags fetch), so
nothing here spawns a process or touches a live Ollama daemon. The invariants under
test are (1) the judge satisfies the `ComparativeJudge` protocol so
`score_action_impact` and `compare_attempts` accept it unchanged, (2) it POSTs the
pinned chat model to `/api/generate` and returns the model text verbatim, and
(3) every failure mode — daemon down, malformed reply, un-pulled model — surfaces
LOUDLY, never as a default verdict or a silent paid-API fallback.
"""

from __future__ import annotations

import json

import pytest

from membench.bbon.comparative_judge import (
    ComparativeJudgeError,
    GenerationLengthError,
    compare_attempts,
)
from membench.bbon.local_stack_judge import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_TIMEOUT_S,
    LocalStackComparativeJudge,
)
from membench.bbon.models import Attempt, deterministic_id
from membench.bbon.narrative_diff import generate_narrative_diff
from membench.memory_systems.local_stack import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_OLLAMA_EMBEDDING_MODEL,
    LocalModelStack,
    LocalStackUnavailableError,
)
from membench.metrics.action_impact import ActionImpactInputs, score_action_impact


def _ollama_reply(text: str) -> bytes:
    """A non-streaming /api/generate response, whose ``response`` field is the text."""
    return json.dumps({"model": DEFAULT_CHAT_MODEL, "response": text, "done": True}).encode()


def _tags(*names: str) -> bytes:
    return json.dumps({"models": [{"name": n} for n in names]}).encode()


_ATTEMPT_ID = deterministic_id({"attempt": "judge-fixture"})


def _step(index: int, kind: str):
    from membench.bbon.models import AttemptStep

    return AttemptStep(
        id=deterministic_id({"i": index, "kind": kind}),
        attempt_id=_ATTEMPT_ID,
        step_index=index,
        kind=kind,
    )


# --- protocol + happy path -------------------------------------------------------


def test_model_property_is_the_pinned_chat_model() -> None:
    judge = LocalStackComparativeJudge(stack=LocalModelStack(chat_model="qwen2.5"))
    assert judge.model == "qwen2.5"


def test_default_stack_is_env_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default stack must honor the env-pinned model (e.g. §4.5 Nemotron) with no
    # code change — a bare LocalModelStack() would silently ignore the env var.
    monkeypatch.setenv("MEMBENCH_LOCAL_CHAT_MODEL", "nemotron")
    assert LocalStackComparativeJudge().model == "nemotron"


def test_complete_posts_pinned_model_to_generate_and_returns_response() -> None:
    seen: list[tuple[str, dict]] = []

    def post(url: str, body: bytes) -> bytes:
        seen.append((url, json.loads(body)))
        return _ollama_reply('{"winner": "B", "confidence": 0.9, "rationale": "warm won"}')

    stack = LocalModelStack(ollama_base_url="http://gpu-box:11434", chat_model="llama3.1")
    judge = LocalStackComparativeJudge(stack=stack, post=post)
    reply = judge.complete("the prompt")

    url, payload = seen[0]
    assert url == "http://gpu-box:11434/api/generate"
    assert payload == {
        "model": "llama3.1",
        "prompt": "the prompt",
        "stream": False,
        "options": {"num_ctx": DEFAULT_CONTEXT_TOKENS},
    }
    assert json.loads(reply)["winner"] == "B"


def test_generation_options_are_forwarded_when_set() -> None:
    """A caller can pin sampling and bound generation. Both matter: sending no
    options leaves the daemon applying the model's own defaults, which are
    sampled, and leaves output unbounded, so a model in a repetition loop holds
    the socket for the whole timeout. The window is not in that set -- it is sent
    whether the caller asks or not, which the test below pins."""
    seen: list[dict] = []

    def post(url: str, body: bytes) -> bytes:
        seen.append(json.loads(body))
        return _ollama_reply("capped")

    options = {"temperature": 0.0, "seed": 0, "num_predict": 4096}
    judge = LocalStackComparativeJudge(post=post, options=options)
    assert judge.complete("the prompt") == "capped"
    assert seen[0]["options"] == {**options, "num_ctx": DEFAULT_CONTEXT_TOKENS}


def test_the_context_window_is_stated_even_when_no_options_are_given() -> None:
    """Measured against a live daemon: with no options at all, an 83K-character
    prompt was read whole here (prompt_eval_count 28,596) because this box happens
    to run OLLAMA_CONTEXT_LENGTH=32768. That is the daemon's configuration
    answering a question the caller never asked, and on a box started with a
    smaller window the identical request returns a confident answer about a
    clipped prompt. So the window ships with every request."""
    seen: list[dict] = []

    def post(url: str, body: bytes) -> bytes:
        seen.append(json.loads(body))
        return _ollama_reply("answered")

    assert LocalStackComparativeJudge(post=post).complete("the prompt") == "answered"
    assert seen[0]["options"]["num_ctx"] == DEFAULT_CONTEXT_TOKENS


def test_a_caller_can_override_the_window() -> None:
    """Defaulting the window is not the same as owning it. A caller with a longer
    prompt than the default covers must be able to raise it, and the merge has to
    put the caller on top -- the other order would send the default and drop the
    request the caller actually made."""
    seen: list[dict] = []

    def post(url: str, body: bytes) -> bytes:
        seen.append(json.loads(body))
        return _ollama_reply("answered")

    judge = LocalStackComparativeJudge(post=post, options={"num_ctx": 65536})
    judge.complete("a very long prompt")
    assert seen[0]["options"]["num_ctx"] == 65536


def test_a_prompt_read_to_the_edge_of_the_window_is_refused() -> None:
    """The silent half of truncation. Measured on a live daemon: a prompt clipped
    to fit reports prompt_eval_count one below the window -- 2047/2048, 4095/4096,
    8191/8192 -- with done true, done_reason ``length`` or ``stop``, and a reply
    that reads like an answer. The needle prompt that answered CORMORANT-47 under
    a 32,768-token window answered ``The passphrase is 1366.`` under a 2,048-token
    one. Nothing downstream can tell those apart, so the judge has to."""

    def post(url: str, body: bytes) -> bytes:
        return json.dumps(
            {"response": "a confident answer", "done": True, "prompt_eval_count": 2047}
        ).encode()

    judge = LocalStackComparativeJudge(post=post, options={"num_ctx": 2048})
    with pytest.raises(ComparativeJudgeError, match="clipped to fit"):
        judge.complete("a prompt longer than the window")


def test_a_clipped_prompt_is_named_ahead_of_the_generation_cap() -> None:
    """The two faults overlap and their advice is opposite. A prompt clipped to
    2,048 tokens came back done_reason=length when num_predict was set, and a
    caller told to raise num_predict would be tuning the half that is not broken:
    the prompt was cut before the model read it. So the window is diagnosed first,
    and the error names the window even when the payload also says length."""

    def post(url: str, body: bytes) -> bytes:
        return json.dumps(
            {
                "response": "an answer about the part that fit",
                "done": True,
                "done_reason": "length",
                "prompt_eval_count": 2047,
            }
        ).encode()

    judge = LocalStackComparativeJudge(post=post, options={"num_ctx": 2048})
    with pytest.raises(ComparativeJudgeError, match="clipped to fit"):
        judge.complete("a prompt longer than the window")


def test_a_capped_generation_on_a_prompt_that_fits_still_names_the_cap() -> None:
    """The other half of that ordering: when the prompt is nowhere near the window,
    done_reason=length means what it says and must keep its own error type. Putting
    the clip check first is only allowed if it stays quiet here."""

    def post(url: str, body: bytes) -> bytes:
        return json.dumps(
            {
                "response": "a long answer, cut",
                "done": True,
                "done_reason": "length",
                "prompt_eval_count": 26,
            }
        ).encode()

    judge = LocalStackComparativeJudge(post=post, options={"num_ctx": 2048})
    with pytest.raises(GenerationLengthError):
        judge.complete("a short prompt")


def test_a_prompt_that_fits_is_not_refused() -> None:
    """The other side of the same boundary, and the reason it is a measured
    threshold rather than a cautious margin: a prompt that fits stops thousands of
    tokens short (26 against a 2,048 window in the live run), so nothing honest
    sits near the edge. A guard that fired below it would reject good judgments."""

    def post(url: str, body: bytes) -> bytes:
        return json.dumps(
            {"response": "a good answer", "done": True, "prompt_eval_count": 26}
        ).encode()

    judge = LocalStackComparativeJudge(post=post, options={"num_ctx": 2048})
    assert judge.complete("a short prompt") == "a good answer"


def test_a_reply_with_no_token_count_is_not_refused() -> None:
    """A payload without prompt_eval_count supports no conclusion either way --
    older daemons omit it, and every injected double here does. A guard built to
    catch fabricated answers is the last place to fabricate a verdict from a
    missing field."""

    def post(url: str, body: bytes) -> bytes:
        return _ollama_reply("no counts in this payload")

    assert LocalStackComparativeJudge(post=post).complete("p") == "no counts in this payload"


def test_default_timeout_is_set() -> None:
    judge = LocalStackComparativeJudge()
    assert judge.timeout_s == DEFAULT_TIMEOUT_S


# --- consumed unchanged by both seams --------------------------------------------


def test_score_action_impact_accepts_the_local_judge_unchanged() -> None:
    # A pair that DIFFERS on tool_choice so the judge is actually consulted.
    inp = ActionImpactInputs(
        on_steps=(_step(0, "Read"), _step(1, "Edit")),
        off_steps=(_step(0, "Read"),),
        on_status="completed",
        off_status="failed",
        work_id="mem-lvp.6.1",
    )
    verdict_json = json.dumps(
        {
            "memory_changed_tool_choice": True,
            "memory_changed_plan": True,
            "memory_changed_output": False,
            "memory_prevented_known_failure": True,
            "memory_improved_verification": False,
            "rationale": "memory added the verifying edit step",
        }
    )
    judge = LocalStackComparativeJudge(post=lambda u, b: _ollama_reply(verdict_json))

    metrics = score_action_impact(inp, judge=judge)
    assert metrics.memory_changed_tool_choice is True
    assert metrics.memory_prevented_known_failure is True
    assert metrics.memory_changed_output is False


def test_compare_attempts_accepts_the_local_judge_unchanged() -> None:
    left = Attempt(
        id=deterministic_id({"arm": "cold"}),
        work_id="w",
        arm="cold",
        status="failed",
        result={"total_tokens": 900},  # type: ignore[arg-type]
    )
    right = Attempt(
        id=deterministic_id({"arm": "warm"}),
        work_id="w",
        arm="warm",
        status="completed",
        result={"total_tokens": 300},  # type: ignore[arg-type]
    )
    diff = generate_narrative_diff(left, right, [], [])
    judge = LocalStackComparativeJudge(
        post=lambda u, b: _ollama_reply(
            '{"winner": "B", "confidence": 0.8, "rationale": "warm fewer tokens"}'
        )
    )
    judgment = compare_attempts(left, right, diff, judge)
    assert judgment.winner_attempt_id == right.id
    assert judgment.model == DEFAULT_CHAT_MODEL


# --- fail loud: daemon down, no paid-API fallback --------------------------------


def test_complete_raises_loud_when_daemon_unreachable() -> None:
    def post(url: str, body: bytes) -> bytes:
        raise OSError("connection refused")

    judge = LocalStackComparativeJudge(post=post)
    with pytest.raises(LocalStackUnavailableError, match="will not fall back to a paid API"):
        judge.complete("prompt")


def test_complete_raises_on_non_json_reply() -> None:
    judge = LocalStackComparativeJudge(post=lambda u, b: b"<html>502 Bad Gateway</html>")
    with pytest.raises(ComparativeJudgeError, match="not valid JSON"):
        judge.complete("prompt")


def test_complete_raises_when_reply_missing_response_field() -> None:
    judge = LocalStackComparativeJudge(post=lambda u, b: json.dumps({"done": True}).encode())
    with pytest.raises(ComparativeJudgeError, match="response"):
        judge.complete("prompt")


# --- preflight: delegates to the stack, fails loud -------------------------------


def test_preflight_passes_when_daemon_and_chat_model_present() -> None:
    judge = LocalStackComparativeJudge()

    def fetch(url: str) -> bytes:
        return _tags(f"{DEFAULT_CHAT_MODEL}:latest", f"{DEFAULT_OLLAMA_EMBEDDING_MODEL}:latest")

    judge.preflight(fetch=fetch)  # does not raise


def test_preflight_requires_chat_model_pulled() -> None:
    # Only the embedder is present; an instruct judge must still fail loud.
    judge = LocalStackComparativeJudge()

    def fetch(url: str) -> bytes:
        return _tags(f"{DEFAULT_OLLAMA_EMBEDDING_MODEL}:latest")

    with pytest.raises(LocalStackUnavailableError, match=f"ollama pull {DEFAULT_CHAT_MODEL}"):
        judge.preflight(fetch=fetch)


def test_preflight_raises_when_daemon_unreachable() -> None:
    judge = LocalStackComparativeJudge()

    def fetch(url: str) -> bytes:
        raise OSError("connection refused")

    with pytest.raises(LocalStackUnavailableError, match="ollama serve"):
        judge.preflight(fetch=fetch)


def _payload(**fields: object) -> bytes:
    base: dict[str, object] = {"model": DEFAULT_CHAT_MODEL, "response": "half an answer"}
    base.update(fields)
    return json.dumps(base).encode()


def test_a_generation_the_daemon_abandoned_raises() -> None:
    """The failure this endpoint reports without looking like one: HTTP 200,
    done=false, no done_reason, no token counts, and text that stops mid-line.
    Returning it would hand a judge a verdict computed over less than the caller
    sent, with nothing downstream able to tell."""
    judge = LocalStackComparativeJudge(post=lambda u, b: _payload(done=False))
    with pytest.raises(ComparativeJudgeError, match="unfinished generation"):
        judge.complete("a very long prompt")


def test_a_generation_cut_off_by_the_token_limit_names_that_cause() -> None:
    """Distinguished from the context case because the fix differs: raise
    num_predict, or ask the prompt for less."""
    judge = LocalStackComparativeJudge(post=lambda u, b: _payload(done=True, done_reason="length"))
    with pytest.raises(GenerationLengthError, match="num_predict"):
        judge.complete("a prompt whose answer runs long")


def test_the_two_unfinished_generations_are_different_types() -> None:
    """A caller re-sends one and must not re-send the other: an abandoned
    generation succeeded on the next identical attempt, while a length stop under
    greedy decoding reproduces itself token for token. The distinction is not in
    the message, so a retry loop can only act on it if it is in the type. Both
    stay `ComparativeJudgeError`, so existing handlers keep working."""
    abandoned = LocalStackComparativeJudge(post=lambda u, b: _payload(done=False))
    with pytest.raises(ComparativeJudgeError) as caught:
        abandoned.complete("a very long prompt")
    assert not isinstance(caught.value, GenerationLengthError)


def test_a_reply_that_stopped_on_its_own_is_returned() -> None:
    judge = LocalStackComparativeJudge(
        post=lambda u, b: _payload(response="whole", done=True, done_reason="stop")
    )
    assert judge.complete("a prompt") == "whole"


def test_a_payload_with_no_done_field_is_accepted() -> None:
    """Some daemon versions omit it, and so does every injected double already in
    this file. Refusing those would break callers over a field they never had."""
    judge = LocalStackComparativeJudge(post=lambda u, b: _payload(response="whole"))
    assert judge.complete("a prompt") == "whole"
