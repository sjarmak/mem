"""Arm separation, provable from a mint with no agent spawned (pre-registration §5, items 1-3).

Not free of side effects: minting the bd arm shells a real `git init` and `bd init`, so these
run under the same outside-the-sandbox store discipline as a paid leg. They buy no tokens.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Collection, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from membench.harbor.agent_memory import native_memory_path
from membench.runner.arm_context import capability_of, scaffold_of
from membench.runner.beads_arm_grid import (
    ARM_ESTABLISH_INSTRUCTION,
    COMMAND_NOT_FOUND,
    RECORD_CLAUSE,
    SHARED_SYSTEM_PATH,
    SHARED_TOOLCHAIN_COMMANDS,
    ArmProtocolError,
    arm_cell_calls,
    arm_cell_legs,
    arm_cell_store,
    assert_goal_allowlist_is_the_protocol,
    carries_native_memory,
    child_path_of,
    engagement_of,
    out_of_sandbox_operands,
    remint_config_dir,
    replant_context,
    run_arm_cell,
)
from membench.runner.e1_grid import ESTABLISH_INSTRUCTION
from membench.runner.headless_agent import (
    MemoryChannel,
    assistant_event,
    result_event,
    serialize_stream,
)
from membench.runner.memory_arm import ARM_NAMES, SHARED_PROTOCOL
from membench.runner.realagent_probe import CONFIG_FILE, REAL_TOOL
from membench.runner.tool_surface import BD_CONTEXT_FILES, CONFIG_DIR_ENV, MEMORY_COMMAND
from membench.runner.toolreq_builtin import simulated_builtin_runner
from membench.runner.toolreq_realagent import ToolReqRealAgentTask, adapt_sequence
from membench.schemas.trace import ToolCall
from membench.spawn import Runner
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


def test_no_arm_carries_the_operators_path_passthrough() -> None:
    """`MemoryToolSurface.env()` appends the operator's PATH, which on this machine has a real bd
    on it. A floor arm that inherited it would not be a floor -- and under ruling 2(a) the bd arm
    must not inherit it either, or it reaches tooling the floors cannot."""
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            entries = child_path_of(store.env())
            assert entries[0] == str(store.surface.bin_dir), name
            assert entries[1] == str(store.toolchain), name
            assert entries[2:] == [
                entry for entry in SHARED_SYSTEM_PATH if Path(entry).is_dir()
            ], name


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


def test_every_arm_runs_the_same_establish_instruction_and_the_same_goal_request() -> None:
    """One instruction for three arms. It now carries a recording clause (mem-q34kw), which is a
    treatment only if an arm hears it differently — so identity across the arms is the property,
    and it is asserted here rather than trusted to the one expression that builds it."""
    task = _task()
    prompts = set()
    goal_requests = set()
    for name in ARM_NAMES:
        establish, goal = arm_cell_legs(task, name)
        prompts.add(establish.step.user_request)
        goal_requests.add(goal.step.user_request)
        assert goal.memory == {}, name
        assert establish.memory == task.oracle_memory, name
    assert prompts == {ARM_ESTABLISH_INSTRUCTION}
    assert goal_requests == {task.goal_step.user_request}


def test_the_establish_instruction_asks_for_a_recording_and_names_no_mechanism() -> None:
    """The pilot's establish leg made zero tool calls under the silent instruction, on every arm,
    so nothing was recallable and the primary contrast was zero by arithmetic. The clause is what
    fixes that; naming a mechanism in it is what would break the arms' symmetry, since `bd` is on
    one arm's PATH, absent from the floor's, and never a tool name on the comparator's."""
    assert ARM_ESTABLISH_INSTRUCTION.startswith(ESTABLISH_INSTRUCTION)
    assert RECORD_CLAUSE in ARM_ESTABLISH_INSTRUCTION
    assert "record" in RECORD_CLAUSE.lower()
    for mechanism in ("bd", "beads", "memory", "tool", "CLAUDE.md", "file"):
        assert mechanism not in RECORD_CLAUSE.split(), RECORD_CLAUSE


def test_the_establish_allowlist_is_the_arms_and_the_goal_allowlist_is_the_protocols() -> None:
    task = _task()
    assert arm_cell_legs(task, "beads")[0].step.available_tools == ["Bash"]
    assert arm_cell_legs(task, "builtin")[0].step.available_tools == []
    goal_allowlists = {
        tuple(arm_cell_legs(task, name)[1].step.available_tools) for name in ARM_NAMES
    }
    assert goal_allowlists == {SHARED_PROTOCOL.goal_tools}


