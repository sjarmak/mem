"""mem-rk41.3.2 — builtin native-memory persistent-env arm (establish/goal, two calls).

Covers the invariants the bead's PLAN-REVIEW made mandatory:

* H3 — the establish call carries NO tool allowlist (``available_tools=[]``), so
  ``--allowedTools`` never blocks Claude Code's own memory-write path.
* H4 — the establish turn's facts are delivered via ``available_memory`` (like the
  oracle arm), never embedded in the instruction prose, so TRUSTED/RECALLED
  meaningfully varies the establish framing.
* H2 — engagement is CONTENT-based (does the opaque token actually reach native memory),
  not file-existence — an empty scaffolded dir never counts. Native memory is an INDEX
  plus TOPIC FILES, and the fact lands in the topic file, so the check must search both.
* The shared sandbox cwd is FIREWALLED between the two legs: it has to be shared (the
  memory path is keyed on its slug) but its contents are a second continuity channel that
  ``--allowedTools`` cannot close, since an auto-loaded ``CLAUDE.md`` is not a tool call.
* A goal PASS with engaged=False is accounted as a LEAK, not a builtin win.
* The dry-run simulator proves the two-call shared cwd/config-dir wiring end to end for
  zero tokens, persisting in the REAL native-memory layout so the free path guards the
  glob (it does not and cannot prove a real ``claude -p`` session persists at all — that
  is the paid preflight's job, tested at the driver level).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from membench.harbor.agent_memory import native_memory_path
from membench.runner import toolreq_builtin_grid as grid
from membench.runner.headless_agent import ENV_MODEL, HeadlessAgentError, MemoryChannel
from membench.runner.realagent_probe import CONFIG_FILE, REAL_TOOL, ArmOutcome
from membench.runner.toolreq_builtin import (
    ARM,
    SIMULATED_TOPIC_FILE,
    BuiltinDiagnostics,
    _establish_step,
    _memory_engaged,
    run_builtin_arm,
    simulated_builtin_runner,
)
from membench.runner.toolreq_realagent import (
    ToolReqRealAgentTask,
    adapt_sequence,
    load_corpus_with_sequences,
    task_fingerprint,
)
from membench.schemas.sequence import (
    BenchmarkSequence,
    ExpectedAction,
    OutcomeCheck,
    SequenceStep,
)

CURRENT = "30 days"
STALE = "90 days"


def _toolreq_seq(
    seq_id: str = "w-t0", *, current: str = CURRENT, stale: str = STALE
) -> BenchmarkSequence:
    """One tool-requiring sequence. ``current``/``stale`` are parameters, not constants, so a test
    can build a DIFFERENT world under the SAME (positional) work_id — which is the shape the
    regenerated-corpus cache defect takes."""
    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"{seq_id} initiative",
        steps=[
            SequenceStep(
                step_id=f"{seq_id}-s0",
                user_request="Record.",
                expected_memory_writes={"m-v1": f"the retention window is {stale}"},
            ),
            SequenceStep(
                step_id=f"{seq_id}-s1",
                user_request="Record.",
                expected_memory_writes={"m-v2": f"the retention window is {current}"},
                superseded_memory_ids=["m-v1"],
            ),
            SequenceStep(
                step_id=f"{seq_id}-goal",
                user_request=(
                    "Deliver the current initiative. Using the tool `apply_config`, apply "
                    "the current value of: the retention window."
                ),
                available_tools=["apply_config"],
                expected_memory_reads=["m-v2"],
                outcome_checks=[
                    OutcomeCheck(
                        check_id=f"{seq_id}-goal-check",
                        requires_memory=["m-v2"],
                        requires_action=[
                            ExpectedAction(
                                tool="apply_config", arg_values=[current], forbidden_values=[stale]
                            )
                        ],
                    )
                ],
            ),
        ],
    )


def _task(seq_id: str = "w-t0"):
    return adapt_sequence(_toolreq_seq(seq_id))


def _corpus_one(tmp_path: Path, work_id: str = "w-0") -> list[ToolReqRealAgentTask]:
    """Seed a one-task frozen corpus under ``tmp_path/corpus`` and load it — the same
    scaffold every driver test needs (mirrors ``test_toolreq_realagent._corpus_one``)."""
    corpus = tmp_path / "corpus"
    (corpus / "0").mkdir(parents=True)
    (corpus / "0" / "sequences.json").write_text(
        json.dumps([_toolreq_seq(work_id).model_dump()]), encoding="utf-8"
    )
    _, tasks = load_corpus_with_sequences(corpus)
    return tasks


def _stream_json_runner(
    *, tool_use_when: Callable[[list[str]], bool], tool_name: str, tool_input: dict[str, object]
):
    """Build a fake `claude -p` runner that emits one `tool_use` event (`tool_name` /
    `tool_input`) iff `tool_use_when(argv)` is true, always followed by a terminal
    `result` event — the shared shape behind this file's per-call-site runner stubs."""

    def run(argv, **kwargs):
        argv_list = list(argv)
        events: list[dict[str, object]] = []
        if tool_use_when(argv_list):
            events.append(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "name": tool_name, "input": tool_input}],
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                }
            )
        events.append({"type": "result", "result": "done"})
        stdout = "\n".join(json.dumps(e) for e in events)
        return subprocess.CompletedProcess(argv_list, returncode=0, stdout=stdout, stderr="")

    return run


# --- establish step (H3, H4) ----------------------------------------------------------


def test_establish_step_has_no_tool_allowlist() -> None:
    task = _task()
    step = _establish_step(task)
    assert step.available_tools == []


def test_establish_step_does_not_embed_facts_in_prose() -> None:
    # H4: facts flow through `available_memory`, never the instruction text itself.
    task = _task()
    step = _establish_step(task)
    for value in task.current_opaque_values:
        assert value not in step.user_request
    for content in task.oracle_memory.values():
        assert content not in step.user_request


