"""The §5 artifact: it is written, it proves what it claims, and it refuses when it cannot."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from membench.runner import arm_separation
from membench.runner.arm_separation import (
    TRANSCRIPT_READ_SPELLINGS,
    ArmSeparationError,
    deny_battery,
    render_arm_separation,
)
from membench.runner.memory_arm import ARM_NAMES
from membench.runner.native_memory_hook import NATIVE_MEMORY_HOOK_EXIT_BLOCK
from membench.runner.tool_surface import MEMORY_COMMAND
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, adapt_sequence
from tests.toolreq_helpers import toolreq_seq


def _task() -> ToolReqRealAgentTask:
    return adapt_sequence(toolreq_seq("sep-t0"))


def test_the_deny_battery_blocks_every_spelling_and_says_nothing(tmp_path: Path) -> None:
    rows = deny_battery(tmp_path)
    assert len(rows) == len(TRANSCRIPT_READ_SPELLINGS)
    for row in rows:
        assert row["exit_code"] == NATIVE_MEMORY_HOOK_EXIT_BLOCK, row["label"]
        assert row["reach_recorded"] is True, row["label"]
        # The refusal may not coach the floor arm in the subject under test.
        assert MEMORY_COMMAND not in row["stderr"].split(), row["label"]


def test_a_spelling_that_gets_through_refuses_instead_of_reporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report's own failure is never one of its fields: a row saying "this one was allowed"
    would be cited as the artifact proving the arms separate."""
    monkeypatch.setattr(
        arm_separation,
        "TRANSCRIPT_READ_SPELLINGS",
        (("a read outside the pin, which is not a leak", "cat /etc/hostname"),),
    )
    with pytest.raises(ArmSeparationError, match="did not stop"):
        deny_battery(tmp_path)


@pytest.mark.skipif(
    shutil.which(MEMORY_COMMAND) is None, reason="minting the beads arm needs a real bd on PATH"
)
def test_the_artifact_records_one_scaffold_one_goal_call_and_three_settings(
    tmp_path: Path,
) -> None:
    out = render_arm_separation(tmp_path / "separation.json", _task(), root=tmp_path / "deny")
    document = json.loads(out.read_text(encoding="utf-8"))

    assert [row["arm"] for row in document["arms"]] == list(ARM_NAMES)
    assert len({row["scaffold_digest"] for row in document["arms"]}) == 1
    assert len({json.dumps(row["goal_call"]) for row in document["arms"]}) == 1
    assert len({row["settings_digest"] for row in document["arms"]}) == 3
    assert len(document["deny_battery"]) == len(TRANSCRIPT_READ_SPELLINGS)

    by_arm = {row["arm"]: row for row in document["arms"]}
    assert by_arm["none"]["memory_command_exit"] == 127
    assert by_arm["builtin"]["memory_command_exit"] == 127
    assert by_arm["beads"]["memory_command_exit"] is None
    # Each arm's capability paragraph is its own, and none of them is empty.
    assert all(row["capability_words"] >= 20 for row in document["arms"])

    # §5 item 8: the establish leg's transcript is GONE from the goal leg's config dir for the
    # two arms that re-mint, and present for the comparator, whose cross-leg continuity is the
    # treatment rather than a leak.
    for name in ("beads", "none"):
        assert by_arm[name]["goal_leg_config_dir_is_fresh"] is True, name
        assert by_arm[name]["establish_transcript_reachable_from_goal_leg"] is False, name
        assert not any(
            path.startswith("projects/") for path in by_arm[name]["goal_leg_config_dir_files"]
        ), by_arm[name]["goal_leg_config_dir_files"]
    assert by_arm["builtin"]["goal_leg_config_dir_is_fresh"] is False
    assert by_arm["builtin"]["establish_transcript_reachable_from_goal_leg"] is True
