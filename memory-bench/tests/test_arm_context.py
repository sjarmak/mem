"""Presentation parity for the three-arm beads grid (gate 9 of the pre-registration).

These run before any spend. A scaffold differential is not detectable after the fact: it would
show up as an arm effect, in the direction of whichever arm got the extra sentence.
"""

from __future__ import annotations

import pytest

from membench.runner.arm_context import (
    ARM_CONTEXT_ARMS,
    ArmContextError,
    capability_of,
    context_words,
    render_arm_context,
    scaffold_of,
)

BD_CAPABILITY = """\
## Durable storage

`bd remember` and `bd recall` are the two halves of one store, and the store persists across
sessions in this project."""


def _render(arm: str) -> str:
    return render_arm_context(arm, bd_capability=BD_CAPABILITY if arm == "beads" else None)


def test_every_arm_renders() -> None:
    assert set(ARM_CONTEXT_ARMS) == {"beads", "none", "builtin"}
    for arm in ARM_CONTEXT_ARMS:
        assert _render(arm).strip()


def test_the_scaffold_is_byte_identical_across_the_three_arms() -> None:
    scaffolds = {arm: scaffold_of(_render(arm)) for arm in ARM_CONTEXT_ARMS}
    distinct = set(scaffolds.values())
    assert len(distinct) == 1, f"scaffold differs between arms: {scaffolds}"


def test_the_arms_differ_only_in_the_capability_paragraph() -> None:
    capabilities = {arm: capability_of(_render(arm)) for arm in ARM_CONTEXT_ARMS}
    assert len(set(capabilities.values())) == 3, capabilities
    for arm, text in capabilities.items():
        assert text, arm


def test_no_arm_is_given_silence_where_another_is_given_a_paragraph() -> None:
    """The floor arm states that nothing persists. An empty slot would be a second, unmeasured
    difference: absence of instruction, on top of absence of a store."""
    for arm in ARM_CONTEXT_ARMS:
        assert context_words(capability_of(_render(arm))) >= 20, arm


def test_the_authored_paragraphs_are_within_the_pre_registered_ratio() -> None:
    """Only the two AUTHORED paragraphs are in scope here. The bd paragraph is captured at
    provision time from whatever `bd init` shipped, so its length is not this module's to set
    and a stub asserted against here would measure the stub.

    There is deliberately no three-way ratio assertion anywhere (ruling 1(a)). The real captured
    bd text measures ~1,338 words against these two at 35 and 37, and that gap is DISCLOSED in
    the write-up rather than gated: a gate here could only be satisfied by shortening bd's own
    deployment text, which would measure a deployment of bd that nobody ships."""
    counts = {arm: context_words(capability_of(_render(arm))) for arm in ("none", "builtin")}
    assert max(counts.values()) <= 1.5 * min(counts.values()), counts


def test_no_arm_but_beads_can_carry_bd_text() -> None:
    for arm in ("none", "builtin"):
        rendered = _render(arm)
        assert "bd " not in rendered, arm
        with pytest.raises(ArmContextError):
            render_arm_context(arm, bd_capability=BD_CAPABILITY)


def test_the_beads_arm_refuses_to_render_without_its_captured_paragraph() -> None:
    for missing in (None, "", "   \n"):
        with pytest.raises(ArmContextError):
            render_arm_context("beads", bd_capability=missing)


def test_an_unknown_arm_is_refused() -> None:
    with pytest.raises(ArmContextError):
        render_arm_context("oracle")


def test_the_capability_paragraph_names_no_moment_to_use_it() -> None:
    """Disposition is the endpoint. A paragraph that told the agent when to reach for its store
    would supply the behaviour being measured."""
    timing = ("before you start", "at the start of", "when you begin", "first check", "remember to")
    for arm in ARM_CONTEXT_ARMS:
        lowered = capability_of(_render(arm)).lower()
        for phrase in timing:
            assert phrase not in lowered, f"{arm} is coached: {phrase!r}"


def test_the_scaffold_names_no_persistence_facility() -> None:
    scaffold = scaffold_of(_render("none")).lower()
    for word in ("memory.md", "bd ", "remember", "recall", "persist"):
        assert word not in scaffold, word
