"""Tests for `membench.bbon.comparative_judge`: the stub judge, prompt assembly,
reply parsing (the validation gate), the full `compare_attempts` orchestration, and
the headless `claude -p` judge driven through an INJECTED runner — no real claude is
ever spawned, no network is touched."""

import json
import subprocess
from typing import Any

import pytest

from membench.bbon.comparative_judge import (
    CLI_DEFAULT_MODEL,
    ClaudeComparativeJudge,
    ComparativeJudgeError,
    StubComparativeJudge,
    build_judge_prompt,
    compare_attempts,
    judge_cache_key,
    parse_judgment_reply,
)
from membench.bbon.models import Attempt, NarrativeDiff, deterministic_id
from membench.bbon.narrative_diff import generate_narrative_diff


def _attempt(arm: str, status: str = "completed", **result: object) -> Attempt:
    return Attempt(
        id=deterministic_id({"arm": arm}),
        work_id=f"w-{arm}",
        arm=arm,
        status=status,  # type: ignore[arg-type]
        result=result,
    )


def _diff() -> tuple[Attempt, Attempt, NarrativeDiff]:
    left = _attempt("cold", "failed", total_tokens=900)
    right = _attempt("warm", "completed", total_tokens=300)
    return left, right, generate_narrative_diff(left, right, [], [])


# --- StubComparativeJudge ---------------------------------------------------------


def test_stub_winner_mode_emits_parseable_json() -> None:
    judge = StubComparativeJudge(winner="A", confidence=0.7, rationale="cold did more")
    reply = judge.complete("ignored prompt")
    parsed = json.loads(reply)
    assert parsed == {"winner": "A", "confidence": 0.7, "rationale": "cold did more"}
    assert judge.model == "stub"


def test_stub_fn_mode_receives_prompt() -> None:
    seen: list[str] = []

    def fn(prompt: str) -> str:
        seen.append(prompt)
        return '{"winner": "B", "confidence": 0.9, "rationale": "warm"}'

    judge = StubComparativeJudge(fn=fn)
    judge.complete("the prompt")
    assert seen == ["the prompt"]


def test_stub_requires_exactly_one_mode() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        StubComparativeJudge()
    with pytest.raises(ValueError, match="exactly one"):
        StubComparativeJudge(winner="A", fn=lambda _p: "{}")


def test_stub_rejects_bad_winner() -> None:
    with pytest.raises(ValueError, match="winner must be"):
        StubComparativeJudge(winner="left")


# --- cache key + prompt -----------------------------------------------------------


def test_judge_cache_key_is_deterministic_and_field_sensitive() -> None:
    base = judge_cache_key("l", "r", "v1", "stub")
    assert base == judge_cache_key("l", "r", "v1", "stub")
    assert base != judge_cache_key("l", "r", "v1", "claude")
    assert base != judge_cache_key("r", "l", "v1", "stub")


def test_build_prompt_names_both_arms_and_asks_for_json() -> None:
    left, right, diff = _diff()
    prompt = build_judge_prompt(left, right, diff)
    assert "Attempt A is the cold arm" in prompt
    assert "Attempt B is the warm arm" in prompt
    assert '"winner": "A" | "B"' in prompt


def test_build_prompt_rejects_unknown_version() -> None:
    left, right, diff = _diff()
    with pytest.raises(ValueError, match="unknown prompt version"):
        build_judge_prompt(left, right, diff, prompt_version="v2")


# --- parse_judgment_reply (the validation gate) -----------------------------------


def test_parse_maps_winner_a_to_left() -> None:
    left, right, _ = _diff()
    judgment = parse_judgment_reply(
        '{"winner": "A", "confidence": 0.8, "rationale": "x"}',
        left,
        right,
        content_hash=deterministic_id({"k": 1}),
        model="stub",
    )
    assert judgment.winner_attempt_id == left.id
    assert judgment.confidence == 0.8


def test_parse_tolerates_surrounding_prose() -> None:
    left, right, _ = _diff()
    reply = (
        'Sure! Here is my verdict:\n{"winner": "B", "confidence": 0.6, "rationale": "warm"}\nDone.'
    )
    judgment = parse_judgment_reply(
        reply, left, right, content_hash=deterministic_id({"k": 1}), model="stub"
    )
    assert judgment.winner_attempt_id == right.id


