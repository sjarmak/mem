"""§4.4 real-run substrate: HeadlessClaudeAgent prompt assembly + stream parsing, the
trajectory driver, and the mem-mtqi/pjh8.2 memory-channel framing (recalled vs trusted)
+ model-resolution. Hermetic — a fake CLI runner returns a canned Claude Code
stream-json; no real `claude`, no network, no scix-batch."""

from __future__ import annotations

import errno
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

import pytest

from membench.metrics.action_impact_run import ArmStepTrajectory
from membench.runner.agent import Agent
from membench.runner.headless_agent import (
    ENV_API_KEY,
    ENV_MODEL,
    VERSION_TIMEOUT_S,
    HeadlessAgentError,
    HeadlessClaudeAgent,
    MemoryChannel,
    RecordingRunner,
    _render_only_runner,
    _stream_result_text,
    _stream_usage_tokens,
    _tool_calls_from_stream,
    a_paid_run_carries_the_metered_api_key,
    a_paid_run_needs_a_model,
    assistant_event,
    build_agent_prompt,
    one_cycle,
    resolve_cli_version,
    result_event,
    serialize_stream,
)
from membench.runner.trajectory_run import (
    run_arm_trajectories,
    run_sequence_arms,
    run_step_trajectory,
)
from membench.schemas.sequence import BenchmarkSequence, SequenceStep
from membench.schemas.trace import ToolCall
from membench.spawn import Runner


def _step(
    step_id: str = "s1",
    request: str = "Fix the failing import",
    tools: list[str] | None = None,
) -> SequenceStep:
    return SequenceStep(
        step_id=step_id,
        user_request=request,
        available_tools=tools if tools is not None else ["Read", "Edit", "Bash"],
    )


def _stream_json(
    *tool_uses: tuple[str, dict[str, Any]],
    result: str = "done",
    usage: tuple[int, int] | None = (10, 5),
) -> str:
    """A minimal Claude Code stream-json transcript: one assistant event whose content
    holds the given tool_use blocks, then a terminal result event.

    Shorthand over ``serialize_stream`` (this file's own default usage, varargs tool_uses), not a
    second opinion on the format."""
    return serialize_stream([assistant_event(tool_uses, usage=usage), result_event(result)])


def _fake_runner(stdout: str, *, returncode: int = 0, stderr: str = ""):
    captured: dict[str, Any] = {}

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    runner.captured = captured  # type: ignore[attr-defined]
    return runner


# --------------------------------------------------------------------------- #
# the stream-json serializer — the parsers' inverse
# --------------------------------------------------------------------------- #
def test_serialized_stream_round_trips_through_every_parser() -> None:
    """The property that earns the serializer its home beside the parsers: what it writes,
    they read back. A serializer that drifted from the format would fail HERE rather than
    in whichever simulator happened to notice."""
    stream = serialize_stream(
        [
            assistant_event(
                [("Write", {"file_path": "config.json", "content": "v2"})], usage=(7, 3)
            ),
            result_event("wrote it"),
        ]
    )
    assert _tool_calls_from_stream(stream) == [
        ToolCall(name="Write", arguments={"file_path": "config.json", "content": "v2"})
    ]
    assert _stream_usage_tokens(stream) == (7, 3)
    assert _stream_result_text(stream) == "wrote it"


def test_assistant_event_keeps_tool_uses_in_order() -> None:
    """Stream ORDER is what `_tool_calls_from_stream` and `bbon.extract` both key on."""
    stream = serialize_stream(
        [assistant_event([("Read", {"path": "a.py"}), ("Grep", {"q": "import"})]), result_event()]
    )
    assert [call.name for call in _tool_calls_from_stream(stream)] == ["Read", "Grep"]


def test_assistant_event_without_usage_omits_the_key() -> None:
    """`usage=None` must yield an event carrying NO usage key — that absence is what
    `_stream_usage_tokens`' honest-unmeasured (0, 0) branch is tested against."""
    event = assistant_event([("Read", {})], usage=None)
    message = event["message"]
    assert isinstance(message, dict)
    assert "usage" not in message
    assert _stream_usage_tokens(serialize_stream([event])) == (0, 0)


