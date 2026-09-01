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

from membench.runner.agent import AgentStepResult
from membench.runner.e1_smoke import (
    SMOKE_KEY,
    SMOKE_TOKEN,
    SMOKE_VALUE,
    halt_reason,
    run_smoke,
)
from membench.runner.e1_smoke import main as smoke_main
from membench.runner.headless_agent import (
    HeadlessClaudeAgent,
    Leg,
    MemoryChannel,
    _render_only_runner,
    assistant_event,
    render_cell_calls,
    result_event,
    serialize_stream,
)
from membench.runner.metrics import compute_metrics
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
    partition_memory_calls,
    provision_memory_tool,
    resolve_bd_binary,
    settings_fingerprint,
    surface_fingerprint,
)
from membench.runner.toolreq_builtin import _wipe_cwd_contents
from membench.schemas.metrics import EfficiencyMetrics
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


@pytest.mark.parametrize(
    "command,expected",
    [
        # --- shell KEYWORDS in front of the command word. d9809a2's `_verb_of_segment` skipped
        # only NAME=value prefixes, so the keyword BECAME the segment's command word and every one
        # of these counted as no call. E1's primary endpoint IS the call rate, so each of these is
        # a bias toward the near-zero null the series exists to rule out.
        ("if bd recall k; then echo hit; fi", ["recall"]),
        ("if ! bd recall k; then bd remember v --key k; fi", ["recall", "remember"]),
        ("for i in 1 2; do bd recall $i; done", ["recall"]),
        ("while read k; do bd recall $k; done < keys", ["recall"]),
        ("until bd recall k; do sleep 1; done", ["recall"]),
        (
            "if bd recall k > /dev/null 2>&1; then echo yes; else bd memories; fi",
            ["recall", "memories"],
        ),
        ("{ bd recall k; }", ["recall"]),
        # --- COMMAND SUBSTITUTION. `$(...)` broke the segment via its `(`; the backtick form did
        # not appear in _SEGMENT_BREAKS at all.
        ("echo `bd recall k`", ["recall"]),
        ("v=`bd recall k`", ["recall"]),
        ("echo $(bd recall k)", ["recall"]),
        # ...and the same substitution INSIDE a double-quoted run, which 5e45493 copied through
        # without scanning. Capturing a recall into a variable is the canonical agent spelling and
        # quoting it is the habitual one, so this miss ran in the direction of the near-zero null.
        ('v="$(bd recall k)"', ["recall"]),
        ('echo "$(bd recall k)"', ["recall"]),
        ('echo "`bd recall k`"', ["recall"]),
        ('test -n "$(bd recall k)" && echo yes', ["recall"]),
        ('echo "$(bd memories)"', ["memories"]),
        ('echo "prefix $(bd recall a) $(bd remember b c)"', ["recall", "remember"]),
        # A SINGLE-quoted run is literal - the shell expands nothing there - so the same text
        # must not manufacture a call. This is the over-count direction of the same change.
        ("echo '$(bd recall k)'", []),
        ("echo '`bd recall k`'", []),
        ('echo "$(( 1 + 2 ))"', []),
        # --- lesser wrappers named in review alongside the substitution defect
        ("watch bd memories", ["memories"]),
        ("watch -n 5 bd memories", ["memories"]),
        ("script -c 'bd recall k' /dev/null", ["recall"]),
        # NOT widened, and deliberately: an escaped space makes ONE argv word, so bd is handed
        # "recall k" and has no such subcommand. Counting it would invent a call the shell does
        # not make, which is the over-count this table's second half exists to prevent.
        ("bd recall\\ k", []),
        # --- TRANSPARENT WRAPPERS: the process they exec is bd, so the call is a memory call.
        ("sudo bd recall k", ["recall"]),
        ("sudo -u bot bd recall k", ["recall"]),
        ("env bd recall k", ["recall"]),
        ("env FOO=1 bd recall k", ["recall"]),
        ("/usr/bin/env bd recall k", ["recall"]),
        ("timeout 30 bd recall k", ["recall"]),
        ("timeout -k 5 30s bd recall k", ["recall"]),
        ("command bd recall k", ["recall"]),
        ("nohup bd recall k &", ["recall"]),
        ("time bd recall k", ["recall"]),
        ("exec bd recall k", ["recall"]),
        ("stdbuf -oL bd memories", ["memories"]),
        ("cat keys | xargs -n1 bd recall", ["recall"]),
        ("cat keys | xargs -I{} bd recall {}", ["recall"]),
        ("sudo -u bot env FOO=1 timeout 5 bd recall k", ["recall"]),
        # --- an interpreter's `-c` STRING is a command line, not an opaque argument
        ("bash -c 'bd recall k'", ["recall"]),
        ('sh -c "bd recall k"', ["recall"]),
        ("sh -lc 'bd recall a; bd remember b'", ["recall", "remember"]),
        ("sudo bash -c 'bd recall k'", ["recall"]),
        ('eval "bd recall k"', ["recall"]),
        # --- and none of the wrappers may manufacture a call the shell would not make: the scan
        # stops at the first non-option word and it has to BE bd.
        ("timeout 30 echo bd recall k", []),
        ("sudo echo bd recall k", []),
        ("command -v bd", []),
        ("bash -c 'echo bd recall k'", []),
        ("bash --version", []),
        ("for bd in a b; do echo $bd; done", []),
    ],
)
def test_counter_sees_wrapped_and_keyworded_calls(command: str, expected: list[str]) -> None:
    """Every form here returned ``[]`` against d9809a2 (the wrapper/keyword ones) or was invented
    to pin the other direction of the same fix. Reproduced independently by two verifiers before
    it was written."""
    assert memory_verbs_in_command(command) == expected


