"""Tests for the autotune substrate: config validation, objective scoring, the
keep/discard ledger, and the sweep runner driven with an injected transport (no
network). The agent's next-config decision is NOT tested — there is none to test
(it lives in the model, per ZFC); only the deterministic substrate is.
"""

import json
from pathlib import Path

import pytest

from membench.autotune.config import TrialConfig
from membench.autotune.ledger import (
    TrialRecord,
    append_record,
    best_record,
    keep_decision,
    next_trial_id,
    read_ledger,
)
from membench.autotune.objective import TrialObjective, score_rows
from membench.engines.client import StreamChunk
from membench.engines.endpoints import EngineEndpoint
from membench.engines.run import sweep_cell
from membench.engines.sweep import SweepRow

# ---- config ---------------------------------------------------------------------


def _cfg(**over: object) -> TrialConfig:
    base: dict[str, object] = {
        "engine": "vllm",
        "concurrencies": [1, 4],
        "max_tokens": 64,
        "temperature": 0.0,
        "groups": 1,
        "prompts_per_group": 8,
        "prefix_words": 100,
    }
    base.update(over)
    return TrialConfig.from_dict(base)


def test_config_roundtrips_through_dict() -> None:
    cfg = _cfg()
    assert TrialConfig.from_dict(cfg.to_dict()) == cfg
    assert cfg.total_requests == 8


def test_config_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown config keys"):
        TrialConfig.from_dict({"engine": "vllm", "concurrencies": [1], "max_token": 5})


def test_config_rejects_bad_engine_and_values() -> None:
    with pytest.raises(ValueError, match="engine must be one of"):
        _cfg(engine="tensorrt")
    with pytest.raises(ValueError, match="concurrencies"):
        _cfg(concurrencies=[0])
    with pytest.raises(ValueError, match="temperature"):
        _cfg(temperature=5.0)


def test_config_from_json_file(tmp_path: Path) -> None:
    f = tmp_path / "c.json"
    f.write_text(json.dumps(_cfg().to_dict()), encoding="utf-8")
    assert TrialConfig.from_json_file(f).engine == "vllm"


# ---- objective ------------------------------------------------------------------


def _row(concurrency: int, tps: float | None, ttft: float | None) -> SweepRow:
    return SweepRow(
        engine="vllm",
        concurrency=concurrency,
        requests=10,
        completed=10,
        failed=0,
        wall_s=1.0,
        request_throughput=10.0,
        output_token_throughput=tps,
        ttft_p50_s=ttft,
        ttft_p90_s=ttft,
        itl_median_p50_s=0.01,
        output_tps_p50=tps,
        kv_cache_usage_before=0.1,
        kv_cache_usage_after=0.5,
        prefix_cache_hit_rate_before=None,
        prefix_cache_hit_rate_after=0.8,
        num_waiting_after=0.0,
        preemptions_delta=0.0,
    )


def test_objective_picks_max_tps_within_slo() -> None:
    rows = [
        _row(1, tps=100.0, ttft=0.2),
        _row(16, tps=400.0, ttft=0.45),  # best: highest tps still under SLO
        _row(32, tps=600.0, ttft=0.9),  # faster but blows the 0.5s SLO → excluded
    ]
    obj = score_rows(rows, ttft_p50_slo_s=0.5)
    assert obj.slo_met is True
    assert obj.score == 400.0
    assert obj.best_concurrency == 16


def test_objective_zero_when_no_cell_meets_slo() -> None:
    rows = [_row(32, tps=600.0, ttft=0.9)]
    obj = score_rows(rows, ttft_p50_slo_s=0.5)
    assert obj.score == 0.0
    assert obj.slo_met is False
    assert obj.best_concurrency is None


def test_objective_tie_breaks_to_lower_concurrency() -> None:
    rows = [_row(8, tps=300.0, ttft=0.3), _row(32, tps=300.0, ttft=0.4)]
    obj = score_rows(rows, ttft_p50_slo_s=0.5)
    assert obj.best_concurrency == 8  # same tps, cheaper cell wins


def test_objective_rejects_nonpositive_slo() -> None:
    with pytest.raises(ValueError):
        score_rows([_row(1, 100.0, 0.2)], ttft_p50_slo_s=0.0)


# ---- ledger ---------------------------------------------------------------------