def test_establish_step_instructs_remembering() -> None:
    # Q2 resolved to the usability-ceiling framing: explicitly ask the agent to retain.
    step = _establish_step(_task())
    assert "remember" in step.user_request.lower()


def test_argv_omits_allowed_tools_for_establish_but_not_goal() -> None:
    from membench.runner.headless_agent import HeadlessClaudeAgent

    task = _task()
    establish_argv = HeadlessClaudeAgent(constrain_tools=True)._argv("p", _establish_step(task))
    goal_argv = HeadlessClaudeAgent(constrain_tools=True)._argv("p", task.goal_step)
    assert "--allowedTools" not in establish_argv
    assert "--allowedTools" in goal_argv and "Write" in goal_argv


# --- content-based engagement gate (H2, M1) --------------------------------------------


def test_memory_engaged_true_when_token_reaches_memory_md(tmp_path: Path) -> None:
    memory_dir = tmp_path / "projects" / "-tmp-sandbox" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("remembered: toolreq-abc123-CURRENT", encoding="utf-8")
    assert _memory_engaged(tmp_path, ["toolreq-abc123-CURRENT"]) is True


def test_memory_engaged_false_when_absent(tmp_path: Path) -> None:
    assert _memory_engaged(tmp_path, ["toolreq-abc123-CURRENT"]) is False


def test_memory_engaged_false_on_empty_scaffolded_file(tmp_path: Path) -> None:
    # H2: an empty file (CC scaffolds the dir regardless of content) is NOT engagement.
    memory_dir = tmp_path / "projects" / "-tmp-sandbox" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("", encoding="utf-8")
    assert _memory_engaged(tmp_path, ["toolreq-abc123-CURRENT"]) is False


def test_memory_engaged_globs_any_project_slug(tmp_path: Path) -> None:
    # M1: never predict the exact slug — a glob finds it regardless of cwd-derived name.
    memory_dir = tmp_path / "projects" / "-some-other-tempdir-slug" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("value=toolreq-xyz-CURRENT", encoding="utf-8")
    assert _memory_engaged(tmp_path, ["toolreq-xyz-CURRENT"]) is True


def test_memory_engaged_true_when_the_token_lands_in_a_topic_file(tmp_path: Path) -> None:
    # Claude Code native memory is an INDEX plus TOPIC FILES: MEMORY.md carries one-line
    # pointers ("- [Title](topic.md) — hook") and the FACT itself lives in the sibling
    # <topic>.md. Every native-memory dir on this box has that shape, without exception.
    # Globbing only MEMORY.md therefore scores a WORKING builtin as engaged=0 — which on
    # the paid path is a false PREFLIGHT HALT ("native memory may be disabled") or, with
    # --skip-preflight, records the arm's BEST possible result (a clean 3/3 off native
    # memory) as a LEAK. The token must be found wherever native memory actually put it.
    memory_dir = tmp_path / "projects" / "-tmp-sandbox" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text(
        "- [Retention window](retention-window.md) — the window to recall later\n",
        encoding="utf-8",
    )
    (memory_dir / "retention-window.md").write_text(
        "the retention window is toolreq-abc123-CURRENT", encoding="utf-8"
    )
    assert _memory_engaged(tmp_path, ["toolreq-abc123-CURRENT"]) is True


# --- simulated dry-run runner (proves the two-call wiring, no tokens) ------------------


def test_simulated_runner_establish_writes_iff_prompt_carries_every_value(tmp_path: Path) -> None:
    runner = simulated_builtin_runner(["tok-a", "tok-b"])
    argv = ["claude", "-p", "carries tok-a and tok-b", "--output-format", "stream-json"]
    completed = runner(argv, env={"CLAUDE_CONFIG_DIR": str(tmp_path)}, cwd="/sandbox")
    assert completed.returncode == 0
    index_path = Path(native_memory_path(config_dir=str(tmp_path), workdir="/sandbox"))
    assert index_path.is_file()
    # the FACT lands in the topic file, not the index — the real native-memory layout
    topic_path = index_path.parent / SIMULATED_TOPIC_FILE
    assert "tok-a" in topic_path.read_text(encoding="utf-8")


def test_simulated_runner_establish_no_write_when_prompt_missing_a_value(tmp_path: Path) -> None:
    runner = simulated_builtin_runner(["tok-a", "tok-b"])
    argv = ["claude", "-p", "carries only tok-a", "--output-format", "stream-json"]
    runner(argv, env={"CLAUDE_CONFIG_DIR": str(tmp_path)}, cwd="/sandbox")
    memory_path = Path(native_memory_path(config_dir=str(tmp_path), workdir="/sandbox"))
    assert not memory_path.is_file()


def test_simulated_runner_goal_call_passes_iff_marker_present(tmp_path: Path) -> None:
    runner = simulated_builtin_runner(["tok-a"])
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path)}
    # bare goal prompt never carries the value itself
    bare_argv = ["claude", "-p", "## Task\nwrite it", "--output-format", "stream-json"]

    # no establish call happened -> no marker -> goal call makes no tool call
    completed = runner(bare_argv, env=env, cwd="/sandbox")
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert all(e["type"] != "assistant" for e in events)

    # now simulate the establish call, then re-run the (identical) bare goal call
    runner(["claude", "-p", "tok-a", "--output-format", "stream-json"], env=env, cwd="/sandbox")
    completed2 = runner(bare_argv, env=env, cwd="/sandbox")
    events2 = [json.loads(line) for line in completed2.stdout.splitlines()]
    (assistant_event,) = [e for e in events2 if e["type"] == "assistant"]
    (block,) = assistant_event["message"]["content"]
    assert block["name"] == REAL_TOOL
    assert block["input"]["content"] == "tok-a"


