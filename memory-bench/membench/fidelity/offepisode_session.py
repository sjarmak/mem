"""One paid capture session per off-episode packet: does background knowledge intrude?

The campaign observed six of seven failed opportunity captures asserting Python version
history no supplied artifact establishes. That is one episode and one claim shape, and two
readings fit it equally well. Either background knowledge leaks in whenever a durable record
is written, which makes a write-time gate a general intervention -- or that packet's own seed
invited version reasoning because it discusses CPython behaviour, which scopes the finding to
one packet and means the report's generalization has to narrow.

This module buys the difference. It runs the campaign's own capture instruction, unchanged,
over evidence packets from three unrelated technologies whose artifacts state no version at
all, and reports what the agent chose to persist.

**What is mechanical here and what is not.** The scoring below is the pre-registered kind:
each packet's manifest named, before any session was bought, the background claims an agent
might reach for and the token that would betray each one. Checking a persisted record for
those tokens is a string search against a list written down in advance, which is why the
number can be trusted. It is not a judgment about what the record MEANS. The wider question
the bead also asks -- claims with no resolving citation anywhere in the packet -- is a
reading, so this module emits each record verbatim for a person rather than scoring it.
`membench.fidelity.gate` can offer a second, model-driven opinion on the same records; that
is description and not a verdict, and stays so until it can beat a one-token baseline on the
retro set.

**Session hygiene.** Each session gets a fresh sandbox cwd with no agent context above it, a
bead store minted outside that sandbox so a workspace wipe cannot reach it, a run root
checked for the `.beads` hazard that would otherwise silently answer from an ancestor's
workspace, a verified-empty inventory before the agent runs, and an output directory refused
if it already exists. A session is spent once.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from membench.fidelity.offepisode_corpus import EVIDENCE_ARTIFACTS, OffEpisodePacket
from membench.fidelity.token_baseline import VERSION_SHAPE
from membench.runner.headless_agent import HeadlessClaudeAgent, resolve_model
from membench.runner.sandbox import assert_no_bead_store_above, paid_sandbox
from membench.runner.tool_surface import (
    HOST_DENIED_TOOLS,
    MEMORY_ALLOWED_TOOLS,
    MemoryToolSurface,
    endogenous_memory_tool_calls,
    endogenous_memory_verbs,
    harness_call,
    provision_memory_tool,
)
from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep
from membench.spawn import Runner

__all__ = [
    "CAPTURE_INSTRUCTION_NAME",
    "CaptureSession",
    "build_step",
    "load_capture_instruction",
    "persisted_records",
    "run_capture_session",
    "score_intrusion",
    "summarize",
]

CAPTURE_INSTRUCTION_NAME = "CAPTURE_INSTRUCTION.md"

# Used ONLY to hand a reader candidates the pre-registered token list could not have
# anticipated -- never as a verdict. A record that trips this may be quoting a duration or a
# file path, which is why the output field is named `candidates` and nothing downstream sums
# it. Shared with `token_baseline`, whose whole rule this is: the competitor the gate has to
# beat and this reader aid have to be the same pattern, or the comparison is between two
# different things.


@dataclass(frozen=True)
class CaptureSession:
    """The identity of one session, so a result can never be read as another's."""

    packet_id: str
    repeat: int

    @property
    def name(self) -> str:
        return f"{self.packet_id}__r{self.repeat}"


def load_capture_instruction(root: Path) -> str:
    """The frozen capture instruction shared by every packet, read verbatim.

    Checked rather than trusted: an instruction that does not name the evidence files leaves
    the agent with nothing to cite, and the session would measure the prompt's omission
    instead of the agent's behaviour."""
    text = (root / CAPTURE_INSTRUCTION_NAME).read_text()
    if not text.strip():
        raise ValueError(f"{root / CAPTURE_INSTRUCTION_NAME} is empty; there is nothing to run")
    missing = [name for name in EVIDENCE_ARTIFACTS if name not in text]
    if missing:
        raise ValueError(
            f"the capture instruction does not name {', '.join(missing)}, so the agent would "
            "not be pointed at the evidence and the session would measure nothing"
        )
    return text