def test_braces_break_a_segment_only_when_they_stand_alone() -> None:
    """The tokenizer change the ``xargs -I{}`` case rests on: an attached brace is literal text,
    a standalone one is shell grouping. Breaking on every brace split the word and lost the call."""
    assert command_segments("xargs -I{} bd recall {}") == [["xargs", "-I{}", "bd", "recall", "{}"]]
    assert command_segments("{ bd recall k; }") == [["bd", "recall", "k"]]


def test_a_quoted_substitution_is_scanned_but_a_quoted_literal_is_not() -> None:
    """The two halves of the double-quote rule, pinned at the tokenizer rather than the counter.

    The shell RUNS ``$(...)`` inside double quotes and does not inside single quotes, so the
    scanner has to split on exactly that line. Pinning it here as well as in the verb table is
    deliberate: the verb table would still pass if the substitution leaked into the surrounding
    word instead of becoming its own segment."""
    assert command_segments('v="$(bd recall k)"') == [["v="], ["bd", "recall", "k"]]
    assert command_segments("echo '$(bd recall k)'") == [["echo", "$(bd recall k)"]]
    # Nested, because an agent that quotes once tends to quote twice.
    assert ["bd", "recall", "k"] in command_segments('echo "$(echo \\"$(bd recall k)\\")"')
    # Unterminated: the tail is still scanned rather than swallowed whole.
    assert ["bd", "recall", "k"] in command_segments('echo "$(bd recall k')


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
    surface = provision_memory_tool(tmp_path, sandbox=None)
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
    surface = provision_memory_tool(tmp_path, sandbox=None)
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
    surface = provision_memory_tool(tmp_path, sandbox=None)
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
        provision_memory_tool(tmp_path, sandbox=None, bd_binary="/bin/sh", runner=failing)


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
    a = provision_memory_tool(tmp_path / "a", sandbox=None)
    b = provision_memory_tool(tmp_path / "b", sandbox=None)
    assert a.store_dir != b.store_dir
    assert a.fingerprint() == b.fingerprint() == surface_fingerprint()


# --------------------------------------------------------------------------------------
# the smoke, free path
# --------------------------------------------------------------------------------------


def answering_runner(final_answer: str) -> object:
    """A ``claude -p`` stand-in that makes the memory call and then answers IN ITS OWN WORDS.

    This is the geometry ``simulated_runner`` cannot produce. That simulator sets ``final_answer``
    to raw shim stdout, so the seeded sentence is present by construction and every assertion about
    recovery passes whether the estimator is right or wrong — which is exactly how d9809a2 shipped
    a gate that HALTS on a correctly recalling agent. Here the answer is supplied by the test, so
    the estimator is the only thing under test."""

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        stream = serialize_stream(
            [
                assistant_event([("Bash", {"command": f"bd recall {SMOKE_KEY}"})]),
                result_event(final_answer),
            ]
        )
        return subprocess.CompletedProcess(list(argv), 0, stream, "")

    return run