# --- run_builtin_arm: dry-run end to end ------------------------------------------------


def test_dry_run_arm_engages_and_passes_every_repeat() -> None:
    task = _task()
    outcome, diag = run_builtin_arm(
        task, repeats=3, model="", dry_run=True, channel=MemoryChannel.RECALLED
    )
    assert outcome.arm == ARM
    assert outcome.passes == outcome.runs == 3
    assert diag.engaged == 3
    assert diag.leaked == 0
    # the honest dry-run simulator never claims a tool_use on the establish leg
    assert diag.establish_tool_calls == 0


def test_establish_tool_calls_are_counted_not_discarded() -> None:
    # The establish call's own tool_calls must be captured (the security-review finding:
    # it runs with NO --allowedTools clamp, so its actions need an audit trail).
    task = _task()
    bash_happy_runner = _stream_json_runner(
        tool_use_when=lambda argv: "--allowedTools" not in argv,  # the establish call only
        tool_name="Bash",
        tool_input={"command": "ls"},
    )

    _, diag = run_builtin_arm(
        task,
        repeats=2,
        model="",
        dry_run=False,
        channel=MemoryChannel.RECALLED,
        runner=bash_happy_runner,
    )
    assert diag.establish_tool_calls == 2  # one Bash call per repeat's establish leg


def test_dry_run_arm_channel_recorded_on_outcome() -> None:
    task = _task()
    outcome, _ = run_builtin_arm(
        task, repeats=1, model="", dry_run=True, channel=MemoryChannel.TRUSTED
    )
    assert outcome.channel == "trusted"


@pytest.mark.parametrize("channel", [MemoryChannel.RECALLED, MemoryChannel.TRUSTED])
def test_goal_call_is_bare_under_either_channel(channel: MemoryChannel) -> None:
    # ONE HeadlessClaudeAgent drives both legs, carrying the arm's channel. That is only
    # sound because the goal call surfaces memory={} and `build_agent_prompt` emits NO
    # memory block for empty memory under EITHER channel — so the channel's trust framing
    # cannot leak the opaque value into the bare goal prompt. If that ever stops holding,
    # the goal call starts being handed the answer and every builtin "pass" is a leak.
    task = _task()
    value = task.current_opaque_values[0]
    prompts: list[str] = []

    def recording_runner(argv, **kwargs):
        prompts.append(argv[2])
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")

    run_builtin_arm(
        task, repeats=1, model="", dry_run=False, channel=channel, runner=recording_runner
    )
    establish_prompt, goal_prompt = prompts
    assert value in establish_prompt  # establish is handed the fact...
    assert value not in goal_prompt  # ...the goal call is not, under either channel
    assert "Established facts" not in goal_prompt
    assert "recalled from" not in goal_prompt.lower()


def test_fresh_sandbox_and_config_dir_per_repeat() -> None:
    # A regression test that would actually FAIL if the two TemporaryDirectory context
    # managers were hoisted out of the per-repeat loop and shared across repeats: record
    # every (cwd, CLAUDE_CONFIG_DIR) pair the runner is invoked with and assert every
    # repeat used a distinct pair. `diag.engaged == runs` alone does not catch this
    # regression class — a shared dir would still show every repeat as "engaged" because
    # the marker file from repeat 1 would still be sitting there for repeat 2 onward.
    task = _task()
    seen: list[tuple[object, object]] = []

    def recording_runner(argv, **kwargs):
        seen.append((kwargs.get("cwd"), (kwargs.get("env") or {}).get("CLAUDE_CONFIG_DIR")))
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")

    run_builtin_arm(
        task,
        repeats=5,
        model="",
        dry_run=False,
        channel=MemoryChannel.RECALLED,
        runner=recording_runner,
    )
    # 2 calls (establish + goal) per repeat, both sharing the SAME pair within a repeat.
    assert len(seen) == 10
    per_repeat_pairs = [seen[i] for i in range(0, 10, 2)]
    assert seen[0] == seen[1]  # establish and goal share one sandbox+config_dir
    assert len(set(per_repeat_pairs)) == 5  # every repeat's pair is distinct from every other


def test_simulated_establish_persists_to_a_topic_file_not_the_index(tmp_path: Path) -> None:
    # The simulator must write where REAL native memory writes: the fact in a <topic>.md,
    # the index holding only a pointer. A simulator that parked the fact in MEMORY.md
    # would be fitted to whatever the engagement check globs — which is exactly how a
    # MEMORY.md-only glob (scoring a working builtin as engaged=0) survived a green
    # dry-run. Pinning the layout here makes the FREE path a real guard on the glob.
    runner = simulated_builtin_runner(["toolreq-abc123-CURRENT"])
    cwd = tmp_path / "sandbox"
    cwd.mkdir()
    config_dir = tmp_path / "config"
    runner(
        ["claude", "-p", "facts: toolreq-abc123-CURRENT"],
        env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        cwd=str(cwd),
    )
    index = Path(native_memory_path(config_dir=str(config_dir), workdir=str(cwd)))
    topic = index.parent / SIMULATED_TOPIC_FILE
    assert "toolreq-abc123-CURRENT" in topic.read_text(encoding="utf-8")  # fact: topic file
    assert "toolreq-abc123-CURRENT" not in index.read_text(encoding="utf-8")  # index: pointer
    assert _memory_engaged(config_dir, ["toolreq-abc123-CURRENT"]) is True


# --- cwd firewall: the shared sandbox is not a second continuity channel ---------------


