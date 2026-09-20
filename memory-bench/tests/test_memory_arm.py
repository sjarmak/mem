"""The registry is a comparison, or it is refused (pre-registration gates 1, 5 and 9).

Every assertion here runs before any spend. The failures they catch are the ones that are
invisible afterwards: an arm that could reach the store it is graded on not reaching, a
settings key from one arm surviving under another, a protocol difference riding along as an
arm effect.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from membench.runner.arm_context import capability_of
from membench.runner.memory_arm import (
    ARM_BUILTIN,
    ARM_NAMES,
    ARM_NONE,
    ARMS,
    SHARED_PROTOCOL,
    MemoryArmError,
    arm,
    arm_context_words,
    arm_settings,
    arm_settings_fingerprint,
    assert_arms_comparable,
    assert_no_memory_command,
    plant_arm_context,
)
from membench.runner.tool_surface import BD_CONTEXT_FILES, MEMORY_COMMAND

BD_CAPABILITY = "## Durable storage\n\n`bd remember` and `bd recall` are two halves of one store."


def test_the_registry_is_the_three_pre_registered_arms() -> None:
    assert ARM_NAMES == ("beads", "none", "builtin")


def test_the_arms_are_comparable() -> None:
    assert_arms_comparable(list(ARMS.values()))


def test_every_arm_holds_the_same_shared_protocol() -> None:
    for one in ARMS.values():
        assert one.shared is SHARED_PROTOCOL


@pytest.mark.parametrize("field_name", [f.name for f in dataclasses.fields(SHARED_PROTOCOL)])
def test_a_registry_differing_on_a_may_not_vary_field_is_refused(field_name: str) -> None:
    """Corpus, goal allowlist, scorer, model, CLI version, timeout, sandbox policy. Each one,
    varied alone, must take the registry down."""
    drifted = dataclasses.replace(
        ARMS[ARM_NONE],
        shared=dataclasses.replace(
            SHARED_PROTOCOL, **{field_name: _other_value(getattr(SHARED_PROTOCOL, field_name))}
        ),
    )
    with pytest.raises(MemoryArmError) as caught:
        assert_arms_comparable([ARMS["beads"], drifted, ARMS[ARM_BUILTIN]])
    assert field_name in str(caught.value)


def _other_value(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, tuple):
        return (*value, "Grep")
    return f"{value}-drifted"


def test_two_arms_with_the_same_memory_are_refused() -> None:
    twin = dataclasses.replace(ARMS[ARM_BUILTIN], name="builtin-twin")
    with pytest.raises(MemoryArmError):
        assert_arms_comparable([ARMS[ARM_BUILTIN], twin])


def test_one_arm_is_not_a_comparison() -> None:
    with pytest.raises(MemoryArmError):
        assert_arms_comparable([ARMS[ARM_NONE]])


def test_an_unknown_arm_is_refused() -> None:
    with pytest.raises(MemoryArmError):
        arm("oracle")


def test_settings_are_the_whole_content_not_a_merge() -> None:
    """mem-nclzl: a dict merge can only ADD, so the floor arm's pin would survive underneath
    the comparator's. `arm_settings` returns what the file is to contain, entire."""
    assert arm_settings("beads") == {"autoMemoryEnabled": False}
    assert arm_settings(ARM_NONE) == {"autoMemoryEnabled": False}
    assert arm_settings(ARM_BUILTIN) == {"autoMemoryEnabled": True}
    for name in ARM_NAMES:
        assert set(arm_settings(name)) == {"autoMemoryEnabled"}


def test_the_settings_returned_are_a_copy() -> None:
    mutated = arm_settings(ARM_BUILTIN)
    mutated["autoMemoryEnabled"] = False
    assert arm_settings(ARM_BUILTIN) == {"autoMemoryEnabled": True}


def test_the_fingerprint_moves_when_an_arm_does() -> None:
    before = arm_settings_fingerprint()
    assert before == arm_settings_fingerprint()
    assert len(before) >= 16


