"""The harness seam: the capture protocol read from the bd side, on any agent runtime.

The question the flywheel asks is which config, settings and conditions get an agent to call the
bd CLI for memory. That question is not about Claude Code; a harness that can be spawned as a
command and handed a PATH and an environment can run the same establish leg, and the endpoint has
to be read from what bd was actually handed (its receipts) rather than from a transcript format
one runtime happens to emit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from membench.runner.agent_harness import (
    HARNESS_CLAUDE_CODE,
    AgentHarness,
    HarnessError,
    claude_code_harness,
    command_harness,
)
from membench.runner.bd_receipt_surface import attributed_invocations
from membench.runner.bd_receipts import CONTEXT_KEYS, InstrumentationError
from membench.runner.beads_arm_fire import admissible_cells
from membench.runner.beads_arm_grid import (
    PROTOCOL_CAPTURE,
    arm_cell_store,
    run_arm_cell,
    shared_toolchain,
)
from membench.runner.beads_capture import capture_keys, reached
from membench.runner.beads_capture_fire import capture_identity
from membench.runner.e1_grid import ResumeMismatchError
from membench.runner.headless_agent import MemoryChannel
from membench.runner.memory_arm import ARM_BEADS, ARM_BUILTIN, ARM_NONE, MemoryArmError
from membench.runner.toolreq_corpus import twin_tasks
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, adapt_sequence
from tests.test_beads_capture import BD_BUILD
from tests.toolreq_helpers import toolreq_seq

MODEL = "sonnet"


def _task(seq_id: str = "hx-t0") -> ToolReqRealAgentTask:
    return adapt_sequence(toolreq_seq(seq_id))


def _foreign(conditions: dict[str, str] | None = None) -> AgentHarness:
    """A harness that is not Claude Code: spawned as a command, the prompt on its argv."""
    return command_harness(
        name="agent-x",
        version="0.4.2",
        argv_template=("sh", "-c", "true", "{prompt}"),
        conditions=conditions or {},
    )


def _bd_calling_runner(token: str, seen: list[dict[str, Any]]) -> Any:
    """A stand-in for the spawned harness that does what a cooperating agent would do: it calls
    `bd remember` through the PATH and environment it was handed, and nothing else. It emits no
    transcript at all, so anything the cell learns about the call must come from bd's side."""

    def run(argv: Any, **kwargs: Any) -> Any:
        seen.append({"argv": list(argv), **kwargs})
        env = kwargs["env"]
        shim = shutil.which("bd", path=env["PATH"])
        assert shim is not None
        # Content plus an explicit key: a bare slug-like token is a READ of a key that does not
        # exist and is refused (mem-bd-remember-list-is-not-a-write), not a write.
        subprocess.run(
            [shim, "remember", f"the current value is {token}", "--key", "current-value"],
            env=env,
            cwd=kwargs["cwd"],
            check=True,
            capture_output=True,
        )
        return subprocess.CompletedProcess(list(argv), 0, "noted", "")

    return run


# ---------------------------------------------------------------------------------------
# The endpoint is read from bd's receipts, whatever spawned the agent
# ---------------------------------------------------------------------------------------


def test_a_foreign_harness_leg_is_reached_and_engaged_from_bd_receipts() -> None:
    task = _task()
    seen: list[dict[str, Any]] = []
    cell = run_arm_cell(
        task,
        ARM_BEADS,
        repeat=0,
        model=MODEL,
        channel=MemoryChannel.TRUSTED,
        runner=_bd_calling_runner(task.current_opaque_values[0], seen),
        protocol=PROTOCOL_CAPTURE,
        harness=_foreign(),
    )
    assert len(seen) == 1
    # No transcript, so the stream-derived fields are empty; the receipt-derived ones are not.
    assert cell.endogenous_verbs == ()
    assert cell.bd_invocations == 1
    assert cell.engaged is True
    assert reached(cell) is True


def test_the_claude_harness_reads_the_same_receipts_when_its_hook_is_absent() -> None:
    """The default harness. The PreToolUse hook is what attributes a call to a tool_use_id; when
    the runtime never runs it (a stand-in here, a hook fault in the field) the leg attribution
    from the spawn environment still lands, so the call is measured rather than lost."""
    task = _task()
    seen: list[dict[str, Any]] = []
    cell = run_arm_cell(
        task,
        ARM_BEADS,
        repeat=0,
        model=MODEL,
        channel=MemoryChannel.TRUSTED,
        runner=_bd_calling_runner(task.current_opaque_values[0], seen),
        protocol=PROTOCOL_CAPTURE,
    )
    assert cell.bd_invocations == 1
    assert reached(cell) is True
    assert seen[0]["argv"][0] == "claude"