def _cwd_scavenging_runner(value: str):
    """Establish leg (no ``--allowedTools``): persists the value into a file in the SHARED
    sandbox cwd instead of into native memory — which an unconstrained establish call is
    free to do. Goal leg: passes iff that file is still readable. Claude Code auto-loads
    ``CLAUDE.md``/``AGENTS.md`` from the cwd at session start with NO tool call, so a
    Write-only ``--allowedTools`` clamp does not close this channel; only emptying the cwd
    does."""

    def run(argv, **kwargs):
        argv_list = list(argv)
        cwd = kwargs.get("cwd")
        assert isinstance(cwd, str)
        scavengeable = Path(cwd) / "CLAUDE.md"
        events: list[dict[str, object]] = []
        if "--allowedTools" not in argv_list:  # establish leg
            scavengeable.write_text(f"remember: {value}", encoding="utf-8")
        elif scavengeable.is_file() and value in scavengeable.read_text(encoding="utf-8"):
            events.append(  # goal leg: passes off the leftover file, never touching memory
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": REAL_TOOL,
                                "input": {"file_path": CONFIG_FILE, "content": value},
                            }
                        ],
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                }
            )
        events.append({"type": "result", "result": "done"})
        stdout = "\n".join(json.dumps(e) for e in events)
        return subprocess.CompletedProcess(argv_list, returncode=0, stdout=stdout, stderr="")

    return run


def test_goal_leg_cannot_scavenge_a_cwd_file_the_establish_leg_left_behind() -> None:
    # The establish and goal legs MUST share a sandbox cwd (the native-memory path is
    # keyed on the cwd slug), but the cwd's CONTENTS are a second, unfirewalled continuity
    # channel outside the memory channel under test. Left open, an establish call that
    # writes CLAUDE.md and never touches native memory still yields a passing goal leg —
    # and because `engaged` is measured off the establish leg while `leaked` only fires on
    # (pass AND NOT engaged), the accounting would record it as a clean builtin SEPARATES.
    # Wiping the cwd's contents between legs closes the channel structurally: the slug (so
    # the memory path, so continuity) is unchanged, but nothing is left to scavenge.
    task = _task()
    (current_opaque,) = task.current_opaque_values

    outcome, diag = run_builtin_arm(
        task,
        repeats=2,
        model="",
        dry_run=False,
        channel=MemoryChannel.RECALLED,
        runner=_cwd_scavenging_runner(current_opaque),
    )
    assert outcome.passes == 0  # the leftover file is gone: nothing to scavenge
    assert diag.engaged == 0  # and this runner never engaged native memory at all
    assert diag.leaked == 0  # so there is no pass to misreport as a builtin win


def test_cwd_wipe_preserves_the_sandbox_dir_so_the_memory_path_survives() -> None:
    # The wipe must empty the cwd, never replace it: the native-memory path is derived
    # from the cwd SLUG, so a fresh cwd between legs would silently move the memory file
    # out from under the goal call and break the very continuity we are measuring.
    task = _task()
    seen_cwds: list[object] = []

    def recording_runner(argv, **kwargs):
        seen_cwds.append(kwargs.get("cwd"))
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")

    run_builtin_arm(
        task,
        repeats=1,
        model="",
        dry_run=False,
        channel=MemoryChannel.RECALLED,
        runner=recording_runner,
    )
    assert seen_cwds[0] == seen_cwds[1]  # establish and goal still share ONE cwd path


def test_establish_tool_names_are_recorded_not_just_counted() -> None:
    # The audit trail exists so a reviewer gating the PAID fire can see what the
    # unconstrained establish call actually did — a bare count cannot answer "did it write
    # into the shared cwd", which is the question that matters.
    task = _task()
    bash_happy_runner = _stream_json_runner(
        tool_use_when=lambda argv: "--allowedTools" not in argv,  # the establish call only
        tool_name="Bash",
        tool_input={"command": "ls"},
    )
    _, diag = run_builtin_arm(
        task,
        repeats=2,
        model="",
        dry_run=False,
        channel=MemoryChannel.RECALLED,
        runner=bash_happy_runner,
    )
    assert diag.establish_tool_calls == 2
    assert diag.establish_tool_names == ("Bash",)


# --- the mechanism knob: a pristine config dir must have native memory ON --------------


def test_fresh_config_dir_turns_native_memory_on() -> None:
    # `autoMemoryEnabled` is a $CLAUDE_CONFIG_DIR/settings.json key — CONFIG-DIR-LOCAL, so
    # this driver fully owns it (account2's settings.json on this box carries it false).
    # Each repeat mints a PRISTINE EMPTY config dir, so without seeding it the mechanism
    # under test would ride on whatever Claude Code's default for that key happens to be —
    # a coin flip on the paid path, and one that would send a halted run chasing an
    # account-level problem that does not exist.
    task = _task()
    seen: list[dict[str, object]] = []

    def settings_recording_runner(argv, **kwargs):
        env = kwargs.get("env") or {}
        config_dir = Path(str(env.get("CLAUDE_CONFIG_DIR")))
        settings = config_dir / "settings.json"
        seen.append(json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {})
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")

    run_builtin_arm(
        task,
        repeats=1,
        model="",
        dry_run=False,
        channel=MemoryChannel.RECALLED,
        runner=settings_recording_runner,
    )
    assert seen, "the runner never saw a config dir"
    assert all(s.get("autoMemoryEnabled") is True for s in seen)


# --- leak accounting (H2): a pass without engagement is NOT a builtin win --------------


def test_leaked_pass_without_engagement_is_flagged_not_counted_as_clean() -> None:
    task = _task()
    (current_opaque,) = task.current_opaque_values
    runner = _leaking_runner_for(current_opaque)
    outcome, diag = run_builtin_arm(
        task, repeats=2, model="", dry_run=False, channel=MemoryChannel.RECALLED, runner=runner
    )
    assert outcome.passes == 2  # score_goal_action sees a genuine Write of the current value
    assert diag.engaged == 0  # but native memory never actually persisted anything
    assert diag.leaked == 2  # so both passes are flagged as leaks, not a builtin win


