"""Tests for the semantic realism axis (model-judged, run on the stub judge).

The judge is always ``StubComparativeJudge`` here — no model, no network. The
``fn`` seam lets each case return a crafted raw reply so the full parse path runs.
"""

import json

import pytest

from membench.bbon.comparative_judge import StubComparativeJudge
from membench.realism.semantic import (
    SemanticJudgeError,
    SemanticVerdict,
    aggregate_semantic,
    build_semantic_prompt,
    parse_semantic_verdict,
    score_semantic_realism,
    task_text_for_sequence,
)
from membench.schemas.sequence import BenchmarkSequence, SequenceStep


def _seq(seq_id="s1"):
    return BenchmarkSequence(
        sequence_id=seq_id,
        title="Migrate the billing schema",
        goal="invoices table has a currency column",
        steps=[
            SequenceStep(step_id="a", user_request="add the currency column"),
            SequenceStep(step_id="b", user_request="backfill existing rows"),
        ],
    )


def _reply(realism, reads_real, rationale="looks plausible enough"):
    return json.dumps({"realism": realism, "reads_real": reads_real, "rationale": rationale})


def test_task_text_includes_title_goal_and_steps():
    text = task_text_for_sequence(_seq())
    assert "Migrate the billing schema" in text
    assert "invoices table has a currency column" in text
    assert "Step 1: add the currency column" in text
    assert "Step 2: backfill existing rows" in text


def test_build_prompt_asks_for_json_only():
    prompt = build_semantic_prompt("Title: X\nStep 1: do the thing")
    assert "do the thing" in prompt
    assert "JSON only" in prompt
    assert "reads_real" in prompt


def test_parse_valid_verdict():
    verdict = parse_semantic_verdict(_reply(0.8, True), model="stub")
    assert verdict == SemanticVerdict(
        realism=0.8, reads_real=True, rationale="looks plausible enough", model="stub"
    )


def test_parse_tolerates_surrounding_prose():
    raw = "Sure, here is my verdict:\n" + _reply(0.3, False) + "\nThanks!"
    verdict = parse_semantic_verdict(raw, model="m")
    assert verdict.realism == 0.3
    assert verdict.reads_real is False


def test_parse_skips_preamble_object_and_selects_verdict():
    # A chain-of-thought judge may emit a reasoning object BEFORE the verdict; the
    # parser must select the object carrying 'realism', not the first object.
    raw = '{"thinking": "weighing the phrasing"}\n' + _reply(0.8, True)
    verdict = parse_semantic_verdict(raw, model="m")
    assert verdict.realism == 0.8
    assert verdict.reads_real is True


def test_parse_selects_first_realism_object_so_bad_verdicts_still_raise():
    # An object that HAS the realism key but is malformed must still raise — it is
    # selected (key present) and strictly validated, never skipped for a later one.
    raw = '{"realism": 1.5, "reads_real": true, "rationale": "x"}' + _reply(0.5, True)
    with pytest.raises(SemanticJudgeError, match="out of"):
        parse_semantic_verdict(raw, model="m")


@pytest.mark.parametrize(
    "raw",
    [
        "no json here",
        '{"realism": "high", "reads_real": true, "rationale": "x"}',  # non-numeric
        '{"realism": 1.5, "reads_real": true, "rationale": "x"}',  # out of range
        '{"realism": true, "reads_real": true, "rationale": "x"}',  # bool, not number
        '{"realism": 0.5, "reads_real": "yes", "rationale": "x"}',  # non-bool
        '{"realism": 0.5, "reads_real": true, "rationale": "  "}',  # empty rationale
        '{"realism": 0.5, "reads_real": true}',  # missing rationale
        "[1, 2, 3]",  # json, but not an object
    ],
)
def test_parse_rejects_malformed(raw):
    with pytest.raises(SemanticJudgeError):
        parse_semantic_verdict(raw, model="stub")


def test_score_semantic_realism_drives_the_full_path():
    judge = StubComparativeJudge(fn=lambda prompt: _reply(0.9, True))
    verdict = score_semantic_realism(_seq(), judge)
    assert verdict.realism == 0.9
    assert verdict.reads_real is True
    assert verdict.model == "stub"


def test_aggregate_mean_and_real_fraction():
    verdicts = [
        SemanticVerdict(0.8, True, "r", "m"),
        SemanticVerdict(0.4, False, "r", "m"),
        SemanticVerdict(0.6, True, "r", "m"),
    ]
    agg = aggregate_semantic(verdicts)
    assert agg.n == 3
    assert agg.mean_realism == pytest.approx(0.6)
    assert agg.real_fraction == pytest.approx(2 / 3)


def test_aggregate_pass_gate():
    high = [SemanticVerdict(0.9, True, "r", "m") for _ in range(4)]
    assert aggregate_semantic(high).passes
    low = [SemanticVerdict(0.2, False, "r", "m") for _ in range(4)]
    assert not aggregate_semantic(low).passes
    # Passes the mean floor but not the believability fraction.
    mixed = [
        SemanticVerdict(0.9, True, "r", "m"),
        SemanticVerdict(0.9, False, "r", "m"),
        SemanticVerdict(0.9, False, "r", "m"),
    ]
    assert not aggregate_semantic(mixed, min_realism=0.5, min_real_fraction=0.6).passes


def test_aggregate_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        aggregate_semantic([])
