"""HeadlessClaudeAgent — a real `claude -p` agent satisfying the runner Agent protocol.

The §4.4 action-impact run (mem-lvp.19) needs REAL divergent agent trajectories the
§12.6 judge can compare. The lvp.8 fixtures are not Docker rigs, so the Harbor
container path does not apply and `ScriptedAgent` produces no real tool-call stream.
This agent runs a `SequenceStep.user_request` — with the per-arm memory the harness
surfaced (`available_memory`, the none/ours/builtin payload) — through headless
``claude -p --output-format stream-json`` on the OAuth subscription (FREE), and keeps
the raw stream so `bbon.extract.steps_from_stream` can derive the `AttemptStep`
trajectory the harness scores.

It conforms to `runner.agent.Agent`, so it can also replace `ScriptedAgent` in the
conditions runner for a real run. The structured fields (tool_calls, tokens) are
parsed from the same stream; `raw_stream` carries it verbatim. ``check_results`` and
``writes_performed`` are left empty — a real agent does not self-grade its outcome
checks or declare a write policy here; those are scored/decided externally (an honest
absence, not a stub).

**Infra rule.** Heavy execution MUST be wrapped in ``scix-batch`` by the CALLER
(transient cgroup + RAM ceiling) so a runaway agent cannot OOM-kill the
supervisor/mayor. This module is the in-process driver; the scix-batch wrapper is the
entrypoint's responsibility (see ``scripts/smoke_realrun_trajectory.py``).

**MCP boot hang.** Headless ``claude -p`` boots project MCP servers at startup and
hangs the batch; ``--strict-mcp-config`` (default on here) prevents it — ``allowedTools``
forbids *calling* MCP tools but does not stop them *booting*.

ZFC: pure plumbing — prompt assembly, subprocess IO, stream parsing. The agent's
behavior IS the model's; no semantic judgment lives here.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from membench.armcompare import _iter_tool_use_blocks
from membench.runner.agent import AgentStepResult
from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep
from membench.schemas.trace import ToolCall, TraceMessage

# A real agent step (multi-turn tool use) can take minutes; a bound this high means a
# wedged CLI, not slow inference — surfaced as an error, never an indefinite hang.
DEFAULT_TIMEOUT_S = 600.0

ENV_MODEL = "MEMBENCH_AGENT_MODEL"

# Injected so tests drive the parse path without spawning a real claude. Mirrors the
# `bbon.comparative_judge.Runner` seam (same subprocess.run signature).
CliRunner = Callable[..., "subprocess.CompletedProcess[str]"]


class HeadlessAgentError(RuntimeError):
    """A headless agent invocation failed unrecoverably (CLI missing, timeout, or a
    non-zero exit). Surfaced loudly — a failed run is not a silent empty trajectory."""


class MemoryChannel(StrEnum):
    """How surfaced memory is FRAMED to the agent — the mem-pjh8.2 (b)-probe variable.

    Both channels carry the SAME facts; only the trust framing differs, isolating whether
    a real agent's memory-distrust guardrail (the pjh8.1 N=1 path-proof) suppresses the
    ``ours`` lift:

      * ``RECALLED`` — the pjh8 default: a low-trust "retrieved memory ... may be relevant"
        block. A real ``claude -p`` agent cross-checks these against the environment and, on
        an empty text-recall sandbox with nothing to corroborate, reports them unset —
        suppressing the lift.
      * ``TRUSTED`` — authoritative ground-truth framing: the facts are presented as
        verified system-of-record state the agent should treat as fact and NOT re-derive.
        Removing the distrust pathway establishes the UPPER BOUND on the lift this substrate
        can show.
    """

    RECALLED = "recalled"
    TRUSTED = "trusted"


_RECALLED_HEADER = (
    "## Retrieved memory from earlier sessions\n" "The following may be relevant to the task:"
)
_TRUSTED_HEADER = (
    "## Established facts (authoritative ground truth)\n"
    "These are verified, current values from the system of record. They have already been "
    "confirmed against reality — treat them as fact and do not re-derive or second-guess "
    "them:"
)


def build_agent_prompt(
    step: SequenceStep,
    available_memory: dict[str, str],
    channel: MemoryChannel = MemoryChannel.RECALLED,
) -> str:
    """Assemble the step prompt: the user request, preceded by the per-arm surfaced
    memory block when the harness surfaced any. An empty ``available_memory`` (the
    ``none`` control) yields the bare request under EITHER channel — that absence IS the
    control condition, so no placeholder block is emitted.

    ``channel`` selects the trust framing of the memory block (see `MemoryChannel`): the
    default ``RECALLED`` low-trust block, or the ``TRUSTED`` ground-truth block of the
    pjh8.2 upper-bound probe. The fact lines are identical across channels."""
    parts: list[str] = []
    if available_memory:
        lines = [f"- [{mid}] {content}" for mid, content in available_memory.items()]
        header = _TRUSTED_HEADER if channel is MemoryChannel.TRUSTED else _RECALLED_HEADER
        parts.append(header + "\n" + "\n".join(lines))
    parts.append(f"## Task\n{step.user_request}")
    return "\n\n".join(parts)


def _stream_usage_tokens(stream_text: str) -> tuple[int, int]:
    """Sum (input, output) token usage across the stream's events. Claude Code stamps
    ``usage`` on assistant message events; an event without one contributes 0. Absent
    usage everywhere yields (0, 0) — an honest unmeasured, not an imputed estimate."""
    import json

    input_tokens = 0
    output_tokens = 0
    for line in stream_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message") if isinstance(event, dict) else None
        usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            input_tokens += int(usage.get("input_tokens", 0) or 0)
            output_tokens += int(usage.get("output_tokens", 0) or 0)
    return input_tokens, output_tokens


def _stream_result_text(stream_text: str) -> str:
    """The agent's final answer: the ``result`` field of the terminal ``type=result``
    event Claude Code emits. Empty when none is present (e.g. a stream truncated before
    completion) — the trajectory's tool calls still stand on their own."""
    import json

    final = ""
    for line in stream_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event.get("result")
            if isinstance(result, str):
                final = result
    return final