def _leaking_runner_for(current_opaque_value: str):
    return _stream_json_runner(
        tool_use_when=lambda argv: "--allowedTools" in argv,  # the goal call only
        tool_name=REAL_TOOL,
        tool_input={"file_path": CONFIG_FILE, "content": current_opaque_value},
    )


def test_diagnostics_is_a_plain_dataclass_not_an_arm_outcome_subtype() -> None:
    # M3: ArmOutcome stays uniform; engagement lives in a SEPARATE sidecar type.
    diag = BuiltinDiagnostics(engaged=1, leaked=0, runs=1)
    assert not hasattr(diag, "arm")
    assert not hasattr(diag, "passes")


# --- the grid (membench/runner/toolreq_builtin_grid.py) ---------------------------------
#
# The arm's cells, its verdict rule, and its cache identity. The RESUME CACHE itself is the
# shared core (`membench.runner.resume_cache`, tested once in `test_resume_cache.py`); what
# is tested here is this grid's ADOPTION of it — that the identity this grid builds actually
# carries the inputs whose absence was the mem-mpxie defect family, and that its cells carry
# bounds the shared schema cannot know about (engagement is a builtin-only concept).


def _cell(
    channel: str = "recalled", *, passes: int = 2, runs: int = 2, engaged: int = 2, leaked: int = 0
) -> grid.BuiltinCell:
    return grid.BuiltinCell(
        arm=ARM,
        channel=channel,
        passes=passes,
        runs=runs,
        engaged=engaged,
        leaked=leaked,
        establish_tool_calls=0,
        establish_tool_names=[],
    )


def test_dry_run_evaluate_task_separates_both_channels() -> None:
    task = _task()
    cells = grid.evaluate_task(task, repeats=2, model="", dry_run=True)
    assert {cell.channel for cell in cells} == {"recalled", "trusted"}
    for cell in cells:
        assert cell.passes == cell.runs == 2
        assert cell.engaged == 2 and cell.leaked == 0


def test_verdict_separates_when_engaged_and_passing() -> None:
    assert "SEPARATES" in grid.task_verdict([_cell(passes=3, runs=3, engaged=3)])


def test_verdict_leak_outranks_pass_count() -> None:
    assert "LEAK" in grid.task_verdict([_cell(passes=2, runs=2, engaged=0, leaked=2)])


def test_verdict_not_engaged_when_mechanism_never_fires() -> None:
    assert "NOT-ENGAGED" in grid.task_verdict([_cell(passes=0, runs=3, engaged=0)])


# --- the cell's cross-field bounds: an impossible engagement claim is unconstructible ----


def test_more_engaged_than_runs_is_unconstructible() -> None:
    with pytest.raises(ValidationError):
        _cell(runs=2, engaged=3)


def test_more_leaks_than_passes_is_unconstructible() -> None:
    # A leak IS a pass — one that happened without engagement — so it cannot outnumber the
    # passes it is drawn from.
    with pytest.raises(ValidationError):
        _cell(passes=1, runs=2, engaged=0, leaked=2)


def test_more_leaks_than_non_engaged_repeats_is_unconstructible() -> None:
    # The same impossibility from the other side, and the one a `leaked <= passes` bound alone
    # would miss: a leaked repeat is BY DEFINITION one that did not engage, so 2 leaks cannot
    # coexist with 2 engaged repeats out of 2 runs. Without this a record can claim a clean
    # sweep (engaged 2/2, passes 2/2) AND carry the leaks that deny it.
    with pytest.raises(ValidationError):
        _cell(passes=2, runs=2, engaged=2, leaked=2)


def test_fewer_leaks_than_passes_minus_engaged_is_unconstructible() -> None:
    # The LOWER bound, and the one the three upper bounds cannot stand in for: they only ever say
    # `leaked` is too BIG. `leaked` counts the repeats that PASSED while NOT engaging, so by
    # inclusion-exclusion at least (passes - engaged) of the passing repeats were leaks. A row
    # claiming 2 passes, 0 engaged and 0 leaks is arithmetically impossible.
    with pytest.raises(ValidationError):
        _cell(passes=2, runs=2, engaged=0, leaked=0)