def test_serialize_stream_emits_one_json_object_per_line() -> None:
    """The JSONL shape `claude -p --output-format stream-json` prints — the parsers walk it
    line by line, so an event spanning lines would silently parse as nothing."""
    stream = serialize_stream([assistant_event([("Read", {})]), result_event()])
    lines = stream.splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["type"] for line in lines] == ["assistant", "result"]


def test_serialize_stream_of_no_events_is_empty_not_whitespace() -> None:
    """A cell that emitted nothing serializes to "" — the parsers' empty-stream branch."""
    assert serialize_stream([]) == ""


# --------------------------------------------------------------------------- #
# prompt assembly
# --------------------------------------------------------------------------- #
def test_prompt_none_arm_is_bare_request() -> None:
    prompt = build_agent_prompt(_step(request="Do X"), {})
    assert "Retrieved memory" not in prompt  # empty surface == the control condition
    assert "Do X" in prompt


def test_prompt_injects_memory_block() -> None:
    prompt = build_agent_prompt(_step(), {"m1": "prefer ripgrep", "m2": "tests live in tests/"})
    assert "Retrieved memory" in prompt
    assert "[m1] prefer ripgrep" in prompt
    assert "[m2] tests live in tests/" in prompt


# --------------------------------------------------------------------------- #
# the CLI seam the paid grids' cache identity is checked against
# --------------------------------------------------------------------------- #
def test_the_recorder_sees_every_call_the_agent_spawns() -> None:
    """The seam, and why it is the runner rather than the step result: a `prompt` field an arm
    populates records what the arm MEANT to send. The runner records what went out."""
    recorder = RecordingRunner(_fake_runner(_stream_json()))
    agent = HeadlessClaudeAgent(runner=recorder)
    agent.run_step(_step(tools=["Read"]), {}, _ctx())
    agent.run_step(_step(tools=["Write"]), {}, _ctx())

    assert len(recorder.calls) == 2
    assert all(argv[:2] == ["claude", "-p"] for argv in recorder.calls)
    assert "Read" in recorder.calls[0][-1] and "Write" in recorder.calls[1][-1]


def test_one_cycle_folds_identical_repeats_and_keeps_leg_order() -> None:
    """A cell repeats ONE cycle, so its recording folds back to that cycle — with the legs still in
    the order they were sent, because that order is a measured input."""
    cycle = [["claude", "-p", "establish"], ["claude", "-p", "goal"]]
    folded = one_cycle(cycle * 3, repeats=3, arm="builtin", channel=MemoryChannel.TRUSTED)
    assert folded.arm == "builtin" and folded.channel == "trusted"
    assert folded.calls == (("claude", "-p", "establish"), ("claude", "-p", "goal"))


@pytest.mark.parametrize(
    "recorded, repeats, why",
    [
        pytest.param([], 2, "no `claude -p` call", id="nothing-ran"),
        pytest.param(
            [["claude", "-p", "a"], ["claude", "-p", "b"], ["claude", "-p", "a"]],
            2,
            "must divide evenly",
            id="does-not-divide",
        ),
        pytest.param(
            [["claude", "-p", "a"], ["claude", "-p", "DIFFERENT"]],
            2,
            "did not send the same invocations",
            id="repeats-diverged",
        ),
    ],
)
def test_a_recording_that_is_not_n_identical_cycles_is_refused(
    recorded: list[list[str]], repeats: int, why: str
) -> None:
    """The fold is VERIFIED, never assumed. Taking cycle 0 and trusting the rest to match is how a
    fingerprint ends up describing one repeat of a run whose other repeats sent something else — and
    a cell that spawned no agent at all measured nothing, so it cannot be folded into anything."""
    with pytest.raises(ValueError, match=why):
        one_cycle(recorded, repeats=repeats, arm="builtin", channel=MemoryChannel.RECALLED)


# --------------------------------------------------------------------------- #
# agent: argv + stream parsing
# --------------------------------------------------------------------------- #
def test_protocol_conformance() -> None:
    assert isinstance(HeadlessClaudeAgent(runner=_render_only_runner), Agent)


