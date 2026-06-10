"""Probe-grade direct scorer (mem-75t.7.6, plan §9.2).

Covers the three gate legs: file-set F1 + changed-line Jaccard + the multiplicative
combined score (`score_probe_direct`), efficiency extraction from a synthetic Claude
Code stream-json transcript with and without usage data (`extract_efficiency`), and
the poor-man oracle-rung payload (`gold_file_list`). All fixtures are synthetic --
no subprocesses, no model calls.
"""

import json

import pytest

from membench.bundle.replay import ReplayResult
from membench.grading.probe_direct import (
    ProbeDirectScore,
    ProbeEfficiency,
    changed_lines,
    extract_efficiency,
    gold_file_list,
    score_probe_direct,
)
from membench.schemas.bundle import BundleEnv, TaskBundle


def _diff(path: str, *body: str) -> str:
    """A minimal unified diff for one file; ``body`` lines carry their +/- prefix."""
    header = (
        f"diff --git a/{path} b/{path}\n"
        f"index 0000000..1111111 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,2 +1,2 @@\n"
    )
    return header + "".join(f"{line}\n" for line in body)


# --- changed_lines: the mechanical unified-diff projection ------------------------


def test_changed_lines_tags_additions_and_removals() -> None:
    diff = _diff("f.py", " ctx", "-old line", "+new line")
    assert changed_lines(diff) == frozenset({("-", "old line"), ("+", "new line")})


def test_changed_lines_excludes_file_headers() -> None:
    # ---/+++ header lines must not register as removals/additions.
    diff = _diff("f.py", " ctx")
    assert changed_lines(diff) == frozenset()


# --- score_probe_direct: file-set F1 edge cases -----------------------------------


def test_identical_diffs_score_one() -> None:
    gold = {"a.py": _diff("a.py", "-x", "+y"), "b.py": _diff("b.py", "+z")}
    score = score_probe_direct(gold, gold)
    assert score == ProbeDirectScore(
        file_precision=1.0,
        file_recall=1.0,
        file_f1=1.0,
        per_file_overlap={"a.py": 1.0, "b.py": 1.0},
        hunk_overlap=1.0,
        combined=1.0,
    )


def test_disjoint_file_sets_score_zero() -> None:
    candidate = {"a.py": _diff("a.py", "+x")}
    gold = {"b.py": _diff("b.py", "+x")}
    score = score_probe_direct(candidate, gold)
    assert score.file_precision == 0.0
    assert score.file_recall == 0.0
    assert score.file_f1 == 0.0
    assert score.per_file_overlap == {}
    assert score.hunk_overlap == 0.0
    assert score.combined == 0.0


def test_partial_file_overlap_f1() -> None:
    shared = _diff("b.py", "-old", "+new")
    candidate = {"a.py": _diff("a.py", "+x"), "b.py": shared}
    gold = {"b.py": shared, "c.py": _diff("c.py", "+y")}
    score = score_probe_direct(candidate, gold)
    assert score.file_precision == pytest.approx(0.5)
    assert score.file_recall == pytest.approx(0.5)
    assert score.file_f1 == pytest.approx(0.5)
    # The single overlapping file matches exactly.
    assert score.per_file_overlap == {"b.py": 1.0}
    assert score.hunk_overlap == pytest.approx(1.0)
    assert score.combined == pytest.approx(0.5)


def test_empty_candidate_scores_zero() -> None:
    score = score_probe_direct({}, {"a.py": _diff("a.py", "+x")})
    assert score.file_precision == 0.0
    assert score.file_recall == 0.0
    assert score.file_f1 == 0.0
    assert score.combined == 0.0


def test_empty_gold_diff_raises() -> None:
    with pytest.raises(ValueError, match="empty gold diff"):
        score_probe_direct({"a.py": _diff("a.py", "+x")}, {})


# --- score_probe_direct: hunk overlap on synthetic diffs --------------------------


def test_hunk_overlap_is_changed_line_jaccard() -> None:
    candidate = {"f.py": _diff("f.py", "+x", "+y")}
    gold = {"f.py": _diff("f.py", "+y", "+z")}
    score = score_probe_direct(candidate, gold)
    # Changed-line sets {+x,+y} vs {+y,+z}: |inter|=1, |union|=3.
    assert score.per_file_overlap == {"f.py": pytest.approx(1 / 3)}
    assert score.hunk_overlap == pytest.approx(1 / 3)
    assert score.file_f1 == pytest.approx(1.0)
    assert score.combined == pytest.approx(1 / 3)