@requires_bd
def test_dry_run_smoke_reports_a_memory_tool_call() -> None:
    result = run_smoke(model="m", dry_run=True, timeout_s=30.0)
    assert result["endogenous_memory_tool_calls"] == 1
    assert result["endogenous_memory_verbs"] == ["recall"]
    assert result["tool_names"] == ["Bash"]
    assert result["recovered_token"] is True
    # and it says out loud that it is not a paid reachability result
    assert result["paid"] is False


def test_emitted_keys_do_not_collide_with_efficiency_metrics() -> None:
    """FIX 4 as a property, not a spelling. ``EfficiencyMetrics.memory_tool_calls`` counts
    harness-performed memory EVENTS; the smoke row counts calls the AGENT chose to make. A consumer
    joining the two on a shared key would compare different quantities silently, so the bare names
    must not appear on the JSON surface at all.

    The property is DIFFERENT quantities under one name, not any shared name. E3b gave
    ``EfficiencyMetrics`` its own ``endogenous_memory_*`` fields carrying the agent-chosen count
    this row already reports, and there the shared key is the correct join rather than the silent
    mismatch: both sides are counted by ``tool_surface`` from observed argv. So those names are
    exempted BY NAME and the bare ones stay banned — an exemption list that a future bare
    ``memory_tool_calls`` cannot slip into."""
    row = run_smoke(
        model="m",
        dry_run=False,
        timeout_s=30.0,
        runner=answering_runner(f"the handle is {SMOKE_TOKEN}"),
    )
    assert "memory_tool_calls" not in row
    assert "memory_verbs" not in row
    deliberately_shared = {
        "endogenous_memory_tool_calls",
        "endogenous_memory_reads",
        "endogenous_memory_writes",
    }
    assert set(EfficiencyMetrics.model_fields) & set(row) <= deliberately_shared


@requires_bd
def test_a_paraphrasing_agent_passes_the_gate() -> None:
    """The defect FIX 2 removes. The prompt asks the agent to REPORT THE HANDLE, so a correctly
    recalling agent answers with the handle, not with the seeded sentence. d9809a2 required the
    sentence verbatim and would have HALTED the whole series on a working surface."""
    row = run_smoke(
        model="m",
        dry_run=False,
        timeout_s=30.0,
        runner=answering_runner(f"I looked it up: the handle is {SMOKE_TOKEN}."),
    )
    assert row["endogenous_memory_tool_calls"] == 1
    assert row["recovered_token"] is True
    # the paraphrase does NOT contain the seeded sentence — which is the whole point
    assert row["recovered_sentence"] is False
    assert halt_reason({**row, "paid": True}) is None


@requires_bd
def test_an_answer_without_the_token_halts() -> None:
    """The other side: a plausible answer the agent could have written without reading the store
    recovers nothing, and must HALT. The token is unguessable precisely so this case is decidable.
    """
    row = run_smoke(
        model="m",
        dry_run=False,
        timeout_s=30.0,
        runner=answering_runner("The staging widget service handle is stored in the memory."),
    )
    assert row["recovered_token"] is False
    reason = halt_reason({**row, "paid": True})
    assert reason is not None
    assert "never recovered the seeded token" in reason


@requires_bd
def test_the_token_is_not_recovered_as_a_fragment() -> None:
    row = run_smoke(
        model="m",
        dry_run=False,
        timeout_s=30.0,
        runner=answering_runner(f"the handle is x{SMOKE_TOKEN}9"),
    )
    assert row["recovered_token"] is False


def test_halt_gate_passes_only_when_the_surface_both_answers_and_is_called() -> None:
    assert (
        halt_reason(
            {"endogenous_memory_tool_calls": 1, "recovered_token": True, "paid": True},
        )
        is None
    )


def test_halt_gate_halts_on_zero_calls() -> None:
    reason = halt_reason(
        {"endogenous_memory_tool_calls": 0, "recovered_token": False, "paid": True}
    )
    assert reason is not None
    assert "no memory tool call" in reason


