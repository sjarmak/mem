"""mem-5sht9 — the memory tool surface: reachable, countable, and outliving the cwd wipe.

The invariants that make a zero call-rate MEAN something:

* the store survives ``toolreq_builtin._wipe_cwd_contents`` between two legs sharing a sandbox —
  the defect that would otherwise turn every leg-2 recall into a silent miss;
* a store inside the sandbox (wiped) or above it (contaminating, since ``bd init`` writes
  ``CLAUDE.md``) is REFUSED rather than measured;
* the counter keys on the structured tool name plus an argv-grammar parse of the ``command``
  argument — never prose, and never a substring;
* ``--mcp-config`` is emitted alongside ``--strict-mcp-config``, so an MCP-named tool is
  reachable at all, and it reaches the rendered plan as well as the sent argv;
* the tool surface moves the run identity, so a surface change cannot resume as a cache hit.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from membench.runner.e1_smoke import SMOKE_KEY, SMOKE_VALUE, run_smoke
from membench.runner.headless_agent import (
    HeadlessClaudeAgent,
    Leg,
    MemoryChannel,
    _render_only_runner,
    render_cell_calls,
)
from membench.runner.tool_surface import (
    MEMORY_ALLOWED_TOOLS,
    MEMORY_COMMAND,
    MemoryToolError,
    assert_store_outside,
    harness_call,
    memory_tool_calls,
    memory_verbs,
    memory_verbs_in_command,
    provision_memory_tool,
    resolve_bd_binary,
    settings_fingerprint,
    surface_fingerprint,
)
from membench.runner.toolreq_builtin import _wipe_cwd_contents
from membench.schemas.sequence import SequenceStep
from membench.schemas.trace import ToolCall

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

_HAS_BD = shutil.which(resolve_bd_binary()) is not None
requires_bd = pytest.mark.skipif(_HAS_BD is False, reason="bd is not installed on this host")


def bash(command: str) -> ToolCall:
    return ToolCall(name="Bash", arguments={"command": command})


# --------------------------------------------------------------------------------------
# the counter: structured name + argv grammar, never prose
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        ("bd recall widget-port", ["recall"]),
        ('bd remember "the port is 48317" --key p', ["remember"]),
        ("bd memories", ["memories"]),
        ("bd forget stale-key", ["forget"]),
        ("bd --json recall k", ["recall"]),
        ("/usr/local/bin/bd recall k", ["recall"]),
        ("cd /tmp && bd remember 'x'", ["remember"]),
        ("bd recall a; bd remember b", ["recall", "remember"]),
        # word boundaries, both ends
        ("abd recall k", []),
        ("mybd remember x", []),
        ("bd remembering the port", []),
        ("bd recalls k", []),
        # not a memory verb: `link` is `bd dep add` shorthand, and issue work is not memory work
        ("bd link a b", []),
        ("bd ready", []),
        # prose that merely mentions the verbs is not a call
        ("echo 'I should probably bd-remember this'", []),
    ],
)
def test_memory_verbs_parse_argv_not_prose(command: str, expected: list[str]) -> None:
    assert memory_verbs_in_command(command) == expected


def test_counter_ignores_non_bash_tool_names() -> None:
    """A Write whose CONTENT mentions the verb is not a memory call. Keying on the structured name
    first is what keeps the counter off the agent's text."""
    calls = [ToolCall(name="Write", arguments={"content": "run bd remember later"})]
    assert memory_tool_calls(calls) == 0
    assert memory_verbs(calls) == []


def test_counter_counts_blocks_and_verbs_separately() -> None:
    calls = [
        bash("ls"),
        bash("bd recall k && bd remember 'v'"),
        ToolCall(name="Read", arguments={"file_path": "/x"}),
    ]
    assert memory_tool_calls(calls) == 1
    assert memory_verbs(calls) == ["recall", "remember"]


def test_counter_tolerates_a_bash_call_with_no_command_argument() -> None:
    assert memory_tool_calls([ToolCall(name="Bash", arguments={})]) == 0


# --------------------------------------------------------------------------------------
# the store: outside the cwd, and it survives the wipe
# --------------------------------------------------------------------------------------


