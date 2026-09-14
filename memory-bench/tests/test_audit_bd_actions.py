"""Stricter local action audit never upgrades filename/key echoes into content recovery."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from membench.runner.bd_experiment import _pair_dir, corpus_fingerprint
from membench.runner.e1_reliability import RELIABILITY_VERSION
from membench.runner.headless_agent import assistant_event, serialize_stream, tool_result_event
from membench.runner.leg_plans import TRIAL_ROLES
from membench.runner.resume_cache import digest
from membench.runner.toolreq_corpus import load_twin_corpus, superseded_values
from membench.schemas.trace import ToolCall
from tests.toolreq_helpers import corpus

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_bd_actions.py"


def audit_module() -> Any:
    spec = importlib.util.spec_from_file_location("audit_bd_actions", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path,content,expected",
    [
        ("/work/config.json", '{"setting":"token-value"}', True),
        ("./sub/../config.json", '{"nested":["token-value"]}', True),
        ("/else/config.json", '{"setting":"token-value"}', False),
        ("/work/token-value", "{}", False),
        ("/work/config.json", '{"token-value":0}', False),
        ("/work/config.json", "token-value", False),
        ("/work/config.json", '{"a":"token-value", "old":"stale-value"}', False),
        ("/work/config.json", '{"a":"token-value", "n":NaN}', False),
    ],
)
def test_write_requires_correct_artifact_and_json_values(
    path: str, content: str, expected: bool
) -> None:
    call = ToolCall(
        name="Write", arguments={"file_path": path, "content": content}, result="written"
    )
    assert (
        audit_module().valid_write(
            call, cwd="/work", required=("token-value",), forbidden=("stale-value",)
        )
        is expected
    )


@pytest.fixture
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("audit-corpus")
    corpus(root, "w-0")
    return root / "corpus"


def fixture(
    root: Path, corpus_dir: Path, *, leg_plan: tuple[str, ...] | None = None
) -> tuple[Any, dict[str, Any], Path]:
    _, tasks = load_twin_corpus(corpus_dir)
    task = next(t for t in tasks if t.variant == "necessary")
    pair = {"condition": "explicit", "work_id": task.work_id, "variant": task.variant, "repeat": 0}
    manifest = {
        "schedule": [pair],
        "conditions": {"explicit": {}},
        "planned_pairs": 1,
        "corpus_fingerprint": corpus_fingerprint([task]),
        **({"leg_plan": list(leg_plan)} if leg_plan is not None else {}),
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    directory = _pair_dir(root, pair)
    (directory / "legs").mkdir(parents=True)
    (directory / "started.json").write_text(
        json.dumps({"pair": pair, "manifest_digest": digest(manifest)})
    )
    return task, pair, directory


def test_missing_goal_remains_in_schedule(tmp_path: Path, corpus_dir: Path) -> None:
    _, _, _ = fixture(tmp_path, corpus_dir)
    report = audit_module().audit(tmp_path, corpus_dir=corpus_dir)
    assert report["scheduled_pairs"] == 1
    assert report["groups"][0]["strict_artifact_success"]["unknown"] == 1
    assert report["pairs"][0]["strict_handoff"] is None


@pytest.mark.parametrize("escape_token", [False, True])
def test_recall_order_targets_valid_write_not_earlier_invalid_write(
    tmp_path: Path, escape_token: bool, corpus_dir: Path
) -> None:
    task, pair, directory = fixture(tmp_path, corpus_dir)
    token = task.current_opaque_values[0]
    content = json.dumps({"a": token})
    if escape_token:
        content = content.replace("toolreq", "\\u0074oolreq")
    calls = [
        {"type": "system", "subtype": "init", "cwd": "/work", "session_id": "session"},
        assistant_event(
            [("Write", {"file_path": "/wrong/config.json", "content": json.dumps(token)}, "bad")]
        ),
        tool_result_event("bad", "written"),
        assistant_event([("Bash", {"command": "bd recall key"}, "read")]),
        tool_result_event("read", token),
        assistant_event(
            [
                (
                    "Write",
                    {"file_path": "/work/config.json", "content": content},
                    "good",
                )
            ]
        ),
        tool_result_event("good", "written"),
    ]
    identity = {
        "invocation_id": "inv",
        "tool_use_id": "read",
        "session_id": "session",
        "leg_id": "leg",
        "operation_argv": ["recall", "key"],
    }
    receipts = [
        {**identity, "event": "start"},
        {**identity, "event": "finish", "returncode": 0, "stdout": token, "stderr": ""},
    ]
    establish = {
        **pair,
        "leg": 0,
        "role": "establish",
        "status": "ok",
        "bd_evidence": {"bd_capture_complete": True, "bd_evidence_unknown": False},
    }
    goal = {
        **pair,
        "leg": 1,
        "role": "goal",
        "status": "ok",
        "stream": serialize_stream(calls),
        "bd_evidence": {"goal_action_success": True},
        "bd_receipts": receipts,
        "bd_receipt_leg_id": "leg",
    }
    (directory / "legs" / "0.json").write_text(json.dumps(establish))
    (directory / "legs" / "1.json").write_text(json.dumps(goal))
    report = audit_module().audit(tmp_path, corpus_dir=corpus_dir)
    row = report["pairs"][0]
    assert row["strict_artifact_success"] is True
    assert row["qualifying_write_ids"] == ["good"]
    assert row["strict_recall_before_action"] is True
    assert row["strict_handoff"] is True
    assert report["discrepancies"] == []
    # Only the goal carried a transcript with receipts, so only the goal was rescored.
    assert row["legs_rescored"] == ["goal"]
    assert row["rescored_legs"]["goal"]["bd_recall_complete"] is True
    assert "trial_saved" not in row


def test_error_or_unanswered_write_cannot_qualify() -> None:
    mod = audit_module()
    for result, error in ((None, False), ("failed", True)):
        call = ToolCall(
            name="Write",
            arguments={"file_path": "config.json", "content": '"token"'},
            result=result,
            is_error=error,
        )
        assert not mod.valid_write(call, cwd="/work", required=("token",), forbidden=())


def test_wrong_path_discrepancy_and_cli_preserve_raw_evidence(
    tmp_path: Path, corpus_dir: Path
) -> None:
    task, pair, directory = fixture(tmp_path, corpus_dir)
    stream = serialize_stream(
        [
            {"type": "system", "subtype": "init", "cwd": "/work"},
            assistant_event(
                [
                    (
                        "Write",
                        {
                            "file_path": "/else/config.json",
                            "content": json.dumps(task.current_opaque_values),
                        },
                        "write",
                    )
                ]
            ),
            tool_result_event("write", "written"),
        ]
    )
    goal = {
        **pair,
        "leg": 1,
        "role": "goal",
        "status": "ok",
        "stream": stream,
        "bd_evidence": {"goal_action_success": True},
        "bd_receipts": [],
        "bd_receipt_leg_id": "leg",
    }
    path = directory / "legs" / "goal.json"
    raw = json.dumps(goal).encode()
    path.write_bytes(raw)
    out = tmp_path / "audit"
    assert (
        audit_module().main([str(tmp_path), "--out", str(out), "--corpus-dir", str(corpus_dir)])
        == 0
    )
    report = json.loads((out / "audit.json").read_text())
    assert len(report["discrepancies"]) == 1
    assert report["pairs"][0]["strict_handoff"] is False
    assert report["pairs"][0]["write_checks"][0]["reason"] == "wrong_path"
    assert str(path.relative_to(tmp_path)) in report["artifact_sha256"]
    assert path.read_bytes() == raw
    assert "Qualifying Write" in (out / "report.md").read_text()


def test_missing_or_conflicting_cwd_is_unknown(corpus_dir: Path) -> None:
    mod = audit_module()
    assert mod.stream_cwd("not-json\n{}\n") is None
    stream = serialize_stream(
        [{"type": "system", "subtype": "init", "cwd": path} for path in ("/one", "/two")]
    )
    assert mod.stream_cwd(stream) is None
    _, tasks = load_twin_corpus(corpus_dir)
    goal = {"status": "ok", "stream": stream}
    audit = mod.goal_audit(tasks[0], goal, rescored=None)
    assert audit["strict_artifact_success"] is None
    assert mod.rescore_leg(tasks[0], goal, leg=1, role="goal") is None


def test_corpus_and_pair_identity_are_verified(tmp_path: Path, corpus_dir: Path) -> None:
    _, _, directory = fixture(tmp_path, corpus_dir)
    started = directory / "started.json"
    row = json.loads(started.read_text())
    started.write_text(json.dumps({**row, "manifest_digest": "bad"}))
    with pytest.raises(ValueError, match="identity"):
        audit_module().audit(tmp_path, corpus_dir=corpus_dir)
    started.write_text(json.dumps(row))
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    path.write_text(json.dumps({**manifest, "corpus_fingerprint": "bad"}))
    with pytest.raises(ValueError, match="corpus"):
        audit_module().audit(tmp_path, corpus_dir=corpus_dir)


def test_duplicate_json_keys_are_not_an_unambiguous_artifact() -> None:
    call = ToolCall(
        name="Write",
        arguments={"file_path": "config.json", "content": '{"a":"stale","a":"token"}'},
        result="written",
    )
    assert not audit_module().valid_write(
        call, cwd="/work", required=("token",), forbidden=("stale",)
    )


def test_later_unrelated_write_cannot_move_goal_after_recall(
    tmp_path: Path, corpus_dir: Path
) -> None:
    task, pair, directory = fixture(tmp_path, corpus_dir)
    token = task.current_opaque_values[0]
    stream = serialize_stream(
        [
            {"type": "system", "subtype": "init", "cwd": "/work", "session_id": "s"},
            assistant_event(
                [("Write", {"file_path": "config.json", "content": json.dumps(token)}, "goal")]
            ),
            tool_result_event("goal", "written"),
            assistant_event([("Bash", {"command": "bd recall k"}, "read")]),
            tool_result_event("read", token),
            assistant_event([("Write", {"file_path": "notes.txt", "content": "done"}, "later")]),
            tool_result_event("later", "written"),
        ]
    )
    identity = {
        "invocation_id": "inv",
        "tool_use_id": "read",
        "session_id": "s",
        "leg_id": "leg",
        "operation_argv": ["recall", "k"],
    }
    receipts = [
        {**identity, "event": "start"},
        {**identity, "event": "finish", "returncode": 0, "stdout": token, "stderr": ""},
    ]
    establish = {
        **pair,
        "leg": 0,
        "role": "establish",
        "status": "ok",
        "bd_evidence": {"bd_capture_complete": True},
    }
    goal = {
        **pair,
        "leg": 1,
        "role": "goal",
        "status": "ok",
        "stream": stream,
        "bd_receipts": receipts,
        "bd_receipt_leg_id": "leg",
        "bd_evidence": {
            "goal_action_success": True,
            "bd_recall_complete": True,
            "bd_recall_before_action": True,
        },
    }
    (directory / "legs" / "0.json").write_text(json.dumps(establish))
    (directory / "legs" / "1.json").write_text(json.dumps(goal))
    report = audit_module().audit(tmp_path, corpus_dir=corpus_dir)
    row = report["pairs"][0]
    assert row["strict_artifact_success"] is True
    assert row["strict_recall_before_action"] is False
    assert row["strict_handoff"] is False
    assert len(report["recall_before_action_discrepancies"]) == 1
    assert len(report["handoff_discrepancies"]) == 1
    assert report["discrepancies"] == []


def test_a_trial_run_is_audited_at_the_goal_slot_its_plan_names(
    tmp_path: Path, corpus_dir: Path
) -> None:
    """In a four-leg trial the goal is leg 2 and capture is read from the revise leg (leg 1);
    an audit that hardcoded the pair's positions would read the wrong sessions."""
    task, pair, directory = fixture(tmp_path, corpus_dir, leg_plan=TRIAL_ROLES)
    token = task.current_opaque_values[0]
    stream = serialize_stream(
        [
            {"type": "system", "subtype": "init", "cwd": "/work", "session_id": "s"},
            assistant_event([("Bash", {"command": "bd recall k"}, "read")]),
            tool_result_event("read", token),
            assistant_event(
                [
                    (
                        "Write",
                        {"file_path": "/work/config.json", "content": json.dumps({"a": token})},
                        "good",
                    )
                ]
            ),
            tool_result_event("good", "written"),
        ]
    )
    identity = {
        "invocation_id": "inv",
        "tool_use_id": "read",
        "session_id": "s",
        "leg_id": "leg",
        "operation_argv": ["recall", "k"],
    }
    receipts = [
        {**identity, "event": "start"},
        {**identity, "event": "finish", "returncode": 0, "stdout": token, "stderr": ""},
    ]
    stale = superseded_values(task)[0]

    def write_session(token: str, use: str) -> tuple[str, list[dict[str, Any]]]:
        """A session whose one bd call is ``bd remember <token> --key k 2>&1``, answered as an
        in-place update, with the receipt the wrapper wrote for it."""
        command = f'bd remember "{token}" --key k 2>&1'
        session = serialize_stream(
            [
                {"type": "system", "subtype": "init", "cwd": "/work", "session_id": "s"},
                assistant_event([("Bash", {"command": command}, use)]),
                tool_result_event(use, f"Updated [k]: {token}"),
            ]
        )
        who = {**identity, "tool_use_id": use, "operation_argv": ["remember", token, "--key", "k"]}
        return session, [
            {**who, "event": "start"},
            {**who, "event": "finish", "returncode": 0, "stdout": f"Updated [k]: {token}"},
        ]

    revise_stream, revise_receipts = write_session(token, "rev")
    stale_stream, stale_receipts = write_session(stale, "stale")
    # Saved at scorer v4, which read the redirected call as compound: every call unattributed.
    unattributed = [{"position": 0, "verb": "remember", "key": "k", "outcome": "unattributed"}]
    legs = {
        "establish": {"bd_evidence": {"bd_capture_complete": False}},
        "revise": {
            "stream": revise_stream,
            "bd_receipts": revise_receipts,
            "bd_receipt_leg_id": "leg",
            "bd_evidence": {"bd_capture_complete": True, "bd_calls": unattributed},
        },
        "goal": {
            "stream": stream,
            "bd_evidence": {"goal_action_success": True},
            "bd_receipts": receipts,
            "bd_receipt_leg_id": "leg",
        },
        "stale_writer": {
            "stream": stale_stream,
            "bd_receipts": stale_receipts,
            "bd_receipt_leg_id": "leg",
            "bd_evidence": {"bd_calls": unattributed},
        },
    }
    for index, role in enumerate(TRIAL_ROLES):
        evidence = {"scoring_version": 4, "leg": index, "role": role, "status": "ok"}
        row = {
            **pair,
            "leg": index,
            "role": role,
            "status": "ok",
            **legs[role],
            "bd_evidence": {**evidence, **legs[role]["bd_evidence"]},
        }
        (directory / "legs" / f"{index}.json").write_text(json.dumps(row))
    out = tmp_path / "audit"
    assert (
        audit_module().main([str(tmp_path), "--out", str(out), "--corpus-dir", str(corpus_dir)])
        == 0
    )
    report = json.loads((out / "audit.json").read_text())
    assert report["leg_plan"] == list(TRIAL_ROLES)
    row = report["pairs"][0]
    assert row["capture_role"] == "revise"
    assert row["capture"] is True
    assert row["strict_artifact_success"] is True
    assert row["strict_recall_before_action"] is True
    assert row["strict_handoff"] is True

    # The establish leg saved no transcript, so it alone stays unrescored.
    assert row["legs_rescored"] == ["revise", "goal", "stale_writer"]
    assert row["rescored_legs"]["revise"]["scoring_version"] == RELIABILITY_VERSION
    # Saved verdicts come from a scorer that never saw these calls; the rescore reads them.
    assert row["trial_saved"]["revision"] is None
    assert row["trial_saved"]["stale_write"] is None
    assert row["trial_rescored"]["revision"] == "updated_in_place"
    assert row["trial_rescored"]["stale_write"] == "updated_in_place"
    assert row["trial_rescored"]["retrieval_current_only"] is True
    assert row["trial_rescored"]["goal_action_success"] is True
    assert row["trial_rescored"]["capture_superseded"] is None  # establish was not rescored
    moved = report["trial_discrepancies"][0]["moved"]
    assert moved["revision"] == {"saved": None, "rescored": "updated_in_place"}
    assert "capture_superseded" not in moved
    group = report["groups"][0]
    assert group["legs_rescored"] == 3 and group["legs_scheduled"] == 4
    assert group["trial"]["revision"]["saved"] == {"unknown": 1, "scheduled": 1}
    assert group["trial"]["revision"]["rescored"]["updated_in_place"] == 1
    assert group["trial"]["retrieval_current_only"]["rescored"]["success"] == 1
    text = (out / "report.md").read_text()
    assert (
        "| explicit | necessary | revision | none; unknown 1 | updated_in_place 1; unknown 0 |"
        in text
    )
    assert "Sessions rescored: 3 of 4" in text

    goal_path = directory / "legs" / "2.json"
    goal = json.loads(goal_path.read_text())
    goal_path.write_text(json.dumps({**goal, "leg": 1}))
    with pytest.raises(ValueError, match="identity"):
        audit_module().audit(tmp_path, corpus_dir=corpus_dir)
