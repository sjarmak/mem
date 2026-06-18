"""Tests for the streaming client: SSE parsing, metric assembly, and a full
``complete()`` run with an injected transport + clock — no network, no real time.
"""

import json

import pytest

from membench.engines.client import (
    StreamChunk,
    StreamingClient,
    TokenLogprob,
    assemble_stream_metrics,
    parse_sse_line,
)
from membench.engines.endpoints import EngineEndpoint


def _endpoint() -> EngineEndpoint:
    return EngineEndpoint(
        name="vllm",
        base_url="http://localhost:8001/v1",
        metrics_url="http://localhost:8001/metrics",
        model="test-model",
        metric_prefix="vllm",
    )


def _chunk(text: str = "", finish: str | None = None, logprob: float | None = None) -> str:
    choice: dict[str, object] = {"delta": {"content": text} if text else {}}
    if finish is not None:
        choice["finish_reason"] = finish
    if logprob is not None:
        choice["logprobs"] = {"content": [{"token": text, "logprob": logprob}]}
    return "data: " + json.dumps({"choices": [choice]})


# ---- parse_sse_line -------------------------------------------------------------


def test_parse_blank_and_comment_and_done_are_none() -> None:
    assert parse_sse_line("") is None
    assert parse_sse_line(": keep-alive") is None
    assert parse_sse_line("data: [DONE]") is None


def test_parse_text_delta() -> None:
    chunk = parse_sse_line(_chunk("hello"))
    assert chunk == StreamChunk(text="hello", logprobs=(), finished=False)


def test_parse_finish_reason() -> None:
    chunk = parse_sse_line(_chunk("", finish="stop"))
    assert chunk is not None and chunk.finished is True


def test_parse_logprobs() -> None:
    chunk = parse_sse_line(_chunk("hi", logprob=-0.25))
    assert chunk is not None
    assert chunk.logprobs == (TokenLogprob(token="hi", logprob=-0.25),)


def test_malformed_json_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_sse_line("data: {not json")


def test_unexpected_shape_yields_empty_chunk_not_crash() -> None:
    # A keep-alive-ish data line with no choices must not abort a long generation.
    assert parse_sse_line('data: {"choices": []}') == StreamChunk(text="")


# ---- assemble_stream_metrics ----------------------------------------------------


def test_assemble_ttft_itl_and_throughput() -> None:
    # Sent at t=0; tokens land at t=1, 1.5, 2.0 → ttft=1.0, itls=[0.5, 0.5].
    events = [
        (1.0, StreamChunk(text="a")),
        (1.5, StreamChunk(text="b")),
        (2.0, StreamChunk(text="c", finished=True)),
    ]
    result = assemble_stream_metrics(events, t_start=0.0)
    assert result.text == "abc"
    assert result.ttft_s == 1.0
    assert result.itl_s == (0.5, 0.5)
    assert result.output_tokens == 3
    assert result.total_s == 2.0
    assert result.finish_reason_seen is True
    assert result.median_itl_s() == 0.5
    # decode phase = total - ttft = 1.0s for (3-1) tokens → 2.0 tps.
    assert result.output_tps() == pytest.approx(2.0)


def test_empty_stream_has_no_ttft() -> None:
    result = assemble_stream_metrics([], t_start=0.0)
    assert result.ttft_s is None
    assert result.output_tokens == 0
    assert result.output_tps() is None
    assert result.median_itl_s() is None


def test_median_itl_even_count_averages_middle_pair() -> None:
    events = [
        (1.0, StreamChunk(text="a")),
        (1.2, StreamChunk(text="b")),
        (1.9, StreamChunk(text="c")),
    ]
    # itls = [0.2, 0.7]; median of two = 0.45
    result = assemble_stream_metrics(events, t_start=0.0)
    assert result.median_itl_s() == pytest.approx(0.45)


# ---- build_payload + complete (injected transport/clock) ------------------------


def test_build_payload_sets_stream_and_logprobs() -> None:
    client = StreamingClient(endpoint=_endpoint())
    raw = client.build_payload(
        [{"role": "user", "content": "hi"}],
        max_tokens=64,
        temperature=0.0,
        logprobs=True,
        top_logprobs=5,
    )
    body = json.loads(raw)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["logprobs"] is True
    assert body["top_logprobs"] == 5
    assert body["model"] == "test-model"


def test_complete_with_injected_transport_and_clock() -> None:
    lines = [_chunk("Hel"), _chunk("lo"), _chunk("", finish="stop"), "data: [DONE]"]

    def transport(url: str, payload: bytes) -> list[str]:
        assert url == "http://localhost:8001/v1/chat/completions"
        assert json.loads(payload)["stream"] is True
        return lines

    # Clock: t_start consumed first, then one reading per parsed (non-None) chunk.
    ticks = iter([0.0, 1.0, 1.4, 1.4])
    client = StreamingClient(endpoint=_endpoint(), transport=transport, clock=lambda: next(ticks))
    result = client.complete([{"role": "user", "content": "hi"}])
    assert result.text == "Hello"
    assert result.ttft_s == 1.0
    assert result.output_tokens == 2
    assert result.finish_reason_seen is True