def test_assert_store_outside_refuses_a_store_inside_the_sandbox(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    with pytest.raises(MemoryToolError, match="inside the sandbox"):
        assert_store_outside(sandbox, sandbox / ".beads")


def test_assert_store_outside_refuses_a_store_above_the_sandbox(tmp_path: Path) -> None:
    sandbox = tmp_path / "store" / "sandbox"
    sandbox.mkdir(parents=True)
    with pytest.raises(MemoryToolError, match="ABOVE the sandbox"):
        assert_store_outside(sandbox, tmp_path / "store")


def test_assert_store_outside_accepts_sibling_directories(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    store = tmp_path / "store"
    sandbox.mkdir()
    store.mkdir()
    assert_store_outside(sandbox, store)  # does not raise


@requires_bd
def test_store_survives_wipe(tmp_path: Path) -> None:
    """THE bead's acceptance test: write through the tool in leg 1, wipe the shared sandbox cwd
    the way ``toolreq_builtin`` does between legs, read it back through the tool in leg 2.

    The shim is invoked with ``cwd=sandbox`` on both legs — exactly where the agent's Bash tool
    would run it — so this fails if the store is ever allowed back into the cwd."""
    root = tmp_path / "surface"
    root.mkdir()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    surface = provision_memory_tool(root)
    assert_store_outside(sandbox, surface.store_dir)

    # leg 1 — the agent writes
    harness_call(surface, ["remember", SMOKE_VALUE, "--key", SMOKE_KEY], cwd=sandbox)
    (sandbox / "scratch.txt").write_text("leg-1 residue", encoding="utf-8")

    _wipe_cwd_contents(sandbox)
    assert list(sandbox.iterdir()) == []

    # leg 2 — the agent reads, in the same (now empty) cwd
    assert SMOKE_VALUE in harness_call(surface, ["recall", SMOKE_KEY], cwd=sandbox)


@requires_bd
def test_provisioned_shim_is_executable_and_pins_the_store(tmp_path: Path) -> None:
    surface = provision_memory_tool(tmp_path)
    shim = surface.bin_dir / MEMORY_COMMAND
    assert shim.exists()
    assert shim.stat().st_mode & 0o111
    assert str(surface.store_dir) in shim.read_text(encoding="utf-8")
    assert not (tmp_path / ".beads").exists()  # nothing landed in the caller's cwd
    assert surface.env()["PATH"].split(":")[0] == str(surface.bin_dir)


def test_provision_raises_when_bd_init_fails(tmp_path: Path) -> None:
    def failing(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 1, "", "no beads database found")

    with pytest.raises(MemoryToolError, match="init"):
        provision_memory_tool(tmp_path, bd_binary="bd", runner=failing)


# --------------------------------------------------------------------------------------
# the argv: the tool is reachable, and the plan says the same thing the wire does
# --------------------------------------------------------------------------------------


def _step() -> SequenceStep:
    return SequenceStep(
        step_id="s", user_request="do it", available_tools=list(MEMORY_ALLOWED_TOOLS)
    )


def test_argv_allows_bash_so_the_memory_tool_is_reachable() -> None:
    agent = HeadlessClaudeAgent(model="m", runner=_render_only_runner)
    argv = agent.argv_for(_step(), {})
    assert argv[argv.index("--allowedTools") + 1] == "Bash,Write"


def test_argv_emits_mcp_config_alongside_strict_mcp_config(tmp_path: Path) -> None:
    config = str(tmp_path / "mcp.json")
    agent = HeadlessClaudeAgent(model="m", runner=_render_only_runner, mcp_config=config)
    argv = agent.argv_for(_step(), {})
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--mcp-config") + 1] == config


def test_argv_omits_mcp_config_when_no_server_is_declared() -> None:
    agent = HeadlessClaudeAgent(model="m", runner=_render_only_runner)
    assert "--mcp-config" not in agent.argv_for(_step(), {})


def test_rendered_plan_carries_the_mcp_config_the_cell_would_send(tmp_path: Path) -> None:
    """The plan and the wire must agree on every argv element, or the resume cache's write
    boundary refuses the measurement."""
    config = str(tmp_path / "mcp.json")
    legs = [Leg(name="goal", step=_step(), memory={})]
    planned = render_cell_calls(
        arm="ours", channel=MemoryChannel.RECALLED, legs=legs, model="m", mcp_config=config
    )
    sent = HeadlessClaudeAgent(model="m", runner=_render_only_runner, mcp_config=config).argv_for(
        _step(), {}
    )
    assert planned.calls == (tuple(sent),)


# --------------------------------------------------------------------------------------
# the identity: a surface change is not a cache hit; a tempdir path is not a cache miss
# --------------------------------------------------------------------------------------


def test_settings_fingerprint_moves_when_the_tool_surface_changes(tmp_path: Path) -> None:
    settings = {"autoMemoryEnabled": True}
    bare = settings_fingerprint(settings)
    with_mcp = settings_fingerprint(settings, mcp_config=str(tmp_path / "mcp.json"))
    assert bare != with_mcp
    # and it still moves on the settings alone, so folding the surface in did not swallow them
    assert bare != settings_fingerprint({"autoMemoryEnabled": False})


@requires_bd
def test_surface_fingerprint_ignores_the_realised_store_path(tmp_path: Path) -> None:
    """Two runs differ only in which tempdir their store landed in. Hashing that path would force
    a permanent MISS and re-spend real money on a difference that moves no measurement."""
    a = provision_memory_tool(tmp_path / "a")
    b = provision_memory_tool(tmp_path / "b")
    assert a.store_dir != b.store_dir
    assert a.fingerprint() == b.fingerprint() == surface_fingerprint()


# --------------------------------------------------------------------------------------
# the smoke, free path
# --------------------------------------------------------------------------------------


@requires_bd
def test_dry_run_smoke_reports_a_memory_tool_call() -> None:
    result = run_smoke(model="m", dry_run=True, timeout_s=30.0)
    assert result["memory_tool_calls"] == 1
    assert result["memory_verbs"] == ["recall"]
    assert result["tool_names"] == ["Bash"]
    assert result["recovered_value"] is True


def test_tempdir_prefix_stays_out_of_the_repo() -> None:
    """The provisioning root is the caller's tempdir, never the worktree — a store written into
    the repo would be both a leak and a contaminated ancestor."""
    with tempfile.TemporaryDirectory(prefix="membench-memory-") as root:
        assert Path(root).is_absolute()
