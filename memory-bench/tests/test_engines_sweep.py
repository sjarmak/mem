"""Tests for the pure sweep aggregation + workload builders."""

from pathlib import Path

import pytest

from membench.engines.client import StreamChunk, assemble_stream_metrics
from membench.engines.metrics_scrape import EngineRuntimeStats
from membench.engines.sweep import aggregate_rows, percentile
from membench.engines.workload import load_prompts_jsonl, prefix_sharing_workload

# ---- percentile -----------------------------------------------------------------


def test_percentile_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 0.5) == 3.0
    assert percentile(values, 0.9) == 5.0
    assert percentile(values, 1.0) == 5.0


def test_percentile_empty_is_none() -> None:
    assert percentile([], 0.5) is None


def test_percentile_rejects_out_of_range_q() -> None:
    with pytest.raises(ValueError):
        percentile([1.0], 1.5)


# ---- aggregate_rows -------------------------------------------------------------


def _result(ttft: float, itls: list[float]) -> object:
    # Build a StreamResult via the real assembler so derived metrics are consistent.
    times = [ttft]
    for gap in itls:
        times.append(times[-1] + gap)
    events = [(t, StreamChunk(text="x")) for t in times]
    return assemble_stream_metrics(events, t_start=0.0)


def test_aggregate_counts_failures_and_throughput() -> None:
    results = [_result(0.1, [0.05, 0.05]), _result(0.2, [0.05])]
    before = EngineRuntimeStats(0.1, 0.0, 0, 0, 10.0)
    after = EngineRuntimeStats(0.7, 0.9, 2, 5, 13.0)
    row = aggregate_rows(
        engine="vllm",
        concurrency=4,
        requests=3,  # one more dispatched than completed → 1 failure
        results=results,  # type: ignore[arg-type]
        wall_s=2.0,
        before=before,
        after=after,
    )
    assert row.engine == "vllm"
    assert row.completed == 2
    assert row.failed == 1
    assert row.request_throughput == pytest.approx(1.0)  # 2 completed / 2.0s
    assert row.kv_cache_usage_before == 0.1
    assert row.kv_cache_usage_after == 0.7
    assert row.prefix_cache_hit_rate_after == 0.9
    assert row.num_waiting_after == 5
    assert row.preemptions_delta == pytest.approx(3.0)  # 13 - 10
    assert row.ttft_p50_s is not None


def test_aggregate_handles_missing_snapshots() -> None:
    row = aggregate_rows(
        engine="sglang",
        concurrency=1,
        requests=1,
        results=[_result(0.1, [0.05])],  # type: ignore[arg-type]
        wall_s=1.0,
        before=None,
        after=None,
    )
    assert row.kv_cache_usage_before is None
    assert row.preemptions_delta is None


# ---- workload -------------------------------------------------------------------


def test_prefix_sharing_workload_shape() -> None:
    prompts = prefix_sharing_workload(groups=2, prompts_per_group=3, prefix_words=40)
    assert len(prompts) == 6
    # All prompts in group 0 share the same system prefix; group 1 differs.
    g0_prefix = prompts[0][0]["content"]
    assert all(p[0]["content"] == g0_prefix for p in prompts[:3])
    assert prompts[3][0]["content"] != g0_prefix
    # tails vary within a group
    assert prompts[0][1]["content"] != prompts[1][1]["content"]


def test_prefix_workload_rejects_bad_counts() -> None:
    with pytest.raises(ValueError):
        prefix_sharing_workload(groups=0, prompts_per_group=1, prefix_words=10)


def test_load_prompts_jsonl_both_shapes(tmp_path: Path) -> None:
    f = tmp_path / "prompts.jsonl"
    f.write_text(
        '{"messages": [{"role": "system", "content": "ctx"}, {"role": "user", "content": "q"}]}\n'
        '{"prompt": "just a string"}\n',
        encoding="utf-8",
    )
    prompts = load_prompts_jsonl(f)
    assert len(prompts) == 2
    assert prompts[0][0] == {"role": "system", "content": "ctx"}
    assert prompts[1] == [{"role": "user", "content": "just a string"}]


def test_load_prompts_jsonl_malformed_fails_loud(tmp_path: Path) -> None:
    f = tmp_path / "bad.jsonl"
    f.write_text('{"nope": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="expected 'messages' list or 'prompt' string"):
        load_prompts_jsonl(f)