def test_the_goal_allowlist_comes_from_the_protocol_and_not_from_the_corpus() -> None:
    """mem-q34kw, the defect the one-task pilot bought. Every goal step in the live corpus carries
    `available_tools=['Write']` while the protocol declares four tools including `Bash`, and the
    old code passed the corpus step through untouched. `bd` is reachable only through `Bash`, so
    the arm under test could not deliver into the leg it is scored on. A corpus that disagrees is
    the normal case, not a hypothetical, and the protocol is what wins."""
    task = _task()
    starved = replace(
        task, goal_step=task.goal_step.model_copy(update={"available_tools": ["Write"]})
    )
    assert starved.goal_step.available_tools == ["Write"]
    _, goal = arm_cell_legs(starved, "beads")
    assert tuple(goal.step.available_tools) == SHARED_PROTOCOL.goal_tools
    assert "Bash" in goal.step.available_tools


def test_the_goal_allowlist_gate_refuses_a_subset_and_a_reordering() -> None:
    """Equality, not containment: the shape that shipped was a strict SUBSET of the declaration,
    which is exactly what a containment check would have waved through."""
    assert_goal_allowlist_is_the_protocol(SHARED_PROTOCOL.goal_tools)
    for wrong in (
        ("Write",),
        SHARED_PROTOCOL.goal_tools[:-1],
        tuple(reversed(SHARED_PROTOCOL.goal_tools)),
        (*SHARED_PROTOCOL.goal_tools, "WebFetch"),
        (),
    ):
        with pytest.raises(ArmProtocolError):
            assert_goal_allowlist_is_the_protocol(wrong)


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


def test_the_floor_arm_cannot_carry_a_value_through_its_config_dir(tmp_path: Path) -> None:
    """Gate 5's structural close, driven by the adversary that used to defeat it.

    The simulated CLI persists whatever its prompt carried into the config dir's native-memory
    layout and reads it back on the bare call, WITHOUT consulting `autoMemoryEnabled` -- it models
    a CLI whose native memory is on, which is the one condition the floor arm's pin exists to
    prevent. Before `remint_config_dir` it carried the establish leg's value straight into the
    goal leg and the floor arm passed. Now the goal leg runs on a config dir the establish leg
    never wrote to, and there is nothing to read back."""
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
    assert cell.passed is False
    assert cell.leaked is False
    assert cell.pinned_off is True
    assert cell.status == "ok"


