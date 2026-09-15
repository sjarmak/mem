"""One recording for a whole run: cards, every session in plan order, mechanical labels."""

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from membench.runner.e1_reliability import RELIABILITY_VERSION
from membench.runner.headless_agent import assistant_event, serialize_stream, tool_result_event
from membench.runner.leg_plans import TRIAL_ROLES

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_trial_video.py"
STORE = "/tmp/membench-memory-abc123/config/projects/-tmp-e1-r4-xyz/memory"
CWD = "/tmp/e1-r4-xyz"


def render_module() -> Any:
    spec = importlib.util.spec_from_file_location("render_trial_video", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _receipt(use: str, argv: list[str], code: int, stdout: str) -> list[dict[str, Any]]:
    identity = {"invocation_id": "inv", "tool_use_id": use, "session_id": "s", "leg_id": "leg"}
    return [
        {**identity, "event": "start", "operation_argv": argv},
        {
            **identity,
            "event": "finish",
            "operation_argv": argv,
            "returncode": code,
            "stdout": stdout,
        },
    ]


def _evidence(index: int, role: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scoring_version": RELIABILITY_VERSION,
        "leg": index,
        "role": role,
        "status": "ok",
        "bd_capture_complete": bool(calls),
        "bd_recall_complete": role == "goal",
        "goal_action_success": True if role == "goal" else None,
        "bd_calls": calls,
    }


def _bd_leg(index: int, role: str) -> dict[str, Any]:
    """A session that used bd: a chained establish, an in-place revise, a recall, a stale write."""
    if role == "establish":
        command = (
            "bd remember 'window is 7 years' --key window 2>&1 && bd remember 'eu' --key region"
        )
        receipts = _receipt(
            "u1",
            ["remember", "window is 7 years", "--key", "window"],
            0,
            "Remembered [window]: x\n",
        ) + _receipt("u1", ["remember", "eu", "--key", "region"], 0, "Remembered [region]: eu\n")
        result = "Remembered [window]: x\nRemembered [region]: eu"
        calls = [{"position": 0, "verb": "remember", "key": "window", "outcome": "unattributed"}]
    elif role == "revise":
        command = "bd remember 'window is 5 years' --key window 2>&1"
        receipts = _receipt(
            "u1", ["remember", "window is 5 years", "--key", "window"], 0, "Updated [window]: y\n"
        )
        result = "Updated [window]: y"
        calls = [
            {
                "position": 0,
                "verb": "remember",
                "key": "window",
                "outcome": "updated",
                "current_values": ["5 years"],
            }
        ]
    elif role == "goal":
        command = "bd recall window 2>&1"
        receipts = _receipt("u1", ["recall", "window"], 0, "window is 5 years\n")
        result = "window is 5 years"
        calls = [{"position": 0, "verb": "recall", "key": "window", "outcome": "returned"}]
    else:
        command = "bd prime 2>/dev/null || echo none"
        receipts = _receipt("u1", ["prime"], 0, "[bd prime] ...\n")
        result = "[bd prime] ..."
        calls = []
    events: list[dict[str, Any]] = [
        {"type": "system", "subtype": "init", "cwd": CWD, "session_id": "s"},
        assistant_event([("Bash", {"command": command}, "u1")]),
        tool_result_event("u1", result),
    ]
    if role == "goal":
        events += [
            assistant_event(
                [("Write", {"file_path": f"{CWD}/config.json", "content": "{}"}, "u2")]
            ),
            tool_result_event("u2", "ok"),
        ]
    events.append({"type": "result", "duration_ms": 12000, "usage": {"output_tokens": 50}})
    return {
        "rung": "R4",
        "variant": "necessary",
        "work_id": "world-seed0-task1",
        "leg": index,
        "role": role,
        "status": "ok",
        "cwd": CWD,
        "stream": serialize_stream(events),
        "bd_receipts": receipts,
        "bd_receipt_leg_id": "leg",
        "bd_evidence": _evidence(index, role, calls),
    }


def _native_leg(index: int, role: str) -> dict[str, Any]:
    """A session that never called bd: it read and edited its own Claude Code memory file."""
    events: list[dict[str, Any]] = [
        {"type": "system", "subtype": "init", "cwd": CWD, "session_id": "s"},
        assistant_event([("Read", {"file_path": f"{STORE}/MEMORY.md"}, "u1")]),
        tool_result_event("u1", "- window is 7 years"),
        assistant_event([("Bash", {"command": f"ls {STORE}"}, "u2")]),
        tool_result_event("u2", "MEMORY.md"),
    ]
    if role == "revise":
        events += [
            assistant_event(
                [
                    (
                        "Edit",
                        {"file_path": f"{STORE}/MEMORY.md", "old_string": "7", "new_string": "5"},
                        "u3",
                    )
                ]
            ),
            tool_result_event("u3", "ok"),
        ]
    events.append({"type": "result", "duration_ms": 9000, "usage": {"output_tokens": 20}})
    return {
        **_bd_leg(index, role),
        "stream": serialize_stream(events),
        "bd_receipts": [],
        "bd_evidence": _evidence(index, role, []),
    }


def _manifest() -> dict[str, Any]:
    return {
        "leg_plan": list(TRIAL_ROLES),
        "model": "claude-sonnet-4-6",
        "cli_version": "2.1.271",
        "bd_version": "bd version 1.3.0-rc.1 (e9d2f1778)",
        "conditions": {
            "generic": {"rung": "R4", "bd_context": False, "native_memory_hook_mode": "observe"},
            "redirect": {"rung": "R4", "bd_context": True, "native_memory_hook_mode": "redirect"},
        },
    }


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "bd-history-trial"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps(_manifest()))
    for condition, make in (("redirect", _bd_leg), ("generic", _native_leg)):
        legs = run / "pairs" / condition / "abc123" / "0" / "legs"
        legs.mkdir(parents=True)
        # Written out of plan order on disk: the plan, not the file listing, orders the sessions.
        for index, role in reversed(list(enumerate(TRIAL_ROLES))):
            (legs / f"R4__necessary__world-seed0-task1__{index}.json").write_text(
                json.dumps(make(index, role))
            )
    return run


