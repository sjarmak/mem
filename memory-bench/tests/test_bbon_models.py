"""Tests for `membench.bbon.models`: canonical JSON, content-addressed ids, and
the schema validators. Pure — no model, no network."""

import math

import pytest
from pydantic import ValidationError

from membench.bbon.models import (
    Attempt,
    AttemptStep,
    Judgment,
    canonicalize,
    deterministic_id,
)

_HEX64 = "a" * 64


def test_canonicalize_is_key_order_independent() -> None:
    assert canonicalize({"b": 1, "a": 2}) == canonicalize({"a": 2, "b": 1})
    assert canonicalize({"a": 2, "b": 1}) == '{"a":2,"b":1}'


def test_canonicalize_primitives_and_nesting() -> None:
    assert canonicalize(None) == "null"
    assert canonicalize(True) == "true"
    assert canonicalize(False) == "false"
    assert canonicalize("x") == '"x"'
    assert canonicalize(3) == "3"
    assert canonicalize([1, "a", {"k": None}]) == '[1,"a",{"k":null}]'


def test_canonicalize_bool_not_treated_as_int() -> None:
    # bool is an int subclass; the order in canonicalize must catch it first.
    assert canonicalize(True) == "true"
    assert canonicalize(1) == "1"


def test_canonicalize_integer_valued_float_is_compact() -> None:
    assert canonicalize(2.0) == "2"
    assert canonicalize(0.5) == "0.5"


def test_canonicalize_rejects_non_finite_float() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonicalize(math.inf)


def test_canonicalize_rejects_unsupported_type() -> None:
    with pytest.raises(TypeError, match="cannot canonicalize"):
        canonicalize({1, 2, 3})


def test_deterministic_id_is_stable_and_hex64() -> None:
    first = deterministic_id({"work_id": "w1", "arm": "warm"})
    second = deterministic_id({"arm": "warm", "work_id": "w1"})
    assert first == second
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)


def test_deterministic_id_distinguishes_content() -> None:
    assert deterministic_id({"work_id": "w1", "arm": "warm"}) != deterministic_id(
        {"work_id": "w1", "arm": "cold"}
    )


def test_attempt_rejects_bad_id() -> None:
    with pytest.raises(ValidationError):
        Attempt(id="not-hex", work_id="w", arm="warm", status="completed")


def test_attempt_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        Attempt(id=_HEX64, work_id="w", arm="warm", status="running")  # type: ignore[arg-type]


def test_attempt_step_defaults_empty_blocks() -> None:
    step = AttemptStep(id=_HEX64, attempt_id=_HEX64, step_index=0, kind="Read")
    assert step.input == {} and step.output == {} and step.observation == {}


def test_judgment_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        Judgment(
            left_attempt_id=_HEX64,
            right_attempt_id=_HEX64,
            winner_attempt_id=_HEX64,
            confidence=1.5,
            rationale="x",
            model="stub",
            prompt_version="v1",
            content_hash=_HEX64,
        )
