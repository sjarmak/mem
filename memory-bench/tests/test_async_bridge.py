"""Tests for AsyncClientBridge — the sync wrapper an async-native backend client
(NAT MemoryEditor, Graphiti) uses to satisfy the SYNC SemanticMemoryClient Protocol
(mem-lvp.5a).

Hermetic: a trivial async stub coroutine stands in for the backend, so no network
and no model. The load-bearing invariant from the mem-lvp.12 concurrency audit
(failure mode #5) is ONE loop per bridge instance — never shared/global — so these
assert loop identity is reused within an instance but DISTINCT across instances.
"""

import asyncio

import pytest

from membench.memory_systems.async_bridge import AsyncClientBridge


async def _echo(value: int) -> int:
    await asyncio.sleep(0)
    return value


async def _boom() -> None:
    raise ValueError("kaboom")


def test_run_returns_coroutine_result() -> None:
    bridge = AsyncClientBridge()
    try:
        assert bridge.run(_echo(7)) == 7
    finally:
        bridge.close()


def test_same_loop_reused_across_calls() -> None:
    # The whole point: the held loop stays warm across many sequential calls, so a
    # backend's Redis/graph-driver connection lifecycle is not thrashed.
    bridge = AsyncClientBridge()
    try:
        bridge.run(_echo(1))
        first = bridge.loop
        bridge.run(_echo(2))
        bridge.run(_echo(3))
        assert bridge.loop is first
    finally:
        bridge.close()


def test_exception_inside_coroutine_propagates_unchanged() -> None:
    bridge = AsyncClientBridge()
    try:
        with pytest.raises(ValueError, match="kaboom"):
            bridge.run(_boom())
    finally:
        bridge.close()


def test_separate_instances_hold_distinct_loops() -> None:
    # No shared/global/module-level loop: two bridges are two isolated loops, so
    # they cannot serialize on each other or contaminate connections across arms.
    a = AsyncClientBridge()
    b = AsyncClientBridge()
    try:
        assert a.loop is not b.loop
    finally:
        a.close()
        b.close()


def test_close_tears_down_loop_and_run_after_close_raises() -> None:
    bridge = AsyncClientBridge()
    bridge.run(_echo(1))
    bridge.close()
    assert bridge.loop.is_closed()
    # No silent re-creation: run() after close must fail loudly.
    leftover = _echo(2)
    with pytest.raises(RuntimeError, match="closed"):
        bridge.run(leftover)
    leftover.close()  # never reached the loop; close it so no ResourceWarning


def test_close_is_idempotent() -> None:
    bridge = AsyncClientBridge()
    bridge.close()
    bridge.close()  # second close must not raise
    assert bridge.loop.is_closed()


def test_context_manager_closes_loop_on_exit() -> None:
    with AsyncClientBridge() as bridge:
        assert bridge.run(_echo(42)) == 42
        loop = bridge.loop
    assert loop.is_closed()


def test_close_drains_unexhausted_async_generators() -> None:
    # A backend that streams via `async for` may leave an async generator started
    # but not exhausted on the held loop. close() must finalize it (shutdown_asyncgens)
    # rather than abandon it — otherwise the runtime emits "Task was destroyed but it
    # is pending" and the generator's cleanup (connection release) never runs.
    bridge = AsyncClientBridge()
    finalized = {"done": False}

    async def streaming_gen():
        try:
            yield 1
            yield 2
        finally:
            finalized["done"] = True

    async def start_but_dont_exhaust():
        gen = streaming_gen()
        await gen.__anext__()  # start it; the loop now tracks it, left pending
        return gen

    gen = bridge.run(start_but_dont_exhaust())  # keep a ref so it isn't GC'd early
    assert finalized["done"] is False
    bridge.close()
    assert finalized["done"] is True, "close() must drain tracked async generators"
    assert gen is not None