def test_a_subclass_that_re_adds_a_runner_default_is_refused() -> None:
    """mem-9gvej fix (2): the subclass-re-adds-default vector. A frozen kw_only subclass can declare
    ``runner: Runner = subprocess.run`` and mypy --strict reports NO error — reopening the "real and
    unrecorded, by omission" hole the base removed. ``__post_init__`` introspects the CONCRETE
    class's field default, so it REFUSES such a subclass (both a plain default and a
    ``default_factory``) while still ALLOWING the base with an explicit runner. A ``callable()``
    check would NOT catch this: ``subprocess.run`` is callable."""

    @dataclass(frozen=True, kw_only=True)
    class _DefaultRunner(HeadlessClaudeAgent):
        runner: Runner = subprocess.run

    with pytest.raises(TypeError, match="runner"):
        _DefaultRunner()

    @dataclass(frozen=True, kw_only=True)
    class _FactoryRunner(HeadlessClaudeAgent):
        runner: Runner = field(default_factory=lambda: subprocess.run)

    with pytest.raises(TypeError, match="runner"):
        _FactoryRunner()

    # the base with an explicit runner still constructs — the guard fires only on a defaulted field.
    HeadlessClaudeAgent(runner=_render_only_runner)


def test_argv_has_stream_json_and_strict_mcp_and_tools() -> None:
    runner = _fake_runner(_stream_json(("Read", {"path": "a.py"})))
    agent = HeadlessClaudeAgent(runner=runner)
    agent.run_step(_step(tools=["Read", "Edit"]), {}, _ctx())
    argv = runner.captured["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert "stream-json" in argv and "--verbose" in argv
    assert "--strict-mcp-config" in argv  # boot-hang guard
    assert "--allowedTools" in argv and "Read,Edit" in argv


def test_no_model_flag_when_unpinned() -> None:
    runner = _fake_runner(_stream_json())
    HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())
    assert "--model" not in runner.captured["argv"]


def test_model_flag_when_pinned() -> None:
    runner = _fake_runner(_stream_json())
    HeadlessClaudeAgent(model="claude-sonnet-4-6", runner=runner).run_step(_step(), {}, _ctx())
    argv = runner.captured["argv"]
    assert "--model" in argv and "claude-sonnet-4-6" in argv


def test_run_step_parses_stream_into_result() -> None:
    runner = _fake_runner(
        _stream_json(
            ("Read", {"path": "a.py"}), ("Edit", {"path": "a.py"}), result="fixed", usage=(120, 40)
        )
    )
    result = HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())
    assert result.final_answer == "fixed"
    assert [t.name for t in result.tool_calls] == ["Read", "Edit"]
    assert result.input_tokens == 120 and result.output_tokens == 40
    assert result.raw_stream  # verbatim stream kept for bbon extraction
    assert result.check_results == {} and result.writes_performed == {}


def test_run_step_raises_on_nonzero_exit() -> None:
    runner = _fake_runner("", returncode=1, stderr="boom")
    with pytest.raises(HeadlessAgentError, match="exit 1"):
        HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())


def test_run_step_raises_on_missing_cli() -> None:
    def runner(argv, **kwargs):
        raise FileNotFoundError("claude")

    with pytest.raises(HeadlessAgentError, match="not found"):
        HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())


def test_run_step_raises_on_timeout() -> None:
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 600)

    with pytest.raises(HeadlessAgentError, match="did not finish within"):
        HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())


def test_run_step_raises_on_permission_error() -> None:
    def runner(argv, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", "claude")

    with pytest.raises(HeadlessAgentError, match="could not spawn"):
        HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())


def test_run_step_raises_on_enoexec() -> None:
    def runner(argv, **kwargs):
        raise OSError(errno.ENOEXEC, "Exec format error", "claude")

    with pytest.raises(HeadlessAgentError, match="could not spawn"):
        HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())


# --------------------------------------------------------------------------- #
# driver: agent -> bbon extract -> ArmStepTrajectory
# --------------------------------------------------------------------------- #
def test_run_step_trajectory_extracts_attempt_steps() -> None:
    runner = _fake_runner(_stream_json(("Read", {"path": "a.py"}), ("Grep", {"q": "import"})))
    agent = HeadlessClaudeAgent(runner=runner)
    traj = run_step_trajectory(agent, _step(), arm="ours", sequence_id="seq1", work_id="mem-x")
    assert isinstance(traj, ArmStepTrajectory)
    assert traj.arm == "ours" and traj.sequence_id == "seq1" and traj.step_id == "s1"
    assert [s.kind for s in traj.steps] == ["Read", "Grep"]  # one AttemptStep per tool_use
    assert traj.status == "completed"
    assert traj.work_id == "mem-x"


