"""The three arms of the beads grid, as one frozen registry.

The subject under test is the beads memory system. `beads` is the treatment, `none` is the
floor, `builtin` is the comparator -- the agent's own native memory, which is what a reader
will ask about the moment beads beats nothing.

The registry exists so the may-vary / may-not-vary split is a STRUCTURE rather than a habit.
An arm may differ in: the store it has, the settings its config dir is seeded with, the hook
mode, the capability paragraph, and the tools the ESTABLISH leg is allowed. It may not differ
in: the corpus, the GOAL leg's tool allowlist, the scorer, the model, the CLI version, the
timeout, or the sandbox policy. Those seven live on one shared record that all three arms hold,
and `assert_arms_comparable` refuses a registry whose arms do not hold the same one. A
difference in any of them is an arm effect with another name.

Settings are SCRUBBED, never merged (mem-nclzl). A dict merge can only ADD keys, so seeding the
builtin arm after the floor arm would leave `autoMemoryEnabled: false` sitting under the
builtin arm's `true` if the floor's dict were merged in first; `arm_settings` returns the arm's
whole intended content and the seeder writes exactly that.

`assert_no_memory_command` is the floor and comparator arms' own gate: it RESOLVES the memory
command under the leg's effective PATH and refuses the fire if it resolves at all. Refuses,
never warns -- a floor leg that could reach bd is not a floor, and the resulting rate would be
published as one.

ZFC: a registry, a digest, a PATH lookup. No model call, no judgment.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from membench.runner.arm_context import (
    ARM_BEADS,
    ARM_CONTEXT_BUILTIN,
    ARM_CONTEXT_NONE,
    context_words,
    render_arm_context,
)
from membench.runner.e1_grid import RUNG_SETTINGS
from membench.runner.resume_cache import digest
from membench.runner.tool_surface import (
    BD_CONTEXT_FILES,
    MEMORY_COMMAND,
    MEMORY_VERBS,
    NATIVE_MEMORY_HOOK_MODE_DENY,
    NATIVE_MEMORY_HOOK_MODE_OBSERVE,
)
from membench.runner.toolreq_builtin import BUILTIN_SETTINGS

ARM_NONE = "none"
ARM_BUILTIN = "builtin"


class MemoryArmError(RuntimeError):
    """An arm that cannot be run as specified, or a registry that is not a comparison."""


@dataclass(frozen=True)
class SharedProtocol:
    """Everything the arms are NOT allowed to differ in. One instance, held by all three."""

    corpus: str
    goal_tools: tuple[str, ...]
    scorer: str
    model: str
    cli_version: str
    timeout_s: int
    sandbox_policy: str


@dataclass(frozen=True)
class MemoryArm:
    """One arm. `capability_paragraph` is None for the arm whose paragraph is CAPTURED from what
    `bd init` shipped rather than authored here (see `arm_context`)."""

    name: str
    settings: Mapping[str, object]
    provisions_bd: bool
    establish_tools: tuple[str, ...]
    capability_paragraph: str | None
    hook_mode: str
    store_seed: bool
    verbs: tuple[str, ...]
    shared: SharedProtocol


# The one protocol the three arms share. Named values, so a change to any of them moves the
# fingerprint and cannot be made for one arm alone.
SHARED_PROTOCOL = SharedProtocol(
    corpus="beads-three-arm-32",
    # `Bash` in every arm, so no allowlisted tool NAME carries an invitation one arm lacks.
    goal_tools=("Bash", "Read", "Write", "Edit"),
    scorer="states_value",
    model="sonnet",
    cli_version="pinned-at-fire",
    timeout_s=900,
    sandbox_policy="paid_sandbox",
)

ARMS: Mapping[str, MemoryArm] = MappingProxyType(
    {
        ARM_BEADS: MemoryArm(
            name=ARM_BEADS,
            # Imported, not retyped: the floor rung's pin dict is the SAME object the ladder
            # seeds, so the two cannot drift into two different floors.
            settings=RUNG_SETTINGS["R0"],
            provisions_bd=True,
            establish_tools=("Bash",),
            capability_paragraph=None,
            hook_mode=NATIVE_MEMORY_HOOK_MODE_DENY,
            store_seed=True,
            verbs=MEMORY_VERBS,
            shared=SHARED_PROTOCOL,
        ),
        ARM_NONE: MemoryArm(
            name=ARM_NONE,
            settings=RUNG_SETTINGS["R0"],
            provisions_bd=False,
            establish_tools=("Bash",),
            capability_paragraph=ARM_CONTEXT_NONE,
            hook_mode=NATIVE_MEMORY_HOOK_MODE_DENY,
            store_seed=False,
            verbs=(),
            shared=SHARED_PROTOCOL,
        ),
        ARM_BUILTIN: MemoryArm(
            name=ARM_BUILTIN,
            settings=BUILTIN_SETTINGS,
            provisions_bd=False,
            # No allowlist on the establish leg, so the CLI's own memory-write path is never
            # blocked by `--allowedTools` (`toolreq_builtin`). This is a may-vary field for
            # exactly this reason: clamping it would measure the clamp.
            establish_tools=(),
            capability_paragraph=ARM_CONTEXT_BUILTIN,
            hook_mode=NATIVE_MEMORY_HOOK_MODE_OBSERVE,
            store_seed=False,
            verbs=(),
            shared=SHARED_PROTOCOL,
        ),
    }
)

ARM_NAMES: tuple[str, ...] = tuple(ARMS)


def arm(name: str) -> MemoryArm:
    if name not in ARMS:
        raise MemoryArmError(f"unknown arm {name!r}; the grid's arms are {ARM_NAMES}")
    return ARMS[name]


def arm_settings(name: str) -> dict[str, object]:
    """The WHOLE content the arm's `settings.json` is to carry. Scrubbed, not merged: the caller
    writes this and nothing else, so no other arm's key can survive underneath it."""
    return dict(arm(name).settings)


