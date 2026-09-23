"""The harness seam: which runtime spawns the agent a cell measures.

The flywheel's question is which config, settings and conditions get an agent to call the bd CLI
for memory. Nothing in that question names Claude Code. What a cell needs from a runtime is
small: spawn it with a prompt, in a sandbox cwd, under a PATH and an environment the cell owns,
and let it call ``bd`` through the shim on that PATH. Everything the capture endpoint reads is
then on bd's side (``bd_receipt_surface``), so a runtime that emits no transcript is measured the
same way as one that does.

Two constructors. ``claude_code_harness`` is the runtime this rig was built on and keeps every
Claude-specific instrument (the PreToolUse hooks, the native-memory comparator). ``command_harness``
is any runtime that can be run as a command with the prompt on its argv; it has no native-memory
comparator, so the ``builtin`` arm is refused on it and a turn there is beads against the floor.

``conditions`` are the axis under test, expressed the one way every runtime understands:
environment variables exported to the spawned agent. They are fingerprinted into the resume
identity, so a turn run under one set of conditions can never pool with a turn run under another.

ZFC: plumbing. No model call, no judgment.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from membench.runner.agent import AgentStepResult
from membench.runner.bd_receipts import CALLER_AGENT, CALLER_HARNESS, CONTEXT_KEYS
from membench.runner.headless_agent import (
    HeadlessAgentError,
    MemoryChannel,
    build_agent_prompt,
    cell_agent,
)
from membench.runner.tool_surface import CONFIG_DIR_ENV, MEMORY_COMMAND
from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep
from membench.schemas.trace import TraceMessage
from membench.spawn import Runner, run_checked

HARNESS_CLAUDE_CODE = "claude-code"
PROMPT_SLOT = "{prompt}"
MODEL_SLOT = "{model}"

# What the cell itself sets on the spawned agent's environment. A condition may not name one of
# these: it would be a treatment that changes what the agent can reach or which leg its bd calls
# are booked under, and the fingerprint would record it as a condition.
RIG_OWNED_ENV: tuple[str, ...] = (
    "PATH",
    "PWD",
    "HOME",
    "XDG_CONFIG_HOME",
    CONFIG_DIR_ENV,
    *CONTEXT_KEYS.values(),
)

COMMAND_TIMEOUT_S = 900.0


class HarnessError(ValueError):
    """A harness that cannot be built as asked."""


class LegAgent(Protocol):
    """What a cell needs of an agent: one step, run."""

    def run_step(
        self, step: SequenceStep, available_memory: dict[str, str], ctx: StepContext
    ) -> AgentStepResult: ...


AgentBuilder = Callable[..., LegAgent]


@dataclass(frozen=True)
class AgentHarness:
    """One agent runtime, as a cell sees it."""

    name: str
    version: str
    # The command the legs spawn by name; linked into every arm's shared toolchain.
    binary: str
    # Whether the runtime has a native memory of its own that the `builtin` arm can measure.
    native_memory: bool
    # Claude marks each actual tool command in PreToolUse, so its process starts as harness-origin.
    # A generic command harness has no tool hook; its process is itself the agent boundary.
    leg_caller: str
    conditions: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    # A command harness never inherits the operator's home. If login material is needed, the
    # operator supplies a directory containing only that material; each cell gets a private copy.
    isolated_home: bool = False
    home_seed: Path | None = field(default=None, repr=False, compare=False)
    home_seed_fingerprint: str = field(default="none", repr=False)
    build: AgentBuilder = field(default=cell_agent, repr=False, compare=False)

    def agent(
        self,
        *,
        model: str,
        channel: MemoryChannel,
        runner: Runner,
        cwd: str,
        env: Mapping[str, str],
    ) -> LegAgent:
        return self.build(model=model, channel=channel, runner=runner, cwd=cwd, env=env)

    def leg_env(self, base: Mapping[str, str], *, leg_id: str, session_id: str) -> dict[str, str]:
        """The environment one leg is spawned under: the cell's own, the conditions, and the
        attribution bd's receipts book the leg's calls under. Set here for every runtime, so a
        runtime without the Claude hook still attributes its calls to the leg; the hook, where it
        runs, adds the per-tool-call id on top."""
        return {
            **base,
            **self.conditions,
            CONTEXT_KEYS["leg_id"]: leg_id,
            CONTEXT_KEYS["session_id"]: session_id,
            CONTEXT_KEYS["caller"]: self.leg_caller,
        }

    def identity(self) -> dict[str, Any]:
        identity = {
            "harness": self.name,
            "harness_binary": self.binary,
            "harness_conditions_fingerprint": conditions_fingerprint(self.conditions),
        }
        if self.isolated_home:
            identity.update(
                {
                    "harness_home_isolation": "minted-per-cell",
                    "harness_home_seed_fingerprint": self.home_seed_fingerprint,
                }
            )
        return identity

    def mint_home(self, destination: Path) -> Path | None:
        """Create this cell's private home, copying only explicitly supplied login material."""
        if not self.isolated_home:
            return None
        if destination.exists():
            raise HarnessError(f"refusing to reuse harness home {destination}")
        if self.home_seed is None:
            destination.mkdir(parents=True)
        else:
            if _home_seed_fingerprint(self.home_seed) != self.home_seed_fingerprint:
                raise HarnessError(
                    "harness home seed changed after the resume identity was constructed"
                )
            shutil.copytree(self.home_seed, destination)
        (destination / ".config").mkdir(exist_ok=True)
        return destination


