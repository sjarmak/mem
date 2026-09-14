"""The recording is the saved transcript replayed verbatim, paced for reading, never re-run."""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from membench.runner.headless_agent import assistant_event, serialize_stream, tool_result_event

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_leg_cast.py"


def render_module() -> Any:
    spec = importlib.util.spec_from_file_location("render_leg_cast", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # The script declares a dataclass under ``from __future__ import annotations``; dataclasses
    # resolves those string annotations through ``sys.modules[cls.__module__]``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _text_event(text: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _receipt(use: str, argv: list[str], code: int) -> list[dict[str, Any]]:
    identity = {"invocation_id": "inv", "tool_use_id": use, "session_id": "s", "leg_id": "leg"}
    return [
        {**identity, "event": "start", "operation_argv": argv},
        {**identity, "event": "finish", "operation_argv": argv, "returncode": code, "stdout": ""},
    ]


def _leg(*, result_lines: int = 1) -> dict[str, Any]:
    command = "bd remember --key window 'toolreq-current'"
    stream = serialize_stream(
        [
            {"type": "system", "subtype": "init", "cwd": "/work", "session_id": "s"},
            _text_event("The retention window changed, so I will revise the stored value."),
            assistant_event([("Bash", {"command": command}, "u1")]),
            tool_result_event("u1", "Updated [window]: toolreq-current"),
            assistant_event([("Bash", {"command": "bd recall window"}, "u2")]),
            tool_result_event("u2", "\n".join(f"line {i}" for i in range(result_lines))),
            assistant_event([("Write", {"file_path": "/work/config.json", "content": "{}"}, "u3")]),
            {
                "type": "result",
                "duration_ms": 41000,
                "total_cost_usd": 0.07,
                "usage": {"output_tokens": 900},
            },
        ]
    )
    return {
        "rung": "R4",
        "variant": "necessary",
        "work_id": "world-seed0-task1",
        "leg": 1,
        "role": "revise",
        "status": "ok",
        "stream": stream,
        "bd_receipts": _receipt("u1", ["remember", "--key", "window", "toolreq-current"], 0)
        + _receipt("u2", ["recall", "window"], 1),
        "bd_evidence": {
            "leg": 1,
            "role": "revise",
            "status": "ok",
            "bd_capture_complete": True,
            "bd_calls": [
                {
                    "position": 0,
                    "verb": "remember",
                    "key": "window",
                    "outcome": "updated",
                    "current_values": ["toolreq-current"],
                },
                {"position": 1, "verb": "recall", "key": "window", "outcome": "returned"},
            ],
        },
    }


def _output(lines: list[str]) -> tuple[dict[str, Any], list[list[Any]], str]:
    """The header, the timed events, and the replayed text with its colour codes removed."""
    header = json.loads(lines[0])
    events = [json.loads(line) for line in lines[1:]]
    text = re.sub(r"\x1b\[[0-9;]*m", "", "".join(event[2] for event in events))
    return header, events, text


def test_the_cast_replays_every_exchange_with_its_receipt_and_score() -> None:
    mod = render_module()
    header, events, text = _output(mod.cast_lines(_leg(), limit=12))
    assert header["version"] == 2 and header["width"] == 120 and header["height"] == 30
    assert header["title"] == "revise world-seed0-task1 necessary"
    times = [event[0] for event in events]
    assert times == sorted(times) and times[0] == 0.0
    assert all(event[1] == "o" for event in events)
    assert "revise session" in text
    assert "bd remember --key window 'toolreq-current'" in text
    assert "Updated [window]: toolreq-current" in text
    assert "exit 0" in text and "exit 1" in text
    assert "bd remember --key window toolreq-current" in text  # the receipt's argv
    assert "I will revise the stored value" in text
    assert "> Write /work/config.json" in text
    assert "no result recorded" in text  # u3 was never answered
    assert "remember [window] → " in text and "updated" in text
    assert "current toolreq-current" in text
    assert "41s reported by the CLI" in text and "900 output tokens" in text
    assert "\n" not in text.replace("\r\n", "")


def test_typed_commands_are_emitted_one_character_at_a_time() -> None:
    mod = render_module()
    _, events, _ = _output(mod.cast_lines(_leg(), limit=12))
    singles = [event for event in events if len(event[2]) == 1]
    assert len(singles) >= len("bd recall window")


def test_long_results_are_clipped_and_the_clip_is_announced() -> None:
    mod = render_module()
    _, _, text = _output(mod.cast_lines(_leg(result_lines=40), limit=12))
    assert "line 11" in text and "line 12" not in text
    assert "(+28 lines)" in text


def test_speed_scales_the_clock_and_must_be_positive() -> None:
    mod = render_module()
    _, slow, _ = _output(mod.cast_lines(_leg(), limit=12))
    _, fast, _ = _output(mod.cast_lines(_leg(), speed=2.0, limit=12))
    assert fast[-1][0] == pytest.approx(slow[-1][0] / 2, abs=0.01)
    with pytest.raises(ValueError, match="positive"):
        mod.cast_lines(_leg(), speed=0, limit=12)


def test_a_run_directory_renders_one_cast_per_leg_named_by_its_layout(tmp_path: Path) -> None:
    mod = render_module()
    run = tmp_path / "run"
    for condition, leg_index, role in (("explicit", 0, "establish"), ("redirect", 1, "revise")):
        legs = run / "pairs" / condition / "abc123" / "0" / "legs"
        legs.mkdir(parents=True)
        (legs / f"R4__necessary__world-seed0-task1__{leg_index}.json").write_text(
            json.dumps({**_leg(), "leg": leg_index, "role": role})
        )
    out = tmp_path / "casts"
    assert mod.main([str(run), "--out", str(out)]) == 0
    names = sorted(path.name for path in out.iterdir())
    assert names == [
        "explicit__world-seed0-task1__necessary__r0__0-establish.cast",
        "redirect__world-seed0-task1__necessary__r0__1-revise.cast",
    ]
    _, _, text = _output((out / names[1]).read_text().splitlines())
    assert "redirect" in text and "repeat" in text
    with pytest.raises(ValueError, match="No saved legs"):
        mod.render_run(tmp_path / "empty", out, speed=1.0, limit=12, video=False)


def test_an_audit_overlay_shows_the_rescored_score_above_the_saved_one(tmp_path: Path) -> None:
    """The first paid trial was scored before redirections were read, so every saved call was
    unattributed. The audit rescored the same transcripts; a recording made afterwards shows
    both scores, each labelled with the scorer version that produced it."""
    mod = render_module()
    run = tmp_path / "run"
    legs = run / "pairs" / "redirect" / "abc123" / "0" / "legs"
    legs.mkdir(parents=True)
    saved = _leg()
    saved["bd_evidence"] = {
        **saved["bd_evidence"],
        "scoring_version": 4,
        "bd_calls": [
            {"position": 0, "verb": "remember", "key": "window", "outcome": "unattributed"}
        ],
    }
    (legs / "R4__necessary__world-seed0-task1__1.json").write_text(json.dumps(saved))
    pair = {
        "condition": "redirect",
        "work_id": "world-seed0-task1",
        "variant": "necessary",
        "repeat": 0,
    }
    audit = tmp_path / "audit.json"
    rescored = {**_leg()["bd_evidence"], "scoring_version": 5}
    audit.write_text(
        json.dumps({"audit_version": 4, "pairs": [{**pair, "rescored_legs": {"revise": rescored}}]})
    )
    out = tmp_path / "casts"
    assert mod.main([str(run), "--out", str(out), "--audit", str(audit)]) == 0
    _, _, text = _output(next(out.iterdir()).read_text().splitlines())
    assert "rescored by audit (scorer v5)" in text
    assert "saved at run time (scorer v4)" in text
    assert text.index("rescored by audit") < text.index("saved at run time")
    assert text.index("updated") < text.index("unattributed")

    stale = tmp_path / "old-audit.json"
    stale.write_text(json.dumps({"audit_version": 3, "pairs": [pair]}))
    with pytest.raises(ValueError, match="predates"):
        mod.load_audit(stale)


def test_a_single_leg_renders_to_the_named_cast(tmp_path: Path) -> None:
    mod = render_module()
    source = tmp_path / "leg.json"
    source.write_text(json.dumps(_leg()))
    out = tmp_path / "nested" / "leg.cast"
    assert mod.main([str(source), "--out", str(out)]) == 0
    header, _, _ = _output(out.read_text().splitlines())
    assert header["version"] == 2


def test_video_rendering_runs_agg_then_ffmpeg_and_surfaces_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = render_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    cast = tmp_path / "leg.cast"
    cast.write_text("{}\n")
    seen: list[list[str]] = []

    def ok(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    gif, mp4 = mod.write_video(cast, run=ok)
    assert [argv[0] for argv in seen] == ["agg", "ffmpeg"]
    assert seen[0][-2:] == [str(cast), str(gif)] and seen[1][-1] == str(mp4)
    assert gif.suffix == ".gif" and mp4.suffix == ".mp4"

    def broken(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "boom\nfont missing")

    with pytest.raises(RuntimeError, match="agg exited 1") as failure:
        mod.write_video(cast, run=broken)
    assert "boom | font missing" in str(failure.value)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="not installed"):
        mod.write_video(cast, run=ok)
