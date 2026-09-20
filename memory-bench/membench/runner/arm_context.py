"""The agent-visible context every arm of the three-arm beads grid is given.

One scaffold, one differing paragraph. The whole point of the grid is that the arms differ in
what durable memory they HAVE, so anything else that differs in what the agent reads is a
confound bought for free. `render_arm_context` builds `<shared preamble> + <arm capability
paragraph> + <shared closing>` by accumulation from two module constants, so an arm cannot
carry a scaffold sentence another arm lacks: there is nowhere for such a sentence to live.

`scaffold_of` exists for the test, not for the runtime. It strips the capability slot back out
of a rendered text, and the presentation-parity gate asserts that the three arms' scaffolds are
BYTE-identical. Rendering is what is asserted, not the constants -- a test that compared the
constants would pass while `render_arm_context` appended something for one arm only.

Each paragraph states the AFFORDANCE and says nothing about WHEN to use it. Disposition is the
endpoint; a paragraph that said "check your memory before starting" would supply the behaviour
the grid is buying an answer about. For the same reason the bd paragraph is CAPTURED from what
`bd init` shipped (`tool_surface.capture_bd_context` plus `BD_CONTEXT_ADDENDUM`) rather than
written here: a paraphrase of bd's own deployment text would measure prose this rig authored.

What this module does NOT defeat: a command grammar is a stronger instruction than a file path,
whatever the word counts say. That residue is reported, not removed -- `arm_context_words` per
arm goes in the summary, and a ratio above 1.5x forces the confound statement in the write-up.

ZFC: string accumulation and a word count. No model call, no judgment.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# The capability paragraph sits between these. They are in every arm's text, so they cannot
# themselves be a differential; they exist so `scaffold_of` can remove the slot without knowing
# what went in it, which is what makes the parity test an assertion about rendering.
CAPABILITY_OPEN = "<!-- arm-capability:begin -->"
CAPABILITY_CLOSE = "<!-- arm-capability:end -->"

# Says what the situation is and nothing about memory. Every sentence here must be true of all
# three arms; a sentence true of only two is a scaffold differential wearing a shared constant's
# clothes.
ARM_CONTEXT_PREAMBLE = """\
# Working context

You are working in this directory. The work comes to you in more than one session, and each
session starts fresh: what you can see at the start of a session is this file, the working
directory, and the task you are given."""

# Closes without naming a facility. "what is described above" resolves to the capability
# paragraph, which is the one place the arms are allowed to differ.
ARM_CONTEXT_CLOSING = """\
Other than what is described above, this working directory and the task description are what
you have."""

ARM_CONTEXT_NONE = """\
## Durable storage

This session has no durable store. Nothing written during it is carried to a later session:
the working directory is emptied between sessions, and no facility outside it is available to
you."""

ARM_CONTEXT_BUILTIN = """\
## Durable storage

You have your own memory. It lives under your configuration directory, as a `MEMORY.md` index
and the topic files it links, and its contents are available to you in later sessions in this
project."""

# `beads` is absent on purpose: its paragraph is captured at provision time, not authored here.
# A default would be a paraphrase of bd's deployment text, which is the thing the capture exists
# to avoid, and a silently-defaulted arm is the indistinguishable null this rig keeps refusing.
_AUTHORED_CAPABILITIES: Mapping[str, str] = MappingProxyType(
    {"none": ARM_CONTEXT_NONE, "builtin": ARM_CONTEXT_BUILTIN}
)

ARM_BEADS = "beads"
ARM_CONTEXT_ARMS: tuple[str, ...] = (ARM_BEADS, "none", "builtin")


class ArmContextError(RuntimeError):
    """A context that cannot be rendered as specified. Never a fallback: an arm that ran on a
    substituted context is an arm whose result answers a different question."""


def render_arm_context(arm: str, *, bd_capability: str | None = None) -> str:
    """The full agent-visible context for one arm.

    `bd_capability` is the captured bd deployment block with the addendum appended, and is
    REQUIRED for `beads` and REFUSED for the others -- a floor arm carrying bd's own text would
    describe a store it does not have, and would do it in bd's vocabulary."""
    if arm not in ARM_CONTEXT_ARMS:
        raise ArmContextError(f"unknown arm {arm!r}; the grid's arms are {ARM_CONTEXT_ARMS}")
    if arm == ARM_BEADS:
        if not (bd_capability and bd_capability.strip()):
            raise ArmContextError(
                "the beads arm's capability paragraph is captured from what `bd init` shipped "
                "(`tool_surface.capture_bd_context` + `BD_CONTEXT_ADDENDUM`) and must be passed "
                "in; there is no authored default, because a paraphrase would measure this "
                "rig's own prose."
            )
        capability = bd_capability.strip()
    else:
        if bd_capability is not None:
            raise ArmContextError(f"arm {arm!r} is not the bd arm and cannot carry bd context")
        capability = _AUTHORED_CAPABILITIES[arm]
    return "\n\n".join(
        (
            ARM_CONTEXT_PREAMBLE,
            CAPABILITY_OPEN,
            capability,
            CAPABILITY_CLOSE,
            ARM_CONTEXT_CLOSING,
        )
    )


def capability_of(rendered: str) -> str:
    """The capability paragraph a rendered context carries."""
    start = rendered.find(CAPABILITY_OPEN)
    end = rendered.find(CAPABILITY_CLOSE)
    if start < 0 or end < start:
        raise ArmContextError("rendered context has no capability slot")
    return rendered[start + len(CAPABILITY_OPEN) : end].strip()


def scaffold_of(rendered: str) -> str:
    """The rendered context with the capability slot emptied. Byte-equal across arms, or the
    presentation-parity gate has caught a scaffold differential."""
    start = rendered.find(CAPABILITY_OPEN)
    end = rendered.find(CAPABILITY_CLOSE)
    if start < 0 or end < start:
        raise ArmContextError("rendered context has no capability slot")
    return rendered[: start + len(CAPABILITY_OPEN)] + rendered[end:]


def context_words(text: str) -> int:
    """Whitespace-delimited words. The presentation measure published per arm; deliberately the
    crudest count there is, so it cannot be argued with after the fact."""
    return len(text.split())
