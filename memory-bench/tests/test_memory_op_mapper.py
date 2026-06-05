import pytest

from membench.mapper import UnknownMemoryToolError, normalize_operation
from membench.schemas.memory_event import MemoryBackend, MemoryOperation


@pytest.mark.parametrize(
    "tool,backend,expected",
    [
        ('Read("~/memory/foo.md")', MemoryBackend.FILESYSTEM, MemoryOperation.READ),
        ('Write("~/memory/foo.md")', MemoryBackend.FILESYSTEM, MemoryOperation.WRITE),
        ('Edit("~/memory/foo.md")', MemoryBackend.FILESYSTEM, MemoryOperation.UPDATE),
        ('grep("x", ~/memory)', MemoryBackend.FILESYSTEM, MemoryOperation.SEARCH),
        ("add_memory", MemoryBackend.MCP, MemoryOperation.WRITE),
        ("search_memories", MemoryBackend.MCP, MemoryOperation.SEARCH),
        ("vector_search", MemoryBackend.VECTOR_DB, MemoryOperation.SEARCH),
        ("upsert", MemoryBackend.VECTOR_DB, MemoryOperation.WRITE),
        ("upsert_entity", MemoryBackend.KG, MemoryOperation.WRITE),
        ("relationship_query", MemoryBackend.KG, MemoryOperation.SEARCH),
    ],
)
def test_normalize_known_tools(tool, backend, expected):
    assert normalize_operation(tool, backend) == expected


def test_unknown_tool_raises_not_silently_defaults():
    with pytest.raises(UnknownMemoryToolError):
        normalize_operation("frobnicate(...)", MemoryBackend.FILESYSTEM)