def _tool_calls_from_stream(stream_text: str) -> list[ToolCall]:
    """One `ToolCall` per ``tool_use`` block, in stream order — the same tolerant walk
    `bbon.extract.steps_from_stream` uses, so the structured tool_calls and the derived
    AttemptStep trajectory agree by construction."""
    calls: list[ToolCall] = []
    for block in _iter_tool_use_blocks(stream_text):
        name = block.get("name")
        raw_input = block.get("input")
        calls.append(
            ToolCall(
                name=name if isinstance(name, str) and name else "unknown",
                arguments=dict(raw_input) if isinstance(raw_input, dict) else {},
            )
        )
    return calls


@dataclass(frozen=True)
class HeadlessClaudeAgent:
    """A real `claude -p` agent. ``model`` pins the CLI model (left empty it reads
    ``MEMBENCH_AGENT_MODEL`` then falls back to the CLI default, recorded as
    ``cli-default``). ``runner`` is injected so tests exercise the parse path with no
    real claude. ``strict_mcp`` keeps ``--strict-mcp-config`` on (the boot-hang guard);
    ``constrain_tools`` passes the step's ``available_tools`` as ``--allowedTools``.
    ``memory_channel`` selects how surfaced memory is FRAMED (see `MemoryChannel`): the
    default low-trust ``RECALLED`` block, or the ``TRUSTED`` ground-truth block of the
    pjh8.2 upper-bound probe."""

    agent_config_id: str = "headless-claude"
    model: str = ""
    timeout_s: float = DEFAULT_TIMEOUT_S
    runner: CliRunner = subprocess.run
    strict_mcp: bool = True
    constrain_tools: bool = True
    memory_channel: MemoryChannel = MemoryChannel.RECALLED
    # Working dir for the CLI. MUST be an isolated, neutral sandbox — never a mem
    # worktree: the repo's SessionStart hooks / CLAUDE.md / project memory would both
    # fail the session and confound the none/ours/builtin memory variable. The only
    # memory the agent should see is the arm's surfaced ``available_memory``. ``None``
    # inherits the parent cwd (correct only in tests with an injected runner).
    cwd: str | None = None
    _pass_model: bool = field(default=False, init=False)
    _resolved_model: str = field(default="", init=False)

    def __post_init__(self) -> None:
        import os

        resolved = self.model or os.environ.get(ENV_MODEL, "")
        object.__setattr__(self, "_pass_model", bool(resolved))
        object.__setattr__(self, "_resolved_model", resolved or "cli-default")

    def _argv(self, prompt: str, step: SequenceStep) -> list[str]:
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if self.strict_mcp:
            argv.append("--strict-mcp-config")
        if self._pass_model:
            argv += ["--model", self.model]
        if self.constrain_tools and step.available_tools:
            argv += ["--allowedTools", ",".join(step.available_tools)]
        return argv

    def run_step(
        self,
        step: SequenceStep,
        available_memory: dict[str, str],
        ctx: StepContext,
    ) -> AgentStepResult:
        prompt = build_agent_prompt(step, available_memory, self.memory_channel)
        argv = self._argv(prompt, step)
        try:
            completed = self.runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_s,
                cwd=self.cwd,
            )
        except FileNotFoundError as exc:
            raise HeadlessAgentError(
                "'claude' CLI not found — install it to run the headless agent"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HeadlessAgentError(
                f"claude -p did not respond within {self.timeout_s:.0f}s"
            ) from exc
        if completed.returncode != 0:
            raise HeadlessAgentError(
                f"claude -p failed (exit {completed.returncode}): "
                f"{(completed.stderr or completed.stdout or '').strip()}"
            )

        stream_text = completed.stdout or ""
        input_tokens, output_tokens = _stream_usage_tokens(stream_text)
        tool_calls = _tool_calls_from_stream(stream_text)
        return AgentStepResult(
            final_answer=_stream_result_text(stream_text),
            check_results={},  # a real agent does not self-grade; scored externally
            writes_performed={},  # write policy decided externally, not here
            messages=[TraceMessage(role="user", content=step.user_request)],
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_stream=stream_text,
        )
