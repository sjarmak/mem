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

- **The child PATH is the harness `bin_dir` and nothing else** for the arms without a store.
  `MemoryToolSurface.env()` appends the operator's own PATH, which on this machine has a real bd
  on it; a floor arm inheriting that is not a floor. `NoStoreSurface` overrides exactly that.
- **A `bd` that exits 127 is planted anyway.** A floor arm whose bd invocation dies with "command
  not found" and one whose bd invocation dies some other way are different error surfaces, and
  the difference is visible to the agent. 127 is what a missing command gives; the stub makes the
  arms agree on the surface while disagreeing on the store.
- **Settings are written whole, never merged** (mem-nclzl). The hook install merges into them
  afterwards, which is correct and is the opposite direction: it adds `hooks` without disturbing
  the pin. What must never happen is one arm's pin surviving underneath another's.

ZFC: filesystem plumbing and a PATH lookup. No model call, no judgment.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from membench.runner.arm_context import ARM_BEADS
from membench.runner.e1_grid import (
    native_memory_pinned_off,
    pin_precedence_fingerprint,
)
from membench.runner.headless_agent import seed_config_dir
from membench.runner.memory_arm import (
    MemoryArm,
    MemoryArmError,
    arm,
    arm_settings,
    assert_no_memory_command,
    plant_arm_context,
)
from membench.runner.native_memory_hook import install_native_memory_hook
from membench.runner.sandbox import paid_sandbox
from membench.runner.tool_surface import (
    CONFIG_DIR_ENV,
    MEMORY_COMMAND,
    MemoryToolSurface,
    capture_bd_context,
    provision_memory_tool,
)

# What a shell reports for a command it cannot find. The floor arms plant a stub that exits with
# it, so an arm without a store fails the way a missing command fails.
COMMAND_NOT_FOUND = 127

_STUB_BODY = f"""#!/bin/sh
echo "{MEMORY_COMMAND}: command not found" >&2
exit {COMMAND_NOT_FOUND}
"""


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


@dataclass(frozen=True)
class ArmCellStore:
    """One repeat's minted surface for one arm. The two legs of the cell share all of it."""

    arm: MemoryArm
    surface: MemoryToolSurface
    sandbox: Path
    config_dir: Path
    hook_log: Path
    pinned_off: bool
    probe: str
    context_files: tuple[str, ...]
    # Held on the store so the re-plant after the cwd wipe cannot render a DIFFERENT context
    # than the mint did. One planting path for every arm and both legs: `plant_bd_context`
    # writes bd's block without the shared scaffold, so using it for the goal leg would give
    # the bd arm's two legs different presentations and nothing downstream could see it.
    bd_capability: str | None

    def env(self) -> dict[str, str]:
        """What the cell hands the agent. `PWD` is pinned to the sandbox because the agent merges
        this over the operator's environment, whose `PWD` is the checkout the corpus lives in."""
        return {**self.surface.env(), "PWD": str(self.sandbox)}


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
def arm_cell_store(arm_name: str, *, label: str) -> Iterator[ArmCellStore]:
    """Mint one repeat's surface for one arm and tear it down when both legs have run.

    `label` only names the sandbox, so a directory left behind by a crash says which cell it came
    from. Everything that decides what the agent can reach comes from the registry."""
    one = arm(arm_name)
    with (
        tempfile.TemporaryDirectory(prefix="membench-arm-") as root_name,
        paid_sandbox(f"arm-{arm_name}-") as sandbox,
    ):
        root = Path(root_name)
        bd_capability: str | None = None
        surface: MemoryToolSurface
        if one.provisions_bd:
            surface = provision_memory_tool(root, sandbox=sandbox)
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
        if not one.provisions_bd:
            # The stub this mint just planted is the one permitted resolution; anything else on
            # this PATH would give the arm a store it is graded on not having.
            assert_no_memory_command(surface.env(), allow_stub=surface.bin_dir / MEMORY_COMMAND)
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


def child_path_of(env: Mapping[str, str]) -> list[str]:
    """The PATH entries a child of this env would search, for the separation report."""
    return [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]


__all__ = [
    "ARM_BEADS",
    "COMMAND_NOT_FOUND",
    "ArmCellStore",
    "NoStoreSurface",
    "arm_cell_store",
    "child_path_of",
    "replant_context",
]
