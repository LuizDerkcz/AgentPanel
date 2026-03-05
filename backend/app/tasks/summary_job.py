"""
Asyncio-native periodic task for AI summary generation.
Started as part of the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging

from app.services.summarizer import run_summary_batch

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _periodic_summary_loop(interval_minutes: int) -> None:
    logger.info("Summary scheduler started — interval: %d minute(s).", interval_minutes)
    while True:
        try:
            await run_summary_batch()
        except Exception as e:
            logger.error("Summary batch job failed: %s", e)
        await asyncio.sleep(interval_minutes * 60)


async def start_summary_scheduler(interval_minutes: int) -> None:
    global _task
    _task = asyncio.create_task(
        _periodic_summary_loop(interval_minutes),
        name="summary_batch",
    )


def stop_summary_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        logger.info("Summary scheduler stopped.")
