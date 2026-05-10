"""Tests for the BackgroundTaskScheduler."""

from __future__ import annotations

import asyncio

import pytest

from app.services.worker import BackgroundTaskScheduler


@pytest.mark.asyncio
async def test_schedule_runs_task_to_completion() -> None:
    scheduler = BackgroundTaskScheduler()
    seen: list[str] = []

    async def work() -> None:
        await asyncio.sleep(0)
        seen.append("done")

    task = scheduler.schedule(work, "demo")
    await task
    assert seen == ["done"]


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_tasks() -> None:
    scheduler = BackgroundTaskScheduler()
    completed = asyncio.Event()

    async def slow() -> None:
        await asyncio.sleep(0.05)
        completed.set()

    scheduler.schedule(slow, "slow")
    assert scheduler.pending == 1
    await scheduler.drain(timeout=2.0)
    assert completed.is_set()
    assert scheduler.pending == 0


@pytest.mark.asyncio
async def test_drain_swallows_task_errors() -> None:
    scheduler = BackgroundTaskScheduler()

    async def boom() -> None:
        raise RuntimeError("nope")

    scheduler.schedule(boom, "boom")
    # Errors inside scheduled tasks are logged, not propagated.
    await scheduler.drain(timeout=2.0)
    assert scheduler.pending == 0
