"""Arm separation, provable from a mint with no agent spawned (pre-registration §5, items 1-3).

Not free of side effects: minting the bd arm shells a real `git init` and `bd init`, so these
run under the same outside-the-sandbox store discipline as a paid leg. They buy no tokens.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from membench.runner.arm_context import capability_of, scaffold_of
from membench.runner.beads_arm_grid import (
    COMMAND_NOT_FOUND,
    arm_cell_store,
    child_path_of,
    replant_context,
)
from membench.runner.memory_arm import ARM_NAMES
from membench.runner.tool_surface import BD_CONTEXT_FILES, CONFIG_DIR_ENV, MEMORY_COMMAND

pytestmark = pytest.mark.skipif(
    shutil.which(MEMORY_COMMAND) is None, reason="minting the beads arm needs a real bd on PATH"
)


def _context_text(store: object) -> str:
    sandbox = store.sandbox  # type: ignore[attr-defined]
    return (sandbox / BD_CONTEXT_FILES[0]).read_text(encoding="utf-8")


def test_each_arm_mints_and_seeds_its_own_settings() -> None:
    seen = {}
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            settings = json.loads((store.config_dir / "settings.json").read_text(encoding="utf-8"))
            seen[name] = settings
    assert seen["beads"]["autoMemoryEnabled"] is False
    assert seen["none"]["autoMemoryEnabled"] is False
    assert seen["builtin"]["autoMemoryEnabled"] is True


def test_the_hook_merges_in_without_displacing_the_pin() -> None:
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            settings = json.loads((store.config_dir / "settings.json").read_text(encoding="utf-8"))
            assert "hooks" in settings, name
            assert "autoMemoryEnabled" in settings, name
            assert store.pinned_off is (name != "builtin"), name


def test_a_working_bd_resolves_in_exactly_one_arm() -> None:
    """The floor arms DO resolve the name -- they plant a stub that exits 127, so their error
    surface matches a missing command. What must resolve in only one arm is a bd with a store
    behind it, so the question is where the resolution POINTS."""
    resolves = {}
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            path = os.pathsep.join(child_path_of(store.env()))
            found = shutil.which(MEMORY_COMMAND, path=path)
            stub = store.surface.bin_dir / MEMORY_COMMAND
            resolves[name] = found is not None and (store.arm.provisions_bd or Path(found) != stub)
    assert resolves == {"beads": True, "none": False, "builtin": False}


def test_the_arms_without_a_store_see_only_the_harness_bin_dir() -> None:
    """`MemoryToolSurface.env()` appends the operator's PATH, which on this machine has a real bd
    on it. A floor arm that inherited it would not be a floor."""
    for name in ("none", "builtin"):
        with arm_cell_store(name, label="test") as store:
            entries = child_path_of(store.env())
            assert entries == [str(store.surface.bin_dir)], name


def test_the_planted_stub_exits_command_not_found() -> None:
    with arm_cell_store("none", label="test") as store:
        stub = store.surface.bin_dir / MEMORY_COMMAND
        done = subprocess.run([str(stub), "remember", "x"], capture_output=True, text=True)
        assert done.returncode == COMMAND_NOT_FOUND
        assert "not found" in done.stderr


def test_every_arm_pins_its_own_config_dir() -> None:
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            assert store.env()[CONFIG_DIR_ENV] == str(store.config_dir)


def test_the_planted_context_differs_in_exactly_one_paragraph() -> None:
    scaffolds, capabilities = {}, {}
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            assert store.context_files == BD_CONTEXT_FILES
            text = _context_text(store)
            scaffolds[name] = scaffold_of(text)
            capabilities[name] = capability_of(text)
    assert len(set(scaffolds.values())) == 1, scaffolds
    assert len(set(capabilities.values())) == 3, capabilities


def test_the_beads_paragraph_is_bds_own_text_not_this_rigs() -> None:
    with arm_cell_store("beads", label="test") as store:
        assert store.bd_capability
        assert capability_of(_context_text(store)) == store.bd_capability.strip()


def test_replanting_after_a_wipe_restores_the_same_bytes() -> None:
    for name in ARM_NAMES:
        with arm_cell_store(name, label="test") as store:
            before = _context_text(store)
            for filename in BD_CONTEXT_FILES:
                (store.sandbox / filename).unlink()
            assert replant_context(store) == BD_CONTEXT_FILES
            assert _context_text(store) == before, name


def test_the_store_is_outside_the_sandbox_the_wipe_can_reach() -> None:
    with arm_cell_store("beads", label="test") as store:
        assert store.sandbox not in store.surface.store_dir.parents
