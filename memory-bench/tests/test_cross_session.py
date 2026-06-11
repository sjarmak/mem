"""Unit tests for the cross-session iteration metrics (mem-75t.9 PHASE 3).

Synthetic session views and stream-json texts only — no real transcripts, no
store, no subprocess extractor (a stub stands in for `mem extract-errors`).
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from membench.cross_session import (
    BeadCrossSession,
    PairMetrics,
    SessionView,
    aggregate_metrics,
    baseline_signatures,
    bead_cross_session,
    build_session_view,
    pair_metrics,
)


def _view(
    session_id: str,
    start: str | None = None,
    *,
    files_read: frozenset[str] = frozenset(),
    relaxed: frozenset[str] = frozenset(),
    turns: int = 1,
    tool_calls: int = 0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> SessionView:
    return SessionView(
        session_id=session_id,
        transcript_path=f"/t/{session_id}.jsonl",
        start=start,
        end=start,
        turns=turns,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        files_read=files_read,
        relaxed_signatures=relaxed,
        exact_signatures=relaxed,
    )


# --- build_session_view ------------------------------------------------------


def _stream_text() -> str:
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b.ts"}},
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": "src/a.ts(12,5): error TS2345: bad arg",
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 50, "output_tokens": 10},
            },
        },
    ]
    return "\n".join(json.dumps(e) for e in events)


def _stub_extractor(output: str) -> Sequence[Mapping[str, Any]]:
    assert "TS2345" in output
    return [
        {
            "tool": "tsc",
            "file": "src/a.ts",
            "line": 12,
            "error_class": "TS2345",
            "message": "bad arg",
            "signature": "tsc:src/a.ts:12:TS2345",
        }
    ]


def test_build_session_view_projects_stream() -> None:
    view = build_session_view(
        session_id="s1",
        transcript_path="/t/s1.jsonl",
        stream_text=_stream_text(),
        extractor=_stub_extractor,
        start="2026-06-01T10:00:00.000Z",
        end="2026-06-01T11:00:00.000Z",
    )
    assert view.turns == 2
    assert view.tool_calls == 1
    assert view.input_tokens == 150
    assert view.output_tokens == 30
    assert view.files_read == frozenset({"/a/b.ts"})
    # relaxed = tool:basename:error_class (line + dir dropped)
    assert view.relaxed_signatures == frozenset({"tsc:a.ts:TS2345"})
    assert view.exact_signatures == frozenset({"tsc:src/a.ts:12:TS2345"})
    assert view.start == "2026-06-01T10:00:00.000Z"


def test_build_session_view_skips_extractor_on_empty_output() -> None:
    def explode(_: str) -> Sequence[Mapping[str, Any]]:
        raise AssertionError("extractor must not run on empty output")

    view = build_session_view(
        session_id="s1",
        transcript_path="/t/s1.jsonl",
        stream_text="",
        extractor=explode,
    )
    assert view.relaxed_signatures == frozenset()
    assert view.turns == 0


# --- pair metrics ------------------------------------------------------------


def test_pair_redundant_reads() -> None:
    prev = _view("s1", files_read=frozenset({"a", "b", "c"}))
    nxt = _view("s2", files_read=frozenset({"b", "c", "d", "e"}))
    pair = pair_metrics(prev, nxt)
    assert pair.redundant_reads == 2
    assert pair.next_reads == 4
    assert pair.redundant_read_fraction == 0.5


def test_pair_no_next_reads_is_typed_absence() -> None:
    pair = pair_metrics(_view("s1", files_read=frozenset({"a"})), _view("s2"))
    assert pair.next_reads == 0
    assert pair.redundant_read_fraction is None


def test_pair_recurrence_true() -> None:
    prev = _view("s1", relaxed=frozenset({"tsc:a.ts:TS2345", "go:x.go:undefined: y"}))
    nxt = _view("s2", relaxed=frozenset({"tsc:a.ts:TS2345"}))
    pair = pair_metrics(prev, nxt)
    assert pair.recurrence is True
    assert pair.recurred_signatures == ("tsc:a.ts:TS2345",)


def test_pair_recurrence_false_when_next_clean() -> None:
    pair = pair_metrics(_view("s1", relaxed=frozenset({"tsc:a.ts:TS2345"})), _view("s2"))
    assert pair.recurrence is False
    assert pair.recurred_signatures == ()


def test_pair_recurrence_excludes_baseline_signatures() -> None:
    baseline = frozenset({"misspell:hooks.go:misspell"})
    prev = _view("s1", relaxed=baseline | {"tsc:a.ts:TS2345"})
    nxt = _view("s2", relaxed=baseline | {"tsc:a.ts:TS2345"})
    pair = pair_metrics(prev, nxt, exclude=baseline)
    assert pair.recurrence is True
    assert pair.recurred_signatures == ("tsc:a.ts:TS2345",)
    # when ONLY the baseline signature recurs, the pair stops being eligible
    only_noise = pair_metrics(
        _view("s1", relaxed=baseline), _view("s2", relaxed=baseline), exclude=baseline
    )
    assert only_noise.recurrence is None


def test_pair_gap_seconds() -> None:
    prev = _view("s1", "2026-06-01T10:00:00Z")
    nxt = _view("s2", "2026-06-01T11:30:00Z")
    pair = pair_metrics(prev, nxt)
    assert pair.gap_seconds == 5400.0
    # unknown bound -> typed absence
    assert pair_metrics(_view("s1"), nxt).gap_seconds is None


def test_baseline_signatures_threshold() -> None:
    def bead(work_id: str, sig: str) -> BeadCrossSession:
        return bead_cross_session(work_id, [_view("a", relaxed=frozenset({sig}))])

    beads = [
        bead("mem-1", "misspell:hooks.go:misspell"),
        bead("mem-2", "misspell:hooks.go:misspell"),
        bead("mem-3", "misspell:hooks.go:misspell"),
        bead("mem-4", "tsc:a.ts:TS2345"),
    ]
    assert baseline_signatures(beads, min_beads=3) == frozenset({"misspell:hooks.go:misspell"})
    assert baseline_signatures(beads, min_beads=5) == frozenset()


def test_pair_recurrence_none_when_prev_had_no_errors() -> None:
    pair = pair_metrics(_view("s1"), _view("s2", relaxed=frozenset({"tsc:a.ts:TS2345"})))
    assert pair.recurrence is None


# --- per-bead metrics --------------------------------------------------------


def test_bead_cross_session_orders_by_start_and_sums_cost() -> None:
    v2 = _view("s2", "2026-06-02T00:00:00Z", turns=3, tool_calls=4, input_tokens=10)
    v1 = _view("s1", "2026-06-01T00:00:00Z", turns=2, tool_calls=1, input_tokens=5)
    bead = bead_cross_session("mem-1", [v2, v1])
    assert [s.session_id for s in bead.sessions] == ["s1", "s2"]
    assert bead.iterations == 2
    assert len(bead.pairs) == 1
    assert bead.total_turns == 5
    assert bead.total_tool_calls == 5
    assert bead.total_input_tokens == 15
    assert bead.total_output_tokens is None


def test_bead_cross_session_unknown_start_sorts_last() -> None:
    bead = bead_cross_session("mem-1", [_view("s2", None), _view("s1", "2026-06-01T00:00:00Z")])
    assert [s.session_id for s in bead.sessions] == ["s1", "s2"]


# --- aggregation -------------------------------------------------------------


def _bead(work_id: str, views: list[SessionView]) -> BeadCrossSession:
    return bead_cross_session(work_id, views)


def test_aggregate_metrics_summary() -> None:
    beads = [
        _bead(
            "mem-1",
            [
                _view(
                    "a",
                    "2026-06-01T00:00:00Z",
                    files_read=frozenset({"f1", "f2"}),
                    relaxed=frozenset({"sig1"}),
                ),
                _view(
                    "b",
                    "2026-06-02T00:00:00Z",
                    files_read=frozenset({"f1", "f3"}),
                    relaxed=frozenset({"sig1"}),
                ),
            ],
        ),
        _bead(
            "mem-2",
            [
                _view("c", "2026-06-01T00:00:00Z", relaxed=frozenset({"sig2"})),
                _view("d", "2026-06-02T00:00:00Z"),
                _view("e", "2026-06-03T00:00:00Z"),
            ],
        ),
    ]
    summary = aggregate_metrics(beads)
    assert summary["n_beads"] == 2
    assert summary["iterations_histogram"] == {2: 1, 3: 1}
    assert summary["n_pairs"] == 3
    # mem-1 a->b: 1 of 2 next reads redundant; other pairs have no next reads.
    assert summary["pairs_with_next_reads"] == 1
    assert summary["mean_redundant_read_fraction"] == 0.5
    # eligible pairs (prev had errors): mem-1 a->b (recurred), mem-2 c->d (not).
    assert summary["recurrence_eligible_pairs"] == 2
    assert summary["recurrent_pairs"] == 1
    assert summary["pair_recurrence_rate"] == 0.5
    assert summary["beads_with_eligible_pair"] == 2
    assert summary["beads_with_recurrence"] == 1
    assert summary["bead_recurrence_rate"] == 0.5


def test_aggregate_metrics_empty() -> None:
    summary = aggregate_metrics([])
    assert summary["n_beads"] == 0
    assert summary["mean_redundant_read_fraction"] is None
    assert summary["pair_recurrence_rate"] is None


def test_pair_metrics_is_frozen_value() -> None:
    pair = pair_metrics(_view("s1"), _view("s2"))
    assert isinstance(pair, PairMetrics)