def _unaccountable_pass_runner(values: Sequence[str]) -> Runner:
    """A CLI that answers the GOAL leg correctly and never persists anything.

    Deliberately says nothing about where the value came from: the point of `leaked` is that a
    pass the arm's own store cannot account for is booked as a leak whatever its channel, and a
    guard tied to one channel stops firing the moment that channel is closed -- which is what
    just happened to the config-dir one."""

    def run(argv: Collection[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        argv_list = list(argv)
        prompt = argv_list[2] if len(argv_list) > 2 else ""
        events: list[dict[str, object]] = []
        if values and not all(value in prompt for value in values):
            # The bare call is the goal leg: its prompt never carries the values.
            events.append(
                assistant_event(
                    [(REAL_TOOL, {"file_path": CONFIG_FILE, "content": " ".join(values)})]
                )
            )
        events.append(result_event())
        return subprocess.CompletedProcess(
            argv_list, returncode=0, stdout=serialize_stream(events), stderr=""
        )

    return run


def test_a_floor_pass_is_booked_as_a_leak_and_never_as_a_win(tmp_path: Path) -> None:
    """A floor arm that answers correctly has no store the answer can have come from, so the
    accounting has to book it as a leak. If `leaked` ever stopped firing, this reads as a floor
    arm that solved a memory-necessary task without memory."""
    task = _task()
    cell = run_arm_cell(
        task,
        "none",
        repeat=0,
        model="sonnet",
        channel=MemoryChannel.TRUSTED,
        runner=_unaccountable_pass_runner(list(task.current_opaque_values)),
    )
    assert cell.arm == "none"
    assert cell.engaged is False
    assert cell.passed is True
    assert cell.leaked is True
    assert cell.pinned_off is True
    assert cell.status == "ok"


def test_the_comparator_keeps_its_config_dir_across_the_legs() -> None:
    """The builtin arm's memory IS the config dir, so re-minting it would delete the store mid-
    cell and publish the deletion as the agent choosing not to remember."""
    with arm_cell_store("builtin", label="remint") as store:
        assert carries_native_memory(store.arm) is True
        assert remint_config_dir(store, leg=2) is store


def test_the_floor_and_beads_arms_get_a_fresh_config_dir_for_the_goal_leg() -> None:
    for name in ("beads", "none"):
        with arm_cell_store(name, label=f"remint-{name}") as store:
            goal = remint_config_dir(store, leg=2)
            assert goal.config_dir != store.config_dir, name
            assert goal.hook_log != store.hook_log, name
            assert goal.env()["CLAUDE_CONFIG_DIR"] == str(goal.config_dir), name
            assert goal.pinned_off is True, name
            # The pin and the hook both survive the re-mint; a fresh dir that lost either would
            # hand the goal leg a different arm than the establish leg ran.
            assert (goal.config_dir / "settings.json").exists(), name
            assert child_path_of(goal.env()) == child_path_of(store.env()), name


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


def _shape(store: object) -> list[str]:
    roles = {
        str(store.surface.bin_dir): "<cell>/bin",  # type: ignore[attr-defined]
        str(store.toolchain): "<cell>/toolchain",  # type: ignore[attr-defined]
    }
    return [roles.get(entry, entry) for entry in child_path_of(store.env())]  # type: ignore[attr-defined]


def test_every_arm_searches_the_same_path_shape() -> None:
    """Ruling 2(a). Before this, the beads arm inherited the operator's whole PATH and the two
    floor arms saw their own `bin_dir` alone -- a difference in what tooling the arm HAS."""
    shapes = {}
    for name in ARM_NAMES:
        with arm_cell_store(name, label=f"path-{name}") as store:
            shapes[name] = _shape(store)
    assert len(set(map(tuple, shapes.values()))) == 1, shapes
    assert shapes["none"][:2] == ["<cell>/bin", "<cell>/toolchain"], shapes


def test_no_arm_can_reach_the_operators_own_path() -> None:
    """The specific leak this closes: `/home/ds/.local/bin` holds the real `bd` and `dolt`."""
    operator = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    for name in ARM_NAMES:
        with arm_cell_store(name, label=f"leak-{name}") as store:
            entries = child_path_of(store.env())
            outside = [
                entry
                for entry in entries
                if not entry.startswith(str(store.surface.bin_dir).rsplit("/", 1)[0])
                and entry not in SHARED_SYSTEM_PATH
            ]
            assert not outside, f"{name} reaches {outside}"
            for entry in operator:
                if entry in SHARED_SYSTEM_PATH:
                    continue
                assert entry not in entries, f"{name} inherited {entry}"


def test_every_arm_reaches_the_same_shared_tools() -> None:
    reachable = {}
    for name in ARM_NAMES:
        with arm_cell_store(name, label=f"tools-{name}") as store:
            reachable[name] = sorted(entry.name for entry in store.toolchain.iterdir())
    assert len(set(map(tuple, reachable.values()))) == 1, reachable
    assert reachable["none"] == sorted(SHARED_TOOLCHAIN_COMMANDS), reachable


def test_the_shared_toolchain_never_carries_the_memory_command() -> None:
    """Equalizing the toolchain must not hand a floor arm the store it is graded on lacking."""
    assert MEMORY_COMMAND not in SHARED_TOOLCHAIN_COMMANDS
    for name in ("none", "builtin"):
        with arm_cell_store(name, label=f"nobd-{name}") as store:
            assert not (store.toolchain / MEMORY_COMMAND).exists()
            resolved = shutil.which(MEMORY_COMMAND, path=store.env()["PATH"])
            assert resolved == str(store.surface.bin_dir / MEMORY_COMMAND), resolved


def test_the_beads_arm_still_resolves_its_own_shim_first() -> None:
    with arm_cell_store("beads", label="shim-first") as store:
        resolved = shutil.which(MEMORY_COMMAND, path=store.env()["PATH"])
        assert resolved == str(store.surface.bin_dir / MEMORY_COMMAND), resolved


def test_the_establish_leg_reports_operands_the_wipe_cannot_reach(tmp_path: Path) -> None:
    """The floor arm is now ASKED to record and has no store to record into, so `/tmp` and
    `$HOME` are the channels left to it. Nothing here voids anything — prereg §9 names the
    out-of-sandbox detector as a mitigation this run does not buy — but the one-task pilot has
    to be able to SEE it, or the next diagnosis costs another instrumented round."""
    sandbox = tmp_path / "cell"
    (sandbox / "sub").mkdir(parents=True)

    def call(name: str, **arguments: object) -> ToolCall:
        return ToolCall(name=name, arguments=dict(arguments))

    inside = [
        call("Write", file_path=str(sandbox / "notes.md")),
        call("Write", file_path=str(sandbox / "sub" / "notes.md")),
        call("Bash", command=f"cd {sandbox} && echo hi > notes.md"),
        call("Bash", command="bd remember 'the token is 4'"),
    ]
    assert out_of_sandbox_operands(inside, sandbox=sandbox) == ()

    outside = [
        call("Write", file_path="/tmp/handoff.md"),
        call("Bash", command="echo token > ~/.carry"),
        call("Bash", command="cp state /var/tmp/keep"),
    ]
    assert out_of_sandbox_operands(outside, sandbox=sandbox) == (
        "/tmp/handoff.md",
        "/var/tmp/keep",
        "~/.carry",
    )
    # The sandbox itself is not outside itself, and a call carrying no operand contributes none.
    assert (
        out_of_sandbox_operands(
            [call("Write", file_path=str(sandbox)), call("Read")], sandbox=sandbox
        )
        == ()
    )