def test_hunk_overlap_distinguishes_addition_from_removal() -> None:
    # The same text added on one side and removed on the other must NOT overlap.
    candidate = {"f.py": _diff("f.py", "+same text")}
    gold = {"f.py": _diff("f.py", "-same text")}
    score = score_probe_direct(candidate, gold)
    assert score.hunk_overlap == 0.0
    assert score.combined == 0.0


def test_both_empty_changed_line_sets_overlap_one() -> None:
    # A mode-only / header-only diff on both sides is an identical (empty) change.
    candidate = {"f.py": _diff("f.py", " ctx")}
    gold = {"f.py": _diff("f.py", " ctx")}
    score = score_probe_direct(candidate, gold)
    assert score.per_file_overlap == {"f.py": 1.0}
    assert score.combined == 1.0


def test_hunk_overlap_averaged_across_overlapping_files() -> None:
    candidate = {
        "a.py": _diff("a.py", "+x"),  # exact match -> 1.0
        "b.py": _diff("b.py", "+p"),  # disjoint lines -> 0.0
    }
    gold = {
        "a.py": _diff("a.py", "+x"),
        "b.py": _diff("b.py", "+q"),
    }
    score = score_probe_direct(candidate, gold)
    assert score.hunk_overlap == pytest.approx(0.5)
    assert score.combined == pytest.approx(0.5)  # f1 = 1.0


# --- extract_efficiency: synthetic stream-json transcripts ------------------------


def _assistant(blocks: list[dict], usage: dict | None = None) -> dict:
    message: dict = {"content": blocks}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "message": message}


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/x"}}
TOOL_RESULT = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}


def test_extract_efficiency_with_usage() -> None:
    stream = _stream(
        {"type": "system", "subtype": "init"},
        _assistant([TOOL_USE, TOOL_USE], usage={"input_tokens": 10, "output_tokens": 5}),
        {"type": "user", "message": {"content": [TOOL_RESULT]}},
        _assistant(
            [{"type": "text", "text": "done"}], usage={"input_tokens": 7, "output_tokens": 3}
        ),
    )
    assert extract_efficiency(stream) == ProbeEfficiency(
        turns=2, tool_calls=2, input_tokens=17, output_tokens=8
    )


def test_extract_efficiency_without_usage_returns_none_tokens() -> None:
    stream = _stream(
        _assistant([TOOL_USE]),
        _assistant([{"type": "text", "text": "done"}]),
    )
    eff = extract_efficiency(stream)
    assert eff.turns == 2
    assert eff.tool_calls == 1
    assert eff.input_tokens is None
    assert eff.output_tokens is None


def test_extract_efficiency_partial_usage_sums_present_fields() -> None:
    stream = _stream(
        _assistant([TOOL_USE], usage={"input_tokens": 4, "output_tokens": 2}),
        _assistant([{"type": "text", "text": "x"}]),  # no usage on this event
    )
    eff = extract_efficiency(stream)
    assert eff.input_tokens == 4
    assert eff.output_tokens == 2


def test_extract_efficiency_tolerates_non_json_and_ignores_non_assistant() -> None:
    stream = "\n".join(
        [
            "not json at all",
            "",
            json.dumps({"type": "user", "message": {"content": [TOOL_RESULT]}}),
            json.dumps(_assistant([TOOL_USE])),
            json.dumps({"type": "result", "usage": {"input_tokens": 999}}),
        ]
    )
    eff = extract_efficiency(stream)
    assert eff.turns == 1
    assert eff.tool_calls == 1
    # The cumulative result event is deliberately NOT a token source (double count).
    assert eff.input_tokens is None


def test_extract_efficiency_empty_stream() -> None:
    assert extract_efficiency("") == ProbeEfficiency(
        turns=0, tool_calls=0, input_tokens=None, output_tokens=None
    )


# --- gold_file_list: the poor-man oracle-rung payload -----------------------------


def test_gold_file_list_sorted_paths() -> None:
    bundle = TaskBundle(
        work_id="mem-x.1",
        rig="mem",
        issue_title="fix the thing",
        trace_ref="/traces/mem-x.1.jsonl",
        output=ReplayResult(
            calls=(),
            file_diffs={"src/b.py": _diff("src/b.py", "+x"), "src/a.py": _diff("src/a.py", "+y")},
            replay_success_rate=1.0,
        ),
        env=BundleEnv(repo="org/mem", base_commit="abc123", base_image="img:1"),
        loo_excluded_work_ids=("mem-x.1",),
    )
    assert gold_file_list(bundle) == ("src/a.py", "src/b.py")