def arm_settings_fingerprint() -> str:
    """Digest of the whole registry as it will be seeded and run.

    Part of the resume identity for the reason `rung_settings_fingerprint` is: an arm bought
    under one hook mode or one shared protocol must not be resumed as an arm bought under
    another, and nothing else in the identity can see these fields."""
    return digest(
        {
            "shared": vars(SHARED_PROTOCOL),
            "arms": {
                name: {
                    "settings": dict(one.settings),
                    "provisions_bd": one.provisions_bd,
                    "establish_tools": list(one.establish_tools),
                    "hook_mode": one.hook_mode,
                    "store_seed": one.store_seed,
                    "verbs": list(one.verbs),
                }
                for name, one in sorted(ARMS.items())
            },
        }
    )


def arm_context_words(name: str, *, bd_capability: str | None = None) -> int:
    """Words in the arm's rendered agent-visible context. Published per arm in the summary; a
    ratio above 1.5x across the three forces the confound statement in the write-up."""
    return context_words(render_arm_context(name, bd_capability=bd_capability))


def plant_arm_context(cwd: Path, name: str, *, bd_capability: str | None = None) -> tuple[str, ...]:
    """Write the arm's context into the agent's cwd as both drop-in names.

    Both names, for every arm: the CLI auto-loads `CLAUDE.md` and `AGENTS.md`, and an arm given
    one where another gets two is a presentation differential. Must be re-run after every cwd
    wipe -- a goal leg that lost its context is running a different arm than its establish leg
    (`plant_bd_context`, same hazard)."""
    text = render_arm_context(name, bd_capability=bd_capability)
    planted: list[str] = []
    for filename in BD_CONTEXT_FILES:
        (cwd / filename).write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        planted.append(filename)
    return tuple(planted)


def assert_no_memory_command(env: Mapping[str, str]) -> None:
    """Refuse the fire if the memory command resolves under this leg's effective PATH.

    The arms that must not have bd are graded on not having reached it, so a PATH on which it
    resolves makes the whole leg unfalsifiable: a null would read as disposition when it could
    be luck. `shutil.which` with the leg's own PATH, never `os.environ` -- the point is the
    CHILD's lookup, and the harness's own PATH almost always has bd on it."""
    path = env.get("PATH", "")
    found = shutil.which(MEMORY_COMMAND, path=path)
    if found is not None:
        raise MemoryArmError(
            f"{MEMORY_COMMAND!r} resolves to {found} under this leg's PATH, so this arm is not "
            f"free of it. PATH={path!r}"
        )


def assert_arms_comparable(arms: Sequence[MemoryArm]) -> None:
    """Refuse a registry that is not a comparison.

    Two conditions, and both are failures of the same kind. The arms must hold the SAME shared
    protocol, or a difference in corpus, goal allowlist, scorer, model, CLI version, timeout or
    sandbox policy rides along as an arm effect. And they must actually DIFFER in the memory
    they have, or two cells are measuring one thing under two labels."""
    if len(arms) < 2:
        raise MemoryArmError("a comparison needs at least two arms")
    names = [one.name for one in arms]
    if len(set(names)) != len(names):
        raise MemoryArmError(f"arms are not distinct: {names}")
    shared = {one.shared for one in arms}
    if len(shared) != 1:
        differing = sorted(
            key
            for key in vars(arms[0].shared)
            if len({getattr(one.shared, key) for one in arms}) != 1
        )
        raise MemoryArmError(
            f"arms differ on the shared protocol ({', '.join(differing)}), so any difference "
            "between them is not attributable to the memory they have."
        )
    memory = {
        (one.provisions_bd, one.store_seed, one.hook_mode, one.settings.get("autoMemoryEnabled"))
        for one in arms
    }
    if len(memory) != len(arms):
        raise MemoryArmError(f"two of {names} have the same memory, so they are one arm twice")
