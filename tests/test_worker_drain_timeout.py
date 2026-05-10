"""Tests for worker drain timeout behaviour."""

from __future__ import annotations

import asyncio

import pytest

from app.services.worker import BackgroundTaskScheduler


@pytest.mark.asyncio
async def test_drain_cancels_tasks_on_timeout() -> None:
    scheduler = BackgroundTaskScheduler()

    async def forever() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    task = scheduler.schedule(forever, "hang")
    await scheduler.drain(timeout=0.05)
    # After drain, the hung task should be cancelled.
    assert task.cancelled() or task.done()
