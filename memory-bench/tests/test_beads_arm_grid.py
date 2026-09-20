"""Arm separation, provable from a mint with no agent spawned (pre-registration §5, items 1-3).

Not free of side effects: minting the bd arm shells a real `git init` and `bd init`, so these
run under the same outside-the-sandbox store discipline as a paid leg. They buy no tokens.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from membench.harbor.agent_memory import native_memory_path
from membench.runner.arm_context import capability_of, scaffold_of
from membench.runner.beads_arm_grid import (
    COMMAND_NOT_FOUND,
    arm_cell_calls,
    arm_cell_legs,
    arm_cell_store,
    child_path_of,
    engagement_of,
    replant_context,
    run_arm_cell,
)
from membench.runner.e1_grid import ESTABLISH_INSTRUCTION
from membench.runner.headless_agent import MemoryChannel
from membench.runner.memory_arm import ARM_NAMES
from membench.runner.tool_surface import BD_CONTEXT_FILES, CONFIG_DIR_ENV, MEMORY_COMMAND
from membench.runner.toolreq_builtin import simulated_builtin_runner
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, adapt_sequence
from tests.toolreq_helpers import toolreq_seq

pytestmark = pytest.mark.skipif(
    shutil.which(MEMORY_COMMAND) is None, reason="minting the beads arm needs a real bd on PATH"
)


def _context_text(store: object) -> str:
    sandbox = store.sandbox  # type: ignore[attr-defined]
    return (sandbox / BD_CONTEXT_FILES[0]).read_text(encoding="utf-8")


def test_each_arm_mints_and_seeds_its_own_settings() -> None:
    seen = {}
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            settings = json.loads((store.config_dir / "settings.json").read_text(encoding="utf-8"))
            seen[name] = settings
    assert seen["beads"]["autoMemoryEnabled"] is False
    assert seen["none"]["autoMemoryEnabled"] is False
    assert seen["builtin"]["autoMemoryEnabled"] is True


def test_the_hook_merges_in_without_displacing_the_pin() -> None:
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            settings = json.loads((store.config_dir / "settings.json").read_text(encoding="utf-8"))
            assert "hooks" in settings, name
            assert "autoMemoryEnabled" in settings, name
            assert store.pinned_off is (name != "builtin"), name


def test_a_working_bd_resolves_in_exactly_one_arm() -> None:
    """The floor arms DO resolve the name -- they plant a stub that exits 127, so their error
    surface matches a missing command. What must resolve in only one arm is a bd with a store
    behind it, so the question is where the resolution POINTS."""
    resolves = {}
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            path = os.pathsep.join(child_path_of(store.env()))
            found = shutil.which(MEMORY_COMMAND, path=path)
            stub = store.surface.bin_dir / MEMORY_COMMAND
            resolves[name] = found is not None and (store.arm.provisions_bd or Path(found) != stub)
    assert resolves == {"beads": True, "none": False, "builtin": False}


def test_the_arms_without_a_store_see_only_the_harness_bin_dir() -> None:
    """`MemoryToolSurface.env()` appends the operator's PATH, which on this machine has a real bd
    on it. A floor arm that inherited it would not be a floor."""
    for name in ("none", "builtin"):
        with arm_cell_store(name, label="test") as store:
            entries = child_path_of(store.env())
            assert entries == [str(store.surface.bin_dir)], name


def test_the_planted_stub_exits_command_not_found() -> None:
    with arm_cell_store("none", label="test") as store:
        stub = store.surface.bin_dir / MEMORY_COMMAND
        done = subprocess.run([str(stub), "remember", "x"], capture_output=True, text=True)
        assert done.returncode == COMMAND_NOT_FOUND
        assert "not found" in done.stderr


def test_every_arm_pins_its_own_config_dir() -> None:
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            assert store.env()[CONFIG_DIR_ENV] == str(store.config_dir)


def test_the_planted_context_differs_in_exactly_one_paragraph() -> None:
    scaffolds, capabilities = {}, {}
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            assert store.context_files == BD_CONTEXT_FILES
            text = _context_text(store)
            scaffolds[name] = scaffold_of(text)
            capabilities[name] = capability_of(text)
    assert len(set(scaffolds.values())) == 1, scaffolds
    assert len(set(capabilities.values())) == 3, capabilities


def test_the_beads_paragraph_is_bds_own_text_not_this_rigs() -> None:
    with arm_cell_store("beads", label="test") as store:
        assert store.bd_capability
        assert capability_of(_context_text(store)) == store.bd_capability.strip()


def test_replanting_after_a_wipe_restores_the_same_bytes() -> None:
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            before = _context_text(store)
            for filename in BD_CONTEXT_FILES:
                (store.sandbox / filename).unlink()
            assert replant_context(store) == BD_CONTEXT_FILES
            assert _context_text(store) == before, name


def test_the_store_is_outside_the_sandbox_the_wipe_can_reach() -> None:
    with arm_cell_store("beads", label="test") as store:
        assert store.sandbox not in store.surface.store_dir.parents


# ---------------------------------------------------------------------------------------
# the fire path


def _task(seq_id: str = "arm-t0") -> ToolReqRealAgentTask:
    return adapt_sequence(toolreq_seq(seq_id))


def test_every_arm_runs_the_same_establish_instruction_and_the_same_goal_step() -> None:
    """The instruction is the ladder's memory-SILENT one. An arm told to "remember this" would
    be carrying a treatment the grid is supposed to be measuring."""
    task = _task()
    prompts = set()
    goals = set()
    for name in ARM_NAMES:
        establish, goal = arm_cell_legs(task, name)
        prompts.add(establish.step.user_request)
        goals.add(id(goal.step))
        assert goal.memory == {}, name
        assert establish.memory == task.oracle_memory, name
    assert prompts == {ESTABLISH_INSTRUCTION}
    assert goals == {id(task.goal_step)}


def test_the_establish_allowlist_is_the_arms_and_the_goal_allowlist_is_not() -> None:
    task = _task()
    assert arm_cell_legs(task, "beads")[0].step.available_tools == ["Bash"]
    assert arm_cell_legs(task, "builtin")[0].step.available_tools == []
    goal_allowlists = {
        tuple(arm_cell_legs(task, name)[1].step.available_tools) for name in ARM_NAMES
    }
    assert len(goal_allowlists) == 1


def test_the_goal_leg_argv_is_byte_identical_across_the_arms() -> None:
    """The one leg the endpoint is read off. Its command line may not vary by arm: if it did,
    the arms would differ in what they were asked as well as in what they could remember."""
    task = _task()
    rendered = {
        name: arm_cell_calls(task, name, MemoryChannel.TRUSTED, model="sonnet").calls[-1]
        for name in ARM_NAMES
    }
    assert len({json.dumps(call, sort_keys=True) for call in rendered.values()}) == 1


def test_a_floor_arm_is_never_engaged_even_with_the_token_on_disk() -> None:
    """Engagement is a question about the arm's OWN store, and the floor has none. The token is
    planted where the comparator's store lives, so this fails if the check ever stops asking
    which arm it is looking at."""
    with arm_cell_store("none", label="test") as store:
        target = Path(
            native_memory_path(config_dir=str(store.config_dir), workdir=str(store.sandbox))
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("the value is ZZZ-CUR\n", encoding="utf-8")
        assert engagement_of(store, ("ZZZ-CUR",)) is False


def test_the_comparator_is_engaged_on_content_not_on_the_file_existing() -> None:
    with arm_cell_store("builtin", label="test") as store:
        index = Path(
            native_memory_path(config_dir=str(store.config_dir), workdir=str(store.sandbox))
        )
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text("- [Established facts](topic.md)\n", encoding="utf-8")
        topic = index.parent / "topic.md"
        topic.write_text("nothing of substance\n", encoding="utf-8")
        assert engagement_of(store, ("ZZZ-CUR",)) is False
        topic.write_text("the retention window is ZZZ-CUR\n", encoding="utf-8")
        assert engagement_of(store, ("ZZZ-CUR",)) is True


def test_the_beads_arm_is_engaged_from_its_receipts_and_not_from_a_bare_verb() -> None:
    """A verb token is not an operation (mem-bd-remember-list-is-not-a-write): a receipt for a
    call that carried no value is not engagement."""
    with arm_cell_store("beads", label="test") as store:
        bare = ({"event": "exit", "argv": ["bd", "remember"], "exit_code": 0},)
        assert engagement_of(store, ("ZZZ-CUR",), receipts=bare) is False
        wrote = ({"event": "exit", "argv": ["bd", "remember", "window=ZZZ-CUR"], "exit_code": 0},)
        assert engagement_of(store, ("ZZZ-CUR",), receipts=wrote) is True
        assert engagement_of(store, (), receipts=wrote) is False


def test_a_floor_pass_is_booked_as_a_leak_and_never_as_a_win(tmp_path: Path) -> None:
    """The simulated CLI persists whatever its prompt carried and reads it back, WITHOUT
    consulting `autoMemoryEnabled` -- so it models a CLI whose native memory is on, which is the
    one condition the floor arm's pin exists to prevent. That makes it the right adversary here:
    the floor gets a pass it has no store to account for, and the accounting has to book it as a
    leak. If `leaked` ever stopped firing, this reads as a floor arm that solved the task."""
    task = _task()
    cell = run_arm_cell(
        task,
        "none",
        repeat=0,
        model="sonnet",
        channel=MemoryChannel.TRUSTED,
        runner=simulated_builtin_runner(task.current_opaque_values),
    )
    assert cell.arm == "none"
    assert cell.engaged is False
    assert cell.passed is True
    assert cell.leaked is True
    assert cell.pinned_off is True
    assert cell.status == "ok"


def test_the_comparator_runs_end_to_end_and_carries_the_value(tmp_path: Path) -> None:
    task = _task()
    cell = run_arm_cell(
        task,
        "builtin",
        repeat=0,
        model="sonnet",
        channel=MemoryChannel.TRUSTED,
        runner=simulated_builtin_runner(task.current_opaque_values),
    )
    assert cell.engaged is True
    assert cell.passed is True
    assert cell.leaked is False
    assert cell.pinned_off is False
