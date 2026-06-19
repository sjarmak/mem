"""§4.4 grid action-impact post-hoc scorer (mem-lvp.25). Hermetic — synthetic job dirs
(stream transcripts) + grid result JSONs on a tmp_path, judge is `StubComparativeJudge`.
No real Harbor, Docker, or Ollama."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from membench.bbon.comparative_judge import StubComparativeJudge
from membench.harbor.grid_action_impact import (
    ARM_TO_GRID_CONDITION,
    score_grid_action_impact,
)


def _stream(*tools: str) -> str:
    content = [
        {"type": "tool_use", "id": f"t{i}", "name": t, "input": {}}
        for i, t in enumerate(tools)
    ]
    return (
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}})
        + "\n"
        + json.dumps({"type": "result", "result": "done"})
        + "\n"
    )


def _write_job(jobs_dir: Path, work_id: str, condition: str, stream: str) -> None:
    # load_stream globs "*/agent/claude-code.txt" under the job dir.
    agent_dir = jobs_dir / f"{work_id}.{condition}" / f"{work_id}.{condition}__hash" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "claude-code.txt").write_text(stream, encoding="utf-8")


def _write_grid_result(
    grid_dir: Path, work_id: str, condition: str, repro_passed: bool | None
) -> None:
    payload: dict[str, Any] = {
        "work_id": work_id,
        "condition": condition,
        "score_direct": 1.0 if repro_passed else 0.0,
        "score_artifact": 0.0,
        "direct_mode": "repro",
        "repro_passed": repro_passed,
        "repro_error": None,
        "efficiency": {"turns": 1, "tool_calls": 1, "input_tokens": 10, "output_tokens": 5},
        "candidate_files": [],
    }
    grid_dir.mkdir(parents=True, exist_ok=True)
    (grid_dir / f"{work_id}.{condition}.json").write_text(json.dumps(payload), encoding="utf-8")


def _setup(tmp_path: Path, bundles: dict[str, dict[str, Any]]) -> tuple[Path, Path]:
    """bundles: work_id -> {arm: (stream, repro_passed)} keyed by ACTION arm name."""
    grid_dir = tmp_path / "grid"
    jobs_dir = tmp_path / "jobs"
    for work_id, arms in bundles.items():
        for arm, (stream, passed) in arms.items():
            condition = ARM_TO_GRID_CONDITION[arm]
            _write_job(jobs_dir, work_id, condition, stream)
            _write_grid_result(grid_dir, work_id, condition, passed)
    return grid_dir, jobs_dir


def _verdict(**ov: Any) -> str:
    p = {
        "memory_changed_tool_choice": False, "memory_changed_plan": False,
        "memory_changed_output": False, "memory_prevented_known_failure": False,
        "memory_improved_verification": False, "rationale": "stub",
    }
    p.update(ov)
    return json.dumps(p)


def test_pairs_and_scores_ours_vs_none(tmp_path: Path) -> None:
    grid_dir, jobs_dir = _setup(tmp_path, {
        "bundle-a": {
            "none": (_stream("Bash"), False),
            "ours": (_stream("Write"), True),   # memory → different tool + it passes
        },
    })
    judge = StubComparativeJudge(fn=lambda _p: _verdict(memory_changed_tool_choice=True))
    res = score_grid_action_impact(
        grid_dir, jobs_dir, ["bundle-a"], treated_arms=("ours",), judge=judge
    )
    assert res.paired_work_ids["ours"] == ["bundle-a"]
    ours = res.action_impact["ours"]
    tc = next(t for t in ours.tallies if t.axis == "memory_changed_tool_choice")
    assert tc.true_count == 1  # Write vs Bash diverged → judge → True
    # outcome-lift: ours passed (1.0), none failed (0.0) → +1.0
    assert res.control_pass_rate == 0.0
    assert res.treated_pass_rate["ours"] == 1.0
    assert res.outcome_lift("ours") == 1.0


def test_skips_bundle_missing_treated_arm(tmp_path: Path) -> None:
    # bundle-b has only the control (ours job dir absent) → skipped for ours, not faked.
    grid_dir, jobs_dir = _setup(tmp_path, {
        "bundle-a": {"none": (_stream("Bash"), False), "ours": (_stream("Write"), True)},
        "bundle-b": {"none": (_stream("Bash"), False)},
    })
    res = score_grid_action_impact(
        grid_dir, jobs_dir, ["bundle-a", "bundle-b"], treated_arms=("ours",), judge=None
    )
    assert res.paired_work_ids["ours"] == ["bundle-a"]
    assert any("bundle-b" in s for s in res.skipped["ours"])


def test_builtin_arm_uses_none_condition(tmp_path: Path) -> None:
    # builtin's stream comes from the grid 'none' condition; control from 'none-clean'.
    grid_dir, jobs_dir = _setup(tmp_path, {
        "bundle-a": {
            "none": (_stream("Read"), True),      # none-clean control
            "builtin": (_stream("Read"), True),   # grid 'none' condition
        },
    })
    res = score_grid_action_impact(
        grid_dir, jobs_dir, ["bundle-a"], treated_arms=("builtin",), judge=None
    )
    assert res.paired_work_ids["builtin"] == ["bundle-a"]
    # identical streams + equal status → mechanical pre-filter, tool_choice proven False.
    bi = res.action_impact["builtin"]
    tc = next(t for t in bi.tallies if t.axis == "memory_changed_tool_choice")
    assert tc.true_count == 0 and tc.decided_count == 1


def test_outcome_lift_none_when_oracle_absent(tmp_path: Path) -> None:
    grid_dir, jobs_dir = _setup(tmp_path, {
        "bundle-a": {"none": (_stream("Bash"), None), "ours": (_stream("Write"), None)},
    })
    res = score_grid_action_impact(
        grid_dir, jobs_dir, ["bundle-a"], treated_arms=("ours",), judge=None
    )
    assert res.outcome_lift("ours") is None  # no known oracle → no imputed lift
