"""The three-arm beads grid: minting, and what a mint can be proved to be.

This module owns the per-repeat SURFACE each arm runs on -- the sandbox cwd, the config dir, the
settings, the hook, the PATH, and the planted context -- and nothing about scoring. The fire path
lands beside it; the split is deliberate, because every claim the pre-registration makes about
arm separation is provable from a MINT alone, with no agent spawned and no tokens bought.

Why the ladder's `cell_store` could not simply be called: it provisions bd unconditionally. Two
of the three arms are defined by not having it, and "no bd on this PATH" is the thing they are
graded on. So the bd arm goes through `provision_memory_tool` unchanged, and the other two get a
surface that is the same shape with the store removed.

Three properties the surfaces here must have, each of which has cost this rig a run before:

- **Every arm's child PATH is the same short list, and the operator's own PATH is on none of
  them.** `MemoryToolSurface.env()` appends the operator's PATH, which on this machine has a
  real bd on it; a floor arm inheriting that is not a floor, and `NoStoreSurface` overrides
  exactly that. The bd arm inheriting it is the same defect wearing the other face: the
  separation report caught the bd arm reaching the operator's whole toolchain while the floors
  reached their own `bin_dir` alone, which is a difference in what tooling each arm HAS and
  no gate was watching it. `arm_child_path` gives all three the identical list: the arm's own
  `bin_dir`, a per-cell `toolchain` directory of symlinks to `SHARED_TOOLCHAIN_COMMANDS`, and
  the system directories. The memory command is refused a link there by name, so equalizing
  the toolchain cannot hand a floor arm the store it is graded on not having.
- **A `bd` that exits 127 is planted anyway.** A floor arm whose bd invocation dies with "command
  not found" and one whose bd invocation dies some other way are different error surfaces, and
  the difference is visible to the agent. 127 is what a missing command gives; the stub makes the
  arms agree on the surface while disagreeing on the store.
- **The config dir is re-minted between the legs of a cell**, for every arm whose memory does
  not live in one. `$CLAUDE_CONFIG_DIR/projects/<slug>/*.jsonl` is the session transcript and it
  carries the establish leg's opaque token verbatim, so a goal leg sharing the directory can read
  its predecessor's answer without touching any memory system. The wipe cannot reach it -- it is
  outside the cwd -- and the deny hook only DETECTS the read. `remint_config_dir` removes the
  file. The builtin arm keeps its directory, because for that arm the transcript and the store
  under test are the same directory.
- **Settings are written whole, never merged** (mem-nclzl). The hook install merges into them
  afterwards, which is correct and is the opposite direction: it adds `hooks` without disturbing
  the pin. What must never happen is one arm's pin surviving underneath another's.

ZFC: filesystem plumbing and a PATH lookup. No model call, no judgment.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from membench.harbor.agent_memory import NATIVE_MEMORY_GLOB
from membench.metrics.scorers import states_value
from membench.runner.agent_harness import AgentHarness, claude_code_harness
from membench.runner.arm_context import ARM_BEADS
from membench.runner.bd_receipt_surface import (
    attributed_invocations,
    prepare_receipt_leg,
    read_receipts,
)
from membench.runner.e1_grid import (
    ESTABLISH_INSTRUCTION,
    native_memory_pinned_off,
    pin_precedence_fingerprint,
)
from membench.runner.e1_reliability import observe_calls
from membench.runner.headless_agent import (
    CellCalls,
    Leg,
    MemoryChannel,
    render_cell_calls,
    seed_config_dir,
)
from membench.runner.memory_arm import (
    SHARED_PROTOCOL,
    MemoryArm,
    MemoryArmError,
    arm,
    arm_settings,
    assert_no_memory_command,
    plant_arm_context,
)
from membench.runner.native_memory_hook import hook_reaches, install_native_memory_hook
from membench.runner.realagent_probe import score_goal_action
from membench.runner.sandbox import assert_neutral_ancestry, paid_sandbox
from membench.runner.tool_surface import (
    CONFIG_DIR_ENV,
    MEMORY_COMMAND,
    MemoryToolSurface,
    assert_no_schema_migration,
    capture_bd_context,
    command_segments,
    memory_invocations,
    provision_memory_tool,
)
from membench.runner.toolreq_builtin import wipe_cwd_contents
from membench.runner.toolreq_realagent import ToolReqRealAgentTask
from membench.runtime import StepContext
from membench.schemas.sequence import SequenceStep
from membench.spawn import Runner

# What a shell reports for a command it cannot find. The floor arms plant a stub that exits with
# it, so an arm without a store fails the way a missing command fails.
COMMAND_NOT_FOUND = 127

_STUB_BODY = f"""#!/bin/sh
echo "{MEMORY_COMMAND}: command not found" >&2
exit {COMMAND_NOT_FOUND}
"""


# The tooling every arm reaches, over and above its own `bin_dir` and the harness binary the
# legs spawn by name (`shared_toolchain`). `git` because the corpus is a repository, `dolt`
# because bd's store is one. Identical for all three arms: an arm that could reach a tool another
# could not is an arm difference that is not the memory it has. `bd` is refused by name -- see
# `_link_shared_toolchain`.
SHARED_TOOLCHAIN_COMMANDS: tuple[str, ...] = ("git", "dolt")


def shared_toolchain(harness: AgentHarness) -> tuple[str, ...]:
    """The commands every arm's toolchain links: the shared set plus the harness's own binary."""
    return (*SHARED_TOOLCHAIN_COMMANDS, harness.binary)


def default_harness() -> AgentHarness:
    """The runtime a cell runs on when none is named: Claude Code, its version pinned at fire."""
    return claude_code_harness(version=SHARED_PROTOCOL.cli_version)


# The system directories, on every arm's PATH identically. They hold the ordinary shell utilities
# a `Bash` tool call assumes and no memory tool; `assert_no_memory_command` re-checks that on the
# assembled PATH for the arms without a store rather than trusting this comment.
SHARED_SYSTEM_PATH: tuple[str, ...] = ("/usr/bin", "/bin")


def _link_shared_toolchain(root: Path, commands: Sequence[str]) -> Path:
    """A directory of symlinks to the shared toolchain, minted per cell.

    Symlinks rather than putting the hosting directories on PATH: `dolt` lives beside the real
    `bd` on this machine, so naming its directory would put a working store back on a floor arm's
    PATH by a side door. A link per command names exactly what is shared and nothing adjacent.

    Refuses rather than skipping a missing command. An arm that lost `git` is running a different
    environment than its siblings, and the resulting gap would read as an arm effect."""
    toolchain = root / "toolchain"
    toolchain.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for name in commands:
        if name == MEMORY_COMMAND:
            raise MemoryArmError(
                f"{MEMORY_COMMAND!r} cannot be a shared toolchain command: linking it would give "
                "every arm the store two of them are defined by not having."
            )
        found = shutil.which(name)
        if found is None:
            missing.append(name)
            continue
        link = toolchain / name
        if not link.exists():
            link.symlink_to(found)
    if missing:
        raise MemoryArmError(
            f"the shared toolchain is incomplete on this host: {missing} did not resolve. Every "
            "arm must reach the same tools, so a partial toolchain is refused rather than run."
        )
    return toolchain


def arm_child_path(bin_dir: Path, toolchain: Path) -> str:
    """The one PATH every arm's child searches, in order.

    `bin_dir` FIRST, so the bd arm's store-pinned shim wins and the floor arms' 127 stub wins;
    then the shared toolchain; then the system directories. The operator's own PATH appears
    nowhere, for either kind of arm."""
    entries = [str(bin_dir), str(toolchain)]
    entries.extend(entry for entry in SHARED_SYSTEM_PATH if Path(entry).is_dir())
    return os.pathsep.join(entries)


class NoStoreSurface(MemoryToolSurface):
    """The surface for an arm that has no store: the same object the bd arm hands the agent, with
    the operator's PATH taken back out.

    A subclass rather than a flag on the original, because the original's PATH behaviour is right
    for what it is for -- a shim dir that must win over a real bd still installed on the host --
    and the floor's requirement is the opposite one."""

    def env(self) -> dict[str, str]:
        env = {"PATH": str(self.bin_dir)}
        if self.config_dir is not None:
            env[CONFIG_DIR_ENV] = str(self.config_dir)
        return env

    # `ArmCellStore.env` replaces PATH for EVERY arm, so this override is no longer what keeps
    # the operator's bd off a floor leg. It stays because the surface is handed around on its
    # own -- `assert_no_memory_command(surface.env())` at mint time is one such caller -- and a
    # surface whose bare env leaked the host PATH would make that check meaningless.