def _record(trial_id: int, score: float) -> TrialRecord:
    obj = TrialObjective(
        score=score,
        slo_met=score > 0,
        best_concurrency=4 if score > 0 else None,
        best_output_tps=score if score > 0 else None,
        best_ttft_p50_s=0.3 if score > 0 else None,
        ttft_p50_slo_s=0.5,
        cells_evaluated=2,
    )
    return TrialRecord(trial_id=trial_id, config=_cfg(), objective=obj)


def test_ledger_append_and_read_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "ledger.jsonl"
    append_record(path, _record(0, 100.0))
    append_record(path, _record(1, 250.0))
    records = read_ledger(path)
    assert [r.trial_id for r in records] == [0, 1]
    assert records[1].objective.score == 250.0
    assert records[1].config == _cfg()


def test_read_missing_ledger_is_empty(tmp_path: Path) -> None:
    assert read_ledger(tmp_path / "nope.jsonl") == []


def test_malformed_ledger_row_fails_loud(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    path.write_text('{"trial_id": 0}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed ledger row"):
        read_ledger(path)


def test_next_trial_id_and_best() -> None:
    records = [_record(0, 100.0), _record(1, 300.0), _record(2, 200.0)]
    assert next_trial_id(records) == 3
    assert next_trial_id([]) == 0
    assert best_record(records).trial_id == 1  # type: ignore[union-attr]
    assert best_record([]) is None


def test_keep_decision_requires_strict_improvement() -> None:
    best = _record(0, 300.0)
    assert keep_decision(_record(1, 301.0), best) is True
    assert keep_decision(_record(1, 300.0), best) is False  # ties don't churn incumbent
    assert keep_decision(_record(0, 0.0), None) is True  # first trial always kept


# ---- runner (injected transport, no network) ------------------------------------


def test_sweep_cell_with_injected_transport_and_scraper() -> None:
    endpoint = EngineEndpoint(
        name="vllm",
        base_url="http://localhost:8001/v1",
        metrics_url="http://localhost:8001/metrics",
        model="m",
        metric_prefix="vllm",
    )

    def fake_transport(url: str, payload: bytes) -> list[str]:
        # Two token chunks + a finish chunk, as SSE lines.
        return [
            'data: {"choices": [{"delta": {"content": "a"}}]}',
            'data: {"choices": [{"delta": {"content": "b"}}]}',
            'data: {"choices": [{"finish_reason": "stop"}]}',
            "data: [DONE]",
        ]

    ticks = iter([float(i) for i in range(1000)])

    def client_factory(ep: EngineEndpoint):  # type: ignore[no-untyped-def]
        from membench.engines.client import StreamingClient

        return StreamingClient(endpoint=ep, transport=fake_transport, clock=lambda: next(ticks))

    row = sweep_cell(
        endpoint,
        concurrency=2,
        workload=[[{"role": "user", "content": "hi"}], [{"role": "user", "content": "yo"}]],
        max_tokens=8,
        temperature=0.0,
        logprobs=False,
        client_factory=client_factory,
        scraper=lambda ep: None,  # no metrics endpoint in this test
        clock=lambda: next(ticks),
    )
    assert row.engine == "vllm"
    assert row.requests == 2
    assert row.completed == 2
    assert row.failed == 0
    # Each request produced 2 tokens via the fake stream.
    assert row.output_token_throughput is not None


def test_sweep_cell_counts_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EngineEndpoint(
        name="vllm",
        base_url="http://localhost:8001/v1",
        metrics_url="http://localhost:8001/metrics",
        model="m",
        metric_prefix="vllm",
    )

    def boom_transport(url: str, payload: bytes) -> list[str]:
        raise OSError("connection refused")

    def client_factory(ep: EngineEndpoint):  # type: ignore[no-untyped-def]
        from membench.engines.client import StreamingClient

        return StreamingClient(endpoint=ep, transport=boom_transport)

    row = sweep_cell(
        endpoint,
        concurrency=1,
        workload=[[{"role": "user", "content": "hi"}]],
        max_tokens=8,
        temperature=0.0,
        logprobs=False,
        client_factory=client_factory,
        scraper=lambda ep: None,
    )
    assert row.completed == 0
    assert row.failed == 1  # the failed request is counted, the cell does not abort


def test_streamchunk_importable() -> None:
    # guard: the runner test relies on the client's chunk shape staying public.
    assert StreamChunk(text="x").text == "x"
