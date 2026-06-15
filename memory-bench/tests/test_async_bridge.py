"""Tests for AsyncClientBridge — the sync wrapper an async-native backend client
(NAT MemoryEditor, Graphiti) uses to satisfy the SYNC SemanticMemoryClient Protocol
(mem-lvp.5a).

Hermetic: a trivial async stub coroutine stands in for the backend, so no network
and no model. The load-bearing invariant from the mem-lvp.12 concurrency audit
(failure mode #5) is ONE loop per bridge instance — never shared/global — so these
assert loop identity is reused within an instance but DISTINCT across instances.
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest

from membench.memory_systems.async_bridge import (
    ENV_TRIAL_TIMEOUT_SEC,
    AsyncClientBridge,
    trial_timeout,
)


async def _echo(value: int) -> int:
    await asyncio.sleep(0)
    return value


async def _slowish(value: int) -> int:
    # Slower than the 0.05s timeout the timeout tests use, but short for a test run —
    # used to prove the default (no-timeout) path drives a slow coro to completion.
    await asyncio.sleep(0.1)
    return value


async def _boom() -> None:
    raise ValueError("kaboom")


async def _slow() -> int:
    await asyncio.sleep(10)  # far past any test timeout; cancelled by wait_for
    return 99


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


def test_default_no_timeout_does_not_wrap_slow_coro() -> None:
    # Default (timeout=None) preserves exact current behavior: a coro is driven to
    # completion with no wait_for guard. _slowish sleeps 0.1s — well past the 0.05s
    # budget the timeout tests trip on — yet still returns, proving the default path
    # imposes no deadline.
    bridge = AsyncClientBridge()
    try:
        assert bridge.run(_slowish(5)) == 5
    finally:
        bridge.close()


def test_timeout_set_slow_coro_raises_timeout_error() -> None:
    # A backend stall (Redis/Graphiti/NAT) must not block the harness thread forever:
    # with a timeout, a coro that runs past it raises TimeoutError loudly — never a
    # silent sentinel.
    bridge = AsyncClientBridge(timeout=0.05)
    try:
        with pytest.raises(asyncio.TimeoutError):
            bridge.run(_slow())
    finally:
        bridge.close()


def test_timeout_set_fast_coro_returns_normally() -> None:
    # A timeout guard must not penalize the common case: a coro that finishes within
    # the budget returns its result unchanged.
    bridge = AsyncClientBridge(timeout=5.0)
    try:
        assert bridge.run(_echo(11)) == 11
    finally:
        bridge.close()


def test_loop_recoverable_after_timeout_then_close_drains() -> None:
    # A fired timeout must leave the loop usable: a subsequent run() succeeds and
    # close() still drains cleanly (no abandoned tasks past a timeout).
    bridge = AsyncClientBridge(timeout=0.05)
    try:
        with pytest.raises(asyncio.TimeoutError):
            bridge.run(_slow())
        assert bridge.run(_echo(3)) == 3  # loop still alive after the timeout
    finally:
        bridge.close()
    assert bridge.loop.is_closed()


def test_close_drains_unexhausted_async_generators() -> None:
    # A backend that streams via `async for` may leave an async generator started
    # but not exhausted on the held loop. close() must finalize it (shutdown_asyncgens)
    # rather than abandon it — otherwise the runtime emits "Task was destroyed but it
    # is pending" and the generator's cleanup (connection release) never runs.
    bridge = AsyncClientBridge()
    finalized = {"done": False}

    async def streaming_gen() -> AsyncGenerator[int, None]:
        try:
            yield 1
            yield 2
        finally:
            finalized["done"] = True

    async def start_but_dont_exhaust() -> AsyncGenerator[int, None]:
        gen = streaming_gen()
        await gen.__anext__()  # start it; the loop now tracks it, left pending
        return gen

    gen = bridge.run(start_but_dont_exhaust())  # keep a ref so it isn't GC'd early
    assert finalized["done"] is False
    bridge.close()
    assert finalized["done"] is True, "close() must drain tracked async generators"
    assert gen is not None


# --- trial_timeout: harness config -> AsyncClientBridge(timeout=...) ----------


def test_trial_timeout_unset_returns_none() -> None:
    # No env var = the default unbounded bridge behavior (None), not a fabricated
    # default deadline.
    assert trial_timeout(env={}) is None


def test_trial_timeout_blank_returns_none() -> None:
    # An empty / whitespace-only value reads as "not configured", same as unset.
    assert trial_timeout(env={ENV_TRIAL_TIMEOUT_SEC: "   "}) is None


def test_trial_timeout_valid_value_parsed() -> None:
    assert trial_timeout(env={ENV_TRIAL_TIMEOUT_SEC: "2.5"}) == 2.5


def test_trial_timeout_non_numeric_raises() -> None:
    # A misconfigured value fails loudly at the boundary rather than silently
    # disabling the guard.
    with pytest.raises(ValueError, match="is not a number"):
        trial_timeout(env={ENV_TRIAL_TIMEOUT_SEC: "soon"})


@pytest.mark.parametrize("value", ["0", "-1", "-0.5"])
def test_trial_timeout_non_positive_raises(value: str) -> None:
    # A zero/negative wait_for deadline would trip every trial — reject it.
    with pytest.raises(ValueError, match="must be a positive"):
        trial_timeout(env={ENV_TRIAL_TIMEOUT_SEC: value})


def test_trial_timeout_defaults_to_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # With no env arg it reads os.environ, so the factories pick up the harness config.
    monkeypatch.setenv(ENV_TRIAL_TIMEOUT_SEC, "1.0")
    assert trial_timeout() == 1.0