def _text(cast_path: Path) -> str:
    lines = cast_path.read_text().splitlines()
    events = [json.loads(line) for line in lines[1:]]
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", "".join(event[2] for event in events))


def _positions(text: str, needles: list[str]) -> list[int]:
    found = []
    for needle in needles:
        assert needle in text, needle
        found.append(text.index(needle))
    return found


def test_the_recording_plays_every_session_in_plan_order_between_its_cards(tmp_path: Path) -> None:
    mod = render_module()
    run = _run(tmp_path)
    out = tmp_path / "demo" / "trial.cast"
    assert mod.main([str(run), "--out", str(out)]) == 0
    header = json.loads(out.read_text().splitlines()[0])
    assert header["title"] == "bd version-history trial: bd-history-trial"
    text = _text(out)
    order = _positions(
        text,
        [
            "bd version-history trial: bd-history-trial",
            "8 recorded claude -p sessions: 2 trial(s) of establish → revise → goal → stale_writer",
            "Trial 1 of 2: condition generic",
            "no bd context in the prompt",
            "a hook OBSERVES the agent's own Claude Code memory files",
            "## Memory guidance",
            "Session 1 of 4: establish",
            "Session 2 of 4: revise",
            "Session 3 of 4: goal",
            "Session 4 of 4: stale_writer",
            "Verdicts, condition generic",
            "Trial 2 of 2: condition redirect",
            "the context block bd itself injects into a session is in the prompt",
            "a hook BLOCKS the agent's own Claude Code memory files",
            "Session 1 of 4: establish",
            "Verdicts, condition redirect",
        ],
    )
    # The second trial's "Session 1 of 4" is a later occurrence than the first trial's.
    second_trial = text.index("Trial 2 of 2")
    order[-2] = text.index("Session 1 of 4: establish", second_trial)
    assert order == sorted(order)
    assert text.count("establish session") == 2 and text.count("stale_writer session") == 2
    for role, purpose in mod.ROLE_PURPOSE.items():
        assert purpose.split(".")[0] in text, role


def test_labels_are_read_off_the_receipt_the_path_and_the_command_shape(tmp_path: Path) -> None:
    mod = render_module()
    run = _run(tmp_path)
    out = tmp_path / "trial.cast"
    mod.render_demo(run, out, speed=1.0, limit=12, video=False)
    text = _text(out)
    # bd, from the receipts: one label per chained call, and the compound-command note, which
    # names memory calls only (a chained ``bd prime`` is not one the scorer would attribute).
    assert "one shell command running 2 bd memory call(s) alongside other shell work" in text
    assert "running 1 bd memory call(s)" not in text
    assert "bd remember [window]: stored under a new key" in text
    assert "bd remember [region]: stored under a new key" in text
    assert (
        "bd remember [window]: OVERWROTE the value already stored under this key, in place" in text
    )
    assert "bd recall [window]: read one exact key" in text
    assert "bd prime: loaded the store's context into this session" in text
    # The label sits ABOVE the command it describes.
    assert text.index("OVERWROTE") < text.index("$ bd remember 'window is 5 years'")
    # Native memory, from the path shape; the sandbox write, from the cwd and the role.
    assert "reads its own Claude Code memory file (native memory), not bd" in text
    assert "edits, in place, its own Claude Code memory file (native memory), not bd" in text
    assert (
        "a shell command reaching its own Claude Code memory files (native memory), not bd" in text
    )
    assert "writes a file in the task sandbox (the work the goal session was asked to do)" in text