def test_the_leg_attribution_travels_in_the_spawn_environment() -> None:
    seen: list[dict[str, Any]] = []
    run_arm_cell(
        _task(),
        ARM_BEADS,
        repeat=3,
        model=MODEL,
        channel=MemoryChannel.TRUSTED,
        runner=_bd_calling_runner("x", seen),
        protocol=PROTOCOL_CAPTURE,
        harness=_foreign(),
    )
    env = seen[0]["env"]
    assert env[CONTEXT_KEYS["leg_id"]].endswith("leg-1")
    assert env[CONTEXT_KEYS["session_id"]] == "beads-trusted-3"
    assert CONTEXT_KEYS["tool_use_id"] not in env


def test_an_unattributed_bd_execution_is_refused_never_read_as_no_call() -> None:
    attributed = (
        {"invocation_id": "a", "event": "start", "leg_id": "l", "session_id": "s"},
        {"invocation_id": "a", "event": "finish", "leg_id": "l", "session_id": "s"},
    )
    assert attributed_invocations(attributed) == 1
    unattributed = (
        {"invocation_id": "b", "operation_argv": ["remember"], "instrumentation_error": "missing"},
    )
    with pytest.raises(InstrumentationError, match="unattributed"):
        attributed_invocations(unattributed)
    # A hook-side diagnostic is not an execution and does not count either way.
    assert attributed_invocations(({"instrumentation_error": "bad event", "leg_id": "l"},)) == 0


def test_a_no_call_leg_reads_zero_from_its_receipts() -> None:
    seen: list[dict[str, Any]] = []

    def silent(argv: Any, **kwargs: Any) -> Any:
        seen.append(dict(kwargs))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    cell = run_arm_cell(
        _task(),
        ARM_BEADS,
        repeat=0,
        model=MODEL,
        channel=MemoryChannel.TRUSTED,
        runner=silent,
        protocol=PROTOCOL_CAPTURE,
        harness=_foreign(),
    )
    assert cell.bd_invocations == 0
    assert reached(cell) is False
    assert cell.engaged is False


# ---------------------------------------------------------------------------------------
# What a foreign harness cannot buy
# ---------------------------------------------------------------------------------------


def test_the_comparator_is_refused_on_a_harness_without_native_memory() -> None:
    spawns: list[Any] = []

    def never(argv: Any, **kwargs: Any) -> Any:
        spawns.append(argv)
        raise AssertionError("must not spawn")

    with pytest.raises(MemoryArmError, match="native memory"):
        run_arm_cell(
            _task(),
            ARM_BUILTIN,
            repeat=0,
            model=MODEL,
            channel=MemoryChannel.TRUSTED,
            runner=never,
            protocol=PROTOCOL_CAPTURE,
            harness=_foreign(),
        )
    assert spawns == []


def test_the_floor_runs_on_a_foreign_harness() -> None:
    codes: list[int] = []

    def probing(argv: Any, **kwargs: Any) -> Any:
        # Probed while the mint is alive: the stub is torn down with the cell.
        stub = subprocess.run(["bd"], env=kwargs["env"], capture_output=True, check=False)
        codes.append(stub.returncode)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    cell = run_arm_cell(
        _task(),
        ARM_NONE,
        repeat=0,
        model=MODEL,
        channel=MemoryChannel.TRUSTED,
        runner=probing,
        protocol=PROTOCOL_CAPTURE,
        harness=_foreign(),
    )
    assert cell.status == "ok"
    assert codes == [127]


# ---------------------------------------------------------------------------------------
# The toolchain and the identity follow the harness
# ---------------------------------------------------------------------------------------


def test_the_shared_toolchain_carries_the_harness_binary() -> None:
    assert shared_toolchain(claude_code_harness(version="1")) == ("git", "dolt", "claude")
    assert shared_toolchain(_foreign()) == ("git", "dolt", "sh")
    with arm_cell_store(ARM_NONE, label="hx-tools", harness=_foreign()) as store:
        names = sorted(entry.name for entry in store.toolchain.iterdir())
    assert names == ["dolt", "git", "sh"]


def test_a_harness_whose_binary_is_the_memory_command_is_refused() -> None:
    with pytest.raises(HarnessError, match="bd"):
        command_harness(name="x", version="1", argv_template=("bd", "{prompt}"))


def test_a_command_template_must_carry_the_prompt() -> None:
    with pytest.raises(HarnessError, match="prompt"):
        command_harness(name="x", version="1", argv_template=("sh", "-c", "true"))


def test_the_command_harness_renders_the_prompt_and_model_into_its_argv() -> None:
    harness = command_harness(
        name="x", version="1", argv_template=("agent", "--model", "{model}", "-p", "{prompt}")
    )
    seen: list[list[str]] = []

    def run(argv: Any, **kwargs: Any) -> Any:
        seen.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, "answer", "")

    agent = harness.agent(model="m1", channel=MemoryChannel.TRUSTED, runner=run, cwd=".", env={})
    task = _task()
    from membench.runner.beads_arm_grid import arm_cell_legs
    from membench.runtime import StepContext

    establish, _goal = arm_cell_legs(task, ARM_BEADS)
    result = agent.run_step(
        establish.step, dict(establish.memory), StepContext("t", "s", establish.step.step_id)
    )
    assert seen[0][:4] == ["agent", "--model", "m1", "-p"]
    assert establish.step.user_request in seen[0][4]
    assert result.final_answer == "answer"
    assert result.raw_stream == "answer"
    assert result.tool_calls == []


