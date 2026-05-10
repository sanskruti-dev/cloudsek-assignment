"""In-process background task scheduler built on ``asyncio.create_task``."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class BackgroundTaskScheduler:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> int:
        return sum(1 for t in self._tasks if not t.done())

    def schedule(
        self,
        coro_factory: Callable[[], Awaitable[None]],
        name: str,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(self._run(coro_factory, name), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(
        self,
        coro_factory: Callable[[], Awaitable[None]],
        name: str,
    ) -> None:
        try:
            await coro_factory()
        except Exception:
            logger.exception("background task %s failed", name)

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for all in-flight tasks, bounded by ``timeout``."""
        if not self._tasks:
            return
        in_flight = list(self._tasks)
        try:
            await asyncio.wait_for(
                asyncio.gather(*in_flight, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("drain timed out with %d task(s) pending", self.pending)
            for task in in_flight:
                if not task.done():
                    task.cancel()