def build_step(instruction: str, packet_id: str) -> SequenceStep:
    """One step carrying the instruction unchanged.

    Both endogenous flags are True because that is the whole measurement: the harness neither
    retrieves nor records on the agent's behalf, so a persisted record is one the agent
    decided to write. Were either False the harness would perform that half itself, and a
    zero would be a fact about the plumbing rather than about the agent."""
    return SequenceStep(
        step_id=f"offepisode-capture-{packet_id}",
        user_request=instruction,
        available_tools=list(MEMORY_ALLOWED_TOOLS),
        read_is_endogenous=True,
        write_is_endogenous=True,
    )


def _inventory(raw: str) -> dict[str, str]:
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError(f"unexpected bd memory inventory schema: {raw[:200]!r}")
    result = {key: content for key, content in value.items() if key != "schema_version"}
    bad = sorted(key for key, content in result.items() if not isinstance(content, str))
    if bad:
        raise ValueError(f"bd inventory values must be strings; {', '.join(bad)} are not")
    return result


def persisted_records(
    surface: MemoryToolSurface, *, runner: Runner = subprocess.run
) -> dict[str, str]:
    """Every memory in the store, read back through the same shim the agent was given."""
    return _inventory(harness_call(surface, ["memories", "--json"], runner=runner))


def score_intrusion(records: Mapping[str, str], packet: OffEpisodePacket) -> dict[str, Any]:
    """Score persisted records against the lures this packet declared BEFORE the run.

    Two counts, kept apart because the bead asks for both: every declared lure whose betraying
    token appears, and the narrower version/history subset of the same. A lure the packet
    marks `token_detectable: false` has no token that would betray it, so it is listed as
    unscoreable rather than counted absent -- reporting it clean would claim a coverage the
    packet says in writing it does not have."""
    text = "\n".join(records.values())
    folded = text.casefold()

    hits: list[dict[str, Any]] = []
    unscoreable: list[str] = []
    for lure in packet.lures:
        if not lure.token_detectable:
            unscoreable.append(lure.claim)
            continue
        found = [token for token in lure.betraying_literals if token.casefold() in folded]
        if found:
            hits.append({"claim": lure.claim, "kind": lure.kind, "tokens": found})

    declared = {token.casefold() for lure in packet.lures for token in lure.betraying_literals}
    evidence = "\n".join(packet.packet.artifacts.values()).casefold()
    candidates = sorted(
        {
            token
            for token in VERSION_SHAPE.findall(text)
            if token.casefold() not in declared and token.casefold() not in evidence
        }
    )
    return {
        "records": len(records),
        "declared_lures_hit": hits,
        "declared_lures_hit_count": len(hits),
        "version_lures_hit": [hit for hit in hits if hit["kind"] == "version_history"],
        "version_lures_hit_count": sum(1 for hit in hits if hit["kind"] == "version_history"),
        "unscoreable_lures": unscoreable,
        # Reader triage, never a verdict. See `token_baseline.VERSION_SHAPE`.
        "undeclared_version_shaped_candidates": candidates,
    }


def _materialize(packet: OffEpisodePacket, cwd: Path) -> list[str]:
    """Write the packet's hash-verified artifacts into the sandbox under their own names.

    Refuses to overwrite: the sandbox is minted empty, so anything already sitting at one of
    these names means the cwd is not the one this session thinks it is."""
    for name in EVIDENCE_ARTIFACTS:
        destination = cwd / name
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"{destination} already exists in a sandbox that should be empty")
        destination.write_text(packet.packet.artifacts[name])
    return sorted(EVIDENCE_ARTIFACTS)