def test_zeroing_leaked_on_a_real_leak_record_cannot_rewrite_the_headline(
    tmp_path: Path, monkeypatch
) -> None:
    # The reason the lower bound matters, end to end. `run_builtin_arm` legitimately writes a LEAK
    # cell (passes=2, engaged=0, leaked=2) when the sandbox hands the agent a pass it never earned
    # through native memory — the most severe verdict the arm produces. Zero `leaked` on disk and
    # the row still satisfies every UPPER bound, while `cell_kind` now reads it as NOT-ENGAGED: the
    # task silently moves out of the summary's `leaked` list into `not_engaged`, on the PAID path,
    # at executed=0.
    #
    # The forged record carries the verdict its forged rows IMPLY, not the original LEAK string.
    # That is what makes this a test of the lower bound rather than of the verdict-derivation check
    # — leave the stale LEAK verdict in place and that other check refuses the file first, and this
    # test would pass with no lower bound at all. A record whose every OTHER field agrees with
    # itself is exactly the one nothing else can refuse.
    tasks = _corpus_one(tmp_path)

    def _arm(engaged: int, leaked: int):
        def run(task, *, repeats: int, channel: MemoryChannel, **_kwargs):
            return (
                ArmOutcome(arm=ARM, channel=channel.value, passes=repeats, runs=repeats),
                BuiltinDiagnostics(engaged=engaged, leaked=leaked * repeats, runs=repeats),
            )

        return run

    # The verdict a genuinely NOT-ENGAGED grid produces — taken from a real run, never hand-typed,
    # so the forgery is exactly what a self-consistent record would say.
    monkeypatch.setattr(grid, "run_builtin_arm", _arm(engaged=2, leaked=0))
    engaged_out = tmp_path / "engaged"
    grid.run_corpus(tasks, out_dir=engaged_out, repeats=2, model="", dry_run=True)
    not_engaged_verdict = json.loads((engaged_out / "w-0.json").read_text())["verdict"].replace(
        "SEPARATES: 2/2 (engaged 2/2)", "NOT-ENGAGED: the fact never reached native memory (0/2)"
    )

    out = tmp_path / "out"
    monkeypatch.setattr(grid, "run_builtin_arm", _arm(engaged=0, leaked=1))
    truth = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert truth["leaked"] == ["w-0"] and truth["not_engaged"] == []

    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    for row in record["outcomes"]:
        row["leaked"] = 0  # the edit that erases the LEAK...
    record["verdict"] = not_engaged_verdict  # ...and the verdict those rows now imply
    result_path.write_text(json.dumps(record), encoding="utf-8")

    resumed = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert resumed["executed"] == 1 and resumed["reused"] == 0, "served a forged leak-free record"
    assert resumed["leaked"] == ["w-0"], "the LEAK was downgraded to NOT-ENGAGED"


def test_a_cell_whose_diagnostics_disagree_about_runs_is_refused() -> None:
    # The arm reports `runs` on BOTH halves it returns; they are the same measurement, so a
    # disagreement means one of the two is fabricated and the record must not be written.
    outcome = ArmOutcome(arm=ARM, channel="recalled", passes=2, runs=2)
    diagnostics = BuiltinDiagnostics(engaged=2, leaked=0, runs=3)
    with pytest.raises(ValueError, match="diagnostics claim"):
        grid._cell(outcome, diagnostics)


# --- the resume cache, as this grid now inherits it --------------------------------------


def test_run_corpus_persists_and_is_resumable(tmp_path: Path) -> None:
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"

    first = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert first["executed"] == 1 and first["reused"] == 0
    assert (out / "w-0.json").is_file()
    assert "SEPARATES" in first["per_task"][0]["verdict"]
    assert first["separates_all_channels"] == 1

    second = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert second["executed"] == 0 and second["reused"] == 1


def test_dry_run_cache_never_satisfies_a_paid_run(tmp_path: Path, monkeypatch) -> None:
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    grid.run_corpus(tasks, out_dir=out, repeats=1, model="", dry_run=True)

    calls = {"n": 0}
    real_eval = grid.evaluate_task

    def _spy(task, **_kwargs):
        calls["n"] += 1
        return real_eval(task, repeats=1, model="", dry_run=True)  # simulate, never spend

    monkeypatch.setattr(grid, "evaluate_task", _spy)
    paid = grid.run_corpus(tasks, out_dir=out, repeats=1, model="", dry_run=False)
    assert paid["executed"] == 1 and paid["reused"] == 0
    assert calls["n"] == 1


# THE FOUR LIVE DEFECTS (mem-mpxie). Each was a real hole in this driver's hand-rolled cache on
# 2026-07-14, each is closed by adopting the shared core, and each below is the executable case
# that says so. They are the same shapes the 3-arm grid was already hardened against — which is
# the whole point: the sibling re-earned them because it re-implemented the cache instead of
# adopting it.


def test_a_regenerated_corpus_never_reuses_the_previous_worlds_results(tmp_path: Path) -> None:
    # (a) NO TASK FINGERPRINT. Work ids are POSITIONAL (`w-0`), so regenerating the corpus over the
    # same `--out` reuses them. The old identity was {repeats, dry_run, model} — nothing about the
    # WORLD — so the previous world's numbers were served at executed=0 for a task whose scoring
    # values had changed underneath.
    #
    # The regenerated world here changes ONLY the superseded (stale) value, and that is the point:
    # the stale value is never surfaced (the goal requires the CURRENT memory id) and never named in
    # the request (the leak firewall), so BOTH prompts come out byte-identical while the scorer's
    # `forbidden_values` — what a passing Write must NOT carry — is a different token. It is the one
    # world change `prompt_fingerprint` structurally cannot see, which is why the identity carries
    # `task_fingerprint` ALONGSIDE it rather than in place of it. A weaker case (changing the
    # current value too) would move the prompts as well, and would pass with no task fingerprint at
    # all.
    out = tmp_path / "out"
    tasks = _corpus_one(tmp_path)
    first = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert first["executed"] == 1

    corpus = tmp_path / "corpus"
    (corpus / "0" / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0", current=CURRENT, stale="91 days").model_dump()]),
        encoding="utf-8",
    )
    _, regenerated = load_corpus_with_sequences(corpus)
    assert regenerated[0].work_id == tasks[0].work_id  # the id collides, as in the real corpus
    assert grid.prompt_fingerprint(regenerated[0]) == grid.prompt_fingerprint(tasks[0])
    assert task_fingerprint(regenerated[0]) != task_fingerprint(tasks[0])

    second = grid.run_corpus(regenerated, out_dir=out, repeats=2, model="", dry_run=True)
    assert second["executed"] == 1 and second["reused"] == 0, "served the previous world's numbers"


