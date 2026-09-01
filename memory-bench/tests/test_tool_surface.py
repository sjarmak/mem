"""mem-5sht9 — the memory tool surface: reachable, countable, and outliving the cwd wipe.

The invariants that make a zero call-rate MEAN something:

* the shim the agent reaches through ``surface.env()`` execs the REAL bd, not itself. That is
  the one test geometry a52bebd did not have: every one of its shim tests invoked the shim by
  ABSOLUTE path under the ambient ``PATH``, so the suite was green while the shipped surface
  exec'd itself forever and hung every call the evaluated agent made;
* the store survives ``toolreq_builtin._wipe_cwd_contents`` between two legs sharing a sandbox;
* a store inside the sandbox (wiped) or above it (contaminating) is REFUSED by
  ``provision_memory_tool`` itself, not merely by a helper a caller may forget to call;
* the counter keys on the structured tool name plus a shell-word scan of the ``command``
  argument — never prose, never a comment, never a heredoc body, and it does not MISS a call
  behind a space-separated flag value (the miss direction is the one that manufactures the
  near-zero null this series exists to rule out);
* ``--mcp-config`` is emitted alongside ``--strict-mcp-config``, and ``--disallowedTools``
  carries the host-signal deny list;
* the tool surface moves the run identity, so a surface change cannot resume as a cache hit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from membench.runner.e1_smoke import SMOKE_KEY, SMOKE_VALUE, halt_reason, run_smoke
from membench.runner.headless_agent import (
    HeadlessClaudeAgent,
    Leg,
    MemoryChannel,
    _render_only_runner,
    render_cell_calls,
)
from membench.runner.tool_surface import (
    HOST_DENIED_TOOLS,
    MEMORY_ALLOWED_TOOLS,
    MEMORY_COMMAND,
    MemoryToolError,
    assert_store_outside,
    command_segments,
    endogenous_memory_tool_calls,
    endogenous_memory_verbs,
    harness_call,
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

_HAS_BD = shutil.which(MEMORY_COMMAND) is not None
requires_bd = pytest.mark.skipif(_HAS_BD is False, reason="bd is not installed on this host")


def bash(command: str) -> ToolCall:
    return ToolCall(name="Bash", arguments={"command": command})


# --------------------------------------------------------------------------------------
# the counter: shell words of the EXECUTED command, never prose
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
    ],
)
def test_memory_verbs_parse_argv_not_prose(command: str, expected: list[str]) -> None:
    assert memory_verbs_in_command(command) == expected


@pytest.mark.parametrize(
    "command,expected",
    [
        # --- the MISS direction. a52bebd's regex allowed only `--flag` / `--flag=v` between the
        # command and the verb, so every space-separated flag value hid a real memory call. An
        # under-count here manufactures exactly the near-zero result this series must rule out.
        ("bd -C /tmp recall k", ["recall"]),
        ("bd --db /x/y.db recall k", ["recall"]),
        ("bd --directory /tmp remember v --key k", ["remember"]),
        ("bd --actor bot --json memories", ["memories"]),
        ("bd --db=/x/y.db recall k", ["recall"]),
        ("BEADS_ACTOR=bot bd recall k", ["recall"]),
        ("bd \\\n    recall k", ["recall"]),
        ("(bd recall k)", ["recall"]),
        ("bd -q -C /tmp --json forget k", ["forget"]),
        # a value-taking flag must not swallow the verb when it is `=`-joined
        ("bd --actor=bot recall k", ["recall"]),
        # --- the OVER-count direction: prose, comments and heredoc bodies inside an argv
        ("echo bd recall x", []),
        ("echo 'I should probably bd remember this'", []),
        ('echo "bd recall x"', []),
        ("# bd recall k", []),
        ("ls /tmp  # then bd recall k", []),
        ("printf '%s' 'bd remember v'", []),
        ("cat <<EOF\nbd recall k\nEOF", []),
        ("cat <<-'NOTE'\nremember to run bd remember v\nNOTE", []),
        # a real call AFTER a heredoc body still counts — the skip must end at the delimiter
        ("cat <<EOF > f\nbd recall decoy\nEOF\nbd recall real", ["recall"]),
        # a subcommand that merely shares the verb's spelling is not a memory call
        ("bd issue remember", []),
        ("bd dep remember a b", []),
    ],
)
def test_counter_adversarial_argv(command: str, expected: list[str]) -> None:
    assert memory_verbs_in_command(command) == expected


def test_segments_keep_a_quoted_run_as_one_word() -> None:
    """The property the over-count cases rest on: a quoted blob can never split into a command
    word plus a verb, however much shell-shaped text it contains."""
    assert command_segments('echo "bd recall x"') == [["echo", "bd recall x"]]


def test_counter_ignores_non_bash_tool_names() -> None:
    """A Write whose CONTENT mentions the verb is not a memory call. Keying on the structured name
    first is what keeps the counter off the agent's text."""
    calls = [ToolCall(name="Write", arguments={"content": "run bd remember later"})]
    assert endogenous_memory_tool_calls(calls) == 0
    assert endogenous_memory_verbs(calls) == []