def test_run_arm_trajectories_over_sequence() -> None:
    seq = BenchmarkSequence(
        sequence_id="seq1",
        title="t",
        steps=[_step("s1"), _step("s2", request="Now run the tests")],
    )
    runner = _fake_runner(_stream_json(("Bash", {"cmd": "pytest"})))
    trajs = run_arm_trajectories(HeadlessClaudeAgent(runner=runner), seq, arm="none")
    assert [t.step_id for t in trajs] == ["s1", "s2"]
    assert all(t.arm == "none" for t in trajs)
    assert all([s.kind for s in t.steps] == ["Bash"] for t in trajs)


def test_run_sequence_arms_keys_by_arm() -> None:
    seq = BenchmarkSequence(sequence_id="seq1", title="t", steps=[_step("s1")])
    runner = _fake_runner(_stream_json(("Read", {"path": "a"})))
    agent = HeadlessClaudeAgent(runner=runner)
    # `none` surfaces nothing; `ours` surfaces a memory — both run, keyed by arm.
    out = run_sequence_arms(
        agent,
        seq,
        memory_by_arm={"none": lambda _s: {}, "ours": lambda _s: {"m1": "hint"}},
    )
    assert set(out) == {"none", "ours"}
    assert out["none"][0].arm == "none" and out["ours"][0].arm == "ours"


# --------------------------------------------------------------------------- #
# mem-mtqi / pjh8.2: memory-channel framing (recalled vs trusted) + model resolution
# --------------------------------------------------------------------------- #
def test_recalled_header_is_low_trust() -> None:
    prompt = build_agent_prompt(_step(), {"m1": "max_connections=200"}, MemoryChannel.RECALLED)
    assert "may be relevant" in prompt
    assert "max_connections=200" in prompt
    assert "authoritative" not in prompt.lower()


def test_trusted_header_frames_as_ground_truth() -> None:
    prompt = build_agent_prompt(_step(), {"m1": "max_connections=200"}, MemoryChannel.TRUSTED)
    assert "authoritative ground truth" in prompt.lower()
    assert "do not re-derive" in prompt.lower()
    assert "max_connections=200" in prompt


def test_empty_memory_yields_bare_request_under_both_channels() -> None:
    step = _step(request="Set the postgres max_connections.")
    for channel in (MemoryChannel.RECALLED, MemoryChannel.TRUSTED):
        prompt = build_agent_prompt(step, {}, channel)
        assert prompt == "## Task\nSet the postgres max_connections."


def test_trusted_channel_threads_into_prompt() -> None:
    captured: dict[str, str] = {}

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        # argv[2] is the prompt (claude -p <prompt> ...).
        captured["prompt"] = argv[2]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    agent = HeadlessClaudeAgent(runner=run, memory_channel=MemoryChannel.TRUSTED)
    agent.run_step(_step(), {"m1": "v=1"}, _ctx())
    assert "authoritative ground truth" in captured["prompt"].lower()


def test_no_model_resolves_to_cli_default() -> None:
    agent = HeadlessClaudeAgent(runner=_fake_runner(""))
    assert "--model" not in agent.argv_for(_step(), {})
    assert agent._resolved_model == "cli-default"


def test_explicit_model_passed_and_recorded() -> None:
    agent = HeadlessClaudeAgent(runner=_fake_runner(""), model="claude-sonnet")
    argv = agent.argv_for(_step(), {})
    assert argv[argv.index("--model") + 1] == "claude-sonnet"
    assert agent._resolved_model == "claude-sonnet"


def test_env_model_resolves_and_passes_non_empty_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # The env override must pass the RESOLVED model, not an empty string.
    monkeypatch.setenv(ENV_MODEL, "claude-opus")
    agent = HeadlessClaudeAgent(runner=_fake_runner(""))
    argv = agent.argv_for(_step(), {})
    assert argv[argv.index("--model") + 1] == "claude-opus"
    assert agent._resolved_model == "claude-opus"


