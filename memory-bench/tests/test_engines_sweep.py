"""Tests for the pure sweep aggregation + workload builders."""

from pathlib import Path

import pytest

from membench.engines.client import StreamChunk, assemble_stream_metrics
from membench.engines.metrics_scrape import EngineRuntimeStats
from membench.engines.sweep import aggregate_rows, failed_cells, percentile
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


def test_aggregate_computes_per_cell_delta_hit_rate_for_vllm() -> None:
    # vLLM's prefix_cache_hit_rate is a CUMULATIVE lifetime ratio; the per-cell rate is
    # the delta of the raw counters across the batch: (hits_a-hits_b)/(queries_a-queries_b).
    before = EngineRuntimeStats(
        0.1,
        0.5,
        0,
        0,
        10.0,
        prefix_cache_hits_total=50.0,
        prefix_cache_queries_total=100.0,
    )
    after = EngineRuntimeStats(
        0.7,
        0.7,
        2,
        5,
        13.0,
        prefix_cache_hits_total=140.0,
        prefix_cache_queries_total=200.0,
    )
    row = aggregate_rows(
        engine="vllm",
        concurrency=4,
        requests=2,
        results=[_result(0.1, [0.05]), _result(0.2, [0.05])],  # type: ignore[arg-type]
        wall_s=2.0,
        before=before,
        after=after,
    )
    # (140-50)/(200-100) = 0.9 — the true per-cell rate, NOT the cumulative 0.7.
    assert row.prefix_cache_hit_rate_delta == pytest.approx(0.9)
    # the cumulative snapshots are still recorded for continuity
    assert row.prefix_cache_hit_rate_after == 0.7


def test_delta_hit_rate_none_without_raw_counters() -> None:
    # SGLang has no raw counters → per-cell delta is undefined (fall back to the gauge).
    before = EngineRuntimeStats(0.1, 0.5, 0, 0, None)
    after = EngineRuntimeStats(0.7, 0.83, 2, 5, None)
    row = aggregate_rows(
        engine="sglang",
        concurrency=1,
        requests=1,
        results=[_result(0.1, [0.05])],  # type: ignore[arg-type]
        wall_s=1.0,
        before=before,
        after=after,
    )
    assert row.prefix_cache_hit_rate_delta is None


def test_delta_hit_rate_none_when_no_new_queries() -> None:
    # No queries arrived during the cell → 0/0 is undefined, not a crash or a 0.
    before = EngineRuntimeStats(
        0.1,
        0.5,
        0,
        0,
        1.0,
        prefix_cache_hits_total=50.0,
        prefix_cache_queries_total=100.0,
    )
    after = EngineRuntimeStats(
        0.1,
        0.5,
        0,
        0,
        1.0,
        prefix_cache_hits_total=50.0,
        prefix_cache_queries_total=100.0,
    )
    row = aggregate_rows(
        engine="vllm",
        concurrency=1,
        requests=1,
        results=[_result(0.1, [0.05])],  # type: ignore[arg-type]
        wall_s=1.0,
        before=before,
        after=after,
    )
    assert row.prefix_cache_hit_rate_delta is None


def test_delta_hit_rate_is_zero_for_all_miss_cell() -> None:
    # Queries arrived but none hit → a real 0.0 per-cell rate, distinct from None.
    before = EngineRuntimeStats(
        0.1,
        0.0,
        0,
        0,
        1.0,
        prefix_cache_hits_total=10.0,
        prefix_cache_queries_total=100.0,
    )
    after = EngineRuntimeStats(
        0.1,
        0.0,
        0,
        0,
        1.0,
        prefix_cache_hits_total=10.0,
        prefix_cache_queries_total=228.0,
    )
    row = aggregate_rows(
        engine="vllm",
        concurrency=1,
        requests=1,
        results=[_result(0.1, [0.05])],  # type: ignore[arg-type]
        wall_s=1.0,
        before=before,
        after=after,
    )
    assert row.prefix_cache_hit_rate_delta == 0.0


def test_delta_hit_rate_none_on_counter_reset() -> None:
    # An engine restart between scrapes resets counters → negative delta. A positive
    # ratio from two negatives (or a negative ratio) would be a meaningless headline;
    # the guard must reject it rather than emit a plausible-looking number.
    before = EngineRuntimeStats(
        0.1,
        0.7,
        0,
        0,
        1.0,
        prefix_cache_hits_total=900.0,
        prefix_cache_queries_total=1000.0,
    )
    after = EngineRuntimeStats(
        0.1,
        0.5,
        0,
        0,
        1.0,
        prefix_cache_hits_total=5.0,
        prefix_cache_queries_total=10.0,  # fresh process
    )
    row = aggregate_rows(
        engine="vllm",
        concurrency=1,
        requests=1,
        results=[_result(0.1, [0.05])],  # type: ignore[arg-type]
        wall_s=1.0,
        before=before,
        after=after,
    )
    assert row.prefix_cache_hit_rate_delta is None


def test_failed_cells_gate_flags_rows_with_failures() -> None:
    clean = aggregate_rows(
        engine="vllm",
        concurrency=1,
        requests=1,
        results=[_result(0.1, [0.05])],  # type: ignore[arg-type]
        wall_s=1.0,
        before=None,
        after=None,
    )
    dirty = aggregate_rows(
        engine="vllm",
        concurrency=4,
        requests=3,
        results=[_result(0.1, [0.05])],  # type: ignore[arg-type]  # 2 dispatched lost
        wall_s=1.0,
        before=None,
        after=None,
    )
    assert clean.failed == 0
    assert dirty.failed == 2
    flagged = failed_cells([clean, dirty])
    assert flagged == [dirty]  # only the cell with swallowed failures is flagged


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


def test_cache_bust_makes_prefixes_unique_across_cells() -> None:
    # A per-cell salt isolates the prefix cache between cells replayed against a live
    # engine: same args + different salt → no shared prefix block, so cell B cannot hit
    # cell A's cache. Within a cell the sharing structure is preserved.
    a = prefix_sharing_workload(groups=1, prompts_per_group=3, prefix_words=40, cache_bust="cellA")
    b = prefix_sharing_workload(groups=1, prompts_per_group=3, prefix_words=40, cache_bust="cellB")
    # cross-cell: no prefix is shared (the salt leads the prefix)
    assert a[0][0]["content"] != b[0][0]["content"]
    assert not a[0][0]["content"].startswith(b[0][0]["content"][:20])
    # within-cell sharing is intact: all three group-0 prompts share salted prefix
    assert all(p[0]["content"] == a[0][0]["content"] for p in a)


def test_cache_bust_empty_is_backward_compatible() -> None:
    # Default (no salt) must produce the exact prior prefix shape.
    salted = prefix_sharing_workload(groups=1, prompts_per_group=1, prefix_words=24, cache_bust="")
    plain = prefix_sharing_workload(groups=1, prompts_per_group=1, prefix_words=24)
    assert salted[0][0]["content"] == plain[0][0]["content"]
    assert plain[0][0]["content"].startswith("[memory-group-0]")


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
