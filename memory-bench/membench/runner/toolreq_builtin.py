"""mem-rk41.3.2 — builtin native-memory persistent-env arm for the tool-requiring real-agent grid.

Completes the ours-vs-builtin verdict's third leg (none/oracle staged in mem-rk41.3;
``ours`` in mem-rk41.3.1): does the agent's OWN native-memory feature carry a fact
across two sequential ``claude -p`` calls with no external memory system at all?

Two calls per repeat, sharing ONE sandbox cwd + ONE ``CLAUDE_CONFIG_DIR`` (so Claude
Code's own memory file is the sole continuity channel):

1. **establish** — the id-exact facts (``task.oracle_memory``, opaque-valued like the
   oracle arm) are surfaced via ``available_memory`` under the TRUSTED/RECALLED channel,
   with an explicit instruction to retain them for later. That instruction makes this the
   usability CEILING, so a null reads as "the mechanism doesn't work" rather than "the
   agent wasn't told to" (unprompted uptake is a separate experiment). No tool allowlist
   (``available_tools=[]``), so Claude Code's own memory-write path is never blocked by
   ``--allowedTools``.
2. **goal** — ``task.goal_step`` run BARE (``memory={}``): the only way the current
   opaque value can reach the ``Write`` call is if call 1 actually persisted it and
   Claude Code's native-memory system re-surfaced it unprompted. Write-only tool access
   (matches the oracle/none arms) closes the scavenge vector: the goal call cannot Read
   a leftover file from the establish turn to fake engagement.

Engagement is graded on CONTENT, not file existence: after call 1, every ``MEMORY.md``
under the config dir is checked for the current opaque token. A goal PASS with
engaged=False is a LEAK (the shared sandbox let the goal call scavenge a leftover file
some other way), not a builtin win — same severity class as the ``none``-arm leak
branch in ``realagent_probe``.

ZFC: plumbing + a mechanical token-presence check (``states_value``, the same
word-boundary match ``toolreq_realagent``'s leak firewall uses). No model call, no
semantic judgment.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from membench.harbor.agent_memory import NATIVE_MEMORY_GLOB, native_memory_path
from membench.metrics.scorers import states_value
from membench.runner.headless_agent import CliRunner, HeadlessClaudeAgent, MemoryChannel
from membench.runner.realagent_probe import CONFIG_FILE, REAL_TOOL, ArmOutcome, score_goal_action
from membench.runner.toolreq_realagent import ToolReqRealAgentTask
from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep

ARM = "builtin"

_ESTABLISH_INSTRUCTION = (
    "Take note of the established fact(s) above and remember them: you will need to "
    "recall and use them later in this same session, without being reminded again."
)


def _establish_step(task: ToolReqRealAgentTask) -> SequenceStep:
    """The establish turn's bare instruction. The facts themselves are NOT embedded in
    this prose — they are delivered via ``available_memory`` (like the oracle arm), which
    is what lets the TRUSTED/RECALLED channel framing actually vary how they present."""
    return SequenceStep(
        step_id=f"{task.work_id}-establish",
        user_request=_ESTABLISH_INSTRUCTION,
        available_tools=[],  # H3: no --allowedTools clamp on CC's own memory-write path
    )


def _memory_engaged(config_dir: Path, tokens: Collection[str]) -> bool:
    """True iff any current opaque ``tokens`` reached a native ``MEMORY.md`` under
    ``config_dir``. Matches on CONTENT, not file existence — Claude Code scaffolds an
    empty ``memory/`` dir regardless of whether anything meaningful was written, so
    existence alone would read as engagement."""
    for memory_file in config_dir.glob(NATIVE_MEMORY_GLOB):
        try:
            content = memory_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(states_value(content, token) for token in tokens):
            return True
    return False


@dataclass(frozen=True)
class BuiltinDiagnostics:
    """Sidecar engagement diagnostics for one (arm=builtin, channel) cell — kept OFF
    ``ArmOutcome`` (M3: no ``BuiltinArmOutcome`` subtype) so the eventual
    none/oracle/ours/builtin report merge stays a uniform ``ArmOutcome`` list; the
    driver persists this alongside it. ``leaked`` is the count of repeats that scored a
    PASS despite ``engaged=False`` — an invalid result, not a builtin win."""

    engaged: int
    leaked: int
    runs: int
    # Audit trail for the establish call, which runs with NO --allowedTools clamp (so CC's
    # memory-write path is never blocked) and can therefore genuinely invoke Bash/Read/etc,
    # not just the intended memory write. Summed across repeats so a reviewer gating the
    # paid fire can see what the unconstrained call actually did before authorizing spend.
    establish_tool_calls: int = 0


def simulated_builtin_runner(current_values: Collection[str]) -> CliRunner:
    """Dry-run stand-in for BOTH builtin calls: the establish call honestly persists a
    marker at the (simulator-known) native-memory path iff its prompt carried every
    current value (i.e. iff the arm surfaced them via ``available_memory``); the goal
    call — always bare, so its own prompt never carries the values — checks for that
    marker and Writes the current value(s) iff present. Proves the two-call shared
    cwd/config-dir wiring and scoring path for zero tokens; it CANNOT exercise the one
    link that actually matters — whether a real ``claude -p`` session persists to
    ``MEMORY.md`` when asked. That is the real preflight's job, not this simulator's."""
    values = list(current_values)

    def run(argv: Collection[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        argv_list = list(argv)
        assert argv_list[:2] == ["claude", "-p"], f"unexpected argv layout: {argv_list[:3]}"
        prompt = argv_list[2] if len(argv_list) > 2 else ""
        env = kwargs.get("env")
        cwd = kwargs.get("cwd")
        config_dir = env.get("CLAUDE_CONFIG_DIR") if isinstance(env, Mapping) else None
        events: list[dict[str, object]] = []
        if config_dir and isinstance(cwd, str):
            memory_path = Path(native_memory_path(config_dir=str(config_dir), workdir=cwd))
            if values and all(value in prompt for value in values):
                # establish call: honestly persist iff the arm surfaced every value
                memory_path.parent.mkdir(parents=True, exist_ok=True)
                memory_path.write_text(" ".join(values), encoding="utf-8")
            elif memory_path.is_file():
                persisted = memory_path.read_text(encoding="utf-8")  # once, not per value
                if all(value in persisted for value in values):
                    # goal call: re-surface iff the establish call actually persisted it
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
                                            "content": " ".join(values),
                                        },
                                    }
                                ],
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                            },
                        }
                    )
        events.append({"type": "result", "result": "done"})
        stdout = "\n".join(json.dumps(event) for event in events)
        return subprocess.CompletedProcess(argv_list, returncode=0, stdout=stdout, stderr="")

    return run


def run_builtin_arm(
    task: ToolReqRealAgentTask,
    *,
    repeats: int,
    model: str,
    dry_run: bool,
    channel: MemoryChannel,
    runner: CliRunner | None = None,
) -> tuple[ArmOutcome, BuiltinDiagnostics]:
    """Run ``repeats`` independent establish/goal pairs, each in a fresh sandbox cwd +
    fresh ``CLAUDE_CONFIG_DIR``, and score the goal call externally. ``dry_run`` swaps
    the real ``claude -p`` for ``simulated_builtin_runner`` (no token). ``runner``
    overrides the CLI runner directly (bypassing the dry_run selection) — mainly for
    tests exercising accounting edge cases, like a pass without engagement, that the
    honest dry-run simulator cannot itself produce."""
    if runner is None:
        runner = simulated_builtin_runner(task.current_opaque_values) if dry_run else subprocess.run
    establish_step = _establish_step(task)
    passes = 0
    engaged = 0
    leaked = 0
    establish_tool_calls = 0

    for i in range(repeats):
        with (
            tempfile.TemporaryDirectory(prefix=f"toolreq-{ARM}-sandbox-") as sandbox,
            tempfile.TemporaryDirectory(prefix=f"toolreq-{ARM}-config-") as config_dir_str,
        ):
            config_dir = Path(config_dir_str)
            # ONE agent drives both calls, which is what makes "establish and goal share a
            # sandbox cwd + a CLAUDE_CONFIG_DIR" structural rather than a thing to assert.
            # Safe because the per-call differences are carried by the STEP, not the agent:
            # --allowedTools is derived from `step.available_tools` (so establish runs
            # unconstrained and goal is Write-only), and `memory_channel` only frames a
            # surfaced-memory block, which the bare goal call (memory={}) never has.
            agent = HeadlessClaudeAgent(
                model=model,
                runner=runner,
                memory_channel=channel,
                constrain_tools=True,
                cwd=sandbox,
                env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )

            def _ctx(leg: str, step_id: str, i: int = i) -> StepContext:
                return StepContext(
                    trial_id=f"{ARM}-{channel.value}-{i}-{leg}",
                    session_id=f"{ARM}-{channel.value}-{i}",
                    step_id=step_id,
                )

            establish_result = agent.run_step(
                establish_step,
                dict(task.oracle_memory),
                _ctx("establish", establish_step.step_id),
            )
            establish_tool_calls += len(establish_result.tool_calls)
            repeat_engaged = _memory_engaged(config_dir, task.current_opaque_values)

            result = agent.run_step(task.goal_step, {}, _ctx("goal", task.goal_step.step_id))

        passed = score_goal_action(
            task.goal_step, tool_calls=result.tool_calls, final_answer=result.final_answer
        )
        if passed:
            passes += 1
        if repeat_engaged:
            engaged += 1
        if passed and not repeat_engaged:
            leaked += 1

    outcome = ArmOutcome(arm=ARM, channel=channel.value, passes=passes, runs=repeats)
    diagnostics = BuiltinDiagnostics(
        engaged=engaged, leaked=leaked, runs=repeats, establish_tool_calls=establish_tool_calls
    )
    return outcome, diagnostics