def test_repointing_the_env_model_between_runs_is_a_miss_not_a_relabel(
    tmp_path: Path, monkeypatch
) -> None:
    # (b) RAW MODEL IN THE IDENTITY. `--model` defaults to "" and the agent then reads
    # MEMBENCH_AGENT_MODEL, so caching the raw "" made the driver's primary independent variable
    # invisible: run under one model, repoint the env, resume, and every task was served as
    # `reused` with the FIRST model's numbers relabelled as the second's. The identity now stores
    # the RESOLVED model, and `BaseRunIdentity` refuses an unresolved one structurally.
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"

    monkeypatch.setenv(ENV_MODEL, "model-one")
    first = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert first["executed"] == 1

    monkeypatch.setenv(ENV_MODEL, "model-two")
    second = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert second["executed"] == 1 and second["reused"] == 0, "relabelled one model as another"


def test_a_newer_writers_extra_identity_field_is_a_miss_not_a_subset_match(tmp_path: Path) -> None:
    # (c) SUBSET IDENTITY COMPARISON. The old check was
    # `any(loaded.get(key) != value for key, value in identity.items())`, so a record carrying a
    # field the reader did not know about — a NEWER writer's file, read by an older binary —
    # matched on the fields they happened to share and was served as `reused`. The identity is now
    # a NESTED strict model with extra="forbid", so acceptance is a whole-object ==.
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)

    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["identity"]["a_field_a_newer_writer_added"] = "surely harmless"
    result_path.write_text(json.dumps(record), encoding="utf-8")

    resumed = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert resumed["executed"] == 1 and resumed["reused"] == 0


def test_deeply_nested_json_is_a_miss_not_a_recursionerror_killing_the_sweep(
    tmp_path: Path,
) -> None:
    # (d) NARROW PARSE BOUNDARY. The old loader caught (OSError, ValueError) only, and
    # `json.loads` raises RecursionError — NOT a ValueError — on deeply nested input. One such
    # file (disk rot, a truncated write, a hand edit) escaped the loader and killed the whole
    # PAID sweep mid-resume. Every rejection is now a miss.
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "w-0.json").write_bytes(b"[" * 200_000)
    summary = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert summary["executed"] == 1 and summary["reused"] == 0


def test_the_prompt_fingerprint_covers_the_establish_leg_not_just_the_goal(tmp_path: Path) -> None:
    # The arm sends TWO prompts per cell and the establish leg is the one under test — it is what
    # has to persist the fact. A fingerprint over the goal leg alone would call two runs identical
    # while the establish prompt (the independent variable of the whole arm) differed, and serve
    # the old wording's numbers for the new one.
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    first = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert first["executed"] == 1

    import membench.runner.toolreq_builtin as tb

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tb, "_ESTABLISH_INSTRUCTION", "Remember these. (reworded)")
        second = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert second["executed"] == 1 and second["reused"] == 0, "the establish prompt is not hashed"


def _not_engaged_grid(task: ToolReqRealAgentTask, **_kwargs: object) -> list[grid.BuiltinCell]:
    """An `evaluate_task` stand-in returning a full but NON-separating grid, so a cache HIT and a
    cache MISS yield DIFFERENT headline numbers and the assertion can tell them apart instead of
    accidentally agreeing with the bug."""
    return [_cell(channel.value, passes=0, runs=2, engaged=0) for channel in grid.CHANNELS]


def test_a_partial_cell_grid_never_credits_a_both_channel_separation(
    tmp_path: Path, monkeypatch
) -> None:
    # A cache SHORT a channel must be a MISS: a one-channel record would satisfy "separated on BOTH
    # channels" while `trusted` was never evaluated — and on the PAID identity that verdict is
    # credited with zero `claude -p` calls made.
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    truth = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert truth["separates_all_channels"] == 1  # both channels really do separate

    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["outcomes"] = record["outcomes"][:1]  # drop the `trusted` channel
    result_path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setattr(grid, "evaluate_task", _not_engaged_grid)
    summary = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert summary["executed"] == 1 and summary["reused"] == 0
    assert summary["separates_all_channels"] == 0  # re-measured, not replayed from half a grid
    assert len(json.loads(result_path.read_text())["outcomes"]) == len(grid.CHANNELS)


def test_a_duplicate_channel_cache_never_credits_a_both_channel_separation(
    tmp_path: Path, monkeypatch
) -> None:
    # The dual of the missing-channel case, and NOT caught by an arity check alone: two copies of
    # the `recalled` cell has the right count and the wrong coverage. `trusted` was never measured,
    # yet every kind is SEPARATES, so the both-channel headline would be credited off one channel
    # counted twice.
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)

    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["outcomes"] = [record["outcomes"][0], record["outcomes"][0]]
    result_path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setattr(grid, "evaluate_task", _not_engaged_grid)
    summary = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert summary["executed"] == 1 and summary["reused"] == 0
    assert summary["separates_all_channels"] == 0


def test_a_forged_leak_free_verdict_is_a_miss(tmp_path: Path, monkeypatch) -> None:
    # The verdict is DERIVED, so a persisted one may only be the one its rows imply — and the
    # summary a human reads (`leaked`, `not_engaged`, `separates_all_channels`) is built from
    # these strings. A record whose rows say LEAK and whose verdict says SEPARATES agrees with the
    # run on every other field, so nothing else in it can refuse it.
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)

    result_path = out / "w-0.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    for row in record["outcomes"]:  # the rows now say LEAK...
        row["engaged"] = 0
        row["leaked"] = row["passes"]
    result_path.write_text(json.dumps(record), encoding="utf-8")  # ...but the verdict still says
    assert "SEPARATES" in record["verdict"]  # SEPARATES

    monkeypatch.setattr(grid, "evaluate_task", _not_engaged_grid)
    summary = grid.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert summary["executed"] == 1 and summary["reused"] == 0
    assert summary["leaked"] == [] and summary["separates_all_channels"] == 0


# --- driver (scripts/grid_toolreq_builtin.py) -------------------------------------------

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import grid_toolreq_builtin as driver  # noqa: E402