def run_capture_session(
    packet: OffEpisodePacket,
    *,
    instruction: str,
    session: CaptureSession,
    out: Path,
    work_root: Path,
    model: str,
    timeout_s: float,
    make_runner: Callable[[MemoryToolSurface], Runner],
    paid: bool,
) -> dict[str, Any]:
    """Spend at most one invocation and write every artifact of it under ``out``.

    ``out`` is refused if it exists: a session is bought once, and overwriting one is how a
    failed run quietly becomes a successful one.

    The spawner arrives as a FACTORY over the session's own surface, not as a ready runner,
    because a dry-run simulator has to drive the shim this session just minted and that shim
    does not exist until the session mints it. Production passes ``lambda _: subprocess.run``.
    It takes no default for the reason `HeadlessClaudeAgent.runner` takes none -- "real and
    unrecorded" must never be what a caller gets by omission."""
    if out.exists():
        raise RuntimeError(f"{out} already exists; a session is spent once and never repurchased")
    assert_no_bead_store_above(work_root)
    out.mkdir(parents=True)

    with (
        tempfile.TemporaryDirectory(prefix="offepisode-store-", dir=work_root) as store_root,
        paid_sandbox("offepisode-", parent=work_root) as sandbox,
    ):
        delivered = _materialize(packet, sandbox)
        surface = provision_memory_tool(Path(store_root), sandbox=sandbox)

        before = persisted_records(surface)
        if before:
            raise RuntimeError(
                f"the freshly minted store already holds {sorted(before)}; a record read back "
                "after the session could not be attributed to the agent"
            )

        step = build_step(instruction, packet.packet_id)
        agent = HeadlessClaudeAgent(
            model=model,
            runner=make_runner(surface),
            cwd=str(sandbox),
            env=surface.env(),
            disallowed_tools=HOST_DENIED_TOOLS,
            timeout_s=timeout_s,
        )
        argv = agent.argv_for(step, {})
        ctx = StepContext(
            trial_id="offepisode-capture", session_id=session.name, step_id=step.step_id
        )
        result = agent.run_step(step, {}, ctx)
        after = persisted_records(surface)

    calls = list(result.tool_calls)
    record: dict[str, Any] = {
        "session": session.name,
        "packet_id": packet.packet_id,
        "domain": packet.domain,
        "repeat": session.repeat,
        "paid": paid,
        "model": resolve_model(model),
        "surface_fingerprint": surface.fingerprint(),
        "artifacts_delivered": delivered,
        "endogenous_memory_tool_calls": endogenous_memory_tool_calls(calls),
        "endogenous_memory_verbs": endogenous_memory_verbs(calls),
        "tool_names": [call.name for call in calls],
        "persisted": after,
        "wrote_anything": bool(after),
        "intrusion": score_intrusion(after, packet),
        "final_answer": result.final_answer,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
    (out / "session.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    (out / "argv.json").write_text(json.dumps(argv, indent=2) + "\n")
    (out / "prompt.txt").write_text(instruction)
    (out / "raw.stream.jsonl").write_text(result.raw_stream or "")
    return record


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The reading the bead asks for, over whatever sessions actually ran.

    `sessions_that_wrote` is the denominator that matters. A session that persisted nothing
    cannot show intrusion, and folding it in as a clean result would report the agent's
    silence as evidence of restraint."""
    wrote = [row for row in records if row.get("wrote_anything")]
    per_packet: dict[str, dict[str, int]] = {}
    for row in records:
        bucket = per_packet.setdefault(
            row["packet_id"], {"sessions": 0, "wrote": 0, "intruded": 0, "version": 0}
        )
        bucket["sessions"] += 1
        if not row.get("wrote_anything"):
            continue
        bucket["wrote"] += 1
        if row["intrusion"]["declared_lures_hit_count"]:
            bucket["intruded"] += 1
        if row["intrusion"]["version_lures_hit_count"]:
            bucket["version"] += 1
    return {
        "sessions": len(records),
        "sessions_that_wrote": len(wrote),
        "sessions_with_declared_intrusion": sum(
            1 for row in wrote if row["intrusion"]["declared_lures_hit_count"]
        ),
        "sessions_with_version_intrusion": sum(
            1 for row in wrote if row["intrusion"]["version_lures_hit_count"]
        ),
        "per_packet": per_packet,
    }
