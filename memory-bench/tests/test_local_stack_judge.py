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

from membench.bbon.comparative_judge import ComparativeJudgeError, compare_attempts
from membench.bbon.local_stack_judge import (
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
    assert payload == {"model": "llama3.1", "prompt": "the prompt", "stream": False}
    assert json.loads(reply)["winner"] == "B"


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
        id=deterministic_id({"arm": "cold"}), work_id="w", arm="cold",
        status="failed", result={"total_tokens": 900},  # type: ignore[arg-type]
    )
    right = Attempt(
        id=deterministic_id({"arm": "warm"}), work_id="w", arm="warm",
        status="completed", result={"total_tokens": 300},  # type: ignore[arg-type]
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
