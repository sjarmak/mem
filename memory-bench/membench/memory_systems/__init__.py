"""Reference memory systems for the Phase-1 skeleton (§14 memory_systems/).

Ships none / oracle / filesystem behind one uniform interface. `ours`
(retrieval-v1, mem-di8) and the competitive systems (a-mem / mem0 / graphiti / nat)
plug in LATER (mem-lvp) behind this same `MemorySystem` contract — not here.
"""

from membench.memory_systems.base import MemorySystem, RetrieveResult
from membench.memory_systems.filesystem_system import FilesystemMemory
from membench.memory_systems.none_system import NoneMemory
from membench.memory_systems.oracle_system import OracleMemory

__all__ = [
    "FilesystemMemory",
    "MemorySystem",
    "NoneMemory",
    "OracleMemory",
    "RetrieveResult",
    "build_memory_system",
]


def build_memory_system(name: str, **kwargs) -> MemorySystem:
    """Factory over the skeleton reference set. Raises on unknown names rather than
    silently substituting a default (an unknown memory system is a config error)."""
    systems = {
        "none": NoneMemory,
        "oracle": OracleMemory,
        "filesystem": FilesystemMemory,
    }
    cls = systems.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown reference memory system {name!r}. "
            f"Skeleton set: {sorted(systems)}. "
            f"Competitive systems (a-mem/mem0/graphiti/nat) land in mem-lvp."
        )
    return cls(**kwargs)
