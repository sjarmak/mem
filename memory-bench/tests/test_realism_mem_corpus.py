"""Tests for the real reference corpus loader (realism axis 1, mem-ovi.1).

Every test injects a fake ``runner`` / ``transcript_reader`` — no live ``.mem``
store, no subprocess, no filesystem access. ``parse_transcript`` is exercised
against hand-built Claude Code transcript JSONL fixtures so the sidechain /
isMeta / tool_result filtering rules are pinned by example.
"""

import json

from membench.realism.mem_corpus import (
    default_message_filter,
    load_real_corpus,
    load_work_records,
    parse_transcript,
)


def _line(**kwargs):
    return json.dumps(kwargs)


def _user(text, **extra):
    return _line(type="user", message={"role": "user", "content": text}, **extra)


def _assistant_text(text, **extra):
    return _line(type="assistant", message={"role": "assistant", "content": text}, **extra)


def _assistant_tool_use(name, **extra):
    block = {"type": "tool_use", "name": name, "input": {}}
    return _line(type="assistant", message={"role": "assistant", "content": [block]}, **extra)


def _tool_result(text="ok", **extra):
    block = {"type": "tool_result", "content": text}
    return _line(type="user", message={"role": "user", "content": [block]}, **extra)


# --- load_work_records -------------------------------------------------------


def test_load_work_records_builds_query_argv_with_filters():
    calls = []

    def fake_runner(args):
        calls.append(args)
        return {"records": [{"work_id": "gc-1"}]}

    records = load_work_records(
        "/store", runner=fake_runner, filters={"rig": "mem", "status": "closed"}
    )
    assert records == [{"work_id": "gc-1"}]
    assert calls == [["query", "--store", "/store", "--rig", "mem", "--status", "closed"]]


def test_load_work_records_no_filters_queries_bare():
    calls = []

    def fake_runner(args):
        calls.append(args)
        return {"records": []}

    load_work_records("/store", runner=fake_runner)
    assert calls == [["query", "--store", "/store"]]


def test_load_work_records_requires_runner_or_mem_bin():
    try:
        load_work_records("/store")
    except ValueError as exc:
        assert "runner" in str(exc) and "mem_bin" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# --- parse_transcript: sidechain / isMeta / tool_result filtering -----------


def test_parse_transcript_keeps_plain_user_and_assistant_turns():
    jsonl = "\n".join([_user("do the thing"), _assistant_text("done")])
    messages, tool_calls = parse_transcript(jsonl)
    assert [(m.role, m.content) for m in messages] == [
        ("user", "do the thing"),
        ("assistant", "done"),
    ]
    assert tool_calls == []


def test_parse_transcript_skips_sidechain_entries():
    jsonl = "\n".join(
        [
            _user("main turn"),
            _line(
                type="user",
                isSidechain=True,
                message={"role": "user", "content": "subagent chatter"},
            ),
        ]
    )
    messages, _ = parse_transcript(jsonl)
    assert [m.content for m in messages] == ["main turn"]


def test_parse_transcript_skips_ismeta_entries_by_default():
    jsonl = "\n".join(
        [
            _user("real ask", isMeta=True),
            _user("second real ask"),
        ]
    )
    messages, _ = parse_transcript(jsonl)
    assert [m.content for m in messages] == ["second real ask"]


def test_parse_transcript_skips_tool_result_shaped_entries():
    jsonl = "\n".join([_user("ask"), _tool_result("some output")])
    messages, _ = parse_transcript(jsonl)
    assert [m.content for m in messages] == ["ask"]


def test_parse_transcript_extracts_tool_use_blocks_as_tool_calls():
    jsonl = "\n".join(
        [
            _user("ask"),
            _assistant_tool_use("Bash"),
            _assistant_tool_use("Read"),
        ]
    )
    _, tool_calls = parse_transcript(jsonl)
    assert [c.name for c in tool_calls] == ["Bash", "Read"]


def test_parse_transcript_tool_use_survives_even_when_entry_is_ismeta():
    # isMeta only gates the MESSAGE, not tool_use extraction — a tool call is a
    # real action the session took regardless of how the surrounding turn is
    # flagged for message-filtering purposes.
    block = {"type": "tool_use", "name": "Grep", "input": {}}
    jsonl = _line(type="assistant", isMeta=True, message={"role": "assistant", "content": [block]})
    messages, tool_calls = parse_transcript(jsonl)
    assert messages == []
    assert [c.name for c in tool_calls] == ["Grep"]