@dataclass(frozen=True)
class ArmCellStore:
    """One repeat's minted surface for one arm. The two legs share the sandbox and the store;
    the CONFIG DIR they share only where the arm needs native continuity across them."""

    arm: MemoryArm
    surface: MemoryToolSurface
    sandbox: Path
    config_dir: Path
    hook_log: Path
    pinned_off: bool
    probe: str
    context_files: tuple[str, ...]
    # The tempdir the whole mint lives under, so a fresh per-leg config dir is a sibling of the
    # first rather than a second TemporaryDirectory nobody holds open.
    root: Path
    # The per-cell symlink directory that makes the three arms' reachable tooling identical.
    toolchain: Path
    # Held on the store so the re-plant after the cwd wipe cannot render a DIFFERENT context
    # than the mint did. One planting path for every arm and both legs: `plant_bd_context`
    # writes bd's block without the shared scaffold, so using it for the goal leg would give
    # the bd arm's two legs different presentations and nothing downstream could see it.
    bd_capability: str | None

    def env(self) -> dict[str, str]:
        """What the cell hands the agent.

        PATH is REPLACED, not taken from the surface: `MemoryToolSurface.env()` appends the
        operator's own PATH, and an arm that reached the operator's toolchain while another
        reached only its own `bin_dir` differs in what tools it HAS. Every arm gets
        `arm_child_path`, identically.

        `PWD` is pinned to the sandbox because the agent merges this over the operator's
        environment, whose `PWD` is the checkout the corpus lives in."""
        return {
            **self.surface.env(),
            "PATH": arm_child_path(self.surface.bin_dir, self.toolchain),
            "PWD": str(self.sandbox),
        }


