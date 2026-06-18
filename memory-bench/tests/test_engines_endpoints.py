"""Tests for the engine endpoint config + paid-host fence.

No network: this module is config only. The load-bearing fence is that an engine
endpoint refuses a paid managed host (D4/D16), hostname-based so host trickery is
rejected too.
"""

import pytest

from membench.engines.endpoints import (
    DEFAULT_ENGINES,
    EngineEndpoint,
    PaidHostError,
    resolve_engines,
)


def _local(name: str = "vllm") -> dict[str, str]:
    return {
        "name": name,
        "base_url": "http://localhost:8001/v1",
        "metrics_url": "http://localhost:8001/metrics",
        "model": "m",
        "metric_prefix": "vllm",
    }


def test_resolve_defaults_to_three_engines() -> None:
    engines = resolve_engines(env={})
    assert set(engines) == {"vllm", "sglang", "tokenspeed"}
    assert engines["vllm"].base_url.startswith("http://localhost")
    assert engines["sglang"].base_url.startswith("http://localhost")
    assert engines["vllm"].metric_prefix == "vllm"
    assert engines["sglang"].metric_prefix == "sglang"


def test_tokenspeed_reuses_vllm_metrics_surface() -> None:
    # TokenSpeed ships as a vLLM runner: identical /metrics, so it maps under the
    # "vllm" prefix and needs no new scraper code. Distinct port/name, though.
    ts = resolve_engines(env={})["tokenspeed"]
    assert ts.metric_prefix == "vllm"
    assert ts.base_url == "http://localhost:8003/v1"


def test_tokenspeed_env_overrides() -> None:
    engines = resolve_engines(env={"MEMBENCH_TOKENSPEED_BASE_URL": "http://b200-box:8000/v1"})
    assert engines["tokenspeed"].base_url == "http://b200-box:8000/v1"


def test_env_overrides_are_honored() -> None:
    env = {
        "MEMBENCH_VLLM_BASE_URL": "http://gpu-box:9000/v1",
        "MEMBENCH_SGLANG_MODEL": "Qwen2.5-7B",
    }
    engines = resolve_engines(env=env)
    assert engines["vllm"].base_url == "http://gpu-box:9000/v1"
    assert engines["sglang"].model == "Qwen2.5-7B"


def test_url_builders_strip_trailing_slash() -> None:
    ep = EngineEndpoint(**{**_local(), "base_url": "http://localhost:8001/v1/"})
    assert ep.chat_completions_url() == "http://localhost:8001/v1/chat/completions"
    assert ep.completions_url() == "http://localhost:8001/v1/completions"


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://api.openai.com/v1",
        "https://openai.com/v1",
        "https://api.anthropic.com/v1",
        "https://generativelanguage.googleapis.com/v1",
    ],
)
def test_paid_host_is_refused(bad_url: str) -> None:
    with pytest.raises(PaidHostError):
        EngineEndpoint(**{**_local(), "base_url": bad_url})


def test_paid_marker_does_not_overmatch_decoy_host() -> None:
    # A self-hosted host that merely contains the marker as a non-suffix is allowed;
    # the fence is hostname-suffix based, not a bare substring blocklist.
    ep = EngineEndpoint(**{**_local(), "base_url": "http://openai.com.local:8001/v1"})
    assert ep.base_url.endswith("/v1")


def test_metrics_url_is_also_fenced() -> None:
    with pytest.raises(PaidHostError):
        EngineEndpoint(**{**_local(), "metrics_url": "https://api.openai.com/metrics"})


def test_default_engines_snapshot_is_local() -> None:
    assert DEFAULT_ENGINES["vllm"].name == "vllm"
    assert DEFAULT_ENGINES["sglang"].name == "sglang"
