"""Tests for the Prometheus scraper and the cross-engine runtime-stats mapping.

The fetch is injected; no network. The mapping must keep "metric absent" (None)
distinct from "metric is zero".
"""

import pytest

from membench.engines.metrics_scrape import (
    EngineRuntimeStats,
    parse_prometheus,
    scrape_engine_stats,
)

_VLLM_TEXT = """\
# HELP vllm:gpu_cache_usage_perc GPU KV-cache usage.
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc{model_name="m"} 0.42
vllm:num_requests_running{model_name="m"} 8.0
vllm:num_requests_waiting{model_name="m"} 3.0
vllm:num_preemptions_total{model_name="m"} 5.0
vllm:prefix_cache_hits_total{model_name="m"} 75.0
vllm:prefix_cache_queries_total{model_name="m"} 100.0
"""

_SGLANG_TEXT = """\
sglang:token_usage 0.61
sglang:num_running_reqs 12
sglang:num_queue_reqs 4
sglang:cache_hit_rate 0.83
"""


def test_parse_prometheus_basic() -> None:
    samples = parse_prometheus(_VLLM_TEXT)
    by_name = {s.name: s for s in samples}
    assert by_name["vllm:gpu_cache_usage_perc"].value == 0.42
    assert by_name["vllm:gpu_cache_usage_perc"].labels == {"model_name": "m"}
    # comment lines are skipped
    assert all(not s.name.startswith("#") for s in samples)


def test_parse_tolerates_trailing_timestamp_and_no_labels() -> None:
    samples = parse_prometheus("metric_a 1.5 1700000000\nmetric_b 2.0\n")
    assert {s.name: s.value for s in samples} == {"metric_a": 1.5, "metric_b": 2.0}


def test_vllm_mapping_computes_prefix_hit_rate_from_counters() -> None:
    stats = EngineRuntimeStats.from_samples(parse_prometheus(_VLLM_TEXT), "vllm")
    assert stats.kv_cache_usage == 0.42
    assert stats.prefix_cache_hit_rate == pytest.approx(0.75)
    assert stats.num_running == 8.0
    assert stats.num_waiting == 3.0
    assert stats.num_preemptions_total == 5.0


def test_vllm_exposes_raw_prefix_cache_counters() -> None:
    # The cumulative ratio alone can't give a per-cell hit rate; aggregate_rows needs
    # the raw before/after counters to compute the delta. Expose them on the snapshot.
    stats = EngineRuntimeStats.from_samples(parse_prometheus(_VLLM_TEXT), "vllm")
    assert stats.prefix_cache_hits_total == 75.0
    assert stats.prefix_cache_queries_total == 100.0


def test_sglang_has_no_raw_prefix_cache_counters() -> None:
    # SGLang exports only a windowed cache_hit_rate gauge, no raw counters → None,
    # so the per-cell delta is undefined for SGLang and we fall back to the gauge.
    stats = EngineRuntimeStats.from_samples(parse_prometheus(_SGLANG_TEXT), "sglang")
    assert stats.prefix_cache_hits_total is None
    assert stats.prefix_cache_queries_total is None


def test_vllm_reads_new_kv_cache_usage_name() -> None:
    # Newer vLLM / the NIM build renamed the gauge; the scraper must read it (the live
    # validation against the NeMo NIM caught the old name returning None).
    text = 'vllm:kv_cache_usage_perc{engine="0"} 0.37\n'
    stats = EngineRuntimeStats.from_samples(parse_prometheus(text), "vllm")
    assert stats.kv_cache_usage == 0.37


def test_vllm_kv_cache_prefers_new_name_over_old() -> None:
    text = "vllm:kv_cache_usage_perc 0.50\nvllm:gpu_cache_usage_perc 0.10\n"
    stats = EngineRuntimeStats.from_samples(parse_prometheus(text), "vllm")
    assert stats.kv_cache_usage == 0.50  # new name wins; old kept only as fallback


def test_sglang_mapping_reads_direct_gauges() -> None:
    stats = EngineRuntimeStats.from_samples(parse_prometheus(_SGLANG_TEXT), "sglang")
    assert stats.kv_cache_usage == 0.61
    assert stats.prefix_cache_hit_rate == 0.83
    assert stats.num_running == 12
    assert stats.num_waiting == 4
    # SGLang text here exports no preemption counter → absent, not zero.
    assert stats.num_preemptions_total is None


def test_absent_metric_is_none_not_zero() -> None:
    stats = EngineRuntimeStats.from_samples(
        parse_prometheus("vllm:num_requests_running 0\n"), "vllm"
    )
    assert stats.num_running == 0.0
    assert stats.kv_cache_usage is None  # absent, distinct from 0


def test_prefix_hit_rate_none_when_no_queries() -> None:
    text = "vllm:prefix_cache_hits_total 0\nvllm:prefix_cache_queries_total 0\n"
    stats = EngineRuntimeStats.from_samples(parse_prometheus(text), "vllm")
    assert stats.prefix_cache_hit_rate is None  # 0 queries → undefined, not 0/0 crash


def test_unknown_prefix_raises() -> None:
    with pytest.raises(ValueError, match="unknown engine metric prefix"):
        EngineRuntimeStats.from_samples([], "tensorrt")


def test_scrape_with_injected_fetch() -> None:
    stats = scrape_engine_stats(
        "http://localhost:8001/metrics", "vllm", fetch=lambda url: _VLLM_TEXT
    )
    assert stats.kv_cache_usage == 0.42