def conditions_fingerprint(conditions: Mapping[str, str]) -> str:
    payload = json.dumps(dict(sorted(conditions.items())), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _home_seed_files(seed: Path) -> tuple[Path, ...]:
    if not seed.is_dir():
        raise HarnessError(f"harness home seed is not a directory: {seed}")
    files: list[Path] = []
    for path in sorted(seed.rglob("*")):
        if path.is_symlink():
            raise HarnessError(f"harness home seed may not contain symlinks: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise HarnessError(f"harness home seed may contain only files and directories: {path}")
    return tuple(files)


def _home_seed_fingerprint(seed: Path | None) -> str:
    """Content identity for login material, without publishing its path or contents."""
    if seed is None:
        return "none"
    digest = hashlib.sha256()
    for path in _home_seed_files(seed):
        relative = path.relative_to(seed).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()[:16]


def _checked_conditions(conditions: Mapping[str, str]) -> Mapping[str, str]:
    for key, value in conditions.items():
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise HarnessError(f"condition {key!r} is not an environment variable name")
        if key in RIG_OWNED_ENV:
            raise HarnessError(
                f"condition {key} is set by the rig itself and cannot be a condition; "
                f"the rig-owned names are {', '.join(RIG_OWNED_ENV)}"
            )
    return MappingProxyType(dict(conditions))


def claude_code_harness(
    *, version: str, conditions: Mapping[str, str] | None = None
) -> AgentHarness:
    """The runtime this rig was built on. ``version`` is what ``resolve_cli_version`` read off
    the binary; it is passed in rather than resolved here so a fixture can build one without a
    `claude` on PATH."""
    return AgentHarness(
        name=HARNESS_CLAUDE_CODE,
        version=version,
        binary="claude",
        native_memory=True,
        leg_caller=CALLER_HARNESS,
        conditions=_checked_conditions(conditions or {}),
        build=cell_agent,
    )


def command_harness(
    *,
    name: str,
    version: str,
    argv_template: Sequence[str],
    conditions: Mapping[str, str] | None = None,
    home_seed: Path | None = None,
    timeout_s: float = COMMAND_TIMEOUT_S,
) -> AgentHarness:
    """Any runtime that runs as a command. ``argv_template`` is its command line with the
    prompt as one whole element spelled ``{prompt}`` and, optionally, the model as ``{model}``.
    One whole element, not a substring: a prompt spliced into a shell string is a prompt the
    shell parses. ``home_seed`` is an explicit login-only directory copied into each cell's
    private HOME; no path or credential content is published, only its content fingerprint."""
    template = tuple(argv_template)
    if not template:
        raise HarnessError("a command harness needs a command")
    if PROMPT_SLOT not in template:
        raise HarnessError(
            f"the command template must carry the prompt as one element spelled {PROMPT_SLOT}"
        )
    if template[0] == MEMORY_COMMAND:
        raise HarnessError(
            f"{MEMORY_COMMAND!r} cannot be the harness binary: it is the memory command under "
            "test, and linking it into the shared toolchain would give every arm a store"
        )
    if not name.strip() or not version.strip():
        raise HarnessError("a command harness needs a name and a version")
    checked = _checked_conditions(conditions or {})
    seed_fingerprint = _home_seed_fingerprint(home_seed)

    def build(
        *, model: str, channel: MemoryChannel, runner: Runner, cwd: str, env: Mapping[str, str]
    ) -> LegAgent:
        return CommandAgent(
            harness_name=name,
            argv_template=template,
            model=model,
            channel=channel,
            runner=runner,
            cwd=cwd,
            env=dict(env),
            timeout_s=timeout_s,
        )

    return AgentHarness(
        name=name,
        version=version,
        binary=template[0],
        native_memory=False,
        leg_caller=CALLER_AGENT,
        conditions=checked,
        isolated_home=True,
        home_seed=home_seed,
        home_seed_fingerprint=seed_fingerprint,
        build=build,
    )


@dataclass(frozen=True)
class CommandAgent:
    """An agent spawned as a command. It reports its stdout as the answer and the raw stream,
    and NO tool calls: what the leg did with bd is read from bd's receipts, not from here."""

    harness_name: str
    argv_template: tuple[str, ...]
    model: str
    channel: MemoryChannel
    runner: Runner
    cwd: str
    env: Mapping[str, str]
    timeout_s: float

    def argv_for(self, step: SequenceStep, available_memory: Mapping[str, str]) -> list[str]:
        prompt = build_agent_prompt(step, dict(available_memory), self.channel)
        slots = {PROMPT_SLOT: prompt, MODEL_SLOT: self.model}
        return [slots.get(element, element) for element in self.argv_template]

    def run_step(
        self, step: SequenceStep, available_memory: dict[str, str], ctx: StepContext
    ) -> AgentStepResult:
        completed = run_checked(
            self.argv_for(step, available_memory),
            what=f"{self.harness_name} harness",
            not_found_hint="the harness command must be on the operator's PATH",
            timeout_s=self.timeout_s,
            error=HeadlessAgentError,
            runner=self.runner,
            cwd=self.cwd,
            # Merged over the operator's environment, as the Claude agent's `child_env` is: the
            # cell's PATH and pins win, the rest of the process environment survives.
            env={**os.environ, **self.env},
        )
        text = completed.stdout or ""
        return AgentStepResult(
            final_answer=text.strip(),
            check_results={},
            writes_performed={},
            messages=[TraceMessage(role="user", content=step.user_request)],
            tool_calls=[],
            raw_stream=text,
        )


__all__ = [
    "HARNESS_CLAUDE_CODE",
    "RIG_OWNED_ENV",
    "AgentHarness",
    "CommandAgent",
    "HarnessError",
    "LegAgent",
    "claude_code_harness",
    "command_harness",
    "conditions_fingerprint",
]
