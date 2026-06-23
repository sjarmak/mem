"""Pointwise relevance judge (mem-lvp.31): strict parser, neutral-projection
anti-circularity, leak scan on the fully assembled prompt, deterministic cache key.

Hermetic — the only judge is a pointwise ``StubComparativeJudge``, no model or
network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from membench.bbon.comparative_judge import StubComparativeJudge
from membench.compare.relevance_judge import (
    SIGNATURE_FIELD_NAMES,
    RelevanceInputs,
    RelevanceJudgeError,
    RelevanceVerdict,
    build_relevance_prompt,
    parse_relevance_verdict,
    relevance_cache_key,
    score_relevance,
)
from membench.grading.leak_guard import OutcomeLeakError

PROMPT_VERSION = "rel-v1"


def _inputs(**overrides: Any) -> RelevanceInputs:
    base: dict[str, Any] = {
        "query_work_id": "B",
        "candidate_work_id": "A",
        "query_text": "the test suite hangs when two trials share a temp dir",
        "candidate_text": (
            "reset() must allocate a fresh per-trial scope; reusing one leaked state "
            "across trials and wedged the run. The transferable lesson: isolate trial "
            "state behind the trial id."
        ),
    }
    base.update(overrides)
    return RelevanceInputs(**base)


def _binary_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "relevant": True,
        "transferable_lesson": "isolate per-trial state behind the trial id",
        "rationale": "the candidate's trial-isolation fix is the lesson B needs",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _graded_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "grade": 3,
        "transferable_lesson": "isolate per-trial state behind the trial id",
        "rationale": "the candidate's trial-isolation fix is the lesson B needs",
    }
    payload.update(overrides)
    return json.dumps(payload)


# --------------------------------------------------------------------------- #
# Strict parser — binary
# --------------------------------------------------------------------------- #
def test_parse_binary_ok() -> None:
    v = parse_relevance_verdict(_binary_json(), mode="binary")
    assert isinstance(v, RelevanceVerdict)
    assert v.relevant is True
    assert v.grade is None
    assert v.transferable_lesson
    assert v.rationale


def test_parse_binary_tolerates_prose_around_json() -> None:
    reply = "Here is my verdict:\n" + _binary_json() + "\nThanks."
    v = parse_relevance_verdict(reply, mode="binary")
    assert v.relevant is True


def test_parse_binary_missing_relevant_raises() -> None:
    bad = json.dumps({"transferable_lesson": "x", "rationale": "y"})
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(bad, mode="binary")


def test_parse_binary_non_bool_relevant_raises() -> None:
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(_binary_json(relevant=1), mode="binary")


def test_parse_no_json_raises() -> None:
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict("no json here", mode="binary")


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict("{not valid", mode="binary")


def test_parse_empty_rationale_raises() -> None:
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(_binary_json(rationale="   "), mode="binary")


def test_parse_empty_transferable_lesson_raises() -> None:
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(_binary_json(transferable_lesson=""), mode="binary")


# --------------------------------------------------------------------------- #
# Strict parser — graded
# --------------------------------------------------------------------------- #
def test_parse_graded_ok() -> None:
    v = parse_relevance_verdict(_graded_json(), mode="graded")
    assert v.grade == 3
    assert v.relevant is None


def test_parse_graded_out_of_range_raises() -> None:
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(_graded_json(grade=4), mode="graded")
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(_graded_json(grade=-1), mode="graded")


def test_parse_graded_non_int_raises() -> None:
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(_graded_json(grade=True), mode="graded")
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(_graded_json(grade=2.5), mode="graded")


def test_parse_graded_missing_grade_raises() -> None:
    bad = json.dumps({"transferable_lesson": "x", "rationale": "y"})
    with pytest.raises(RelevanceJudgeError):
        parse_relevance_verdict(bad, mode="graded")


def test_parse_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        parse_relevance_verdict(_binary_json(), mode="ternary")


# --------------------------------------------------------------------------- #
# Prompt — pre-registered criterion (usefulness, not error-token overlap)
# --------------------------------------------------------------------------- #
def test_prompt_states_usefulness_criterion() -> None:
    prompt = build_relevance_prompt(_inputs(), mode="binary", prompt_version=PROMPT_VERSION)
    low = prompt.lower()
    assert "usefulness" in low or "help solve" in low or "plausibly help" in low
    # Explicitly forbids surface error-token overlap as sufficient.
    assert "overlap" in low
    # Requires the rationale to name the transferable lesson.
    assert "transferable_lesson" in prompt
    assert "transferable lesson" in low


def test_prompt_contains_query_and_candidate_text() -> None:
    inp = _inputs()
    prompt = build_relevance_prompt(inp, mode="binary", prompt_version=PROMPT_VERSION)
    assert inp.query_text in prompt
    assert inp.candidate_text in prompt


def test_prompt_binary_vs_graded_response_shape() -> None:
    binary = build_relevance_prompt(_inputs(), mode="binary", prompt_version=PROMPT_VERSION)
    graded = build_relevance_prompt(_inputs(), mode="graded", prompt_version=PROMPT_VERSION)
    assert '"relevant"' in binary
    assert '"grade"' in graded


def test_prompt_unknown_version_raises() -> None:
    with pytest.raises(ValueError):
        build_relevance_prompt(_inputs(), mode="binary", prompt_version="nope")


# --------------------------------------------------------------------------- #
# Anti-circularity — no signature field NAME in any assembled prompt
# --------------------------------------------------------------------------- #
def _signature_field_renderings(field: str) -> list[str]:
    """The forms a STRUCTURED signature field would take if it were rendered into the
    prompt: a quoted JSON key, or a ``label:`` / ``label =`` scaffold. Bare-substring
    matching is meaningless here (``pr`` is in "approach", ``file`` in "profile"); a
    leak is the field surfacing AS A FIELD, not as a fragment of ordinary prose."""
    return [f'"{field}"', f"{field}:", f"{field} :", f"{field}=", f"{field} ="]


def test_no_signature_field_name_appears_in_prompt() -> None:
    # The scaffolding (everything except the two neutral text projections) must render
    # no signature field as a structured key/label — the judge can never see the
    # failure-signature / parse fields ours retrieves on, so it cannot re-derive ours's
    # retrieval key and judge in ours's favor by construction.
    inp = _inputs()
    for mode in ("binary", "graded"):
        prompt = build_relevance_prompt(inp, mode=mode, prompt_version=PROMPT_VERSION)
        scaffold = prompt.replace(inp.query_text, "").replace(inp.candidate_text, "")
        for field in SIGNATURE_FIELD_NAMES:
            for rendering in _signature_field_renderings(field):
                assert (
                    rendering not in scaffold
                ), f"signature field {field!r} leaked into the {mode} prompt as {rendering!r}"


def test_signature_field_names_cover_identifying_and_d6_10() -> None:
    # IDENTIFYING_KEYS + the D6-10 failure-signature/parse field names.
    from membench.grading.leak_guard import IDENTIFYING_KEYS

    for k in IDENTIFYING_KEYS:
        assert k in SIGNATURE_FIELD_NAMES
    for k in ("signature", "error_class", "file", "line", "tool"):
        assert k in SIGNATURE_FIELD_NAMES


# --------------------------------------------------------------------------- #
# Outcome / B-identity leak scan on the FULLY ASSEMBLED prompt
# --------------------------------------------------------------------------- #
def test_leak_scan_catches_b_identity_in_candidate_text() -> None:
    # B's PR value leaks into the candidate text -> the assembled prompt carries it.
    inp = _inputs(
        candidate_text="this fixes the bug, see PR-9001 for the merge",
        b_pr="PR-9001",
    )
    judge = StubComparativeJudge(fn=lambda _p: _binary_json())
    with pytest.raises(OutcomeLeakError):
        score_relevance(inp, judge, mode="binary", prompt_version=PROMPT_VERSION)


def test_leak_scan_catches_b_resolution_value() -> None:
    inp = _inputs(
        candidate_text="the resolution commit deadbeefcafe nails it",
        b_resolution="deadbeefcafe",
    )
    judge = StubComparativeJudge(fn=lambda _p: _binary_json())
    with pytest.raises(OutcomeLeakError):
        score_relevance(inp, judge, mode="binary", prompt_version=PROMPT_VERSION)


def test_leak_scan_passes_clean_prompt() -> None:
    judge = StubComparativeJudge(fn=lambda _p: _binary_json())
    res = score_relevance(_inputs(), judge, mode="binary", prompt_version=PROMPT_VERSION)
    assert res.verdict.relevant is True
    assert res.judge_prompt_leak_checked is True


# --------------------------------------------------------------------------- #
# score_relevance — end-to-end via a pointwise Stub
# --------------------------------------------------------------------------- #
def test_score_relevance_binary_end_to_end() -> None:
    judge = StubComparativeJudge(fn=lambda _p: _binary_json(relevant=False))
    res = score_relevance(_inputs(), judge, mode="binary", prompt_version=PROMPT_VERSION)
    assert res.verdict.relevant is False
    assert res.cache_key == relevance_cache_key("B", "A", PROMPT_VERSION, judge.model)


def test_score_relevance_graded_end_to_end() -> None:
    judge = StubComparativeJudge(fn=lambda _p: _graded_json(grade=2))
    res = score_relevance(_inputs(), judge, mode="graded", prompt_version=PROMPT_VERSION)
    assert res.verdict.grade == 2


def test_score_relevance_malformed_reply_raises() -> None:
    judge = StubComparativeJudge(fn=lambda _p: "no verdict here")
    with pytest.raises(RelevanceJudgeError):
        score_relevance(_inputs(), judge, mode="binary", prompt_version=PROMPT_VERSION)


# --------------------------------------------------------------------------- #
# Deterministic cache key
# --------------------------------------------------------------------------- #
def test_cache_key_deterministic_and_pair_scoped() -> None:
    k1 = relevance_cache_key("B", "A", PROMPT_VERSION, "stub")
    k2 = relevance_cache_key("B", "A", PROMPT_VERSION, "stub")
    assert k1 == k2
    assert relevance_cache_key("B", "C", PROMPT_VERSION, "stub") != k1
    assert relevance_cache_key("B", "A", "rel-v2", "stub") != k1
    assert relevance_cache_key("B", "A", PROMPT_VERSION, "other") != k1
