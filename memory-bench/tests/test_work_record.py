"""WorkRecord pydantic model — golden-record parse, round-trip, and TS field-set
parity (mem-koqed). The field set below is a manual pin against
`src/schemas/workrecord.ts` `WorkRecordSchema`: change both together."""

import pytest
from pydantic import ValidationError

from membench.schemas.work_record import WorkRecord

# Pinned against src/schemas/workrecord.ts WorkRecordSchema's own key order.
TS_WORKRECORD_FIELDS = {
    "work_id",
    "rig",
    "title",
    "task_type",
    "task_type_source",
    "labels",
    "metadata",
    "priority",
    "external_ref",
    "repo",
    "repo_source",
    "lifecycle",
    "agents",
    "trace",
    "outcome",
    "provenance",
    "landed",
    "session_commits",
    "signal",
    "links",
}


def _golden_record() -> dict:
    return {
        "work_id": "mem-koqed",
        "rig": "mem",
        "title": "Pydantic WorkRecord model at the mem_cli.py seam",
        "task_type": "arch-review-fix",
        "task_type_source": "model",
        "labels": ["arch-review"],
        "metadata": {"note": "arbitrary"},
        "priority": 2,
        "external_ref": "gh-1873",
        "repo": "sjarmak/mem",
        "repo_source": "outcome",
        "lifecycle": {
            "created": "2026-07-10T00:00:00Z",
            "started": "2026-07-11T00:00:00Z",
            "closed": None,
            "status": "in_progress",
            "status_history": [{"status": "open", "at": "2026-07-10T00:00:00Z"}],
        },
        "agents": [
            {
                "agent_id": "mem-worker-gc-474136",
                "role": "worker",
                "sequence": 1,
                "sources": ["assignee"],
            }
        ],
        "trace": {
            "jsonl_path": "/traces/mem-koqed.jsonl",
            "n_turns": 12,
            "tool_outcomes": [
                {
                    "runner": "vitest",
                    "command": "npm run check",
                    "status": "pass",
                    "errors": [],
                }
            ],
            "errors": [
                {
                    "tool": "tsc",
                    "severity": "error",
                    "message": "type mismatch",
                    "file": "src/foo.ts",
                    "line": 10,
                    "column": 3,
                }
            ],
            "run": {
                "session_uuid": "abc-123",
                "model": "claude-sonnet-5",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "n_tool_calls": 4,
                "tool_calls_by_type": {"Bash": 2, "Read": 2},
                "n_turns": 12,
            },
            "pr_links": [
                {
                    "session_uuid": "abc-123",
                    "pr_number": 1873,
                    "pr_url": "https://github.com/sjarmak/mem/pull/1873",
                    "pr_repository": "sjarmak/mem",
                }
            ],
        },
        "outcome": {
            "pr": "1873",
            "repo": "sjarmak/mem",
            "pr_state": "merged",
            "commit_sha": "d820207",
            "base_commit": "d820207",
            "ci": "pass",
        },
        "provenance": {
            "work_dir": "/home/ds/projects/mem-koqed",
            "repo": "mem",
            "base_branch": "main",
            "base_commit": "a" * 40,
            "history_state": "recorded",
            "work_dir_source": "metadata",
            "base_branch_source": "metadata",
        },
        "landed": {
            "base_commit": "a" * 40,
            "landed_commit": "b" * 40,
            "n_commits": 3,
            "landed_state": "landed",
        },
        "session_commits": {
            "commits": ["abc1234"],
            "first_commit": "abc1234",
            "true_base": "a" * 40,
            "base_state": "resolved",
        },
        "signal": {"deterministic": {"parsed": True}, "semantic": {}},
        "links": {
            "deps": ["mem-nx3tu"],
            "convoy_id": None,
            "supersedes": [],
            "parent": "mem-nx3tu",
        },
    }


def test_golden_record_parses() -> None:
    record = WorkRecord.model_validate(_golden_record())
    assert record.work_id == "mem-koqed"
    assert record.lifecycle.status == "in_progress"
    assert record.trace is not None
    assert record.trace.run.session_uuid == "abc-123"
    assert record.landed.landed_state == "landed"


def test_golden_record_round_trips() -> None:
    original = _golden_record()
    record = WorkRecord.model_validate(original)
    dumped = record.model_dump(exclude_none=True)
    assert dumped["work_id"] == original["work_id"]
    assert dumped["lifecycle"]["status"] == original["lifecycle"]["status"]
    assert dumped["outcome"]["commit_sha"] == original["outcome"]["commit_sha"]
    # Re-parsing the dump must produce an equal model (idempotent round-trip).
    assert WorkRecord.model_validate(dumped) == record


def test_field_set_matches_ts_contract() -> None:
    assert set(WorkRecord.model_fields) == TS_WORKRECORD_FIELDS


def test_missing_required_lifecycle_status_raises() -> None:
    bad = _golden_record()
    del bad["lifecycle"]["status"]
    with pytest.raises(ValidationError):
        WorkRecord.model_validate(bad)


def test_missing_lifecycle_raises() -> None:
    bad = _golden_record()
    del bad["lifecycle"]
    with pytest.raises(ValidationError):
        WorkRecord.model_validate(bad)


def test_extra_fields_are_allowed_for_forward_compat() -> None:
    record = WorkRecord.model_validate({**_golden_record(), "not_yet_known_field": "value"})
    assert record.model_dump(exclude_none=True)["not_yet_known_field"] == "value"


def test_minimal_record_uses_defaults() -> None:
    record = WorkRecord.model_validate(
        {
            "work_id": "mem-minimal",
            "rig": "mem",
            "title": "minimal",
            "lifecycle": {"created": "2026-07-10T00:00:00Z", "status": "open"},
        }
    )
    assert record.labels == []
    assert record.metadata == {}
    assert record.agents == []
    assert record.links.deps == []
    assert record.links.supersedes == []