def test_conditions_reach_the_child_environment_and_the_identity() -> None:
    seen: list[dict[str, Any]] = []

    def silent(argv: Any, **kwargs: Any) -> Any:
        seen.append(dict(kwargs))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    run_arm_cell(
        _task(),
        ARM_NONE,
        repeat=0,
        model=MODEL,
        channel=MemoryChannel.TRUSTED,
        runner=silent,
        protocol=PROTOCOL_CAPTURE,
        harness=_foreign({"AGENT_X_MEMORY_HINT": "always"}),
    )
    assert seen[0]["env"]["AGENT_X_MEMORY_HINT"] == "always"
    plain = _foreign().identity()
    hinted = _foreign({"AGENT_X_MEMORY_HINT": "always"}).identity()
    assert plain["harness"] == hinted["harness"] == "agent-x"
    assert plain["harness_conditions_fingerprint"] != hinted["harness_conditions_fingerprint"]


def test_a_condition_cannot_overwrite_what_the_rig_owns() -> None:
    for key in ("PATH", "PWD", CONTEXT_KEYS["leg_id"], "CLAUDE_CONFIG_DIR"):
        with pytest.raises(HarnessError, match=key):
            _foreign({key: "x"})


def _identity(harness: AgentHarness) -> dict[str, Any]:
    tasks = twin_tasks([_task(f"hx-t{i}") for i in range(8)])
    return capture_identity(
        model=MODEL,
        corpus="c",
        arms=[ARM_BEADS],
        work_ids=[t.work_id for t in tasks],
        bd_build=BD_BUILD,
        harness=harness,
    )


def test_the_identity_names_the_harness_so_two_harnesses_never_pool() -> None:
    claude = _identity(claude_code_harness(version="2.1.210"))
    foreign = _identity(_foreign())
    assert claude["harness"] == HARNESS_CLAUDE_CODE
    assert claude["cli_version"] == "2.1.210"
    assert foreign["harness"] == "agent-x"
    assert foreign["cli_version"] == "0.4.2"
    tasks = twin_tasks([_task(f"hx-t{i}") for i in range(8)])
    grid = capture_keys(tasks, arms=[ARM_BEADS])
    with pytest.raises(ResumeMismatchError):
        admissible_cells({**claude, "cells": []}, identity=foreign, grid=grid)


def test_a_persisted_row_carries_its_invocation_count() -> None:
    from membench.runner.beads_arm_plan import ArmPlanError, cell_from_row, cell_row
    from tests.test_beads_capture import a_capture_cell

    cell = a_capture_cell(ARM_BEADS, invocations=2)
    row = cell_row(cell)
    assert row["bd_invocations"] == 2
    assert cell_from_row(json.loads(json.dumps(row))) == cell
    with pytest.raises(ArmPlanError):
        cell_from_row({k: v for k, v in row.items() if k != "bd_invocations"})


def test_the_cli_refuses_the_comparator_on_a_foreign_harness_before_spending(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from membench.runner.beads_capture_fire import main
    from membench.runner.e1_grid import EXIT_REFUSED

    code = main(
        [
            "--dry-run",
            "--arms",
            "builtin",
            "beads",
            "--model",
            MODEL,
            "--harness",
            "agent-x",
            "--harness-version",
            "1",
            "--harness-command",
            "sh -c true {prompt}",
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert code == EXIT_REFUSED
    assert "native memory" in capsys.readouterr().err
    assert not (tmp_path / "out.json.cells").exists()


def test_the_cli_builds_the_harness_it_was_asked_for(tmp_path: Path) -> None:
    from membench.runner.beads_capture_fire import harness_of, parse_conditions

    assert parse_conditions(["A=1", "B=x=y"]) == {"A": "1", "B": "x=y"}
    with pytest.raises(HarnessError, match="KEY=VALUE"):
        parse_conditions(["novalue"])
    foreign = harness_of(
        name="agent-x",
        version="0.4.2",
        command=["agent", "{prompt}"],
        conditions={},
        cli_version=lambda: "unused",
    )
    assert foreign.name == "agent-x" and foreign.binary == "agent"
    default = harness_of(
        name=HARNESS_CLAUDE_CODE,
        version=None,
        command=None,
        conditions={},
        cli_version=lambda: "9.9.9",
    )
    assert default.version == "9.9.9" and default.native_memory is True
    with pytest.raises(HarnessError, match="version"):
        harness_of(
            name="agent-x", version=None, command=["a", "{prompt}"], conditions={}, cli_version=str
        )
    with pytest.raises(HarnessError, match="command"):
        harness_of(name="agent-x", version="1", command=None, conditions={}, cli_version=str)
