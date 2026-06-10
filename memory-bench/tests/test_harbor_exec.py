"""ATIF-trajectory -> RunTranscript projection (mem-apg.3.1).

The pure projection is exercised here against a SYNTHESIZED ATIF trajectory that is
first validated by Harbor's own `Trajectory` model (when Harbor is importable), so the
fixture is provably the same shape `harbor run` emits -- not a hand-wavy mock. The
subprocess driver (`run_harbor_job`) needs Docker + a real subscription run and is not
exercised here; only its argv/guard behavior is asserted.
"""

import json
from pathlib import Path

import pytest

from membench.harbor.harbor_exec import (
    build_job_config,
    collect_output,
    derive_files_touched,
    harbor_exec,
    project_claude_stream,
    project_trajectory,
)


def _atif(steps: list[dict]) -> dict:
    """A minimal valid ATIF-v1.2 trajectory dict around the given steps."""
    return {
        "schema_version": "ATIF-v1.2",
        "session_id": "sess-1",
        "agent": {"name": "claude-code", "version": "1.0.0", "model_name": "claude-x"},
        "steps": steps,
    }


def _traj_go_test_failure() -> dict:
    """Agent reads a file, runs a failing test (its stdout is the observation), edits."""
    return _atif(
        [
            {
                "step_id": 1,
                "source": "user",
                "message": "Fix the failing test.",
            },
            {
                "step_id": 2,
                "source": "agent",
                "message": "Let me look and run the tests.",
                "tool_calls": [
                    {
                        "tool_call_id": "c1",
                        "function_name": "Read",
                        "arguments": {"file_path": "/app/internal/workdir/workdir_test.go"},
                    },
                    {
                        "tool_call_id": "c2",
                        "function_name": "Bash",
                        "arguments": {"command": "go test ./..."},
                    },
                ],
                "observation": {
                    "results": [
                        {"source_call_id": "c1", "content": "package workdir ..."},
                        {
                            "source_call_id": "c2",
                            "content": (
                                "--- FAIL: TestWorkdir\n"
                                '    workdir_test.go:42: ctx.rig = "", want "thriva"'
                            ),
                        },
                    ]
                },
            },
            {
                "step_id": 3,
                "source": "agent",
                "message": "Patching.",
                "tool_calls": [
                    {
                        "tool_call_id": "c3",
                        "function_name": "Edit",
                        "arguments": {
                            "file_path": "/app/internal/workdir/workdir.go",
                            "old_string": "a",
                            "new_string": "b",
                        },
                    }
                ],
                "observation": {"results": [{"source_call_id": "c3", "content": "ok"}]},
            },
        ]
    )


# --- ATIF validity: the fixture is what harbor actually emits --------------------


def test_fixture_is_valid_against_harbor_model():
    trajectories = pytest.importorskip("harbor.models.trajectories")
    # Round-trips through Harbor's strict (extra=forbid, sequential step_id) model:
    # if this fails, the fixture has drifted from the real ATIF contract.
    trajectories.Trajectory.model_validate(_traj_go_test_failure())


# --- derive_files_touched: file tool-calls -> (read, written) -------------------


def test_derive_files_splits_read_and_written():
    read, written = derive_files_touched(_traj_go_test_failure())
    assert read == frozenset({"/app/internal/workdir/workdir_test.go"})
    assert written == frozenset({"/app/internal/workdir/workdir.go"})


def test_derive_files_ignores_non_file_tools():
    # Bash/Grep/Glob touch files but not via a structured path arg -> not attributed.
    traj = _atif(
        [
            {
                "step_id": 1,
                "source": "agent",
                "message": "search",
                "tool_calls": [
                    {"tool_call_id": "g", "function_name": "Grep", "arguments": {"pattern": "x"}},
                    {"tool_call_id": "b", "function_name": "Bash", "arguments": {"command": "ls"}},
                ],
            }
        ]
    )
    read, written = derive_files_touched(traj)
    assert read == frozenset()
    assert written == frozenset()


def test_derive_files_notebook_edit_is_written():
    traj = _atif(
        [
            {
                "step_id": 1,
                "source": "agent",
                "message": "edit nb",
                "tool_calls": [
                    {
                        "tool_call_id": "n",
                        "function_name": "NotebookEdit",
                        "arguments": {"notebook_path": "/app/a.ipynb", "new_source": "x"},
                    }
                ],
            }
        ]
    )
    _, written = derive_files_touched(traj)
    assert written == frozenset({"/app/a.ipynb"})


# --- collect_output: observation contents the extractor parses ------------------


def test_collect_output_joins_observation_contents():
    output = collect_output(_traj_go_test_failure())
    assert "--- FAIL: TestWorkdir" in output
    assert 'want "thriva"' in output
    # The agent's prose messages are NOT the parseable output.
    assert "Patching." not in output