def test_parse_transcript_flattens_only_text_blocks():
    blocks = [
        {"type": "thinking", "text": "internal reasoning, not conversational"},
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]
    jsonl = _line(type="assistant", message={"role": "assistant", "content": blocks})
    messages, _ = parse_transcript(jsonl)
    assert messages[0].content == "first\nsecond"


def test_parse_transcript_ignores_non_user_assistant_entry_types():
    jsonl = "\n".join([_user("ask"), _line(type="system", message={"content": "boilerplate"})])
    messages, _ = parse_transcript(jsonl)
    assert [m.content for m in messages] == ["ask"]


def test_parse_transcript_skips_malformed_lines():
    jsonl = "\n".join([_user("ask"), "{not valid json", ""])
    messages, _ = parse_transcript(jsonl)
    assert [m.content for m in messages] == ["ask"]


def test_parse_transcript_custom_message_filter_overrides_default():
    jsonl = "\n".join([_user("keep"), _user("drop")])
    messages, _ = parse_transcript(
        jsonl, message_filter=lambda entry: entry["message"]["content"] == "keep"
    )
    assert [m.content for m in messages] == ["keep"]


def test_default_message_filter_rejects_ismeta_and_tool_result():
    meta_entry = json.loads(_user("x", isMeta=True))
    tool_result_entry = json.loads(_tool_result())
    plain_entry = json.loads(_user("plain"))
    assert default_message_filter(meta_entry) is False
    assert default_message_filter(tool_result_entry) is False
    assert default_message_filter(plain_entry) is True


# --- load_real_corpus: end-to-end over injected fakes -----------------------


def test_load_real_corpus_maps_records_through_transcripts():
    records = [
        {
            "work_id": "gc-1",
            "rig": "mem",
            "lifecycle": {"started": "2026-07-01T00:00:00Z", "closed": "2026-07-01T01:00:00Z"},
            "agents": [{"agent_id": "worker-1"}],
            "trace": {"jsonl_path": "/traces/gc-1.jsonl"},
        }
    ]
    transcripts = {"/traces/gc-1.jsonl": "\n".join([_user("do it"), _assistant_tool_use("Bash")])}

    traces = load_real_corpus(
        "/store",
        runner=lambda args: {"records": records},
        transcript_reader=lambda path: transcripts[path],
    )
    assert len(traces) == 1
    trace = traces[0]
    assert trace.trial_id == "gc-1"
    assert trace.experiment_id == "mem"
    assert trace.start_time == "2026-07-01T00:00:00Z"
    assert trace.end_time == "2026-07-01T01:00:00Z"
    assert trace.agent_config_id == "worker-1"
    assert [m.content for m in trace.messages] == ["do it"]
    assert [c.name for c in trace.tool_calls] == ["Bash"]
    assert trace.memory_events == []


def test_load_real_corpus_skips_record_with_no_trace_path():
    records = [{"work_id": "gc-2", "rig": "mem", "lifecycle": {"created": "2026-07-01T00:00:00Z"}}]
    traces = load_real_corpus("/store", runner=lambda args: {"records": records})
    assert traces == []


def test_load_real_corpus_skips_unreadable_transcript_without_raising():
    records = [
        {
            "work_id": "gc-3",
            "rig": "mem",
            "lifecycle": {"created": "2026-07-01T00:00:00Z"},
            "trace": {"jsonl_path": "/traces/missing.jsonl"},
        }
    ]

    def _raise(path):
        raise OSError(f"no such file: {path}")

    traces = load_real_corpus(
        "/store", runner=lambda args: {"records": records}, transcript_reader=_raise
    )
    assert traces == []


def test_load_real_corpus_falls_back_to_created_and_unknown_agent():
    records = [
        {
            "work_id": "gc-4",
            "rig": "mem",
            "lifecycle": {"created": "2026-07-01T00:00:00Z"},
            "trace": {"jsonl_path": "/traces/gc-4.jsonl"},
        }
    ]
    traces = load_real_corpus(
        "/store",
        runner=lambda args: {"records": records},
        transcript_reader=lambda path: "",
    )
    assert len(traces) == 1
    trace = traces[0]
    assert trace.start_time == "2026-07-01T00:00:00Z"
    assert trace.end_time == "2026-07-01T00:00:00Z"
    assert trace.agent_config_id == "unknown"