def test_a_paid_run_needs_a_model_only_when_paid_and_unpinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ONE spend-refusal rule the three drivers defer to (mem-bzv2p): refuse only a PAID run
    that resolves to no model. A dry run never needs one; an explicit pin or MEMBENCH_AGENT_MODEL
    satisfies it."""
    monkeypatch.delenv(ENV_MODEL, raising=False)
    assert a_paid_run_needs_a_model("", dry_run=False) is True  # paid + unpinned -> refuse
    assert a_paid_run_needs_a_model("", dry_run=True) is False  # dry run never needs a model
    assert a_paid_run_needs_a_model("sonnet", dry_run=False) is False  # explicit pin
    monkeypatch.setenv(ENV_MODEL, "claude-opus")
    assert a_paid_run_needs_a_model("", dry_run=False) is False  # MEMBENCH_AGENT_MODEL names it


def test_a_paid_run_carries_the_metered_api_key_only_when_paid_and_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ONE api-key spend-refusal rule the three paid entrypoints defer to (mem-9bh93): refuse a
    PAID run that carries ANTHROPIC_API_KEY. A dry run spawns nothing, and an empty-string key is
    UNSET by bare truthiness (matching the OAuth-token gate and the CLI's own treatment) — so a CI
    shell that exports `ANTHROPIC_API_KEY=` must not start refusing legitimate paid runs."""
    monkeypatch.setenv(ENV_API_KEY, "sk-ant-real-looking")
    assert a_paid_run_carries_the_metered_api_key(dry_run=False) is True  # paid + key -> refuse
    assert a_paid_run_carries_the_metered_api_key(dry_run=True) is False  # dry run carries any env
    monkeypatch.setenv(ENV_API_KEY, "")  # exported-but-empty is not a credential
    assert a_paid_run_carries_the_metered_api_key(dry_run=False) is False
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    assert a_paid_run_carries_the_metered_api_key(dry_run=False) is False  # unset


# --------------------------------------------------------------------------- #
# mem-lw0j3: naming the INSTRUMENT — the binary, not the model it points at
# --------------------------------------------------------------------------- #
def test_resolve_cli_version_reads_the_version_off_the_binary() -> None:
    # The real shape: `claude --version` -> "2.1.210 (Claude Code)". The version is the
    # token; the product name rides along and is not part of the identity.
    runner = _fake_runner("2.1.210 (Claude Code)\n")
    assert resolve_cli_version(runner) == "2.1.210"
    assert list(runner.captured["argv"]) == ["claude", "--version"]


def test_an_unrecognised_version_banner_is_redacted_and_bounded() -> None:
    """The echo site that `run_checked`'s guard cannot see: the child EXITS 0 here.

    `run_checked` sanitises only its own non-zero arm, so this branch embedded the whole raw
    stdout of a successful child into the exception verbatim -- unbounded and unredacted.
    `run_corpus` resolves the version up front, so it prints on the drivers' SWEEP HALT arm
    like any other halt: the widest of the echo sites, wearing the safest-looking path."""
    leaky = "warning: refreshing sk-ant-oat01-VERSIONCANARY999\n" + ("x" * 50_000)
    with pytest.raises(HeadlessAgentError) as caught:
        resolve_cli_version(_fake_runner(leaky))
    assert "sk-ant-oat01-VERSIONCANARY999" not in str(caught.value)
    assert "sk-ant" not in str(caught.value)
    assert len(str(caught.value)) < 10_000  # and bounded, not the whole 50k banner


@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("2.1.210\n", id="bare-token"),
        pytest.param("2.2\n", id="two-component"),
        pytest.param("2.1.210-beta.1 (Claude Code)\n", id="prerelease-suffix"),
        pytest.param("2.1.210+build.5 (Claude Code)\n", id="build-suffix"),
        pytest.param("2.1.210-rc.1+build.5 (Claude Code)\n", id="prerelease-and-build"),
    ],
)
def test_resolve_cli_version_accepts_the_version_shapes_the_cli_emits(stdout: str) -> None:
    # Tolerant about the SHAPE of a version, strict about it being one: a future CLI that
    # drops the product name or ships a prerelease must not halt a paid sweep.
    assert resolve_cli_version(_fake_runner(stdout)) == stdout.split()[0]


