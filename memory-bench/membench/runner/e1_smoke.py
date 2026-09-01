"""mem-5sht9 — the reachability smoke for the memory tool surface.

ONE ``claude -p`` run, through the real ``HeadlessClaudeAgent``, in a neutral sandbox, with a
provisioned ``bd`` shim on ``PATH`` and a pre-seeded memory. It answers exactly one question: can
the evaluated agent CALL the memory tool at all, and does the tool ANSWER? A failure on either is
a HALT for the whole series, not a data point — every downstream endogeneity number would be a
wiring artifact wearing the shape of arXiv 2607.20972's near-zero-voluntary-use finding.

**Two conditions, because one is not enough.** The gate demands a non-zero memory tool call count
AND recovery of the seeded unguessable ``SMOKE_VALUE``. Keying on the count alone (a52bebd) exited
0 on a live run of 7 calls against a shim that exec'd itself until it hung: the agent tried seven
times, nothing came back, and the smoke reported the surface reachable. A count proves the agent
TRIED; only the value coming back through the store proves the surface ANSWERED.

**PAID, and NOT RUN this round.** The real path spends money and is not authorized here. It is
wired and runnable:

    uv run python -m membench.runner.e1_smoke --model <pinned-model> --json

Its result is UNRUN until an operator runs that command. ``--dry-run`` says so in its own ``paid``
field, so nothing downstream can read a simulated stream as a reachability result.

**It is not an endogeneity measurement, and its prompt says so by construction.** The smoke
INSTRUCTS the agent to recall the key. That is the point: instructed use is the upper bound on
reachability, so a zero cannot be blamed on the agent's disposition. E1's ladder, which does
measure choice, must never reuse this prompt.

Free path: ``--dry-run`` drives a simulated runner that emits the stream a real cooperating agent
would, exercising the provisioning, the argv, the parse and the counter for zero tokens. It proves
the plumbing; it cannot prove a real session reaches the tool. Only the paid run does that.

ZFC: plumbing and a mechanical count. No judgment here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from membench.runner.headless_agent import (
    ENV_OAUTH,
    REFUSE_API_KEY_SET,
    REFUSE_UNPINNED_MODEL,
    HeadlessClaudeAgent,
    a_paid_run_carries_the_metered_api_key,
    a_paid_run_needs_a_model,
    assistant_event,
    resolve_model,
    result_event,
    serialize_stream,
)
from membench.runner.sandbox import paid_sandbox
from membench.runner.tool_surface import (
    HOST_DENIED_TOOLS,
    MEMORY_ALLOWED_TOOLS,
    MemoryToolSurface,
    endogenous_memory_tool_calls,
    endogenous_memory_verbs,
    harness_call,
    provision_memory_tool,
    surface_fingerprint,
)
from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep

# The seeded memory the smoke asks for. The VALUE is opaque and unguessable so a recovered answer
# cannot have been produced by the model's priors — the same reason the builtin arm's engagement
# check uses opaque tokens rather than plausible facts.
SMOKE_KEY = "membench-smoke-port"
SMOKE_VALUE = "the staging widget service listens on port 48317"

SMOKE_PROMPT = (
    "A `bd` command-line tool is on your PATH. It stores durable memories: `bd memories` lists "
    f"them, `bd recall <key>` prints one. Recall the memory stored under the key `{SMOKE_KEY}` "
    "and report the port number it names. Do not guess it."
)


def smoke_step() -> SequenceStep:
    return SequenceStep(
        step_id="e1-smoke",
        user_request=SMOKE_PROMPT,
        available_tools=list(MEMORY_ALLOWED_TOOLS),
    )


def simulated_runner(surface: MemoryToolSurface) -> object:
    """A stand-in for ``claude -p`` that behaves like a cooperating agent: it really invokes the
    shim, and emits the ``tool_use`` block a real session would.

    It CALLS the shim rather than faking the output, for the reason ``toolreq_builtin``'s simulator
    writes to the real native-memory layout: a simulator fitted to what the counter happens to
    match proves nothing about the surface. Here the recovered value comes back through the actual
    store."""

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = f"bd recall {SMOKE_KEY}"
        recalled = harness_call(surface, ["recall", SMOKE_KEY]).strip()
        stream = serialize_stream(
            [
                assistant_event([("Bash", {"command": command, "description": "recall"})]),
                result_event(recalled),
            ]
        )
        return subprocess.CompletedProcess(list(argv), 0, stream, "")

    return run


def run_smoke(*, model: str, dry_run: bool, timeout_s: float) -> dict[str, object]:
    """Provision the surface, seed one memory, run one agent step, count the memory tool calls."""
    # Sandbox FIRST: `provision_memory_tool` checks the store against it before `bd init` writes
    # CLAUDE.md/AGENTS.md/.claude anywhere, so a store the wipe would eat, or one that would
    # contaminate the sandbox's ancestry, is refused before it exists.
    with (
        tempfile.TemporaryDirectory(prefix="membench-memory-") as root,
        paid_sandbox("e1-smoke-") as sandbox,
    ):
        surface = provision_memory_tool(Path(root), sandbox=sandbox)
        harness_call(surface, ["remember", SMOKE_VALUE, "--key", SMOKE_KEY])
        runner = simulated_runner(surface) if dry_run else subprocess.run
        agent = HeadlessClaudeAgent(
            model=model,
            runner=runner,  # type: ignore[arg-type]
            cwd=str(sandbox),
            env=surface.env(),
            disallowed_tools=HOST_DENIED_TOOLS,
            timeout_s=timeout_s,
        )
        step = smoke_step()
        ctx = StepContext(trial_id="e1-smoke", session_id="e1-smoke", step_id=step.step_id)
        result = agent.run_step(step, {}, ctx)
    calls = list(result.tool_calls)
    return {
        "dry_run": dry_run,
        # The reachability claim is only ever about a PAID run. False here means the stream came
        # from the simulator, so no downstream gate can read this row as evidence that a real
        # session reached the tool.
        "paid": not dry_run,
        "model": resolve_model(model),
        "surface_fingerprint": surface_fingerprint(),
        "memory_tool_calls": endogenous_memory_tool_calls(calls),
        "memory_verbs": endogenous_memory_verbs(calls),
        "tool_names": [call.name for call in calls],
        "recovered_value": SMOKE_VALUE in (result.final_answer or ""),
        "final_answer": result.final_answer,
    }


def halt_reason(result: Mapping[str, object]) -> str | None:
    """Why this smoke HALTs the series, or ``None`` if the surface is proven both reachable and
    answering.

    TWO conditions, and the second is the one a52bebd was missing. That gate keyed on the call
    count alone and exited 0 on a live run of 7 calls against a shim that exec'd itself until it
    hung: every call was made, none returned, ``recovered_value`` was False, and the smoke reported
    the surface reachable. A count proves the agent TRIED. Only the unguessable ``SMOKE_VALUE``
    coming back through the store proves the surface ANSWERED — a hung shim, a shim pinned to the
    wrong store, and a bd that errors all produce calls and no value."""
    calls = result.get("memory_tool_calls") or 0
    if not calls:
        return "the agent made no memory tool call — the surface is unreachable"
    if not result.get("recovered_value"):
        return (
            f"the agent called the memory tool {calls}x but never recovered the seeded value — "
            "the surface answers nothing (a hung, mis-pinned, or erroring shim looks exactly "
            "like this, and a call count alone cannot tell them from a working one)"
        )
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="", help="pin the CLI model (else MEMBENCH_AGENT_MODEL)")
    parser.add_argument("--dry-run", action="store_true", help="simulate the CLI; spends nothing")
    parser.add_argument("--json", action="store_true", help="emit the result as one JSON object")
    parser.add_argument("--timeout-s", type=float, default=600.0)
    args = parser.parse_args(argv)

    if a_paid_run_carries_the_metered_api_key(dry_run=args.dry_run):
        print(REFUSE_API_KEY_SET, file=sys.stderr)
        return 2
    if a_paid_run_needs_a_model(args.model, dry_run=args.dry_run):
        print(REFUSE_UNPINNED_MODEL, file=sys.stderr)
        return 2

    result = run_smoke(model=args.model, dry_run=args.dry_run, timeout_s=args.timeout_s)
    if args.json:
        print(json.dumps(result))
    else:
        print(
            f"memory_tool_calls={result['memory_tool_calls']} "
            f"verbs={result['memory_verbs']} recovered={result['recovered_value']} "
            f"paid={result['paid']}"
        )
    halt = halt_reason(result)
    if halt is not None:
        print(
            f"HALT: {halt}. Do NOT run E1/E2/E3 on this wiring. "
            f"(OAuth token set: {bool(os.environ.get(ENV_OAUTH))})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