def test_collect_output_handles_multimodal_content_parts():
    traj = _atif(
        [
            {
                "step_id": 1,
                "source": "agent",
                "message": "run",
                "tool_calls": [
                    {"tool_call_id": "c", "function_name": "Bash", "arguments": {"command": "x"}}
                ],
                "observation": {
                    "results": [
                        {
                            "source_call_id": "c",
                            "content": [
                                {"type": "text", "text": "error: boom"},
                                {"type": "text", "text": "line two"},
                            ],
                        }
                    ]
                },
            }
        ]
    )
    out = collect_output(traj)
    assert "error: boom" in out
    assert "line two" in out


# --- project_trajectory: the full RunTranscript shape grid.py consumes ----------


def test_project_emits_run_transcript_shape():
    transcript = project_trajectory(_traj_go_test_failure())
    assert set(transcript) == {"output", "files_read", "files_written"}
    assert "--- FAIL: TestWorkdir" in transcript["output"]
    assert "/app/internal/workdir/workdir_test.go" in transcript["files_read"]
    assert "/app/internal/workdir/workdir.go" in transcript["files_written"]


def test_project_rejects_malformed_trajectory():
    # Non-sequential step_id -> Harbor's model rejects it; we surface, not swallow.
    # pydantic's ValidationError subclasses ValueError.
    bad = _atif([{"step_id": 5, "source": "user", "message": "hi"}])
    with pytest.raises(ValueError):
        project_trajectory(bad)


def test_projected_files_suffix_match_repo_relative_held_path():
    # The container paths are absolute (/app/...); the scorer's _same_file suffix
    # match aligns them with a repo-relative held path. Guard that contract here so
    # a future projection change that strips the path can't silently break path_reached.
    from membench.grading.trace_score import RunTrace, TraceErrorRef, score_run

    transcript = project_trajectory(_traj_go_test_failure())
    touched = frozenset(transcript["files_read"]) | frozenset(transcript["files_written"])
    run = RunTrace(errors=(), files_touched=touched)
    held = [
        TraceErrorRef(
            tool="go",
            file="internal/workdir/workdir_test.go",
            line=42,
            error_class="assert",
            signature="go:workdir_test.go:42:assert",
        )
    ]
    assert score_run(held, run).path_reached is True


# --- project_claude_stream: harvest from claude-code.txt stdout (ATIF fallback) -


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


def test_claude_stream_projects_files_and_output():
    stdout = _stream(
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Looking and running tests."},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/app/internal/workdir/workdir_test.go"},
                    },
                    {"type": "tool_use", "name": "Bash", "input": {"command": "go test ./..."}},
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "package ok"},
                    {
                        "type": "tool_result",
                        "content": (
                            "--- FAIL: TestWorkdir\n    workdir_test.go:42: ctx.rig mismatch"
                        ),
                    },
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/app/internal/workdir/workdir.go"},
                    }
                ]
            },
        },
        {"type": "result", "subtype": "success"},
    )
    t = project_claude_stream(stdout)
    assert t["files_read"] == ["/app/internal/workdir/workdir_test.go"]
    assert t["files_written"] == ["/app/internal/workdir/workdir.go"]
    assert "--- FAIL: TestWorkdir" in t["output"]
    # Agent prose + Bash command are not the parseable observation output.
    assert "Looking and running tests." not in t["output"]


def test_claude_stream_tolerates_non_json_and_empty_lines():
    stdout = "not json\n\n" + json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "boom"}]}}
    )
    assert project_claude_stream(stdout)["output"] == "boom"


def test_claude_stream_tool_result_list_content():
    stdout = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [{"type": "text", "text": "error: boom"}],
                    }
                ]
            },
        }
    )
    assert "error: boom" in project_claude_stream(stdout)["output"]


# --- build_job_config: minimal OAuth job, no secret on disk ---------------------


def test_job_config_omits_agent_env_so_token_passes_through_unmodified():
    cfg = build_job_config(
        Path("/t/task"), job_name="j", jobs_dir=Path("/t/jobs"), model="claude-x"
    )
    agent = cfg["agents"][0]
    assert agent["name"] == "claude-code"
    assert agent["model_name"] == "claude-x"
    # CRITICAL: no agent.env. Harbor merges agent.env OVER the adapter's real token
    # (base.py _exec: merged_env.update(extra_env)) and never expands ${VAR}, so any
    # CLAUDE_CODE_OAUTH_TOKEN here would clobber the real token -> 401. The adapter reads
    # the token from the harbor PROCESS env instead; keep agent.env absent.
    assert "env" not in agent
    assert cfg["tasks"] == [{"path": "/t/task"}]
    assert cfg["environment"] == {"type": "docker"}
    # No tests/ in reconstructed tasks + we score recurrence ourselves -> verifier off.
    assert cfg["verifier"] == {"disable": True}


# --- harbor_exec: guard behavior (no Docker / no subscription run here) ----------


def test_harbor_exec_missing_task_dir_raises_loud():
    with pytest.raises((FileNotFoundError, RuntimeError)):
        harbor_exec(Path("/nonexistent/membench-task/w1-none"))