def test_receipt_labels_cover_every_bd_answer_without_reading_prose() -> None:
    mod = render_module()

    def receipt(argv: list[str], code: int, stdout: str) -> dict[str, Any]:
        return {"operation_argv": argv, "returncode": code, "stdout": stdout}

    assert mod.receipt_label(receipt(["remember", "x", "--key", "k"], 0, "Remembered [k]: x")) == (
        "bd remember [k]: stored under a new key"
    )
    assert "OVERWROTE" in mod.receipt_label(
        receipt(["remember", "x", "--key", "k"], 0, "Updated [k]: x")
    )
    assert "read it back instead of writing" in mod.receipt_label(
        receipt(["remember", "k"], 0, '(recalled "k")\nx')
    )
    assert mod.receipt_label(receipt(["remember", "x", "--key", "k"], 1, "")) == (
        "bd remember [k]: bd refused the write (exit 1)"
    )
    assert mod.receipt_label(receipt(["remember", "x", "--key", "k"], 0, "")) == (
        "bd remember [k]: no acknowledgement from bd"
    )
    assert mod.receipt_label(receipt(["recall", "k"], 1, "")) == "bd recall [k]: exit 1"
    assert (
        mod.receipt_label(receipt(["memories", "q"], 0, "anything"))
        == "bd memories: searched the store"
    )
    assert mod.receipt_label(receipt(["list"], 0, "")) == "bd list: exit 0"
    assert mod.is_native_memory_path(f"{STORE}/MEMORY.md")
    assert not mod.is_native_memory_path("/tmp/membench-memory-abc123/config/settings.json")
    assert not mod.is_native_memory_path("/home/me/memory/notes.md")


def test_verdicts_come_from_the_audit_when_given_else_from_the_saved_scores(tmp_path: Path) -> None:
    mod = render_module()
    run = _run(tmp_path)
    saved = tmp_path / "saved.cast"
    mod.render_demo(run, saved, speed=1.0, limit=12, video=False)
    text = _text(saved)
    assert f"derived from the scores saved at run time (scorer v{RELIABILITY_VERSION})" in text
    redirect = text[text.index("Verdicts, condition redirect") :]
    assert re.search(r"what session 2 did to the stored value\s+updated_in_place", redirect)
    assert re.search(r"session 3 did the work with the current values\s+yes", redirect)
    assert re.search(r"what session 4 did when it wrote\s+no_write", redirect)
    assert re.search(r"after a rejected write\s+not applicable \(no write was rejected\)", redirect)

    pair = {
        "condition": "redirect",
        "work_id": "world-seed0-task1",
        "variant": "necessary",
        "repeat": 0,
    }
    rescored_leg = {**_evidence(1, "revise", []), "scoring_version": RELIABILITY_VERSION + 1}
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "audit_version": 4,
                "pairs": [
                    {
                        **pair,
                        "rescored_legs": {"revise": rescored_leg},
                        "trial_rescored": {"revision": "wrote_beside", "stale_write": "rejected"},
                    }
                ],
            }
        )
    )
    audited = tmp_path / "audited.cast"
    mod.render_demo(run, audited, speed=1.0, limit=12, video=False, audit=audit)
    text = _text(audited)
    redirect = text[text.index("Verdicts, condition redirect") :]
    assert "rescored by audit_bd_actions (audit v4)" in redirect
    assert re.search(r"what session 2 did to the stored value\s+wrote_beside", redirect)
    assert re.search(r"what session 4 did when it wrote\s+rejected", redirect)
    assert re.search(r"after a rejected write\s+unknown \(not observed by this scorer\)", redirect)
    assert (
        f"rescored by audit (scorer v{RELIABILITY_VERSION + 1})" in text
    )  # the per-session overlay
    generic = text[text.index("Verdicts, condition generic") : text.index("Trial 2 of 2")]
    assert "this trial is not in the audit report (audit v4)" in generic


def test_a_run_without_a_manifest_or_legs_is_refused(tmp_path: Path) -> None:
    mod = render_module()
    with pytest.raises(ValueError, match=r"No manifest\.json"):
        mod.render_demo(tmp_path, tmp_path / "x.cast", speed=1.0, limit=12, video=False)
    (tmp_path / "manifest.json").write_text(json.dumps(_manifest()))
    with pytest.raises(ValueError, match="No saved legs"):
        mod.render_demo(tmp_path, tmp_path / "x.cast", speed=1.0, limit=12, video=False)