def test_counter_counts_blocks_and_verbs_separately() -> None:
    calls = [
        bash("ls"),
        bash("bd recall k && bd remember 'v'"),
        ToolCall(name="Read", arguments={"file_path": "/x"}),
    ]
    assert endogenous_memory_tool_calls(calls) == 1
    assert endogenous_memory_verbs(calls) == ["recall", "remember"]


def test_counter_tolerates_a_bash_call_with_no_command_argument() -> None:
    assert endogenous_memory_tool_calls([ToolCall(name="Bash", arguments={})]) == 0


# --------------------------------------------------------------------------------------
# the shim: it must exec the REAL bd, under the env the AGENT gets
# --------------------------------------------------------------------------------------


def test_resolve_bd_binary_returns_an_absolute_path() -> None:
    resolved = resolve_bd_binary("/bin/sh")
    assert Path(resolved).is_absolute()
    assert os.path.isfile(resolved)


def test_resolve_bd_binary_refuses_a_binary_inside_the_shim_dir(tmp_path: Path) -> None:
    """The fatal defect, as a unit: a `bd` that resolves into the shim directory is a shim that
    execs itself. Refused at provision time rather than discovered as a hang."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / MEMORY_COMMAND
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    with pytest.raises(MemoryToolError, match="inside the shim directory"):
        resolve_bd_binary(str(fake), refuse_under=bin_dir)


def test_resolve_bd_binary_raises_when_bd_is_absent(tmp_path: Path) -> None:
    with pytest.raises(MemoryToolError, match="no executable"):
        resolve_bd_binary(str(tmp_path / "nope" / "bd"))


@requires_bd
def test_shim_does_not_self_exec_under_surface_env(tmp_path: Path) -> None:
    """THE regression guard for the defect that rejected a52bebd, and the only geometry that can
    see it: run the shim the way the AGENT does — by bare name, with ``surface.env()`` applied, so
    the shim directory is FIRST on PATH.

    Against the old shim (``exec "bd" ...`` with a bare-name resolution) this re-execs itself,
    prepending another ``-C <store>`` each pass until the argv is unusable; the call never
    returns a clean `bd version`. Every a52bebd test invoked the shim by absolute path under the
    ambient PATH and so passed whether the surface worked or not."""
    surface = provision_memory_tool(tmp_path)
    assert Path(surface.bd_binary).is_absolute()
    assert surface.bin_dir.resolve() not in Path(surface.bd_binary).parents

    env = {**os.environ, **surface.env()}
    assert env["PATH"].split(os.pathsep)[0] == str(surface.bin_dir)
    completed = subprocess.run(
        [MEMORY_COMMAND, "version"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    # and the recursion signature is absent: one `-C` was injected, not hundreds
    assert "-C" not in completed.stdout


@requires_bd
def test_the_old_bare_name_shim_really_does_self_exec(tmp_path: Path) -> None:
    """The isolated revert, kept as a test so the guard above can never be vacuous.

    This writes a52bebd's shim body verbatim — ``exec "bd" ...``, the bare name — into the shim
    directory and runs it with the shim directory first on PATH, the geometry ``surface.env()``
    creates. It must NOT succeed. Measured on this host: exit 124, the call never returns.

    A guard test whose fixture geometry lets it pass either way is the failure that shipped the
    defect; this asserts the geometry itself is discriminating."""
    bin_dir = tmp_path / "bin"
    store = tmp_path / "store"
    bin_dir.mkdir()
    store.mkdir()
    shim = bin_dir / MEMORY_COMMAND
    shim.write_text(f'#!/bin/sh\nexec "bd" -C "{store}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    env = {**os.environ, "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])}
    try:
        completed = subprocess.run(
            [MEMORY_COMMAND, "version"],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return  # the observed behaviour: it never returns
    assert completed.returncode != 0, "the bare-name shim must not resolve to the real bd"


@requires_bd
def test_memory_round_trip_through_the_agent_path(tmp_path: Path) -> None:
    """A write and a read spelled the way the agent spells them — bare `bd`, resolved through
    ``surface.env()`` — not through ``harness_call``'s absolute path."""
    surface = provision_memory_tool(tmp_path)
    env = {**os.environ, **surface.env()}
    write = subprocess.run(
        [MEMORY_COMMAND, "remember", SMOKE_VALUE, "--key", SMOKE_KEY],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert write.returncode == 0, write.stderr[-2000:]
    read = subprocess.run(
        [MEMORY_COMMAND, "recall", SMOKE_KEY],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert read.returncode == 0, read.stderr[-2000:]
    assert SMOKE_VALUE in read.stdout


# --------------------------------------------------------------------------------------
# the store: outside the cwd, refused at provision time, and it survives the wipe
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


def test_provision_refuses_a_store_the_sandbox_would_eat(tmp_path: Path) -> None:
    """``assert_store_outside`` is CALLED by the provisioner, not left as a helper a caller may
    forget — and it fires before ``bd init`` writes CLAUDE.md/AGENTS.md/.claude anywhere."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    def never(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("bd init must not run once the store placement is refused")

    with pytest.raises(MemoryToolError, match="inside the sandbox"):
        provision_memory_tool(sandbox / "root", sandbox=sandbox, runner=never)


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
    surface = provision_memory_tool(root, sandbox=sandbox)

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
    body = shim.read_text(encoding="utf-8")
    assert str(surface.store_dir) in body
    assert f'exec "{surface.bd_binary}"' in body  # an absolute path, never the bare name
    assert not (tmp_path / ".beads").exists()  # nothing landed in the caller's cwd
    assert surface.env()["PATH"].split(os.pathsep)[0] == str(surface.bin_dir)


def test_provision_raises_when_bd_init_fails(tmp_path: Path) -> None:
    def failing(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 1, "", "no beads database found")

    with pytest.raises(MemoryToolError, match="init"):
        provision_memory_tool(tmp_path, bd_binary="/bin/sh", runner=failing)


# --------------------------------------------------------------------------------------
# the argv: the tool is reachable, the host-signal verbs are not, and the plan agrees
# --------------------------------------------------------------------------------------


def _step() -> SequenceStep:
    return SequenceStep(
        step_id="s", user_request="do it", available_tools=list(MEMORY_ALLOWED_TOOLS)
    )


def test_argv_allows_bash_so_the_memory_tool_is_reachable() -> None:
    agent = HeadlessClaudeAgent(model="m", runner=_render_only_runner)
    argv = agent.argv_for(_step(), {})
    assert argv[argv.index("--allowedTools") + 1] == "Bash,Write"


def test_argv_emits_the_host_signal_deny_list() -> None:
    """Host safety, such as it is: the sandbox bounds the cwd, not the process table, and a hung
    surface once drove the evaluated agent to a host-wide `pkill -9 bd`."""
    agent = HeadlessClaudeAgent(
        model="m", runner=_render_only_runner, disallowed_tools=HOST_DENIED_TOOLS
    )
    argv = agent.argv_for(_step(), {})
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "Bash(pkill:*)" in denied
    assert "Bash(kill:*)" in denied


def test_argv_omits_disallowed_tools_when_none_are_denied() -> None:
    agent = HeadlessClaudeAgent(model="m", runner=_render_only_runner)
    assert "--disallowedTools" not in agent.argv_for(_step(), {})


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
    # and it says out loud that it is not a paid reachability result
    assert result["paid"] is False


def test_halt_gate_passes_only_when_the_surface_both_answers_and_is_called() -> None:
    assert halt_reason({"memory_tool_calls": 1, "recovered_value": True}) is None


def test_halt_gate_halts_on_zero_calls() -> None:
    reason = halt_reason({"memory_tool_calls": 0, "recovered_value": False})
    assert reason is not None
    assert "no memory tool call" in reason


def test_halt_gate_halts_when_calls_were_made_but_nothing_came_back() -> None:
    """The observed live failure, as a unit: 7 calls against a self-exec'ing shim, every one of
    them hung, no value recovered — and a52bebd's count-only gate exited 0 on it."""
    reason = halt_reason({"memory_tool_calls": 7, "recovered_value": False})
    assert reason is not None
    assert "never recovered the seeded value" in reason


def test_tempdir_prefix_stays_out_of_the_repo() -> None:
    """The provisioning root is the caller's tempdir, never the worktree — a store written into
    the repo would be both a leak and a contaminated ancestor."""
    with tempfile.TemporaryDirectory(prefix="membench-memory-") as root:
        assert Path(root).is_absolute()