def test_halt_gate_halts_when_calls_were_made_but_nothing_came_back() -> None:
    """The observed live failure, as a unit: 7 calls against a self-exec'ing shim, every one of
    them hung, no value recovered — and a52bebd's count-only gate exited 0 on it."""
    reason = halt_reason(
        {"endogenous_memory_tool_calls": 7, "recovered_token": False, "paid": True}
    )
    assert reason is not None
    assert "never recovered the seeded token" in reason


def test_halt_gate_refuses_to_bless_an_unpaid_row() -> None:
    """FIX 3. ``paid`` was added to say a simulated row proves nothing and then read by nobody, so
    ``--dry-run`` exited 0 whenever the simulator recovered the value. The exit code is the channel
    a CI gate reads; an advisory JSON field is not one."""
    reason = halt_reason(
        {"endogenous_memory_tool_calls": 1, "recovered_token": True, "paid": False}
    )
    assert reason is not None
    assert "not a paid run" in reason


@requires_bd
def test_dry_run_main_exits_nonzero() -> None:
    """The same refusal at the process boundary, which is where a gate actually observes it."""
    assert smoke_main(["--dry-run", "--json"]) == 1


# --------------------------------------------------------------------------------------
# the store check is not optional, and a memory call is not a non-memory call
# --------------------------------------------------------------------------------------


def test_provision_requires_the_sandbox_to_be_named() -> None:
    """FIX 5. ``sandbox`` defaulted to ``None``, so ``assert_store_outside`` fired only by caller
    discipline: a cell that forgot the argument got a store the wipe eats and a silent miss in the
    second leg. Keyword-only with NO default means every call site has to state what it is doing."""
    with pytest.raises(TypeError):
        provision_memory_tool(Path("/tmp"))  # type: ignore[call-arg]


def test_partition_splits_a_bash_wrapped_memory_call_out() -> None:
    calls = [bash("ls -la"), bash("bd recall k"), ToolCall(name="Read", arguments={})]
    memory, other = partition_memory_calls(calls)
    assert [c.arguments.get("command") for c in memory] == ["bd recall k"]
    assert len(other) == 2


def test_metrics_do_not_score_a_memory_call_as_a_non_memory_call() -> None:
    """FIX 6. ``runner/metrics.py`` passed ``len(agent_result.tool_calls)`` straight through as
    ``non_memory_tool_calls``, so the moment a grid hands the agent this surface every endogenous
    memory call inflates the non-memory cost and the latency that goes with it — the arm that calls
    the tool itself would look like the arm doing the most non-memory work. A docstring was the
    only guard.

    The assertions below also carry the round-3 correction to that fix: FIX 6 as shipped moved the
    bias instead of removing it. It stopped counting the memory call as non-memory and then routed
    it nowhere, so this very test asserted a two-call step reports ``tool_calls_total == 1`` and a
    410ms step reports 10. A published cost metric that drops exactly the calls the agent chose to
    make rewards the arm that uses memory most with the cheapest-looking total, which is the one
    direction the series cannot afford to be wrong in. Total now means total."""
    result = AgentStepResult(
        final_answer="done",
        tool_calls=[
            ToolCall(name="Bash", arguments={"command": "ls"}, latency_ms=10),
            ToolCall(name="Bash", arguments={"command": "bd recall k"}, latency_ms=400),
        ],
        check_results={"c1": True},
        writes_performed={},
    )
    bundle = compute_metrics(
        SequenceStep(step_id="s", user_request="go"),
        result,
        None,
        [],
        reads_enabled=False,
    )
    # The split still holds: the memory call is not non-memory work...
    assert bundle.efficiency.non_memory_tool_calls == 1
    # ...and the harness performed no memory event of its own here...
    assert bundle.efficiency.memory_tool_calls == 0
    # ...but the agent made TWO tool calls costing 410ms, and the totals say so.
    assert bundle.efficiency.tool_calls_total == 2
    assert bundle.efficiency.tool_latency_ms == 410


def test_tempdir_prefix_stays_out_of_the_repo() -> None:
    """The provisioning root is the caller's tempdir, never the worktree — a store written into
    the repo would be both a leak and a contaminated ancestor."""
    with tempfile.TemporaryDirectory(prefix="membench-memory-") as root:
        assert Path(root).is_absolute()
