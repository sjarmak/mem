"""mem-rk41.3.2 — builtin native-memory persistent-env arm (establish/goal, two calls).

Covers the invariants the bead's PLAN-REVIEW made mandatory:

* H3 — the establish call carries NO tool allowlist (``available_tools=[]``), so
  ``--allowedTools`` never blocks Claude Code's own memory-write path.
* H4 — the establish turn's facts are delivered via ``available_memory`` (like the
  oracle arm), never embedded in the instruction prose, so TRUSTED/RECALLED
  meaningfully varies the establish framing.
* H2 — engagement is CONTENT-based (does the opaque token actually reach a native
  ``MEMORY.md``), not file-existence — an empty scaffolded dir never counts.
* A goal PASS with engaged=False is accounted as a LEAK, not a builtin win.
* The dry-run simulator proves the two-call shared cwd/config-dir wiring end to end
  for zero tokens (it does not and cannot prove a real ``claude -p`` session persists
  to ``MEMORY.md`` — that is the paid preflight's job, tested at the driver level).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from membench.harbor.agent_memory import native_memory_path
from membench.runner.headless_agent import MemoryChannel
from membench.runner.realagent_probe import CONFIG_FILE, REAL_TOOL, ArmOutcome
from membench.runner.toolreq_builtin import (
    ARM,
    BuiltinDiagnostics,
    _establish_step,
    _memory_engaged,
    run_builtin_arm,
    simulated_builtin_runner,
)
from membench.runner.toolreq_realagent import adapt_sequence
from membench.schemas.sequence import (
    BenchmarkSequence,
    ExpectedAction,
    OutcomeCheck,
    SequenceStep,
)

CURRENT = "30 days"
STALE = "90 days"


def _toolreq_seq(seq_id: str = "w-t0") -> BenchmarkSequence:
    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"{seq_id} initiative",
        steps=[
            SequenceStep(
                step_id=f"{seq_id}-s0",
                user_request="Record.",
                expected_memory_writes={"m-v1": f"the retention window is {STALE}"},
            ),
            SequenceStep(
                step_id=f"{seq_id}-s1",
                user_request="Record.",
                expected_memory_writes={"m-v2": f"the retention window is {CURRENT}"},
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
                                tool="apply_config", arg_values=[CURRENT], forbidden_values=[STALE]
                            )
                        ],
                    )
                ],
            ),
        ],
    )


def _task(seq_id: str = "w-t0"):
    return adapt_sequence(_toolreq_seq(seq_id))


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


# --- simulated dry-run runner (proves the two-call wiring, no tokens) ------------------


def test_simulated_runner_establish_writes_iff_prompt_carries_every_value(tmp_path: Path) -> None:
    runner = simulated_builtin_runner(["tok-a", "tok-b"])
    argv = ["claude", "-p", "carries tok-a and tok-b", "--output-format", "stream-json"]
    completed = runner(argv, env={"CLAUDE_CONFIG_DIR": str(tmp_path)}, cwd="/sandbox")
    assert completed.returncode == 0
    memory_path = Path(native_memory_path(config_dir=str(tmp_path), workdir="/sandbox"))
    assert memory_path.is_file()
    assert "tok-a" in memory_path.read_text(encoding="utf-8")


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

    def bash_happy_runner(argv, **kwargs):
        argv_list = list(argv)
        events: list[dict[str, object]] = []
        if "--allowedTools" not in argv_list:  # the establish call only
            events.append(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
                        ],
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                }
            )
        events.append({"type": "result", "result": "done"})
        stdout = "\n".join(json.dumps(e) for e in events)
        return subprocess.CompletedProcess(argv_list, returncode=0, stdout=stdout, stderr="")

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
    def run(argv, **kwargs):
        argv_list = list(argv)
        events: list[dict[str, object]] = []
        if "--allowedTools" in argv_list:  # the goal call only
            events.append(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": REAL_TOOL,
                                "input": {
                                    "file_path": CONFIG_FILE,
                                    "content": current_opaque_value,
                                },
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


def test_diagnostics_is_a_plain_dataclass_not_an_arm_outcome_subtype() -> None:
    # M3: ArmOutcome stays uniform; engagement lives in a SEPARATE sidecar type.
    diag = BuiltinDiagnostics(engaged=1, leaked=0, runs=1)
    assert not hasattr(diag, "arm")
    assert not hasattr(diag, "passes")


# --- driver (scripts/grid_toolreq_builtin.py) -------------------------------------------

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import grid_toolreq_builtin as driver  # noqa: E402


def test_dry_run_evaluate_task_separates_both_channels() -> None:
    task = _task()
    cells = driver.evaluate_task(task, repeats=2, model="", dry_run=True)
    assert {outcome.channel for outcome, _ in cells} == {"recalled", "trusted"}
    for outcome, diag in cells:
        assert outcome.passes == outcome.runs == 2
        assert diag.engaged == 2 and diag.leaked == 0


def test_verdict_separates_when_engaged_and_passing() -> None:
    cells = [
        (
            ArmOutcome(arm="builtin", channel="recalled", passes=3, runs=3),
            BuiltinDiagnostics(engaged=3, leaked=0, runs=3),
        )
    ]
    assert "SEPARATES" in driver.task_verdict(cells)


def test_verdict_leak_outranks_pass_count() -> None:
    cells = [
        (
            ArmOutcome(arm="builtin", channel="recalled", passes=2, runs=2),
            BuiltinDiagnostics(engaged=0, leaked=2, runs=2),
        )
    ]
    assert "LEAK" in driver.task_verdict(cells)


def test_verdict_not_engaged_when_mechanism_never_fires() -> None:
    cells = [
        (
            ArmOutcome(arm="builtin", channel="recalled", passes=0, runs=3),
            BuiltinDiagnostics(engaged=0, leaked=0, runs=3),
        )
    ]
    assert "NOT-ENGAGED" in driver.task_verdict(cells)


def test_run_corpus_persists_and_is_resumable(tmp_path: Path) -> None:
    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    seq = _toolreq_seq("w-0")
    seed_dir_file = seed_dir / "sequences.json"
    seed_dir_file.write_text(json.dumps([seq.model_dump()]), encoding="utf-8")

    from membench.runner.toolreq_realagent import load_corpus

    tasks = load_corpus(tmp_path / "corpus")
    out = tmp_path / "out"

    first = driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert first["executed"] == 1 and first["reused"] == 0
    assert (out / "w-0.json").is_file()
    assert "SEPARATES" in first["per_task"][0]["verdict"]

    second = driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert second["executed"] == 0 and second["reused"] == 1


def test_corrupt_cache_file_is_re_executed_not_crashed(tmp_path: Path) -> None:
    from membench.runner.toolreq_realagent import load_corpus

    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    tasks = load_corpus(tmp_path / "corpus")
    out = tmp_path / "out"
    out.mkdir()
    (out / "w-0.json").write_text("{ half-written not json", encoding="utf-8")
    summary = driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert summary["executed"] == 1 and summary["reused"] == 0  # cache-miss, no crash


def test_schema_mismatched_cache_file_is_re_executed_not_crashed(tmp_path: Path) -> None:
    # Valid JSON, matching identity, but missing "cells" (e.g. written by an older
    # version before a BuiltinDiagnostics field existed) — must fall back to re-execute,
    # not raise KeyError/TypeError and kill the whole resumed sweep.
    from membench.runner.toolreq_realagent import load_corpus

    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    tasks = load_corpus(tmp_path / "corpus")
    out = tmp_path / "out"
    out.mkdir()
    (out / "w-0.json").write_text(
        json.dumps({"work_id": "w-0", "repeats": 2, "dry_run": True, "model": ""}),
        encoding="utf-8",
    )
    summary = driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert summary["executed"] == 1 and summary["reused"] == 0


def test_dry_run_cache_never_satisfies_a_paid_run(tmp_path: Path, monkeypatch) -> None:
    from membench.runner.toolreq_realagent import load_corpus

    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    tasks = load_corpus(tmp_path / "corpus")
    out = tmp_path / "out"
    driver.run_corpus(tasks, out_dir=out, repeats=1, model="", dry_run=True)

    calls = {"n": 0}
    real_eval = driver.evaluate_task

    def _spy(task, **_kwargs):
        calls["n"] += 1
        return real_eval(task, repeats=1, model="", dry_run=True)  # simulate, never spend

    monkeypatch.setattr(driver, "evaluate_task", _spy)
    paid = driver.run_corpus(tasks, out_dir=out, repeats=1, model="", dry_run=False)
    assert paid["executed"] == 1 and paid["reused"] == 0
    assert calls["n"] == 1


def test_driver_refuses_to_spend_without_token(tmp_path: Path, monkeypatch) -> None:
    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("must NOT spawn claude when the spend gate fires")

    import membench.runner.toolreq_builtin as tb

    monkeypatch.setattr(tb.subprocess, "run", _boom)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 2


def test_preflight_halts_when_native_memory_never_engages(tmp_path: Path, monkeypatch) -> None:
    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")

    def _never_engages(task, **_kwargs):
        return ArmOutcome(arm="builtin", channel="recalled", passes=0, runs=1), BuiltinDiagnostics(
            engaged=0, leaked=0, runs=1
        )

    monkeypatch.setattr(driver, "run_builtin_arm", _never_engages)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 3


def test_preflight_proceeds_when_engaged(tmp_path: Path, monkeypatch) -> None:
    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")

    calls = {"n": 0}

    def _always_engages(task, **_kwargs):
        calls["n"] += 1
        return ArmOutcome(arm="builtin", channel="recalled", passes=1, runs=1), BuiltinDiagnostics(
            engaged=1, leaked=0, runs=1
        )

    monkeypatch.setattr(driver, "run_builtin_arm", _always_engages)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 0
    # once for the preflight + once per (task x channel) cell in the sweep
    assert calls["n"] == 1 + len(driver.CHANNELS)


def test_skip_preflight_bypasses_the_real_preflight_call(tmp_path: Path, monkeypatch) -> None:
    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token-for-test")

    calls = {"n": 0}

    def _spy(task, **_kwargs):
        calls["n"] += 1
        return ArmOutcome(arm="builtin", channel="recalled", passes=1, runs=1), BuiltinDiagnostics(
            engaged=1, leaked=0, runs=1
        )

    monkeypatch.setattr(driver, "run_builtin_arm", _spy)
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
    assert calls["n"] == len(driver.CHANNELS)