@pytest.mark.parametrize(
    "reply, match",
    [
        ("no json here", "no JSON object"),
        ("{not valid json}", "not valid JSON"),
        ('{"winner": "C", "confidence": 0.5, "rationale": "x"}', "winner must be"),
        ('{"winner": "A", "confidence": "high", "rationale": "x"}', "not a number"),
        ('{"winner": "A", "confidence": 1.4, "rationale": "x"}', "out of"),
        ('{"winner": "A", "confidence": 0.5, "rationale": ""}', "rationale missing"),
        ('{"winner": "A", "confidence": true, "rationale": "x"}', "not a number"),
    ],
)
def test_parse_rejects_malformed_replies(reply: str, match: str) -> None:
    left, right, _ = _diff()
    with pytest.raises(ComparativeJudgeError, match=match):
        parse_judgment_reply(
            reply, left, right, content_hash=deterministic_id({"k": 1}), model="stub"
        )


# --- compare_attempts orchestration -----------------------------------------------


def test_compare_attempts_full_path_with_stub() -> None:
    left, right, diff = _diff()
    judge = StubComparativeJudge(winner="B", confidence=0.85, rationale="warm fewer tokens")
    judgment = compare_attempts(left, right, diff, judge)
    assert judgment.winner_attempt_id == right.id
    assert judgment.model == "stub"
    assert judgment.prompt_version == "v1"
    assert judgment.content_hash == judge_cache_key(left.id, right.id, "v1", "stub")


# --- ClaudeComparativeJudge (injected runner; no real claude) ---------------------


def _fake_completed(stdout: str, returncode: int = 0, stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_claude_unwraps_cli_json_result() -> None:
    captured: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = argv
        inner = '{"winner": "B", "confidence": 0.9, "rationale": "warm"}'
        return _fake_completed(json.dumps({"result": inner, "usage": {}}))

    judge = ClaudeComparativeJudge(runner=runner)
    text = judge.complete("the prompt")
    assert json.loads(text)["winner"] == "B"
    # default (no model pinned): the CLI default is recorded, no --model flag passed.
    assert judge.model == CLI_DEFAULT_MODEL
    assert "--model" not in captured["argv"]
    assert captured["argv"][:2] == ["claude", "-p"]
    assert "--output-format" in captured["argv"]


def test_claude_passes_explicit_model_flag() -> None:
    captured: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = argv
        return _fake_completed("raw text reply")

    judge = ClaudeComparativeJudge(model="claude-haiku-4-5", runner=runner)
    judge.complete("p")
    assert judge.model == "claude-haiku-4-5"
    assert captured["argv"][-2:] == ["--model", "claude-haiku-4-5"]


def test_claude_raw_stdout_passed_through_when_not_wrapper() -> None:
    judge = ClaudeComparativeJudge(runner=lambda *a, **k: _fake_completed("plain reply"))
    assert judge.complete("p") == "plain reply"


def test_claude_non_zero_exit_raises() -> None:
    judge = ClaudeComparativeJudge(
        runner=lambda *a, **k: _fake_completed("", returncode=2, stderr="boom")
    )
    with pytest.raises(ComparativeJudgeError, match=r"exit 2.*boom"):
        judge.complete("p")


def test_claude_missing_binary_raises() -> None:
    def runner(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("claude")

    judge = ClaudeComparativeJudge(runner=runner)
    with pytest.raises(ComparativeJudgeError, match="not found"):
        judge.complete("p")


def test_claude_timeout_raises() -> None:
    def runner(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=90.0)

    judge = ClaudeComparativeJudge(runner=runner)
    with pytest.raises(ComparativeJudgeError, match="did not respond"):
        judge.complete("p")


def test_claude_env_model_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMBENCH_COMPARATIVE_JUDGE_MODEL", "env-model")
    captured: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = argv
        return _fake_completed("x")

    judge = ClaudeComparativeJudge(runner=runner)
    assert judge.model == "env-model"
    judge.complete("p")
    assert captured["argv"][-2:] == ["--model", "env-model"]
