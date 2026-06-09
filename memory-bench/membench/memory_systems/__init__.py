"""Memory systems (arms) behind one uniform interface (§14 memory_systems/).

First-run arm scope is `none` / `ours` / `builtin` (fork 4):

- `none` — the no-memory control (condition A).
- `oracle` — the memory-sensitivity ceiling (condition B), harness-injected.
- `filesystem` — the skeleton's id-based reference integrated system.
- `ours` — retrieval-v1 (mem-di8) over the work-audit graph (condition C);
  failure-triggered/replay-only.
- `builtin` — the agent's own opaque memory (Claude/Codex). Its audit is the
  paid Harbor path owned by **mem-whi**, not implemented here.

The competitive systems (a-mem / mem0 / graphiti / nat) plug in LATER (mem-lvp)
behind this same `MemorySystem` contract.
"""

from typing import Any

from membench.memory_systems.base import (
    MemorySystem,
    RetrievalRequest,
    RetrieveResult,
)
from membench.memory_systems.filesystem_system import FilesystemMemory
from membench.memory_systems.none_system import NoneMemory
from membench.memory_systems.oracle_system import OracleMemory
from membench.memory_systems.ours_system import OursMemory

__all__ = [
    "FilesystemMemory",
    "MemorySystem",
    "NoneMemory",
    "OracleMemory",
    "OursMemory",
    "RetrievalRequest",
    "RetrieveResult",
    "build_memory_system",
]

# Arms whose implementation is owned by another bead — named here so the factory
# rejects them with a precise pointer instead of a generic "unknown system",
# keeping the uniform interface honest about what is wired vs pending.
_DEFERRED = {
    "builtin": "the built-in Claude/Codex memory audit is the paid Harbor path (mem-whi)",
    "a-mem": "competitive arm (mem-lvp)",
    "mem0": "competitive arm (mem-lvp)",
    "graphiti": "competitive arm (mem-lvp)",
    "nat": "competitive arm (mem-lvp)",
}


def build_memory_system(name: str, **kwargs: Any) -> MemorySystem:
    """Factory over the wired arm set. Raises on unknown or deferred names rather
    than silently substituting a default (an unknown memory system is a config
    error, and a deferred one must not masquerade as wired)."""
    systems: dict[str, type[MemorySystem]] = {
        "none": NoneMemory,
        "oracle": OracleMemory,
        "filesystem": FilesystemMemory,
        "ours": OursMemory,
    }
    cls = systems.get(name)
    if cls is None:
        if name in _DEFERRED:
            raise ValueError(f"Memory system {name!r} is not wired here: {_DEFERRED[name]}.")
        raise ValueError(
            f"Unknown memory system {name!r}. Wired: {sorted(systems)}; "
            f"deferred: {sorted(_DEFERRED)}."
        )
    return cls(**kwargs)
