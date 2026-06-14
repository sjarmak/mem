"""AsyncClientBridge — a sync wrapper around a persistent asyncio event loop, so an
async-native backend client (NAT ``MemoryEditor``, Graphiti) can satisfy the SYNC
``SemanticMemoryClient`` Protocol (see semantic_base.py) without making the seam — or
the deterministic fakes — async.

The loop is held across many sequential ``run`` calls so the backend's Redis/graph-
driver connection lifecycle stays warm (``asyncio.run`` per call would tear that down
every time). The loop is created with ``new_event_loop()`` and never installed as the
thread's current loop (no ``set_event_loop``): each bridge owns EXACTLY ONE loop on
its instance, never a shared/global/module-level one. That isolation is load-bearing —
the mem-lvp.12 concurrency audit (failure mode #5) found a shared loop is both a global
serialization point and a shared-connection contamination vector across trials/arms.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from types import TracebackType
from typing import TypeVar

T = TypeVar("T")


class AsyncClientBridge:
    """Holds one persistent event loop and runs coroutines on it synchronously.

    One loop per instance, owned for the instance's lifetime. ``close`` (or exiting
    the context manager) tears the loop down; ``run`` after that raises rather than
    silently spinning up a replacement.
    """

    def __init__(self) -> None:
        # new_event_loop (not get_event_loop) guarantees a fresh, instance-private
        # loop — never the thread's shared current loop.
        self._loop = asyncio.new_event_loop()
        self._closed = False

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The held loop. Exposed for identity/lifecycle assertions, not for callers
        to drive directly — go through ``run``."""
        return self._loop

    def run(self, coro: Awaitable[T]) -> T:
        """Drive ``coro`` to completion on the held loop and return its result.
        Exceptions raised inside the coroutine propagate to the caller unchanged."""
        if self._closed:
            raise RuntimeError("AsyncClientBridge is closed; create a new instance.")
        return self._loop.run_until_complete(coro)

    def close(self) -> None:
        """Stop reusing the loop and release it. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._loop.close()

    def __enter__(self) -> AsyncClientBridge:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