def test_resolve_cli_version_refuses_output_it_does_not_recognise_rather_than_guessing() -> None:
    """`claude --version` prints ONE line. Given more, this refuses instead of picking a line.

    The failure this closes is silent, which is what makes it worth halting over: an update nag
    or a bundled-runtime notice ahead of the version line ("18.2.0 required, please upgrade")
    is itself a well-SHAPED version token, so a parse that scans the whole output for the first
    thing matching would stamp node's version on the cell as the claude binary's — a value that
    looks right and names the wrong instrument, which is this module's entire defect family.
    Refusing is the cheap end: the sweep halts with a diagnostic naming the output it got."""
    with pytest.raises(HeadlessAgentError, match="no recognisable version"):
        resolve_cli_version(
            _fake_runner("18.2.0 required, please upgrade\n2.1.210 (Claude Code)\n")
        )


@pytest.mark.parametrize(
    "runner",
    [
        pytest.param(_fake_runner("", returncode=1, stderr="boom"), id="non-zero-exit"),
        pytest.param(_fake_runner("unknown\n"), id="junk"),
        pytest.param(_fake_runner("\n"), id="empty"),
        pytest.param(_fake_runner("Error: not logged in\n"), id="prose"),
    ],
)
def test_resolve_cli_version_refuses_to_name_an_instrument_it_cannot_identify(
    runner: Runner,
) -> None:
    # A paid run that cannot name its instrument must not spend. Every one of these would
    # otherwise be stored as a `cli_version` a later resume would match against — an
    # unidentified binary's numbers served as a named one's.
    with pytest.raises(HeadlessAgentError):
        resolve_cli_version(runner)


def test_resolve_cli_version_raises_when_the_cli_is_missing() -> None:
    def missing(argv, **kwargs):
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", "claude")

    with pytest.raises(HeadlessAgentError, match="not found"):
        resolve_cli_version(missing)


def test_resolve_cli_version_raises_when_the_probe_hangs() -> None:
    def wedged(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=VERSION_TIMEOUT_S)

    with pytest.raises(HeadlessAgentError, match="did not finish within"):
        resolve_cli_version(wedged)


# --------------------------------------------------------------------------- #
# mem-rk41.3.2 H1: env threading (merged, never replaced)
# --------------------------------------------------------------------------- #
def test_env_none_by_default_inherits_parent_environment() -> None:
    # `None` is subprocess's inherit-the-parent-environment sentinel: an agent that sets no
    # env must not narrow the child's environment at all.
    runner = _fake_runner(_stream_json())
    HeadlessClaudeAgent(runner=runner).run_step(_step(), {}, _ctx())
    assert runner.captured["kwargs"]["env"] is None


def test_agent_with_env_stays_hashable() -> None:
    # `env` carries a plain (unhashable) dict on every real call site; without
    # `field(hash=False)` this would raise `TypeError: unhashable type: 'dict'` and
    # silently break the frozen dataclass's auto-generated __hash__.
    agent = HeadlessClaudeAgent(runner=_render_only_runner, env={"CLAUDE_CONFIG_DIR": "/tmp/x"})
    hash(agent)  # must not raise


def test_env_is_merged_over_os_environ_not_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMBENCH_PARENT_SENTINEL", "still-here")
    runner = _fake_runner(_stream_json())
    agent = HeadlessClaudeAgent(runner=runner, env={"CLAUDE_CONFIG_DIR": "/tmp/builtin-cfg"})
    agent.run_step(_step(), {}, _ctx())
    passed_env = runner.captured["kwargs"]["env"]
    # a raw replace would drop this — a merge keeps the parent environment (PATH, OAuth token)
    assert passed_env["MEMBENCH_PARENT_SENTINEL"] == "still-here"
    assert passed_env["CLAUDE_CONFIG_DIR"] == "/tmp/builtin-cfg"


# --------------------------------------------------------------------------- #
# helper
# --------------------------------------------------------------------------- #
def _ctx():
    from membench.runtime import IdClock, StepContext

    return StepContext(trial_id="t1", session_id="none", step_id="s1", clock=IdClock())