def _engaging_arm(task, *, repeats: int, channel: MemoryChannel, **_kwargs):
    """A `run_builtin_arm` stand-in that engages and passes every repeat — HONESTLY: it reports the
    repeats it was asked for and the channel it was given, so the cells it produces are a real grid
    the schema will accept. A fake that hardcoded one channel would now be refused as a duplicated
    cell, which is the schema doing its job."""
    return (
        ArmOutcome(arm=ARM, channel=channel.value, passes=repeats, runs=repeats),
        BuiltinDiagnostics(engaged=repeats, leaked=0, runs=repeats),
    )


def test_driver_refuses_to_spend_without_token(tmp_path: Path, monkeypatch) -> None:
    _corpus_one(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("must NOT spawn claude when the spend gate fires")

    import membench.runner.toolreq_builtin as tb

    monkeypatch.setattr(tb.subprocess, "run", _boom)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 2


def test_repeats_below_one_is_refused_at_the_flag(tmp_path: Path) -> None:
    # `--repeats 0` runs zero agent turns per cell and would persist 0/0 rows that the verdict rule
    # reads as a confident NOT-ENGAGED for a task that was NEVER EVALUATED. The schema refuses the
    # record (repeats/runs are both >= 1); the flag refuses it first, with a message that says why.
    _corpus_one(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        driver.main(
            [
                "--corpus-dir",
                str(tmp_path / "corpus"),
                "--out",
                str(tmp_path / "out"),
                "--repeats",
                "0",
                "--dry-run",
            ]
        )
    assert exit_info.value.code == 2


def test_preflight_halts_when_native_memory_never_engages(tmp_path: Path, monkeypatch) -> None:
    _corpus_one(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")

    def _never_engages(task, *, repeats: int, channel: MemoryChannel, **_kwargs):
        return (
            ArmOutcome(arm=ARM, channel=channel.value, passes=0, runs=repeats),
            BuiltinDiagnostics(engaged=0, leaked=0, runs=repeats),
        )

    monkeypatch.setattr(driver, "run_builtin_arm", _never_engages)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 3


def test_preflight_agent_error_halts_diagnosed_not_raw_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The most likely REAL preflight failure is an erroring/flaky/timed-out `claude -p` call
    # (HeadlessAgentError), not a clean not-engaged diagnosis. main() must convert it into the
    # documented diagnosed PREFLIGHT HALT (exit 3), never let a raw traceback propagate out.
    _corpus_one(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")

    def _raises(task, **_kwargs):
        raise HeadlessAgentError("claude -p failed: simulated rate-limit")

    monkeypatch.setattr(driver, "run_builtin_arm", _raises)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 3
    err = capsys.readouterr().err
    assert "PREFLIGHT HALT" in err
    assert "simulated rate-limit" in err  # the halt carries the underlying failure


def test_sweep_agent_error_halts_diagnosed_with_resume_pointer(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The preflight is not the only paid boundary: a HeadlessAgentError mid-sweep (the rate-limit at
    # paid call 50 of 180) must get the same diagnosed-halt treatment — exit 3, pointing at the
    # persisted per-task results for a cheap resume — never a raw traceback during the expensive
    # phase. The sweep runs through the GRID, so that is where the failing arm is patched.
    _corpus_one(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")

    def _raises(task, **_kwargs):
        raise HeadlessAgentError("claude -p failed: simulated mid-sweep rate-limit")

    monkeypatch.setattr(driver, "run_builtin_arm", _engaging_arm)  # the preflight passes...
    monkeypatch.setattr(grid, "run_builtin_arm", _raises)  # ...and the sweep then dies
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 3
    err = capsys.readouterr().err
    assert "SWEEP HALT" in err
    assert "simulated mid-sweep rate-limit" in err  # carries the underlying failure
    assert "re-run" in err  # and points at the resume path


def test_preflight_proceeds_when_engaged(tmp_path: Path, monkeypatch) -> None:
    _corpus_one(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")

    calls = {"n": 0}

    def _spy(task, **kwargs):
        calls["n"] += 1
        return _engaging_arm(task, **kwargs)

    monkeypatch.setattr(driver, "run_builtin_arm", _spy)
    monkeypatch.setattr(grid, "run_builtin_arm", _spy)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 0
    # once for the preflight + once per (task x channel) cell in the sweep
    assert calls["n"] == 1 + len(grid.CHANNELS)


def test_skip_preflight_bypasses_the_real_preflight_call(tmp_path: Path, monkeypatch) -> None:
    _corpus_one(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")

    calls = {"n": 0}

    def _spy(task, **kwargs):
        calls["n"] += 1
        return _engaging_arm(task, **kwargs)

    monkeypatch.setattr(driver, "run_builtin_arm", _spy)
    monkeypatch.setattr(grid, "run_builtin_arm", _spy)
    code = driver.main(
        [
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "--out",
            str(tmp_path / "out"),
            "--skip-preflight",
        ]
    )
    assert code == 0
    # no +1 for a preflight call — only the sweep's per-channel cells
    assert calls["n"] == len(grid.CHANNELS)


def test_the_driver_writes_the_summary_the_grid_reserves(tmp_path: Path, monkeypatch) -> None:
    # The summary lands in the SAME directory as the per-task results, so its name is one the tasks
    # are not allowed to claim (resume_cache.assert_usable_work_ids). Driver and grid must therefore
    # agree on that name — a second copy of the string is how a task quietly overwrites the summary,
    # or the summary a task's result.
    _corpus_one(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")
    monkeypatch.setattr(driver, "run_builtin_arm", _engaging_arm)
    monkeypatch.setattr(grid, "run_builtin_arm", _engaging_arm)

    assert driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(out)]) == 0
    assert (out / grid.SUMMARY_NAME).is_file()