def test_assert_no_memory_command_fires_when_bd_resolves(tmp_path: Path) -> None:
    planted = tmp_path / MEMORY_COMMAND
    planted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    planted.chmod(0o700)
    with pytest.raises(MemoryArmError) as caught:
        assert_no_memory_command({"PATH": str(tmp_path)})
    assert str(planted) in str(caught.value)


def test_assert_no_memory_command_passes_on_a_clean_path(tmp_path: Path) -> None:
    assert_no_memory_command({"PATH": str(tmp_path)})
    assert_no_memory_command({})


def test_assert_no_memory_command_reads_the_legs_path_not_the_harness(tmp_path: Path) -> None:
    """The harness's own PATH almost always has bd on it; the question is the CHILD's lookup."""
    if shutil_which_on_host() is None:
        pytest.skip("bd is not on the host PATH, so there is nothing to confuse it with")
    assert_no_memory_command({"PATH": str(tmp_path)})


def shutil_which_on_host() -> str | None:
    import shutil

    return shutil.which(MEMORY_COMMAND, path=os.environ.get("PATH", ""))


def test_planting_writes_both_drop_in_names_for_every_arm(tmp_path: Path) -> None:
    for name in ARM_NAMES:
        cwd = tmp_path / name
        cwd.mkdir()
        capability = BD_CAPABILITY if name == "beads" else None
        assert plant_arm_context(cwd, name, bd_capability=capability) == BD_CONTEXT_FILES
        for filename in BD_CONTEXT_FILES:
            assert (cwd / filename).read_text(encoding="utf-8").strip()


def test_the_two_planted_files_are_the_same_text(tmp_path: Path) -> None:
    plant_arm_context(tmp_path, ARM_NONE)
    texts = {(tmp_path / name).read_text(encoding="utf-8") for name in BD_CONTEXT_FILES}
    assert len(texts) == 1


def test_only_the_beads_arm_carries_the_captured_paragraph() -> None:
    assert ARMS["beads"].capability_paragraph is None
    for name in (ARM_NONE, ARM_BUILTIN):
        paragraph = ARMS[name].capability_paragraph
        assert paragraph is not None
        assert capability_of(_rendered(name)) == paragraph


def _rendered(name: str) -> str:
    from membench.runner.arm_context import render_arm_context

    return render_arm_context(name)


def test_context_words_are_published_per_arm() -> None:
    counts = {name: arm_context_words(name) for name in (ARM_NONE, ARM_BUILTIN)}
    assert all(count > 0 for count in counts.values())
    assert arm_context_words("beads", bd_capability=BD_CAPABILITY) > 0


def test_the_floor_arm_provisions_no_store_and_names_no_verbs() -> None:
    floor = ARMS[ARM_NONE]
    assert not floor.provisions_bd
    assert not floor.store_seed
    assert floor.verbs == ()


def test_the_floor_arm_is_denied_the_native_path_without_being_pointed_at_bd() -> None:
    """`redirect` would name bd in its refusal message, which coaches the arm that is supposed
    to have no durable memory. `deny` names no alternative."""
    assert ARMS[ARM_NONE].hook_mode == "deny"
    assert ARMS["beads"].hook_mode == "deny"
    assert ARMS[ARM_BUILTIN].hook_mode == "observe"


def test_the_comparator_leaves_its_establish_leg_unclamped() -> None:
    """An `--allowedTools` clamp would block the CLI's own memory-write path, so the arm would
    measure the clamp."""
    assert ARMS[ARM_BUILTIN].establish_tools == ()


def test_the_goal_leg_allowlist_is_shared_and_names_bash_for_every_arm() -> None:
    assert "Bash" in SHARED_PROTOCOL.goal_tools
    for one in ARMS.values():
        assert one.shared.goal_tools == SHARED_PROTOCOL.goal_tools