def _plant_missing_command(bin_dir: Path) -> Path:
    stub = bin_dir / MEMORY_COMMAND
    stub.write_text(_STUB_BODY, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return stub


def _no_store_surface(root: Path) -> NoStoreSurface:
    bin_dir = root / "bin"
    config_dir = root / "config"
    bin_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    _plant_missing_command(bin_dir)
    return NoStoreSurface(
        store_dir=root / "store",
        bin_dir=bin_dir,
        # No bd is wrapped, and saying so is the point: a path here would name a binary this arm
        # is defined by not having.
        bd_binary="",
        config_dir=config_dir,
    )


@contextmanager
def arm_cell_store(
    arm_name: str,
    *,
    label: str,
    bd_binary: str | None = None,
    harness: AgentHarness | None = None,
) -> Iterator[ArmCellStore]:
    """Mint one repeat's surface for one arm and tear it down when both legs have run.

    `label` only names the sandbox, so a directory left behind by a crash says which cell it came
    from. Everything that decides what the agent can reach comes from the registry.

    `bd_binary` is the PINNED build the turn is measuring (`--bd-ref`), and it has to arrive here
    rather than being read off PATH in `provision_memory_tool`: the fire records the build in the
    artifact's resume identity, so a cell that mints against the ambient binary instead would
    publish an identity naming a build it never ran. `None` means the ambient bd, which is what
    the fixtures and a flagless local run want.

    `harness` is the runtime the legs will be spawned on. It decides two things here: which
    binary the shared toolchain links, and whether the comparator arm can be minted at all. The
    builtin arm IS the runtime's native memory; on a runtime that has none the arm names nothing,
    and a cell that ran it would publish the floor under the comparator's name."""
    one = arm(arm_name)
    on = harness if harness is not None else default_harness()
    if carries_native_memory(one) and not on.native_memory:
        raise MemoryArmError(
            f"{arm_name}: this arm is the runtime's own native memory, and the {on.name!r} "
            "harness has none; on it a turn is the treatment against the floor"
        )
    with (
        tempfile.TemporaryDirectory(prefix="membench-arm-") as root_name,
        paid_sandbox(f"arm-{arm_name}-") as sandbox,
    ):
        root = Path(root_name)
        bd_capability: str | None = None
        surface: MemoryToolSurface
        if one.provisions_bd:
            surface = provision_memory_tool(root, sandbox=sandbox, bd_binary=bd_binary)
            captured = capture_bd_context(surface.store_dir)
            # The bd arm's capability paragraph IS what bd shipped, plus the addendum that
            # `plant_bd_context` appends; rendering it through `arm_context` keeps the shared
            # scaffold on it, which is what makes the three arms comparable as presentations.
            bd_capability = "\n\n".join(text.strip() for text in sorted(captured.values()))
            if not bd_capability.strip():
                bd_capability = _captured_from_surface(surface)
        else:
            surface = _no_store_surface(root)
        config_dir = surface.config_dir
        if config_dir is None:
            raise MemoryArmError(
                f"{arm_name}: the provisioned surface pins no config dir, so this cell cannot say "
                "what the agent's own memory system was doing while it ran."
            )
        # Whole, not merged: this is the arm's entire intended settings content.
        seed_config_dir(config_dir, arm_settings(arm_name))
        # After the seed and merging into it: the hook is an instrument on top of whatever the arm
        # pinned, and an install that replaced the file would drop the pin.
        hook_log = install_native_memory_hook(config_dir, mode=one.hook_mode)
        toolchain = _link_shared_toolchain(root, shared_toolchain(on))
        # The file this mint just planted at `bin_dir/bd` is the ONE permitted resolution, for
        # every arm, and the reason differs by arm. On an arm without bd that file is the stub
        # that exits 127, and anything else on the PATH would give it a store it is graded on
        # not having. On the TREATMENT arm that file is the shim, whose whole body is
        # `exec <pinned bd> -C <minted store>`, and anything else on the PATH is a bd that
        # reaches a DIFFERENT store with an UNPINNED build -- which is the same loss of
        # attribution, arriving from the other side. Checked against the ASSEMBLED child PATH,
        # toolchain and system directories included, because that is what the agent's shell will
        # search -- checking `surface.env()` alone would pass while a `bd` sat in `/usr/bin`.
        assert_no_memory_command(
            {"PATH": arm_child_path(surface.bin_dir, toolchain)},
            allow_stub=surface.bin_dir / MEMORY_COMMAND,
        )
        planted = plant_arm_context(sandbox, arm_name, bd_capability=bd_capability)
        yield ArmCellStore(
            arm=one,
            surface=surface,
            sandbox=sandbox,
            config_dir=config_dir,
            hook_log=hook_log,
            pinned_off=native_memory_pinned_off(config_dir),
            probe=pin_precedence_fingerprint(cwd=sandbox),
            context_files=planted,
            root=root,
            toolchain=toolchain,
            bd_capability=bd_capability,
        )


def _captured_from_surface(surface: MemoryToolSurface) -> str:
    """The capture `provision_memory_tool` already took, for the case where the scrub has run and
    the files are gone from the store. Refuses rather than planting nothing: a bd arm whose
    capability paragraph went missing is the indistinguishable null this rig keeps refusing."""
    if not surface.bd_context:
        raise MemoryArmError(
            "no bd deployment context was captured, so the beads arm has no capability paragraph "
            "and cannot be rendered; see `tool_surface.capture_bd_context`."
        )
    return "\n\n".join(text.strip() for text in sorted(surface.bd_context.values()))


def replant_context(store: ArmCellStore) -> tuple[str, ...]:
    """Re-plant the arm's context after the between-legs cwd wipe.

    The wipe is indiscriminate by design -- it closes the leg-to-leg scavenge channel -- and eats
    the context with everything else. A goal leg that lost it is running a different arm than the
    establish leg it is paired with, and the pair silently means nothing.

    Renders from the store's OWN captured paragraph, so the goal leg's context is byte-identical
    to the establish leg's by construction rather than by two call sites agreeing."""
    return plant_arm_context(store.sandbox, store.arm.name, bd_capability=store.bd_capability)


def carries_native_memory(one: MemoryArm) -> bool:
    """Whether this arm's OWN store lives in the config dir, and so must survive between legs.

    The same predicate `engagement_of` grades the comparator on, named once: the builtin arm IS
    the CLI's native memory, so its config dir is the thing under test and re-minting it between
    legs would delete the store mid-cell and publish the deletion as a disposition."""
    return one.settings.get("autoMemoryEnabled") is True


def remint_config_dir(store: ArmCellStore, *, leg: int) -> ArmCellStore:
    """A FRESH config dir for the next leg, for every arm whose memory does not live in one.

    Gate 5's structural half. `$CLAUDE_CONFIG_DIR/projects/<slug>/*.jsonl` is the session
    transcript, and it carries the establish leg's opaque token verbatim; a goal leg pointed at
    the same config dir can read its predecessor's answer without touching any memory system.
    The deny hook DETECTS that read, and detection alone would make the gate a promise that the
    recognizer is complete. Minting a new directory removes the file instead, and the hook stays
    as the instrument that says whether anything reached for it.

    Returns a new store; the caller runs the next leg on it. The builtin arm is returned
    unchanged (`carries_native_memory`), because for it the transcript's directory and the
    memory under test are the same directory."""
    if carries_native_memory(store.arm):
        return store
    config_dir = store.root / f"config-leg{leg}"
    if config_dir.exists():
        raise MemoryArmError(
            f"{store.arm.name}: {config_dir} already exists, so this leg would inherit a config "
            "dir another leg wrote -- which is the transcript channel the fresh mint closes."
        )
    config_dir.mkdir(parents=True)
    seed_config_dir(config_dir, arm_settings(store.arm.name))
    hook_log = install_native_memory_hook(config_dir, mode=store.arm.hook_mode)
    return replace(
        store,
        surface=replace(store.surface, config_dir=config_dir),
        config_dir=config_dir,
        hook_log=hook_log,
        pinned_off=native_memory_pinned_off(config_dir),
        probe=pin_precedence_fingerprint(cwd=store.sandbox),
    )


def child_path_of(env: Mapping[str, str]) -> list[str]:
    """The PATH entries a child of this env would search, for the separation report."""
    return [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]


# ---------------------------------------------------------------------------------------
# the fire path
#
# One definition of the two legs, one definition of engagement, one definition of the cell.
# The three arms differ in the surface minted above and in NOTHING here: the same instruction,
# the same goal step, the same scorer, the same order. Anything an arm needs that this path
# cannot express belongs in the registry, where `assert_arms_comparable` can see it.


# The two protocols a cell can be bought under, and the number of legs each buys.
#
# `three-arm` is `docs/prereg-beads-three-arm.md`: establish, then a scored goal leg, and the
# endpoint is the goal leg's pass. `capture` is `docs/prereg-beads-capture.md`: the establish leg
# ALONE, and the endpoint is whether the arm reached for its memory and whether the store then
# held the fact. A capture cell has no goal leg, so it has no pass and never carries one.
#
# Named on the cell and carried into the artifact because the two are not poolable in either
# direction. A capture row pooled into a three-arm grid would enter every rate as a scored zero
# on a leg it never bought; a three-arm row pooled into a capture grid would report a reach rate
# over cells whose establish leg ran under a different registration.
PROTOCOL_THREE_ARM = "three-arm"
PROTOCOL_CAPTURE = "capture"
LEGS_BY_PROTOCOL: Mapping[str, int] = {PROTOCOL_THREE_ARM: 2, PROTOCOL_CAPTURE: 1}


def legs_for(protocol: str) -> int:
    """How many legs ``protocol`` buys. An unknown protocol raises rather than defaulting to the
    pair: a run that cannot say how many legs it buys cannot be priced, and the price is what the
    spend is authorized against."""
    if protocol not in LEGS_BY_PROTOCOL:
        raise ArmProtocolError(
            f"no leg count registered for protocol {protocol!r}; known: "
            f"{sorted(LEGS_BY_PROTOCOL)}"
        )
    return LEGS_BY_PROTOCOL[protocol]


@dataclass(frozen=True)
class ArmCell:
    """One (arm, work_id) cell: what its legs did and what it is allowed to claim.

    Under `PROTOCOL_THREE_ARM` that is a pair of legs and `passed` is the endpoint. Under
    `PROTOCOL_CAPTURE` it is the establish leg alone, `passed` is False because no goal leg was
    bought, and the endpoint is read off `engaged` and the establish-leg instrumentation."""

    arm: str
    work_id: str
    # Carried on the cell, not re-derived by the driver from a task it looked up again: the grid
    # is keyed `(arm, variant, work_id, repeat)`, and a key one half of the rig reconstructs is a
    # key that can disagree with the one the money was spent under.
    variant: str
    repeat: int
    passed: bool
    engaged: bool
    leaked: bool
    establish_tool_names: tuple[str, ...]
    endogenous_verbs: tuple[str, ...]
    # bd's OWN answer per leg (`e1_reliability.observe_calls`), not the argv verb: the discovery
    # gate asks whether a write was acknowledged on the establish leg and a read returned payload
    # on the goal leg, and an argv that spelled `bd remember` tells neither.
    establish_outcomes: tuple[str, ...]
    goal_outcomes: tuple[str, ...]
    native_reaches: int
    pinned_off: bool
    # Whether real money bought this cell. Carried per cell, not inferred from the artifact it
    # lands in: a dry-run cell resumed into a paid grid publishes a simulation as a measurement,
    # and the field that refuses that has to travel with the cell.
    paid: bool
    status: str
    detail: str = ""
    # Absolute paths the establish leg touched that the between-leg wipe cannot reach, as
    # operands, deduplicated and sorted. DIAGNOSTIC ONLY: nothing in the plan gates or voids on
    # it, because an out-of-sandbox write detector is named in prereg §9 as a mitigation this
    # run does not buy, and adding a void rule after the fact is the re-thresholding §4 forbids.
    #
    # It is here because the establish leg is now ASKED to record (mem-q34kw) and the floor arm
    # has no store to record INTO. `/tmp` and `$HOME` stay writable to all three arms, so the
    # instruction that fixes the beads arm's silence is the same instruction that could hand the
    # floor a durable channel. If that happens the necessity gate (§4.1, none-arm success < 0.2)
    # catches it and voids the run — but only after the money is spent. This field is what lets
    # the one-task pilot see it for the price of the pilot.
    establish_out_of_sandbox_operands: tuple[str, ...] = ()
    # What the GOAL leg called, sorted and deduplicated like `establish_tool_names`. Added after
    # the re-fired one-task pilot (mem-0wpq8.1) scored 0/18 on the goal leg — the comparator
    # included, with its store provably engaged — and the cell carried nothing about what that
    # leg did. DIAGNOSTIC ONLY: no gate reads it. Defaulted so `_unmeasured` need not name it,
    # but a persisted row must carry it (`cell_from_row`), so a pre-instrumentation cell cannot
    # be resumed into an instrumented grid as if it had been read.
    goal_tool_names: tuple[str, ...] = ()
    # Which registration bought this cell, and how many legs it therefore ran. Defaulted so the
    # three-arm construction sites need not repeat themselves, but REQUIRED on a persisted row
    # (`cell_from_row`): a row bought before the capture protocol existed cannot say which of the
    # two it is, and guessing would be the pooling these fields exist to make unrepresentable.
    protocol: str = PROTOCOL_THREE_ARM
    legs: int = 2
    # How many times the establish leg executed bd, counted from bd's OWN receipts
    # (`bd_receipt_surface.attributed_invocations`), not from the runtime's transcript. This is
    # what the capture endpoint reads for an arm with a store, on every harness alike; the
    # stream-derived `endogenous_verbs` stays as diagnostics for the runtimes that emit one.
    # Zero on the arms without a store, by construction. Defaulted so `_unmeasured` need not name
    # it; required on a persisted row (`cell_from_row`).
    bd_invocations: int = 0


# The recording clause, and the establish instruction that carries it. IDENTICAL on all three
# arms, which is what keeps it from being a treatment: it names no mechanism, so beads hears it
# with `bd` on PATH, builtin hears it with its own native memory on, and the floor hears it with
# neither, and what each arm then does with it is the measurement.
#
# It is the ladder's topmost clause (`e1_grid.RUNG_CLAUSES[4]`, "After you act, record any durable
# fact you established so a later session can recall it") RE-POINTED at what this leg holds.
# The ladder's wording assumes the agent acted and established something; the establish leg acts
# on nothing, it is TOLD the state and asked to acknowledge it, so read verbatim the clause asks
# for a recording of the empty set and licenses the very no-op it is here to prevent. Same verb,
# same stated purpose, different object.
#
# Not `RUNG_TEXT[4]` either: rung text is cumulative, and its lower clauses assert that a
# persistent memory tool is available in this session — true of the beads arm, false of the floor
# arm whose `bd` exits 127 by construction. An instruction that lies to one arm is not the same
# instruction.
#
# It used to be the silent opener alone, on the argument that saying "remember this" is itself a
# treatment. The one-task pilot (2026-09-21, mem-q34kw) priced that argument: the beads arm's
# establish leg made ZERO tool calls and answered in prose, having been handed bd's own 1,338-word
# shipped capability text as CLAUDE.md and done nothing with it. Three earlier campaigns reached
# the same place, 0 writes over 480 legs and over 160. With nothing written, the beads arm and the
# floor arm are the same machine, and the grid would have bought 1,152 sessions to measure a
# difference arithmetic forbids.
#
# What the grid can no longer answer is whether an agent reaches for memory UNPROMPTED. R0
# measured exactly that, four times, and the answer is on record. Scoring stays on the goal leg
# alone (mem-eg850: pooling the instructed establish leg halved a +0.425 effect to +0.2125), so
# the instruction cannot pay itself.
RECORD_CLAUSE = "Record what you have been told above, so a later session can recall it."
ARM_ESTABLISH_INSTRUCTION = f"{ESTABLISH_INSTRUCTION} {RECORD_CLAUSE}"


class ArmProtocolError(RuntimeError):
    """A leg was rendered with an allowlist the shared protocol does not declare."""


def assert_goal_allowlist_is_the_protocol(tools: Collection[str]) -> None:
    """Refuse a goal leg whose allowlist is not `SHARED_PROTOCOL.goal_tools`, exactly.

    The gate the pilot needed and did not have. `SHARED_PROTOCOL.goal_tools` named four tools and
    every goal step in the corpus named one, `Write`; `arm_cell_legs` passed the corpus step
    through untouched, so the scored leg ran `--allowedTools Write` on all three arms. `bd` is
    reachable only through `Bash`, so the beads arm's store had no channel into the step it is
    graded on, and only the comparator — whose memory is injected without a tool call — could
    deliver anything at all. Nothing compared the declaration against the render, and the
    separation report printed the argv without judging it.

    Equality, not containment: the corpus was a strict SUBSET of the declaration and that is the
    shape that failed. A subset check would have passed it."""
    named = tuple(tools)
    if named != SHARED_PROTOCOL.goal_tools:
        raise ArmProtocolError(
            f"the goal leg would run --allowedTools {list(named)}, and the shared protocol "
            f"declares {list(SHARED_PROTOCOL.goal_tools)}. An arm whose store is reachable only "
            "through a tool the scored leg does not allow cannot deliver, and the contrast it "
            "carries is zero before the agent is consulted."
        )


def arm_cell_legs(task: ToolReqRealAgentTask, arm_name: str) -> tuple[Leg, Leg]:
    """The two calls one (arm, work_id) cell makes, in order.

    The establish instruction is `ARM_ESTABLISH_INSTRUCTION`, the same words for every arm; the
    block above it says why it is no longer the silent one. The establish leg's allowlist is the
    arm's (the comparator runs unclamped, or the clamp blocks the CLI's own memory-write path and
    the arm measures the clamp).

    The GOAL leg is `task.goal_step` with its allowlist REPLACED by `SHARED_PROTOCOL.goal_tools`.
    The protocol is the authority there and the corpus is not: the corpus authored one tool, the
    protocol declares four, and the silent disagreement is what mem-q34kw cost. Overriding rather
    than refusing keeps one derivation of the allowlist instead of two that must be kept equal by
    hand, and `assert_goal_allowlist_is_the_protocol` is what checks the render before a spend."""
    one = arm(arm_name)
    establish = SequenceStep(
        step_id=f"{task.work_id}-establish",
        user_request=ARM_ESTABLISH_INSTRUCTION,
        available_tools=list(one.establish_tools),
    )
    goal = task.goal_step.model_copy(update={"available_tools": list(SHARED_PROTOCOL.goal_tools)})
    assert_goal_allowlist_is_the_protocol(goal.available_tools)
    return (
        Leg("establish", establish, dict(task.oracle_memory)),
        # BARE. With the cwd emptied, the arm's own store is the only channel left that can
        # carry the value into the goal call. That empty dict IS each arm's hypothesis.
        Leg("goal", goal, {}),
    )


def arm_cell_calls(
    task: ToolReqRealAgentTask, arm_name: str, channel: MemoryChannel, *, model: str
) -> CellCalls:
    """The command lines one cell WILL spawn, rendered from the legs it executes."""
    return render_cell_calls(
        arm=arm_name, channel=channel, legs=arm_cell_legs(task, arm_name), model=model
    )


def engagement_of(
    store: ArmCellStore, tokens: Collection[str], *, receipts: tuple[dict[str, Any], ...] = ()
) -> bool:
    """Whether the establish leg put the current value into the arm's OWN store.

    Content, never file existence: the CLI scaffolds an empty `memory/` regardless, and bd writes
    a receipt for a call that stored nothing (mem-bd-remember-list-is-not-a-write -- a verb token
    is not an operation). Each arm is asked about the store it actually has, and the floor arm is
    asked nothing, because it has none: its engagement is False by construction, which is what
    makes a floor PASS a leak to be explained rather than a win to be reported.

    `receipts` are the establish leg's own execution receipts, read from the wrapper the bd arm
    installs; they are the only observation of what bd was handed and what it answered."""
    if not tokens:
        return False
    one = store.arm
    if one.provisions_bd:
        return any(
            states_value(json.dumps(row, sort_keys=True), token)
            for row in receipts
            for token in tokens
        )
    if one.settings.get("autoMemoryEnabled") is True:
        for memory_file in store.config_dir.glob(NATIVE_MEMORY_GLOB):
            try:
                content = memory_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if any(states_value(content, token) for token in tokens):
                return True
    return False


def out_of_sandbox_operands(calls: Sequence[Any], *, sandbox: Path) -> tuple[str, ...]:
    """Absolute path operands a leg named that do not resolve under `sandbox`.

    Structural, not semantic (the ZFC line): an operand is a token the agent literally passed,
    and "under the sandbox" is a path comparison. No judgment about what the agent MEANT by it,
    and no verdict attached — the caller records the strings and a person reads them.

    A `~` token is reported unexpanded, because the shell would have expanded it against a HOME
    the sandbox does not own, which is the case worth seeing."""
    sandbox = sandbox.resolve()
    found: set[str] = set()
    for call in calls:
        arguments = getattr(call, "arguments", {}) or {}
        operands: list[str] = [
            str(arguments[key])
            for key in ("file_path", "path", "notebook_path")
            if arguments.get(key)
        ]
        command = arguments.get("command")
        if isinstance(command, str):
            operands += [
                token
                for segment in command_segments(command)
                for token in segment
                if token.startswith("/") or token.startswith("~")
            ]
        for operand in operands:
            if operand.startswith("~"):
                found.add(operand)
                continue
            try:
                resolved = Path(operand).resolve()
            except (OSError, ValueError):
                continue
            if resolved != sandbox and sandbox not in resolved.parents:
                found.add(operand)
    return tuple(sorted(found))


def run_arm_cell(
    task: ToolReqRealAgentTask,
    arm_name: str,
    *,
    repeat: int,
    model: str,
    channel: MemoryChannel,
    runner: Runner,
    keep_stream: Callable[[str, str], None] | None = None,
    bd_binary: str | None = None,
    protocol: str = PROTOCOL_THREE_ARM,
    harness: AgentHarness | None = None,
) -> ArmCell:
    """Run ONE (arm, work_id) repeat: mint, establish, close the cwd, goal, score.

    `harness` is the runtime that spawns each leg (`agent_harness`); `None` is Claude Code. The
    mint, the legs, the receipts and the scoring are the same whichever runtime it is; what the
    harness owns is the spawn itself and the environment the leg is spawned under.

    Under `PROTOCOL_CAPTURE` the cell stops after the establish leg and its engagement check, and
    never mints the goal leg. The whole difference between the two protocols is expressed as this
    one argument rather than as a second copy of the mint: the establish leg a capture turn reads
    has to be the SAME leg, on the same surface, that the three-arm grid runs, or the capture rate
    would describe a machine the contrast protocol does not use.

    `keep_stream(leg, raw_stream)` is called once per leg, in leg order, with the agent's verbatim
    stream-json, BEFORE the cell is scored. The mint is torn down when this returns and the
    config dir with it, so a caller that wants to read a leg after the fact gets exactly one
    chance to keep it. An empty string is a stand-in runner's honest absence, passed through as
    such: the caller decides whether an absence is written down.

    `runner` has no default for the reason `cell_agent`'s has none: a leg must not reach the paid
    CLI because a caller left an argument out. The order here is the ladder's and the builtin
    arm's, unchanged -- the wipe lands between the legs (it closes the cwd scavenge channel the
    unclamped establish leg opens), the ancestor guard re-runs after it because the wipe cannot
    reach one directory up, and the context is re-planted because the wipe ate it."""
    on = harness if harness is not None else default_harness()
    with arm_cell_store(
        arm_name, label=f"{arm_name}-{task.work_id}-{repeat}", bd_binary=bd_binary, harness=on
    ) as store:
        establish_leg, goal_leg = arm_cell_legs(task, arm_name)

        def _ctx(leg: Leg) -> StepContext:
            return StepContext(
                trial_id=f"{arm_name}-{channel.value}-{repeat}-{leg.name}",
                session_id=f"{arm_name}-{channel.value}-{repeat}",
                step_id=leg.step.step_id,
            )

        def _agent(cell_store: ArmCellStore, leg: Leg, receipts_path: Path | None) -> Any:
            # One agent per LEG, not one per cell: the goal leg may run on a freshly minted
            # config dir, and an agent built once would hand it the establish leg's. The leg's
            # receipt attribution rides in the environment, keyed the way the Claude hook keys
            # it (the receipt file's stem), so a call booked by either route lands in one file.
            ctx = _ctx(leg)
            leg_id = receipts_path.stem if receipts_path is not None else leg.name
            return on.agent(
                model=model,
                channel=channel,
                runner=runner,
                cwd=str(cell_store.sandbox),
                env=on.leg_env(cell_store.env(), leg_id=leg_id, session_id=ctx.session_id),
            )

        instrumented = store.arm.provisions_bd
        establish_receipts_path = (
            prepare_receipt_leg(store.surface, leg=1) if instrumented else None
        )
        establish = _agent(store, establish_leg, establish_receipts_path).run_step(
            establish_leg.step, dict(establish_leg.memory), _ctx(establish_leg)
        )
        if keep_stream is not None:
            keep_stream(establish_leg.name, establish.raw_stream)
        # Inside the `with`, while the sandbox still exists: resolving an operand against a
        # tempdir that has already been removed compares a different string.
        establish_outside = out_of_sandbox_operands(establish.tool_calls, sandbox=store.sandbox)
        receipts = read_receipts(establish_receipts_path) if establish_receipts_path else ()
        engaged = engagement_of(store, task.current_opaque_values, receipts=receipts)
        invocations = attributed_invocations(receipts)

        if legs_for(protocol) == 1:
            # Capture stops here. `native_reaches` is counted off the establish mint alone
            # because it is the only mint this cell made, and the goal-leg fields stay empty
            # rather than being filled with a stand-in: a capture cell did not buy that leg and
            # must not carry anything that reads as if it had.
            assert_no_schema_migration(establish.tool_calls)
            return ArmCell(
                arm=arm_name,
                work_id=task.work_id,
                variant=task.variant,
                repeat=repeat,
                passed=False,
                engaged=engaged,
                leaked=False,
                establish_tool_names=tuple(sorted({call.name for call in establish.tool_calls})),
                endogenous_verbs=tuple(
                    invocation.verb for invocation in memory_invocations(establish.tool_calls)
                ),
                establish_out_of_sandbox_operands=establish_outside,
                establish_outcomes=tuple(
                    observation.outcome for observation in observe_calls(task, establish.tool_calls)
                ),
                goal_outcomes=(),
                native_reaches=len(hook_reaches(store.hook_log)),
                pinned_off=store.pinned_off,
                paid=runner is subprocess.run,
                status="ok",
                goal_tool_names=(),
                protocol=protocol,
                legs=1,
                bd_invocations=invocations,
            )

        wipe_cwd_contents(store.sandbox)
        assert_neutral_ancestry(store.sandbox)
        replant_context(store)
        # Gate 5's structural close, between the legs and after the wipe: the wipe cannot reach
        # the config dir, which is where the establish leg's transcript sits.
        goal_store = remint_config_dir(store, leg=2)

        goal_receipts_path = (
            prepare_receipt_leg(goal_store.surface, leg=2) if instrumented else None
        )
        goal = _agent(goal_store, goal_leg, goal_receipts_path).run_step(
            goal_leg.step, dict(goal_leg.memory), _ctx(goal_leg)
        )
        if keep_stream is not None:
            keep_stream(goal_leg.name, goal.raw_stream)

        # Both legs' logs, because a cell that re-mints has two and a reach on either is a reach
        # by this cell. Counting one would report the re-minting arms as reaching less often
        # than they did, which is the direction that flatters the gate.
        reaches = sum(
            len(hook_reaches(log)) for log in dict.fromkeys((store.hook_log, goal_store.hook_log))
        )
        pinned_off = store.pinned_off and goal_store.pinned_off

    passed = score_goal_action(
        goal_leg.step, tool_calls=goal.tool_calls, final_answer=goal.final_answer
    )
    assert_no_schema_migration([*establish.tool_calls, *goal.tool_calls])
    verbs = tuple(
        invocation.verb
        for invocation in memory_invocations([*establish.tool_calls, *goal.tool_calls])
    )
    return ArmCell(
        arm=arm_name,
        work_id=task.work_id,
        variant=task.variant,
        repeat=repeat,
        passed=passed,
        engaged=engaged,
        # A pass the arm's own store cannot account for. Never a win for any arm, and for the
        # floor it is the only way a pass can happen at all.
        leaked=passed and not engaged,
        establish_tool_names=tuple(sorted({call.name for call in establish.tool_calls})),
        endogenous_verbs=verbs,
        establish_out_of_sandbox_operands=establish_outside,
        establish_outcomes=tuple(
            observation.outcome for observation in observe_calls(task, establish.tool_calls)
        ),
        goal_outcomes=tuple(
            observation.outcome for observation in observe_calls(task, goal.tool_calls)
        ),
        native_reaches=reaches,
        pinned_off=pinned_off,
        # `subprocess.run` IS the paid spawn; every other runner is a stand-in. Asked of the
        # runner the legs actually went through, so a caller cannot label a cell paid.
        paid=runner is subprocess.run,
        status="ok",
        goal_tool_names=tuple(sorted({call.name for call in goal.tool_calls})),
        protocol=protocol,
        legs=2,
        bd_invocations=invocations,
    )


__all__ = [
    "ARM_BEADS",
    "ARM_ESTABLISH_INSTRUCTION",
    "COMMAND_NOT_FOUND",
    "LEGS_BY_PROTOCOL",
    "PROTOCOL_CAPTURE",
    "PROTOCOL_THREE_ARM",
    "RECORD_CLAUSE",
    "ArmCell",
    "ArmCellStore",
    "ArmProtocolError",
    "NoStoreSurface",
    "arm_cell_calls",
    "arm_cell_legs",
    "arm_cell_store",
    "arm_child_path",
    "assert_goal_allowlist_is_the_protocol",
    "carries_native_memory",
    "child_path_of",
    "default_harness",
    "engagement_of",
    "legs_for",
    "out_of_sandbox_operands",
    "remint_config_dir",
    "replant_context",
    "run_arm_cell",
    "shared_toolchain",
]
